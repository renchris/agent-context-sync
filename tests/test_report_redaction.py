"""The setup report's redaction, in both reports: ``agentsync setup-report`` (``setup_report.Redactor`` and
``_build_redactor``) and ``scripts/install.sh``'s shell fallback (``redaction_pairs``, ``redact_stream``).
Every case is tested in both, with made-up names (B13 round 3, CONTRACTS §16.46)."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
import tomllib
import unicodedata
from pathlib import Path

import pytest

from agentsync import arm_local, cli, net, report_cuts, setup_report
from agentsync import loop as loop_module
from agentsync.config import default_config_text, load_config, local_source_table, toml_string
from agentsync.ops import doctor
from agentsync.text import safe_text
from test_install_oneshot import (  # noqa: F401  (fixture)
    _C1253_ROWS,
    _C1285_ROWS,
    _REPORT_CUTS_BLOCK,
    BASH32,
    INSTALL_SH,
    _cloud,
    _fuzzy_report,
    _install_bytes,
    _renderer_home,
    _shell_cut_stdout,
    _stub_id,
    _write_config,
    _write_exe,
    env,
    friction_path,
    install_log_path,
    install_sh,
    real_env,
    report_path,
    wheel,
)
from test_setup_report import (  # noqa: F401  (fixtures)
    _REAL_FULL_NAME,
    _RULING_102_LEAKS,
    R146_FREE_ROWS,
    V5_HAPPY,
    _separator_lines,
    fake_mac,
    section,
    summary_of,
    write_friction,
    write_install_log,
)

OWNER = setup_report._REPO_SLUG.split("/", 1)[0]
REPO = setup_report._REPO_SLUG.split("/", 1)[1]


def _owner_name() -> str:
    """A two-word full name whose words, run together in the other order, are this repository's public owner
    ("Doe Jane" for an owner "janedoe"): made at run time, so no name is written here."""
    half = len(OWNER) // 2
    return f"{OWNER[half:].capitalize()} {OWNER[:half].capitalize()}"


def _gecos(monkeypatch: pytest.MonkeyPatch, full: str) -> None:
    class Entry:
        pw_gecos = full

    monkeypatch.setattr(setup_report, "_full_name", _REAL_FULL_NAME)
    monkeypatch.setattr(setup_report.pwd, "getpwuid", lambda _uid: Entry())


def _shell_report(env: dict[str, str], friction: str, **extra: str) -> str:  # noqa: F811  (fixture name)
    path = Path(env["HOME"]) / "agent-context" / "setup" / "friction.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(friction, encoding="utf-8")
    cp = install_sh({**env, **extra}, "--report-only")
    assert cp.returncode == 0, cp.stderr
    return report_path(env).read_text(encoding="utf-8")


# ---- this repository's own locator (R3, R10, R20, R39) ----------------------------------------------------

URL_LINES = (
    f"issue link (review the report first): {setup_report.ISSUE_URL}",
    f"copy the prompt again from README.md on the main branch of https://github.com/{OWNER}/{REPO}",
    f"- install source: github.com/{OWNER}/{REPO}",
    f"origin git@github.com:{OWNER}/{REPO}.git; GitHub.com/{OWNER.upper()}/{REPO}.",
)
NAME_LINES = (  # the same joined name anywhere else, a fork's owner included: still the name
    f"https://github.com/{OWNER}/{REPO}-fork",
    f"https://codeberg.org/{OWNER}/{REPO}",
    f"~/{OWNER}-notes and /Users/{OWNER}/x",
)


def test_the_repos_own_locator_is_never_cut_by_a_name_run_together() -> None:
    """On a Mac whose full name run together is this repository's public owner, every copy of the issue link
    and the source URL in the report body read github.com/<name>/..., and the Redaction line counted a name
    (B13 R3, R10, R20, R39). The locator is held whole; the joined name stays redacted everywhere else."""
    red = setup_report.Redactor()
    red.add("name", _owner_name(), fuzzy=True)
    for first, second in (_owner_name().split(), reversed(_owner_name().split())):
        red.add("name", f"{first} {second}", fuzzy=True)
    for line in URL_LINES:
        assert red.redact(line) == line
        assert red.scrub(line) == line
    assert not red.counts
    assert red.redact(NAME_LINES[0]) == f"https://github.com/<name>/{REPO}-fork"
    assert red.redact(NAME_LINES[1]) == f"https://codeberg.org/<name>/{REPO}"
    # B13 round 34 C822 (ruling 91): a path in another home is cut whole
    assert red.redact(NAME_LINES[2]) == "~/<name>-notes and /Users/<path>"


def test_the_python_report_keeps_the_repos_own_locator(
    fake_mac: dict[str, Path],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gecos(monkeypatch, _owner_name())
    write_install_log(fake_mac)
    said = "; ".join((*URL_LINES, *NAME_LINES))
    write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", f"| {said} |"))
    text, _summary = summary_of(fake_mac)
    friction = section(text, "Agent friction log")
    for line in URL_LINES:
        assert line in friction, friction
    assert f"github.com/<name>/{REPO}-fork; https://codeberg.org/<name>/{REPO}; ~/<name>-notes" in friction


def test_the_shell_report_keeps_the_repos_own_locator(env: dict[str, str]) -> None:  # noqa: F811
    _stub_id(env, _owner_name())
    said = "\n".join(
        f"F{i} | step 1 | clean | 1 | {line} | -" for i, line in enumerate((*URL_LINES, *NAME_LINES))
    )
    text = _shell_report(env, f"Prompt: v10\n{said}\n")
    for line in URL_LINES:
        assert f"| {line} |" in text, text
    assert f"| https://github.com/<name>/{REPO}-fork |" in text
    assert f"| https://codeberg.org/<name>/{REPO} |" in text
    assert "| ~/<name>-notes and /Users/<path> |" in text, "B13 round 35 ruling 102 (C901): cut whole"


# ---- a second pass over redacted text (R69) ---------------------------------------------------------------


def test_a_second_pass_leaves_a_placeholder_whole_and_a_bracketed_value_is_still_replaced() -> None:
    """The Summary's item lines are redacted twice, and a friction log can hold an earlier report's
    placeholders: a value spelled like a placeholder's word turned ``<host>`` into ``<<folder-1>>``. A
    placeholder this Redactor issued, an earlier report's numbered one and a template field are held; a
    registered value inside angle brackets (``<contoso>``) is still replaced (B13 R69)."""
    red = setup_report.Redactor()
    red.add("host", "contoso-mbp", ignore_case=True)
    red.add("folder", "Host", fuzzy=True)
    once = red.redact("ran on contoso-mbp today")
    assert once == "ran on <host> today"
    assert red.redact(once) == once and dict(red.counts) == {"host": 1}
    red = setup_report.Redactor()
    red.add("folder", "Folder 1", fuzzy=True)
    red.add("folder", "Source", fuzzy=True)
    assert (
        red.redact("<folder-1> and <source-2>, <org-N> and <team>")
        == "<folder-1> and <source-2>, <org-N> and <team>"
    )
    assert not red.counts
    red = setup_report.Redactor()
    red.add("org", "contoso", fuzzy=True)
    red.add("user", "jdoe")
    assert red.redact("git clone <contoso> as <jdoe>") == "git clone <<org-1>> as <<user>>"
    assert red.scrub("<Contoso>") == "<<org-1>>"


def test_the_python_report_holds_a_placeholder_in_an_item_line(fake_mac: dict[str, Path]) -> None:  # noqa: F811
    """A listed folder named "Host"; the error line is redacted in the friction log and twice in the
    Summary's item list."""
    (fake_mac["home"] / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Host").mkdir()
    write_install_log(fake_mac)
    error = "2026-09-29T10:00:31Z | step 3 | error | ran on <host>, saw <source-2> | -\n"
    write_friction(fake_mac, V5_HAPPY.replace("2026-09-29T10:01:00Z", error + "2026-09-29T10:01:00Z", 1))
    text, summary = summary_of(fake_mac)
    assert "<<" not in text
    assert "ran on <host>, saw <source-2>" in section(text, "Agent friction log")
    assert "ran on <host>, saw <source-2>" in summary, summary


def test_the_shell_report_holds_the_same_placeholders(env: dict[str, str]) -> None:  # noqa: F811
    home = Path(env["HOME"])
    org = home / "Library" / "CloudStorage" / "OneDrive-Contoso"
    (org / "Host").mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f'[[source]]\nid = "plans"\nkind = "local"\npath = "{org / "Host"}"\n')
    text = _shell_report(env, "Prompt: v10\nF1 | step 1 | clean | 1 | on <host>, <source-2>; <Contoso> | -\n")
    assert "| on <host>, <source-2>; <<org-1>> |" in text, text


# ---- Unicode case boundaries and accents (R70, R115) ------------------------------------------------------


def _nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def test_a_camelcase_name_with_an_accented_capital_splits() -> None:
    """``CaféPlans`` and ``MonÉtude`` never split at their case boundary (an ASCII-only split), so their
    spelled-apart forms printed in clear; NFD text splits as NFC does, and ASCII names split as before (B13
    R70). The shell report registers no CamelCase forms: it has nothing to split."""
    nfd_words = [unicodedata.normalize("NFC", w) for w in setup_report._tokens(_nfd("CaféPlans"))]
    assert setup_report._tokens("CaféPlans") == nfd_words == ["Café", "Plans"]
    assert setup_report._tokens("MonÉtude") == ["Mon", "Étude"]
    ascii_names = {"ClientAlpha": ["Client", "Alpha"], "HTTPServer": ["HTTP", "Server"], "R2D2": ["R2", "D2"]}
    ascii_names |= {"iPhone": ["i", "Phone"], "ABCDef": ["ABC", "Def"], "v2Plans": ["v2", "Plans"]}
    for name, words in ascii_names.items():
        assert setup_report._tokens(name) == words
    red = setup_report.Redactor()
    red.add("folder", "CaféPlans", fuzzy=True)
    red.add("folder", "MonÉtude", fuzzy=True)
    for text in ("Café Plans", "café-plans", "CAFÉ_PLANS", _nfd("Café Plans"), _nfd("café-plans")):
        assert red.redact(text) == "<folder-1>", text
    for text in ("Mon Étude", "mon-étude", "MON_ÉTUDE", _nfd("Mon Étude"), _nfd("mon-étude")):
        assert red.redact(text) == "<folder-2>", text


def test_an_unaccented_or_mixed_spelling_is_redacted() -> None:
    """``Cafe Resume`` and a string that mixes NFC and NFD letters printed in clear: matching is now
    accent-insensitive, and the bare letter never matches the front of an accented one (B13 R115)."""
    red = setup_report.Redactor()
    red.add("folder", "Café Résumé", fuzzy=True)
    mixed = "Café " + _nfd("Résumé")
    for text in ("Cafe Resume", "cafe-resume", mixed, "Caf" + _nfd("é") + " Résumé", "Café\\ Résumé"):
        assert red.redact(text) == "<folder-1>", text
    assert red.redact(f"~/x/{mixed}/y") == "~/x/<folder-1>/y"
    red = setup_report.Redactor()
    red.add("folder", "Café", fuzzy=True)
    for text in ("Cafés", _nfd("Cafés"), "Cafeteria"):
        assert red.redact(text) == text, "no partial match, no stray accent"
    red = setup_report.Redactor()
    red.add("name", "José")
    assert red.redact(f"José, Jose, {_nfd('José')}; jose") == "<name>, <name>, <name>; jose", (
        "exact: case kept"
    )


def test_the_python_report_redacts_an_unaccented_folder_name(fake_mac: dict[str, Path]) -> None:  # noqa: F811
    (fake_mac["home"] / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Café Résumé").mkdir()
    write_install_log(fake_mac)
    said = "Cafe Resume, " + "Café " + _nfd("Résumé")
    write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", f"| {said} |"))
    text, _summary = summary_of(fake_mac)
    assert re.search(r"\| (<folder-\d+>), \1 \|", section(text, "Agent friction log")), text
    assert "Caf" not in text and "Resume" not in text


def test_the_shell_report_redacts_an_unaccented_folder_name(env: dict[str, str]) -> None:  # noqa: F811
    home = Path(env["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Café Résumé"
    folder.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f'[[source]]\nid = "plans"\nkind = "local"\npath = "{folder}"\n', encoding="utf-8")
    said = f"Cafe Resume, Café Résumé, {_nfd('Café Résumé')}"
    text = _shell_report(env, f"Prompt: v10\nF1 | step 1 | clean | 1 | {said} | -\n")
    assert "| <folder-1>, <folder-1>, <folder-1> |" in text, text


# ---- configured source ids (B13 round 5 F6) ---------------------------------------------------------------

_ID_FRICTION = (
    "Prompt: v13\n"
    '2026-10-12T10:00:00Z | step 2 | deviation | in [[source]] id = "{id}"; I did not apply it; '
    "~/.local/bin/agentsync exclude-empty {id} | -\n"
    "2026-10-12T10:01:00Z | step 2 | deviation | the inbox source and the docs repo are fine | -\n"
)


def test_the_python_report_redacts_a_configured_source_id(fake_mac: dict[str, Path]) -> None:  # noqa: F811
    """A source id is its folder's name in slug form, and the empty-folder wait an agent quotes names it."""
    [sid, *_] = [s.id for s in load_config(fake_mac["config"]).sources]
    write_install_log(fake_mac)
    write_friction(fake_mac, _ID_FRICTION.format(id=sid))
    text, _summary = summary_of(fake_mac)
    assert sid not in section(text, "Agent friction log"), text
    assert "the inbox source and the docs repo are fine" in text


def test_the_shell_report_redacts_a_configured_source_id(env: dict[str, str]) -> None:  # noqa: F811
    """The shell report read only ``path`` from sources.toml, so a quoted id showed the folder's name in slug
    form where the Python report showed a placeholder. Every id in a ``[[source]]`` table is <source-N>, as a
    whole word ("-" is no word character); one of agentsync's own words (``inbox``) is kept for a source that
    is not a cloud folder, and an id outside a ``[[source]]`` table is not read. A cloud folder's id that is
    its folder's name in another spelling is that folder's <folder-N>, as in the Python report (B13 round 6
    G8), so the wait an agent quotes reads as the folder its --source-local names."""
    home = Path(env["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Project Alpha"
    folder.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '[graph]\nid = "graph-tenant"\n\n'
        f'[[source]]\nid = "project-alpha"\nkind = "local"\npath = "{folder}"\n\n'
        f'[[source]]\nid = "inbox"\nkind = "inbox"\npath = "{home}/agent-context/inbox"\n\n'
        f'[[source]]\nid = "gamma-bridge-mail"\nkind = "local"\npath = "{home}/Mail/Gamma Bridge"\n',
        encoding="utf-8",
    )
    said = _ID_FRICTION.format(id="project-alpha") + (
        "2026-10-12T10:02:00Z | step 2 | clean | --source-local "
        "~/Library/CloudStorage/OneDrive-Contoso/Project Alpha; gamma-bridge-mail kept | -\n"
    )
    text = _shell_report(env, said)
    friction = text.split("\n## Agent friction log\n", 1)[1].split("\n## ", 1)[0]
    assert "project-alpha" not in text and "<source-3>" not in text, friction
    assert 'id = "<folder-1>"' in friction and "exclude-empty <folder-1> |" in friction, friction
    # B13 round 41 C2071 (ruling 142): numbered as the Python report numbers ids, in config order among those
    # that take a number of their own, so the shared and the kept id take none (it printed <source-3>)
    assert "OneDrive-<org-1>/<folder-1>; <source-1> kept" in friction, "not a cloud folder: its own number"
    assert "the inbox source and the docs repo are fine" in friction
    assert "\n- <source-N>: a configured source id, or its folder's <folder-N>" in text


def test_the_shell_report_keeps_a_deduplicated_ids_own_number(env: dict[str, str]) -> None:  # noqa: F811
    """Two cloud folders of one name under two organisations: their ids are "reports" and "reports-2". The
    first is the folder's name in another spelling and reads as its <folder-N>; the second is not, and keeps
    its <source-N>, so the report still says which of the two a wait is about (B13 round 6 G8)."""
    home = Path(env["HOME"])
    cloud = home / "Library" / "CloudStorage"
    one, two = cloud / "OneDrive-Contoso" / "Reports", cloud / "OneDrive-Fabrikam" / "Reports"
    one.mkdir(parents=True)
    two.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        f'[[source]]\nid = "reports"\nkind = "local"\npath = "{one}"\n\n'
        f'[[source]]\nid = "reports-2"\nkind = "local"\npath = "{two}"\n',
        encoding="utf-8",
    )
    text = _shell_report(
        env, "Prompt: v13\n2026-10-12T10:00:00Z | step 2 | clean | reports and reports-2 | -\n"
    )
    friction = text.split("\n## Agent friction log\n", 1)[1].split("\n## ", 1)[0]
    # its own number as the Python report counts them, the shared id taking none (B13 round 41 C2071)
    assert "| <folder-1> and <source-1> |" in friction, friction


def _shell_report_with(
    env: dict[str, str],  # noqa: F811
    sources: dict[str, str],
    said: str,
    **extra: str,
) -> str:
    """The shell report of a Mac whose config has one cloud folder per ``sources`` item (id -> folder name
    under OneDrive-Contoso), with ``said`` as the friction log's one line."""
    home = Path(env["HOME"])
    cloud = home / "Library" / "CloudStorage" / "OneDrive-Contoso"
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    tables = []
    for sid, name in sources.items():
        (cloud / name).mkdir(parents=True)
        tables.append(f'[[source]]\nid = "{sid}"\nkind = "local"\npath = "{cloud / name}"\n')
    cfg.write_text("\n".join(tables), encoding="utf-8")
    return _shell_report(env, f"Prompt: v13\n2026-10-12T10:00:00Z | step 2 | clean | {said} | -\n", **extra)


def test_the_shell_report_stays_public_with_a_generic_folders_id(env: dict[str, str]) -> None:  # noqa: F811
    """B13 round 7 H39 and H57 (one defect): round 6 G8 gave a cloud folder's slug id its folder's
    <folder-N>, and G48's skip went by the pair's own value. So the id "downloads" of a configured Downloads
    (lower case: not the generic name, and a word install.sh prints) made the shell report private only
    again, with no issue link. The folder's own name now decides for everything that shares its
    placeholder."""
    text = _shell_report_with(
        env,
        {"documents": "Documents", "downloads": "Downloads", "shared": "Shared"},
        "documents, downloads and shared synced",
    )
    friction = text.split("\n## Agent friction log\n", 1)[1].split("\n## ", 1)[0]
    assert "| <folder-1>, <folder-2> and <folder-3> synced |" in friction, friction
    assert "Private only:" not in text, text.split("\n## Redaction\n", 1)[1]
    assert text.rstrip("\n").splitlines()[-1].startswith(setup_report.ISSUE_URL)


def test_the_shell_report_is_private_with_a_report_words_folder_beside_a_generic_one(
    env: dict[str, str],  # noqa: F811
) -> None:
    """The guard for H39: a folder whose name is a word the report prints, and is no generic name, still
    makes the report private only, with a generic folder and its id beside it."""
    text = _shell_report_with(env, {"downloads": "Downloads", "status": "Status"}, "downloads synced")
    assert "Private only:" in text and setup_report.ISSUE_URL not in text, text


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_the_shell_report_reads_an_accented_folders_id_as_the_folder(
    env: dict[str, str],  # noqa: F811
    form: str,
    locale: str,
) -> None:
    """B13 round 7 H76: the shell's id key kept accents, so the id "cafe-plans" of the cloud folder "Café
    Plans" (the slug has none) was not its folder's name to it: <source-1> here and <folder-1> in the Python
    report, which drops them (``_norm``). In either Unicode form of the name on disk, and in the locale a
    terminal has: a first build asked bash whether the key was ASCII, and under UTF-8 its a-z holds é."""
    name = unicodedata.normalize(form, "Café Plans")
    text = _shell_report_with(
        env, {"cafe-plans": name}, "~/.local/bin/agentsync exclude-empty cafe-plans", LC_ALL=locale
    )
    friction = text.split("\n## Agent friction log\n", 1)[1].split("\n## ", 1)[0]
    assert "exclude-empty <folder-1> |" in friction and "<source-" not in friction, friction
    assert setup_report._norm("cafe-plans") == setup_report._norm(name), "the Python report's rule"


def test_the_shell_generic_source_ids_are_the_reports() -> None:
    """install.sh keeps the same source ids as agentsync's own words as setup_report does."""
    pattern = r'^GENERIC_SOURCE_IDS="(?:\$GENERIC_SOURCE_IDS )?([^"]*)"'
    found = re.findall(pattern, INSTALL_SH.read_text(), re.M)
    assert set(" ".join(found).split()) == set(setup_report._GENERIC_IDS)


# ---- the parts of a full name (R71, R72, R73) -------------------------------------------------------------

NAME_CASES = [
    ("Doe, Jean-Luc", "Jean met Luc; JEAN-LUC and Jean-Luc", "<name> met <name>; <name> and <name>"),
    ("DOE Jane", "Doe and Doe Projects, DOE, Jane", "<name> and <name> Projects, <name>, <name>"),
    ("LI, JANE", "asked Li, LI; Jane Li and Jane agreed", "asked <name>, <name>; <name> and <name> agreed"),
    ("Jane Ål", f"asked ÅL, Ål and {unicodedata.normalize('NFD', 'Ål')}", "asked <name>, <name> and <name>"),
    ("Tanaka, Ai", "asked Ai; the AI agent agreed", "asked <name>; the AI agent agreed"),
    ("Jane Doe (Northwind (EU))", "Northwind team; NORTHWIND; EU", "<name> team; <name>; EU"),
    ("Doe, Jane (NORTHWIND [EU])", "Northwind and NORTHWIND", "<name> and <name>"),
]
NAME_IDS = [
    "hyphenated",
    "all-capitals",
    "two-letter-capitals",
    "non-ascii-two-letter",
    "abbreviation",
    "nested",
]
NAME_IDS += ["nested-capitals"]


@pytest.mark.parametrize(("gecos", "said", "shown"), NAME_CASES, ids=NAME_IDS)
def test_the_python_report_redacts_every_part_of_a_name(
    fake_mac: dict[str, Path],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    gecos: str,
    said: str,
    shown: str,
) -> None:
    """A hyphenated part ("Jean-Luc") registered only whole, an all-capitals one ("DOE") only as written and
    in upper case, so "Jean", "Luc" and "Doe" printed in clear (B13 R71); a nested bracket ("(Northwind
    (EU))") kept the outer one in the core, so the bare company word printed in clear (R73)."""
    _gecos(monkeypatch, gecos)
    write_install_log(fake_mac)
    write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", f"| {said} |"))
    text, _summary = summary_of(fake_mac)
    assert f"| {shown} |" in section(text, "Agent friction log"), section(text, "Agent friction log")


@pytest.mark.parametrize(("gecos", "said", "shown"), NAME_CASES, ids=NAME_IDS)
def test_the_shell_report_redacts_every_part_of_a_name(
    env: dict[str, str],  # noqa: F811
    gecos: str,
    said: str,
    shown: str,
) -> None:
    """The same parts in the shell fallback, which split only at commas, registered no capitalised form, and
    wrote a two-letter part in one form only, with no upper-case and no NFD form (B13 R71, R72)."""
    _stub_id(env, gecos)
    text = _shell_report(env, f"Prompt: v10\nF1 | step 1 | clean | 1 | {said} | -\n", LC_ALL="en_US.UTF-8")
    assert f"| {shown} |" in text, text


# ---- proxies (R4, R27, R42, R67, R68) ---------------------------------------------------------------------

GIT_ERROR = "git pull: Could not resolve proxy: {h} (exit 1) → set the proxy"


def _proxy_friction(host: str) -> str:
    return V5_HAPPY.replace(
        "2026-09-29T10:01:00Z",
        f"2026-09-29T10:00:31Z | step 3 | error | {GIT_ERROR.format(h=host)} | -\n"
        f"2026-09-29T10:00:32Z | step 3 | deviation | tried http://{host}:3128 by hand | -\n"
        "2026-09-29T10:01:00Z",
        1,
    )


@pytest.mark.parametrize("where", ["gitconfig", "environment"])
def test_the_python_report_keeps_its_words_beside_a_proxy_named_proxy(
    fake_mac: dict[str, Path],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    where: str,
) -> None:
    """A proxy host with no dot that is a word the report prints (``proxy``) was registered bare: the report
    read ``HTTPS_<proxy-1>/ALL_<proxy-1>``, ``Could not resolve <<proxy-1>-1>`` (B13 R42, R67). It is matched
    only where a URL names it."""
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    if where == "gitconfig":
        (fake_mac["home"] / ".gitconfig").write_text(
            "[http]\n\tproxy = http://proxy:3128\n", encoding="utf-8"
        )
    else:
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    write_install_log(fake_mac)
    write_friction(fake_mac, _proxy_friction("proxy"))
    text, summary = summary_of(fake_mac)
    assert "<<" not in text
    for part in (section(text, "Agent friction log"), summary):
        assert GIT_ERROR.format(h="proxy") in part, part
    assert re.search(r"tried http://<proxy-\d+>:3128 by hand", text), text
    assert "HTTPS_PROXY/ALL_PROXY set in this shell" in section(text, "Environment")


def test_the_python_report_redacts_a_dotless_proxy_that_is_not_a_word(
    fake_mac: dict[str, Path],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://contosogw:3128")
    write_install_log(fake_mac)
    write_friction(fake_mac, _proxy_friction("contosogw"))
    text, _summary = summary_of(fake_mac)
    assert "contosogw" not in text.lower()
    assert re.search(r"Could not resolve proxy: <proxy-\d+> \(exit 1\)", text), text


@pytest.mark.parametrize(("config", "shown"), [("http://proxy.contoso.example:3128", "set"), ("", "not set")])
def test_the_python_report_says_whether_git_has_a_proxy(
    fake_mac: dict[str, Path],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    config: str,
    shown: str,
) -> None:
    """A proxy set only in git's config was registered for redaction but never shown as set, beside the
    logged "Could not resolve proxy: <proxy-1>" (B13 R27). Set or not set, never the host; an empty
    ``proxy =`` is git's off switch."""
    monkeypatch.setattr(setup_report, "_git_proxy_urls", lambda: [config] if config else [])
    write_install_log(fake_mac)
    write_friction(fake_mac)
    text, _summary = summary_of(fake_mac)
    environment = section(text, "Environment")
    assert f"· proxy in git's config: {shown}" in environment, environment
    assert "contoso.example" not in text


def test_the_python_report_redacts_the_launchagents_own_proxy(
    fake_mac: dict[str, Path],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """doctor's network.proxy.job line prints the LaunchAgent's proxy where it differs from this shell's, and
    only this shell's proxies were registered (B13 R68)."""
    job = "http://jobproxy.fabrikam.example:8080"
    seen: list[tuple[str, ...]] = []

    def launchctl_env(names: tuple[str, ...]) -> dict[str, str]:
        seen.append(tuple(names))
        return {"HTTPS_PROXY": job, "NO_PROXY": "intranet.fabrikam.example"}

    monkeypatch.setattr(cli, "_launchctl_env", launchctl_env)
    assert cli._job_proxy_urls(None) == [job], "URL variables only, never NO_PROXY"
    assert "NO_PROXY" not in seen[0] and "HTTP_PROXY" in seen[0]
    line = f"[FAIL] network.proxy.job — the LaunchAgent resolves {job} (source: env) but this shell does not"
    write_install_log(fake_mac)
    write_friction(fake_mac)
    hooks = setup_report.ReportHooks(doctor=lambda _c: [line], job_proxy=cli._job_proxy_urls)
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    assert "fabrikam" not in text
    assert re.search(r"the LaunchAgent resolves http://<proxy-\d+>:8080", section(text, "Doctor")), text


def test_the_shell_generic_proxy_hosts_are_the_reports() -> None:
    body = INSTALL_SH.read_text().split("\ngeneric_proxy_host() {\n", 1)[1].split("\n}\n", 1)[0]
    words = {
        w.strip()
        for line in body.splitlines()
        if ") return 0 ;;" in line
        for w in line.split(")")[0].split("|")
    }
    assert words == set(setup_report._GENERIC_PROXY_HOSTS)


def _scutil(environ: dict[str, str], body: str) -> None:
    script = "#!/bin/bash\n[ \"$1\" = --proxy ] || exit 1\ncat <<'EOF'\n<dictionary> {\n" + body + "}\nEOF\n"
    _write_exe(Path(environ["PATH"].split(":")[0]) / "scutil", script)


@pytest.mark.parametrize("host", ["proxy", "contosogw"])
def test_the_shell_report_matches_a_dotless_proxy_as_the_python_report_does(
    env: dict[str, str],  # noqa: F811
    host: str,
) -> None:
    """The shell fallback skipped every proxy host with no dot, so "Could not resolve proxy: contosogw" was in
    clear; now a generic word is matched only inside a URL and any other host bare (B13 R42, R67)."""
    (Path(env["HOME"]) / ".gitconfig").write_text(f"[http]\n\tproxy = http://{host}:3128\n")
    friction = (
        "Prompt: v10\n"
        f"F1 | step 1 | error | {GIT_ERROR.format(h=host)} | -\n"
        f"F2 | step 1 | clean | 1 | tried http://{host}:3128 and user@{host}/x by hand | -\n"
    )
    text = _shell_report(env, friction, HTTPS_PROXY=f"http://{host}:3128")
    assert "- proxy environment: yes (set: HTTPS_PROXY)" in text
    assert re.search(r"\| tried http://<proxy-\d+>:3128 and user@<proxy-\d+>/x by hand \|", text), text
    if host == "proxy":
        assert f"| {GIT_ERROR.format(h='proxy')} |" in text
    else:
        assert host not in text and re.search(r"Could not resolve proxy: <proxy-\d+> \(exit 1\)", text)


def test_the_shell_report_names_the_system_proxy_and_gits_by_yes_or_no(env: dict[str, str]) -> None:  # noqa: F811
    """The shell fallback named only the proxy variables, not a system proxy or PAC (B13 R4) nor a proxy in
    git's config (R27): each by its Enable flag (a host key can stay while its flag is 0), never by host."""
    _scutil(
        env,
        "  HTTPSEnable : 0\n  HTTPSProxy : stale.fabrikam.example\n  ProxyAutoConfigEnable : 1\n"
        "  ProxyAutoConfigURLString : http://pac.fabrikam.example/proxy.pac\n"
        "  ProxyAutoDiscoveryEnable : 0\n",
    )
    (Path(env["HOME"]) / ".gitconfig").write_text("[http]\n\tproxy = http://gw.fabrikam.example:3128\n")
    text = _shell_report(env, "Prompt: v10\n")
    assert "- system proxy (scutil --proxy): manual HTTPS no · PAC yes · WPAD no" in text, text
    assert "- proxy in git's config: set (git uses it before HTTPS_PROXY)" in text
    assert "fabrikam" not in text
    (Path(env["HOME"]) / ".gitconfig").write_text("[http]\n\tproxy =\n")
    _scutil(env, "")
    text = _shell_report(env, "Prompt: v10\n")
    assert "- system proxy (scutil --proxy): manual HTTPS no · PAC no · WPAD no" in text
    assert "- proxy in git's config: not set" in text
    _write_exe(Path(env["PATH"].split(":")[0]) / "scutil", "#!/bin/bash\nexit 1\n")
    assert "- system proxy (scutil --proxy): unknown" in _shell_report(env, "Prompt: v10\n")


def test_the_shell_report_redacts_the_launchagents_own_proxy(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    """The LaunchAgent's ``launchctl setenv`` proxy, which doctor's network.proxy.job line prints (R68)."""
    job = "http://jobproxy.fabrikam.example:8080"
    stub = _write_exe(
        tmp_path / "launchctl-getenv",
        f'#!/bin/bash\n[ "$1 $2" = "getenv HTTPS_PROXY" ] && {{ echo {job}; exit 0; }}\nexit 64\n',
    )
    friction = f"Prompt: v10\nF1 | step 1 | clean | 1 | the LaunchAgent resolves {job} | -\n"
    text = _shell_report(env, friction, AGENTSYNC_LAUNCHCTL=str(stub))
    assert "fabrikam" not in text
    assert re.search(r"\| the LaunchAgent resolves http://<proxy-\d+>:8080 \|", text), text


# ---- review of the round's own changes ---------------------------------------------------------------------


def test_an_accented_spelling_of_a_known_value_is_still_matched() -> None:
    """Keyed without accents, a value whose unaccented spelling was registered first was dropped, and the
    earlier value's pattern has no accented letters: "Société Générale" printed in clear after a library
    "Societe-Generale" (review of R115). It keeps the first placeholder and is matched as written."""
    red = setup_report.Redactor()
    red.add("library", "Societe-Generale", fuzzy=True)
    red.add("org", "Société Générale", fuzzy=True)
    assert red.redact("OneDrive-Société Générale/Plans") == "OneDrive-<library-1>/Plans"
    red = setup_report.Redactor()
    red.add("name", "Jose")
    red.add("folder", "José", fuzzy=True)
    assert red.redact("José, josé, Jose") == "<name>, <name>, <name>"


def test_a_value_written_in_angle_brackets_is_matched_whole() -> None:
    """The placeholder hold took any ``<word>``, so a folder named ``<Draft>`` was never matched (review of
    R69): only placeholder shapes are held."""
    red = setup_report.Redactor()
    red.add("folder", "<Draft>", fuzzy=True)
    red.add("org", "contoso", fuzzy=True)
    assert (
        red.redact("see <Draft> and <Draft> Plans; <contoso>")
        == "see <folder-1> and <folder-1> Plans; <<org-1>>"
    )


def test_the_shell_report_leaves_a_particle_in_capitals(env: dict[str, str]) -> None:  # noqa: F811
    """A lower-case two-letter part ("de") names no one alone, in any case: the shell registered its upper
    case, which the Python report never does (review of R72)."""
    _stub_id(env, "Jean de Fontaine")
    text = _shell_report(env, "Prompt: v10\nF1 | step 1 | clean | 1 | DE and de stay; Jean Fontaine | -\n")
    assert "| DE and de stay; <name> |" in text, text


# ---- install.log written in another locale (R111) ----------------------------------------------------------


@pytest.mark.parametrize("where", ["loose", "cloudstorage"])
def test_the_shell_report_matches_a_name_logged_in_utf8_and_reported_in_c(
    env: dict[str, str],  # noqa: F811
    where: str,
) -> None:
    """bash 3.2's ``printf %q`` writes "Café Notes" as ``Café\\ Notes`` under UTF-8 and as ``$'Caf\\303\\251
    Notes'`` under C. The shell built its %q pairs in the report's locale only, so a name a UTF-8 Terminal
    logged printed in clear in a report written under C (B13 R111). The Python report needs no change."""
    home = Path(env["HOME"])
    org = home / "Library" / "CloudStorage" / "OneDrive-Contoso"
    (org / "Café Notes").mkdir(parents=True)
    typed = "Café Notes" if where == "loose" else f"{org}/Café Notes"
    if where == "loose":
        (org / "Café Notes").rmdir()
    word = subprocess.run(
        [BASH32, "-c", 'printf "%q" "$1"', "_", typed],
        capture_output=True,
        text=True,
        check=True,
        env={**env, "LC_ALL": "en_US.UTF-8"},
    ).stdout
    assert "\\ Notes" in word and "$'" not in word, word
    setup = home / "agent-context" / "setup"
    setup.mkdir(parents=True)
    head = "run=20261010T010000Z-1 start install.sh compat=10 commit=- kind=- source=- args=--source-local "
    (setup / "install.log").write_text(f"2026-10-10T01:00:00Z {head}{word}\n", encoding="utf-8")
    said = "a relative folder" if where == "loose" else "Café Notes"  # a loose word: only as the argument
    text = _shell_report(env, f"Prompt: v10\nF1 | step 1 | clean | 1 | {said} | -\n", LC_ALL="C")
    assert "Caf" not in text, text
    if where == "loose":
        assert "args=--source-local <path>\n" in text
    else:
        # in no config: its args= word keeps only its provider folder (B13 round 39 C1601)
        assert "args=--source-local ~/Library/CloudStorage/OneDrive-<org-1>/<path>\n" in text, text


# ---- the Redaction section's "Send it" sentence (R23) ------------------------------------------------------


def _send_it(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("Send it: "))


def test_the_issue_form_labels_are_the_templates() -> None:
    template = (Path(__file__).parent.parent / ".github" / "ISSUE_TEMPLATE" / "setup-report.yml").read_text()
    pairs = re.findall(r"\bid: (\w+)\n\s+attributes:\n\s+label: (.+)\n", template)
    labels = {field: label.strip().strip("\"'") for field, label in pairs}
    assert {f: labels[f] for f in setup_report.ISSUE_FIELD_LABELS} == setup_report.ISSUE_FIELD_LABELS
    assert set(setup_report.ISSUE_FIELDS.values()) == set(setup_report.ISSUE_FIELD_LABELS)


def test_the_send_it_sentence_names_only_the_fields_the_link_fills(
    fake_mac: dict[str, Path],  # noqa: F811
) -> None:
    """The sentence said the link fills "the Outcome, Run type, Prompt and Agent fields (nothing else)"; a
    friction log with no header gives a link with neither, and every link adds the Loop stage (B13 R23)."""
    write_install_log(fake_mac)
    write_friction(fake_mac, "2026-09-29T10:00:31Z | step 1 | error | git pull failed (exit 1) | -\n")
    text, _summary = summary_of(fake_mac)
    link = text.rstrip("\n").splitlines()[-1]
    sentence = _send_it(text)
    filled = setup_report.issue_link_fields(link)
    assert "Outcome" in filled and "Prompt version" not in filled and "Agent tool and model" not in filled
    assert "Prompt" not in sentence and "Agent" not in sentence, sentence
    for label in filled:
        assert label in sentence, (label, sentence)
    assert setup_report.issue_link_fields(setup_report.ISSUE_URL) == []
    blank = setup_report._redaction_section(setup_report.Redactor(), [], [])
    assert 'Send it: the link on the last line opens a blank "Setup report" issue form;' in "\n".join(blank)


def test_a_word_registered_after_a_url_only_proxy_host_is_still_redacted() -> None:
    """A generic proxy host (``http://gateway:8080``) was kept with the bare values, so a later value spelled
    the same (the bracketed word of "Doe, Jane (Gateway)") counted as known and printed in clear (second
    review of R42, R67)."""
    red = setup_report.Redactor()
    setup_report._add_proxy_host(red, "gateway")
    red.add("name", "Gateway")
    red.add("name", "GATEWAY")
    text = "works at Gateway GATEWAY via http://gateway:8080; network.gateway"
    assert red.redact(text) == "works at <name> <name> via http://<proxy-1>:8080; network.gateway"


def test_a_pac_url_that_is_only_a_generic_host_is_matched_inside_a_url(
    fake_mac: dict[str, Path],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PAC URL ``http://proxy`` was registered as a bare value after its scheme: "proxy" again turned the
    report's own words into placeholders (second review of R42, R67)."""
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(
        net, "system_proxy", lambda runner=None: net.SystemProxy(pac_enabled=True, pac_url="http://proxy")
    )
    write_install_log(fake_mac)
    write_friction(fake_mac, _proxy_friction("proxy"))
    text, _summary = summary_of(fake_mac)
    assert "<<" not in text and GIT_ERROR.format(h="proxy") in text
    assert "HTTPS_PROXY/ALL_PROXY set in this shell" in section(text, "Environment")


def test_the_shell_report_keeps_its_words_beside_a_pac_named_proxy(env: dict[str, str]) -> None:  # noqa: F811
    _scutil(env, "  ProxyAutoConfigEnable : 1\n  ProxyAutoConfigURLString : http://proxy\n")
    friction = f"Prompt: v10\nF1 | step 1 | error | {GIT_ERROR.format(h='proxy')} | -\n"
    text = _shell_report(env, friction, HTTPS_PROXY="http://proxy:8080")
    assert "- proxy environment: yes (set: HTTPS_PROXY)" in text
    assert f"| {GIT_ERROR.format(h='proxy')} |" in text, text


def test_an_unreadable_git_config_leaves_the_launchagents_proxy_redacted(
    env: dict[str, str],  # noqa: F811
    tmp_path: Path,
) -> None:
    """Under set -e a failed read of ~/.gitconfig ended the proxy list early, so the LaunchAgent's proxy after
    it was never registered (second review of R68)."""
    job = "http://jobproxy.fabrikam.example:8080"
    stub = _write_exe(
        tmp_path / "launchctl-getenv",
        f'#!/bin/bash\n[ "$1 $2" = "getenv HTTPS_PROXY" ] && {{ echo {job}; exit 0; }}\nexit 64\n',
    )
    gitconfig = Path(env["HOME"]) / ".gitconfig"
    gitconfig.write_text("[http]\n\tproxy = http://gw.fabrikam.example:3128\n")
    gitconfig.chmod(0)
    friction = f"Prompt: v10\nF1 | step 1 | clean | 1 | the LaunchAgent resolves {job} | -\n"
    text = _shell_report(env, friction, AGENTSYNC_LAUNCHCTL=str(stub))
    gitconfig.chmod(0o600)
    assert "fabrikam" not in text, text


def test_the_agents_name_keeps_a_configured_product_word_in_both_reports(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 20 X40, X81 (round 19 W17's shell twin): the shell report redacted a configured Copilot
    folder in the agent's name too, so its friction log, Summary and issue link read "GitHub <folder-N> CLI
    gpt-5", whose placeholder the name around it decodes, where the agentsync report keeps the product word.
    Both now keep it where it stands as a word of the name, and only there: a path in the name and the step
    line still read <folder-N>, and the legend says why."""
    from urllib.parse import parse_qs, urlsplit  # noqa: PLC0415

    from agentsync.config import local_source_table  # noqa: PLC0415

    home = Path(env["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Documents" / "Copilot"
    folder.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(local_source_table("s0", folder), encoding="utf-8")
    agent = "GitHub Copilot CLI gpt-5 in ~/Development/Copilot"
    text = _shell_report(
        env,
        f"Attempt: 2026-10-12T10:00:00Z\nPrompt: v15\nAgent: {agent}\n"
        "2026-10-12T10:00:01Z | step 2 | deviation | Copilot synced | -\n",
    )
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    python = red.redact_agent(agent)
    assert re.fullmatch(r"GitHub Copilot CLI gpt-5 in ~/Development/<folder-\d+>", python), python
    shell = re.sub(r"<folder-\d+>", "<folder-N>", python)
    friction = text.split("\n## Agent friction log\n", 1)[1].split("\n## ", 1)[0]
    assert re.sub(r"<folder-\d+>", "<folder-N>", friction).count(f"Agent: {shell}\n") == 1, friction
    assert re.search(r"\| <folder-\d+> synced \|", friction), friction
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    linked = re.sub(r"[^\w .:/()+,@<>-]", "", shell)  # as _issue_link and link_agent keep it: no "~"
    assert [re.sub(r"<folder-\d+>", "<folder-N>", a) for a in link["agent"]] == [linked], link
    kept = text.replace("GitHub Copilot CLI", "").replace("GitHub%20Copilot%20CLI", "")
    assert "Copilot" not in kept, "only the agent's name keeps the word"
    legend = " ".join(text.split("\n## Redaction\n", 1)[1].split())
    assert "; a configured one's coding-agent product word stays as written in the agent's name" in legend


@pytest.mark.parametrize(
    ("name", "agent", "private"),
    [
        ("Copilot", "GitHub Copilot CLI gpt-5", False),
        ("Copilot", "Kestrel 2", False),
        ("Codex", "Kestrel 2", False),
        ("GitHub Copilot", "Kestrel 2", False),
        ("Status", "GitHub Copilot CLI gpt-5", True),
    ],
)
def test_a_folder_named_only_by_product_words_leaves_both_reports_public(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    agent: str,
    private: bool,
) -> None:
    """B13 round 21 Y7, Y72: with a configured Copilot (or Codex) folder the agentsync report was private
    only, since its report words hold every coding-agent product word, while the shell report, whose
    printed text holds none in lower case, kept the public issue link. The placeholder reads back as a
    product's name, which the agent's name prints anyway (G48's reasoning for Documents), so both readers
    exempt such a name by rule and stay public; a folder named like a report word (Status) still makes both
    private only."""
    from urllib.parse import parse_qs, urlsplit  # noqa: PLC0415

    from agentsync.config import local_source_table  # noqa: PLC0415

    home = Path(env["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Documents" / name
    folder.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(local_source_table("s0", folder), encoding="utf-8")
    said = f"2026-10-12T10:00:01Z | step 2 | deviation | {name} synced | -"
    text = _shell_report(env, f"Attempt: 2026-10-12T10:00:00Z\nPrompt: v15\nAgent: {agent}\n{said}\n")
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    assert re.search(r"<folder-\d+> synced", red.redact(said)), "the folder is still redacted"
    assert red.private_only == private, (name, agent)
    assert ("Private only:" in text) == private, text.split("\n## Redaction\n", 1)[1]
    last = text.rstrip("\n").splitlines()[-1]
    assert last.startswith(setup_report.ISSUE_URL) != private, last
    if not private:
        assert parse_qs(urlsplit(last).query)["agent"] == [agent], last


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    ("name", "private"),
    [("\u200bStatus", True), ("\u2060Status", True), ("\u200bDocuments", False), ("\u200bQuokka", False)],
)
def test_a_folder_safe_text_prints_as_a_report_word_makes_both_reports_private_c1627(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    private: bool,
    locale: str,
) -> None:
    """B13 round 39 C1627 with C1671 and C1681 (ruling 133; privacy): a folder named a zero-width space or a
    word joiner before Status prints as `Status` wherever the CLI shows it through safe_text, so the agentsync
    report went private only while the shell report, whose private-only loop skipped a value holding a
    non-letter, kept the public issue link. fallback_redaction's own loop now takes safe_shown's spelling of a
    folder pair: a generic folder or product word, checked first, stays public (C1635: Documents), any other
    report word makes the report private only, and a word no report prints (Quokka) changes nothing."""
    home = Path(env["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Projects" / name
    folder.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(local_source_table("s0", folder), encoding="utf-8")
    said = "2026-10-12T10:00:01Z | step 2 | deviation | opened the folder | -"
    text = _shell_report(
        env, f"Attempt: 2026-10-12T10:00:00Z\nPrompt: v15\n{said}\n", LC_ALL=locale, LANG=locale
    )
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    assert red.private_only == private, name
    assert ("Private only:" in text) == private, text.split("\n## Redaction\n", 1)[1]
    last = text.rstrip("\n").splitlines()[-1]
    assert last.startswith(setup_report.ISSUE_URL) != private, last
    assert ("paste this report into its Setup report field" in text) != private


def test_an_organisation_named_by_a_product_word_makes_both_reports_private(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 21 Y72: an organisation is no folder, so G48's and Y7's exemptions do not reach it; the
    agentsync report reads codex as a report word and goes private only, and the shell's output_word now
    lists the product words it lacked, so the two agree."""
    from agentsync.config import local_source_table  # noqa: PLC0415

    home = Path(env["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Codex" / "Plans"
    folder.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(local_source_table("s0", folder), encoding="utf-8")
    said = "2026-10-12T10:00:01Z | step 2 | deviation | the codex tenant asked for MFA | -"
    text = _shell_report(env, f"Attempt: 2026-10-12T10:00:00Z\nPrompt: v15\nAgent: Kestrel 2\n{said}\n")
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    assert "<org-" in red.redact(said) and red.private_only
    assert "Private only:" in text and setup_report.ISSUE_URL not in text, text.split("\n## Redaction\n", 1)[
        1
    ]


def test_an_organisation_named_by_a_product_word_stays_private_after_a_library_of_that_word(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 22 Z86 (a Y7 regression): a SharePoint site library named Copilot is registered before the
    organisations, generic by Y7's rule, and the organisation Copilot then found that placeholder known and
    returned before it was marked: the agentsync report went public while install.sh's stayed private only.
    An organisation on a known placeholder is still marked, so both reports are private only."""
    from agentsync.config import local_source_table  # noqa: PLC0415

    home = Path(env["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Copilot" / "Plans"
    folder.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    site = (
        '\n[[source]]\nid = "copilot-site"\nkind = "graph_drive"\n'
        'site = "contoso.sharepoint.com:/sites/Copilot"\nstate = "paused"\n'
    )
    cfg.write_text(local_source_table("s0", folder) + site, encoding="utf-8")
    said = "2026-10-12T10:00:01Z | step 2 | deviation | the copilot tenant asked for MFA | -"
    text = _shell_report(env, f"Attempt: 2026-10-12T10:00:00Z\nPrompt: v15\nAgent: Kestrel 2\n{said}\n")
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    assert "copilot" not in red.redact(said).casefold() and red.private_only
    assert "Private only:" in text and setup_report.ISSUE_URL not in text, text.split("\n## Redaction\n", 1)[
        1
    ]


def test_agentsyncs_own_folders_that_sources_toml_moves_are_redacted(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 22 Z21, Z28: an ``[agentsync]`` table moving the state, docs, cache or log folder printed
    their names in clear in Configuration, Doctor, Environment and Recent errors: the redactor registered
    none of them. Each is registered from its first folder that names something, and the report's own words
    and agentsync's default locations still read in clear."""
    home = Path(env["HOME"])
    monkeypatch.setenv("HOME", env["HOME"])
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    moved = {
        "docs_repo": "~/Northwind Data/agent-context",
        "state_dir": "~/Fabrikam Ops/agentsync-state",
        "cache_dir": "~/Adatum Cache",
        "log_dir": "~/Tailspin Logs/al",
    }
    for value in moved.values():
        (home / value.removeprefix("~/")).mkdir(parents=True)
    cfg.write_text("[agentsync]\n" + "".join(f'{k} = "{v}"\n' for k, v in moved.items()), encoding="utf-8")
    text, red = setup_report.build_report(cfg, hooks=setup_report.ReportHooks())
    for name in ("Northwind", "Fabrikam", "Adatum", "Tailspin"):
        assert name.casefold() not in text.casefold(), [ln for ln in text.splitlines() if name in ln]
    conf = text.split("\n## Configuration\n", 1)[1].split("\n## ", 1)[0]
    assert re.search(r"- state dir: ~/<folder-\d+> \(exists\)", conf), conf
    assert re.search(r"- docs repo: ~/<folder-\d+> \(", conf), conf
    # what agentsync's own words and default locations read as, with folders moved inside them
    own = {
        "~/agent-context/docs-alt": ["docs-alt"],
        "~/Library/Application Support/agentsync2": [],
        "~/.local/state/agentsync-x": [],
        "~/work/agentsync": [],
        "~/Library/Caches/Contoso": ["Contoso"],
        "~/Library/Application Support/agentsync": [],
    }
    for value, want in own.items():
        path = home / value.removeprefix("~/")
        assert setup_report._own_folder_values(path, home / "x") == want, value
    assert setup_report._own_folder_values(home / "y", home / "y") == []  # at its default: nothing
    clear = (
        "agent-context-sync reads ~/Library/Application Support/agentsync/manifest.sqlite and "
        "~/Library/Caches/agentsync; the state file"
    )
    assert red.redact(clear) == clear


@pytest.mark.parametrize(
    ("first", "value", "said", "want"),
    [
        (
            "library",
            "Contoso",
            "ContosoGroup, Contoso2026, AcmeContoso",
            "<library-1>Group, <library-1>2026, Acme<library-1>",
        ),
        ("name", "Jane Doe", "JaneDoeGroup, Jane Doe", "<name>Group, <name>"),
    ],
)
def test_an_organisation_registered_after_another_kind_is_still_cut_out_of_a_longer_word(
    first: str, value: str, said: str, want: str
) -> None:
    """Review backtest er-32#0: _build_redactor registers a SharePoint site's library and the full name before
    the organisations, and add() returned on the known value, so its literal kept the first registrant's
    whole-word mode and "ContosoGroup" printed in clear. The organisation's in-word literal is added
    beside it,
    on the first placeholder."""
    red = setup_report.Redactor()
    red.add(first, value, fuzzy=True)
    red.add("org", value, fuzzy=True)
    assert red.redact(said) == want


@pytest.mark.parametrize(
    ("first", "value", "said", "want"),
    [
        (  # an exact surname first, in two cases (C870's case 1, C835's surname case)
            [("name", "Contoso", {}), ("name", "CONTOSO", {})],
            "Contoso",
            "contoso ran, contoso2026, contoso.sharepoint.com, contoso-docs, contosoHQ, AcmeContoso",
            "<name> ran, <name>2026, <name>.sharepoint.com, <name>-docs, <name>HQ, Acme<name>",
        ),
        (
            [("name", "McContoso", {})],
            "McContoso",
            "Mc-Contoso, mc_contoso, mc contoso",
            "<name>, <name>, <name>",
        ),
        (  # a folded first registrant (C835's host case)
            [("host", "Contoso Group", {"ignore_case": True})],
            "Contoso Group",
            "contoso-group, Contoso_Group, ContosoGroup",
            "<host>, <host>, <host>",
        ),
        (  # joined: the report's own word stays whole
            [("name", "NetWork", {})],
            "NetWork",
            "the network is up, Net Work, net-work",
            "the network is up, <name>, <name>",
        ),
        (  # joined after a fold (C835's joined case)
            [("host", "Ho Me", {"ignore_case": True})],
            "Ho Me",
            "the home folder, Home, ho-me, Ho Me, HoMe",
            "the home folder, Home, <host>, <host>, <host>",
        ),
        (  # context: after an app's name or before a domain only
            [("name", "Link", {})],
            "Link",
            "the link broke, link.sharepoint.com, link-my.sharepoint.com, OneDrive-Link",
            "the link broke, <name>.sharepoint.com, <name>-my.sharepoint.com, OneDrive-<name>",
        ),
    ],
)
def test_an_organisation_after_another_kind_keeps_its_own_mode_and_lookup(
    first: list[tuple[str, str, dict[str, bool]]], value: str, said: str, want: str
) -> None:
    """B13 round 34 C870, C835 (ruling 91): an organisation spelled as a value an exact or folded kind
    registered first got its in-word literal (review backtest er-32#0) but no entry in the fuzzy table, so
    every spelling the literal matched that the first table missed (``contoso2026``, ``mc_contoso``,
    ``contoso-group``) printed in clear in the Python report while the shell's cut it. The literal takes the
    mode and the table the organisation would have had registered first: context, joined or org, so plain
    ``network``, ``home`` and ``link`` in the report's own words stay whole."""
    red = setup_report.Redactor()
    for kind, name, flags in first:
        red.add(kind, name, **flags)
    red.add("org", value, fuzzy=True)
    assert red.redact(said) == want


def test_both_reports_cut_a_path_in_another_home_alike_whatever_byte_sits_in_it_ruling_112(
    tmp_path: Path,
) -> None:
    """B13 round 36 ruling 112 (C1016, C1064, C1017, C1018, C1069; privacy; data integrity): the shell report
    and the agentsync report read a path's bytes from two lists, so a byte that stopped one cut ran the other
    over, and the shell kept another home whole behind a volume named with a comma, a semicolon or a bracket.
    Every line of lane RA's proof (each separator byte in each slot, each prefix in each case) goes through
    the shell's redact_stream once and through setup_report.Redactor, for HOME /Users/dana and the login
    dana: the two print the same line, the documented one, and no hidden name but where the table stops or
    ends the cut, or a blank right after this login keeps what follows."""
    lines = _separator_lines()
    assert len(lines) > 3000
    red = setup_report.Redactor()
    red.add("home", "/Users/dana")
    red.add("user", "dana")
    # one run for every line, split on the newline alone (splitlines splits on \r, \v, \f, \x1c-\x1e, NEL)
    shell = _shell_cut_stdout(tmp_path, "/Users/dana", [line for line, _ in lines], login="dana").split("\n")
    assert shell[-1] == "" and len(shell) == len(lines) + 1, (len(shell), len(lines))
    table = setup_report._CUT_BYTES
    marks = table["blank"] + table["end"] + table["stop"]
    wrong = []
    for (line, cut), got in zip(lines, shell[:-1], strict=True):
        py = red.redact(line)
        if not (got == py == cut):
            wrong.append((line, got, py, cut))
        if not any(c in line for c in marks):
            assert not re.search("olga|Fabrikam|Contoso", got), (line, got)
    assert not wrong, (len(wrong), wrong[:8])


def test_both_reports_cut_a_volume_past_a_skipped_home_and_a_provider_folder_in_any_case_ruling_120(
    tmp_path: Path,
) -> None:
    """B13 round 37 ruling 120 (C1253, C1285; privacy): the shell's scan moved a skipped `/Users/Shared`,
    `<path>` or `<user>` into its output, so a volume before it was never looked back to and the next home on
    the line printed whole, where setup_report cut it to /Volumes/<path>; and both reports matched the
    provider folder after `CloudStorage` or opening `OneDrive - ` in its own case only, so a lowercase one
    kept its tail past a comma. Each row, ruling 102's rows among them, goes through the shell's redact_stream
    and setup_report.Redactor for HOME /Users/dana: the two print the same, the documented line, and a
    volume an earlier cut used is never looked back to again."""
    rows = [*_RULING_102_LEAKS, *_C1253_ROWS, *_C1285_ROWS]
    red = setup_report.Redactor()
    red.add("home", "/Users/dana")
    red.add("user", "dana")
    shell = _shell_cut_stdout(tmp_path, "/Users/dana", [line for line, _ in rows], login="dana").split("\n")
    assert shell[-1] == "" and len(shell) == len(rows) + 1, (len(shell), len(rows))
    wrong = [(line, got, red.redact(line), cut) for (line, cut), got in zip(rows, shell[:-1], strict=True)]
    wrong = [w for w in wrong if not (w[1] == w[2] == w[3])]
    assert not wrong, wrong


def _report_recut_harness(tmp_path: Path, report: Path, other: int = 1) -> subprocess.CompletedProcess[bytes]:
    """install.sh's report_recut over ``report`` (REPORT_PATH), with foreign_home_cut beside it; ``other``
    1 for a render by another build, 0 for this checkout's (B13 round 38 C1453)."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    fns = "\n".join(
        m.group(0)
        for m in re.finditer(
            r"(?ms)^(?:foreign_home_cut|report_recut|report_words_cut|recut_note)\(\) \{[^\n]*\n.*?^\}$|"
            r"^(?:CUT_FIXED|CUT_HOME|REFUSED_HEADS|RECUT_NOTE)=.*?$|"
            + _REPORT_CUTS_BLOCK,  # the generated block whole (B13 round 39 C1633)
            text,
        )
    )
    run = f"set -euo pipefail\n{fns}\nREPORT_PATH={shlex.quote(str(report))}\nreport_recut {other}"
    return subprocess.run(
        [BASH32, "-c", run],
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
    )


def test_the_checkouts_cut_leaves_a_current_python_render_byte_for_byte_c1237(
    fake_mac: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    """B13 round 37 ruling 120 (C1237): a report this checkout's setup_report renders, with ruling 102's paths
    in its install.out tail and friction log, is already cut, so install.sh's second cut gives back every
    byte; an older cut's /Volumes/<path> before a path byte is cut on, and install.sh's own missing-folder
    diagnostic is cut after `is not an existing folder: `."""
    (fake_mac["setup"] / "install.out").write_text(
        "# run=20261110T100000Z-4242 2026-11-10T10:00:00Z install.sh --source-local x\n"
        + "".join(f"{line}\n" for line, _cut in _RULING_102_LEAKS)
        + "usage error: --source-local is not an existing folder: ~/Projects/<path> (see --help)\n"
        # B13 round 38 C1453 with C1481, C1435: names _cut_path keeps whole, which this checkout's cut leaves
        + "usage error: --source-local is not an existing folder: ~/Documents (see --help)\n"
        + "usage error: --source-local is not an existing folder: "
        "~/Library/CloudStorage/OneDrive-Contoso/Projects (see --help)\n"
        # the lane's review: a cut volume before a later /users/, and a cut name before a stop byte
        + "cp /Volumes/Backup/x/Users/olga/y; https://graph.microsoft.com/v1.0/users/me/drive\n"
        + "see /Volumes/Backup/Users/olga/x) then api/users/42/x\n"
        + "cp /Volumes/USB/stuff /tmp/y ; x/Volumes/Backup/Users/olga/y ; https://graph.microsoft.com/v1.0/users/me\n",
        encoding="utf-8",
    )
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    report = tmp_path / "setup-report.md"
    report.write_text(text, encoding="utf-8")
    before = report.read_bytes()
    assert b"--source-local is not an existing folder: ~/Documents (see --help)" in before
    cp = _report_recut_harness(tmp_path, report, other=0)
    assert cp.returncode == 0, cp.stderr
    assert report.read_bytes() == before
    # a Python cut's placeholders are no volume to the second cut, and a name already cut keeps what follows
    kept = (
        b"cp /Volumes/<path>; https://graph.microsoft.com/v1.0/users/me/drive\n"
        b"/Volumes/<path>;x/Users/kim/g\n"
        b"see /Volumes/<path>) then api/users/42/x\n"
        b"cp /Volumes/<path> ; https://graph.microsoft.com/v1.0/users/me\n"
        b"| 3 | error | --source-local is not an existing folder: <path> |\n"
        b"- error: --source-local is not an existing folder: <path>; retried\n"
    )
    report.write_bytes(kept)
    cp = _report_recut_harness(tmp_path, report)
    assert cp.returncode == 0, cp.stderr
    assert report.read_bytes() == kept
    older = (
        b"x /Volumes/<path>\xc2\xa0Drive/Users/olga/Fabrikam y\n"
        b"usage error: --source-local is not an existing folder: ~/Projects/acquisition-northwind"
        b" (see --help)\n"
        b"- last error: usage error: --source-local is not an existing folder: ~/Tailspin Merger\n"
    )
    report.write_bytes(older)
    cp = _report_recut_harness(tmp_path, report)
    assert cp.returncode == 0, cp.stderr
    assert report.read_bytes() == (
        b"x /Volumes/<path> y\n"
        b"usage error: --source-local is not an existing folder: <path> (see --help)\n"
        b"- last error: usage error: --source-local is not an existing folder: <path>\n"
    )
    # B13 round 38 C1453: this checkout's own render takes the other-home cut alone, never the fixed-word cut
    report.write_bytes(older)
    cp = _report_recut_harness(tmp_path, report, other=0)
    assert (cp.returncode, cp.stderr) == (0, b""), cp.stderr  # the lane's review: no missing count file named
    assert report.read_bytes() == older.replace(
        b"/Volumes/<path>\xc2\xa0Drive/Users/olga/Fabrikam", b"/Volumes/<path>"
    )


_R126_CHARS = (
    [chr(c) for c in range(1, 32) if c != 0x2F]
    + [chr(c) for c in (0x7F, 0x85, 0x2028, 0x2029, *(c for c in range(0x80, 0xA0) if c != 0x85))]
    + [chr(c) for c in (*range(0x200B, 0x2010), *range(0x202A, 0x202F), 0x2060, *range(0x2066, 0x206A))]
    + [chr(c) for c in (0xFEFF, 0x061C, 0x20)]
)
"""Ruling 126's proof names (B13 round 38): each C0 control, DEL, each break str.splitlines() honours, each
character _LIST_ESCAPED_RE matches, and a blank, each between two made-up words."""


def _listed(name: str, *, old: bool = False) -> str:
    """``name`` in step 1's list spelling (B13 round 40 C1802: \\n for LF and 0x1E, \\t, \\r, octal per UTF-8
    byte for every other escaped character), through ``arm_local.list_escaped_char``, the one helper
    install.sh's awk is held to (B13 round 41 C2035), or with ``old`` its spelling before C1802 (``?``), each
    built directly, never by index into folder_spellings, which deduplicates its spellings."""
    if not old:
        return setup_report._LIST_ESCAPED_RE.sub(arm_local.list_escaped_char, name)
    table = setup_report._LIST_ESCAPES_OLD
    return setup_report._LIST_ESCAPED_RE.sub(lambda m: table.get(m.group(), "?"), name)


def test_the_old_list_spelling_is_a_frozen_snapshot_and_folder_spellings_reads_the_one_helper_c2035() -> None:
    """B13 round 41 C2035: two lanes added the same list-spelling helper. folder_spellings now spells step 1's
    list through arm_local.list_escaped_char alone, and _LIST_ESCAPES_OLD stays a frozen snapshot of the
    pre-C1802 table, independent of arm_local's on purpose, so an edit to it is deliberate."""
    assert setup_report._LIST_ESCAPES_OLD == {"\n": "\\n", "\x1e": "\\n", "\t": "\\t", "\r": "\\r"}
    assert not hasattr(setup_report, "_LIST_LETTERS") and not hasattr(setup_report, "_octal_bytes")
    for name in ("Contoso\x7fDel", "A\x1eB\tC\rD\nE", "Caf\u00e9\x1bX", "Q\u200bR"):
        want = setup_report._LIST_ESCAPED_RE.sub(arm_local.list_escaped_char, name)
        assert setup_report.folder_spellings(name)[0] == want == _listed(name), name


def _r126_lines(n: int, name: str) -> list[str]:
    """Each line form ruling 126 names, for the folder ``name`` (source s<n>) under OneDrive-Contoso: the
    source-local line, doctor's listable detail and fix, each refusal head, the not-a-folder line and a
    friction row in safe_text's spelling."""
    shown, old = _listed(name), _listed(name, old=True)
    root = "~/Library/CloudStorage/OneDrive-Contoso"
    ansi = setup_report._ansi_c_body(name, setup_report._DOCTOR_LETTERS)
    return [
        f"source-local: {root}/{shown}",
        f"source-local: {root}/{old}",  # an older install.out's list spelling (B13 round 40 C1802)
        f"[warn] source.alpha.listable — {root}/{shown} is empty",
        f"open ~/$'Library/CloudStorage/OneDrive-Contoso/{ansi}' in Finder once",
        *(f"usage error: --source-local {root}/{shown}: {head}x" for head in setup_report._REFUSED_HEADS),
        f"usage error: --source-local is not an existing folder: {root}/{shown} (see --help)",
        f"saw {root}/{safe_text(name)}",
    ]


@pytest.mark.parametrize("group", range(0, len(_R126_CHARS), 12))
def test_both_reports_print_a_folder_name_in_no_spelling_ruling_126(
    env: dict[str, str],  # noqa: F811
    group: int,
) -> None:
    """B13 round 38 ruling 126 (C1414 with C1439, C1432; privacy; data integrity): install.sh's source-local
    line printed a folder's name raw, doctor's listable detail and fix printed spellings no alias knew, and
    safe_text's spelling (a format character dropped) was no pair, so a name holding a control, a break or a
    hidden character printed in clear. Each proof name, configured, goes through the shell report and the
    agentsync report in every line form, its list spelling, its `$'...'` word and its safe_text spelling: no
    fragment of any name prints, and both reports print the same line, doctor's fix included (C1656).

    B13 round 39 ruling 133: one friction row a form, read from both real reports (``_both_reports``), since
    both now cut the not-a-folder and refusal lines by their structure, which a line joining twelve names
    runs together."""
    names = [f"Alder{n:02d}{ch}Birch{n:02d}" for n, ch in enumerate(_R126_CHARS)][group : group + 12]
    said = [line for n, name in enumerate(names) for line in _r126_lines(n, name)]
    shell, python, _sp, _pp = _both_reports(env, names, said, "C")
    assert len(shell) == len(python) == len(said), (len(shell), len(python), len(said))
    leaks = [(who, line) for who, got in (("shell", shell), ("python", python)) for line in got]
    leaks = [(who, line) for who, line in leaks if re.search("Alder|Birch", line)]
    assert not leaks, (len(leaks), leaks[:3], sorted({(w, x[x.index("Alder") :][:7]) for w, x in leaks}))
    # B13 round 39 C1656 (ruling 134): both reports cut every `$'...'` word whole, so doctor's fix reads
    # `open ~/<path> in Finder once` in each, and every line form, the fix line included, is the same line
    pairs = list(zip(shell, python, strict=True))
    fixes = [(a, b) for a, b in pairs if a.startswith("open ")]
    assert fixes and all(a == b == "open ~/<path> in Finder once" for a, b in fixes), fixes
    wrong = [(a, b) for a, b in pairs if a != b]
    assert not wrong, (len(wrong), wrong[:6])


@pytest.mark.parametrize("ch", ["\b", "\f", "\v", "\a"])
def test_a_folder_name_holding_a_control_byte_is_redacted_alike_in_both_reports_c1629(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    ch: str,
) -> None:
    """B13 round 39 C1629 (ruling 133; data integrity): install.sh's pairs split a name at a control byte,
    the agentsync report's separators did not, so for ``Q3<BS>Plans`` the shell cut all five spellings and
    the agentsync report printed ``Q3 Plans``, ``Q3-Plans`` and ``q3_plans`` in clear. Both read
    report_cuts.SEPARATORS now and print the same line."""
    shell, python = _fuzzy_report(
        env, monkeypatch, [f"Q3{ch}Plans"], "opened Q3 Plans and Q3-Plans and Q3?Plans and q3_plans"
    )
    assert shell == python == "opened <folder-1> and <folder-1> and <folder-1> and <folder-1>", (
        shell,
        python,
    )


@pytest.mark.parametrize(
    ("said", "cut"),
    [
        # B13 round 38 C1480: a name is already cut only where _cut_path could have printed it
        ("~/Projects/Northwind<path>", "<path>"),
        ("~/Projects/Northwind/<path>", "<path>"),
        ("<path> Northwind", "<path>"),
        ("~/Projects/<path>", "~/Projects/<path>"),
        # C1481, C1435: a provider folder only as a known app, bare or before placeholders
        ("~/Library/CloudStorage/OneDrive-<org-1>/<path>", "~/Library/CloudStorage/OneDrive-<org-1>/<path>"),
        ("~/Library/CloudStorage/Dropbox/<path>", "~/Library/CloudStorage/Dropbox/<path>"),
        ("~/Library/CloudStorage/OneDrive-Fabrikam/<path>", "<path>"),
        ("~/Library/CloudStorage/Fabrikam-<org-1>/<path>", "<path>"),
        ("~/Library/CloudStorage/OneDrive-Fabrikam", "<path>"),
        ("~/Documents", "<path>"),  # another build's whole-kept name: cut (the known over-cut)
    ],
)
def test_the_recut_keeps_only_a_name_cut_path_could_print_c1480_c1481_c1435(
    tmp_path: Path, said: str, cut: str
) -> None:
    """B13 round 38 C1480, C1481 and C1435 (ruling 126; makes worse True for a faithful port of _cut_path,
    which keeps an unregistered tenant): another build's render kept any name ending in `<path>`, so
    `Northwind<path>` printed a folder's name. There the recut keeps a name only when its last part is
    `<path>` and every earlier part is one _cut_path keeps, a provider folder only as a known app; else the
    name is `<path>`. In the not-a-folder line, and in a refusal of each head (C1432)."""
    report = tmp_path / "setup-report.md"
    lines = [f"usage error: --source-local is not an existing folder: {said} (see --help)"]
    lines += [f"- usage error: --source-local {said}: {h}x" for h in setup_report._REFUSED_HEADS]
    report.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    cp = _report_recut_harness(tmp_path, report)
    assert cp.returncode == 0, cp.stderr
    want = [f"usage error: --source-local is not an existing folder: {cut} (see --help)"]
    want += [f"- usage error: --source-local {cut}: {h}x" for h in setup_report._REFUSED_HEADS]
    assert report.read_text(encoding="utf-8").splitlines() == want


def test_the_recut_takes_the_not_a_folder_names_edge_as_the_python_report_does_c1436(tmp_path: Path) -> None:
    """B13 round 38 C1436 (ruling 126): the name ends at the last ` (see --help)` only before the line's end,
    one of `;,.:`, ` | ` or ` · `; else at the line's end, as setup_report._NOT_A_FOLDER_RE reads it."""
    report = tmp_path / "setup-report.md"
    m = "--source-local is not an existing folder: "
    report.write_text(
        f"{m}~/Deals (see --help); retried\n{m}~/Deals (see --help) then ok\n"
        f"| 2 | {m}~/Deals (see --help) | x |\n",
        encoding="utf-8",
    )
    cp = _report_recut_harness(tmp_path, report)
    assert cp.returncode == 0, cp.stderr
    assert report.read_text(encoding="utf-8").splitlines() == [
        f"{m}<path> (see --help); retried",
        f"{m}<path>",
        f"| 2 | {m}<path> (see --help) | x |",
    ]


_C2500_NAME = "x is not an existing folder: "
"""The not-a-folder head install.sh writes, as an agent quotes it in prose (B13 round 43 C2500)."""


@pytest.mark.parametrize(
    ("said", "cut", "same"),
    [
        ("'~/Projects/<path>' and then I retried", "'~/Projects/<path>' and then I retried", True),
        ('"~/Projects/<path>" and then I retried', '"~/Projects/<path>" and then I retried', True),
        (" (see --help)", " (see --help)", True),
        ("", "", True),
        ("'Contoso Secret' and then I retried", "'<path>' and then I retried", True),
        ('"Contoso \\"Secret\\"" and then I retried', '"<path>" and then I retried', True),
        # unchanged branches, where the recut cuts more than _cut_path by design (ruling 126; makes worse True
        # for a faithful port): a name to install.sh's own end is taken whole, its quote with it (the
        # agentsync report prints `'<path> (see --help)`), and a bare one is cut whole (`~/Projects/<path>`)
        ("'Alpha' Bravo Ridge (see --help)", "<path> (see --help)", False),
        ("~/Projects/Contoso Secret", "<path>", False),
    ],
    ids=[
        "quoted-cut",
        "double-quoted-cut",
        "empty-help",
        "empty-eol",
        "quoted",
        "escaped",
        "help-whole",
        "bare",
    ],
)
def test_the_recut_ends_a_quoted_name_at_its_quote_only_at_its_lines_end_c2500(
    tmp_path: Path, said: str, cut: str, same: bool
) -> None:
    """B13 round 43 ruling 160, C2500: report_words_cut took a not-a-folder name with no ` (see --help)` end
    to the line's end, so another build's render lost the prose after a quoted name (`'~/Projects/<path>'
    and then I retried` read `<path>`) and an empty value read `<path>`, each counted as a cut. It ports
    setup_report._quoted_cut's end-of-line branch through closeq: a quoted name that runs to its line's end
    ends at its matching quote, its opening quote and the rest of the line kept, and only a non-empty unknown
    name is cut; a name to install.sh's own ` (see --help)` end is taken whole, as before. Each line the
    port touches reads as setup_report._redact_lines reads it, and this checkout's own render (other=0) stays
    byte for byte."""
    line = f"{_C2500_NAME}{said}"
    red = setup_report.Redactor()
    red.add("home", "/Users/dana")
    python = setup_report._redact_lines(red, [line])
    report = tmp_path / "setup-report.md"
    report.write_text(f"{line}\n", encoding="utf-8")
    before = report.read_bytes()
    assert _report_recut_harness(tmp_path, report, other=0).returncode == 0
    assert report.read_bytes() == before, "this checkout's own render stays byte for byte"
    cp = _report_recut_harness(tmp_path, report)
    assert cp.returncode == 0, cp.stderr
    shell = report.read_text(encoding="utf-8").removesuffix("\n")
    assert shell == f"{_C2500_NAME}{cut}", shell
    assert (shell == python) is same, (shell, python)


def _provider_lines_harness(tmp_path: Path, report: Path) -> subprocess.CompletedProcess[bytes]:
    """install.sh's report_provider_lines over ``report`` (REPORT_PATH), with FREECUT_AWK and the generated
    block beside it: rc 0 and its SPLIT_WHY on stdout where another build's render gives way to the shell
    report (B13 round 43 ruling 160, C2498)."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    fns = "\n".join(
        m.group(0)
        for m in re.finditer(
            r"(?ms)^FREECUT_AWK='.*?^'$|^(?:report_provider_lines|report_words_cut)\(\) \{\n.*?^\}$|"
            r"^(?:CUT_FIXED|CUT_HOME)=.*?$|" + _REPORT_CUTS_BLOCK,
            text,
        )
    )
    where = shlex.quote(str(report))
    run = f'set -euo pipefail\n{fns}\nREPORT_PATH={where}\nreport_provider_lines\necho "$SPLIT_WHY"'
    return subprocess.run(
        [BASH32, "-c", run],
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
    )


@pytest.mark.parametrize(
    "said",
    [
        *(said for said, _want in R146_FREE_ROWS),
        "OneDrive-<org-1>/Plans x",
        "Dropbox/Plans x",
        "<path> today",
    ],
)
@pytest.mark.parametrize("head", ["saw ", "usage error: --source-local is not an existing folder: "])
def test_another_builds_render_gives_way_exactly_where_the_free_text_cut_changes_a_line_c2498(
    tmp_path: Path, said: str, head: str
) -> None:
    """B13 round 43 ruling 160, C2498 (privacy), its option A: report_recut has no free-text provider rule, so
    another build's render kept `~/Library/CloudStorage/OneDrive-Personal Secret/Plans` and `OneDrive-Tailspin
    Toys today`, which the agentsync report cuts. report_provider_lines scans the render with freecut, the one
    copy the shell report cuts with, and write_report takes the shell report where it fires: exactly on the
    lines setup_report._redact_lines changes. A not-a-folder name, which the recut cuts to <path> itself, is
    read as the recut leaves it, so it never takes the fallback. The recut is unchanged, and this checkout's
    own render (other=0) stays byte for byte."""
    red = setup_report.Redactor()
    red.add("home", "/Users/dana")
    line = f"{head}~/Library/CloudStorage/{said}"
    python = setup_report._redact_lines(red, [line]) if head == "saw " else line
    report = tmp_path / "setup-report.md"
    report.write_text(f"# agentsync setup report\n\n{line}\n", encoding="utf-8")
    before = report.read_bytes()
    cp = _provider_lines_harness(tmp_path, report)
    assert cp.returncode in (0, 1), cp.stderr
    assert (cp.returncode == 0) is (python != line), (line, python, cp.stdout)
    if cp.returncode == 0:
        assert cp.stdout.decode().startswith("a provider folder name after ~/Library/CloudStorage/"), (
            cp.stdout
        )
    assert report.read_bytes() == before, "the scan writes nothing"
    assert _report_recut_harness(tmp_path, report, other=0).returncode == 0
    assert report.read_bytes() == before, "this checkout's own render stays byte for byte"


@pytest.mark.parametrize("filler_kb", [0, 8, 120, 400])
def test_another_builds_large_render_gives_way_on_its_first_provider_word_c2498(
    tmp_path: Path, filler_kb: int
) -> None:
    """B13 round 43 C2498 (privacy), from lane RB's review: the scan's awk exited on its first find, so on a
    render of about 96 KB or more report_words_cut, still writing, died of SIGPIPE, and under pipefail that
    status read as nothing found: another build's render that printed a tenant in clear near its top was kept.
    The scan reads to the end of its input, so a tenant word on line 3 gives way at every size."""
    row = "- plain line {:06d} with nothing to cut in it at all\n"
    filler = "".join(row.format(n) for n in range(filler_kb * 20))
    report = tmp_path / "setup-report.md"
    report.write_text(
        f"# agentsync setup report\n\nsaw ~/Library/CloudStorage/OneDrive-Tailspin Toys today\n{filler}",
        encoding="utf-8",
    )
    assert report.stat().st_size >= filler_kb * 1000
    cp = _provider_lines_harness(tmp_path, report)
    assert cp.returncode == 0, (report.stat().st_size, cp.returncode, cp.stderr)
    assert cp.stdout.decode().startswith("a provider folder name after ~/Library/CloudStorage/"), cp.stdout


@pytest.mark.parametrize(
    "line",
    [
        "debug: config | ~/Projects/acquisition-fabrikam/cfg/sources.toml",
        "| 3 | debug: config | ~/Projects/acquisition-fabrikam/cfg/sources.toml |",
        "debug: config | debug: source checkout ~/src/agent-context-sync",
        "| 3 | debug: config | debug: source checkout ~/src/agent-context-sync |",
        "2026-11-20T00:00:01Z | step 2 | note | debug: config  | -",
        "debug: config ~/agent-context/sources.toml",
        "debug: config ~/Projects/acquisition-fabrikam/cfg/sources.toml",
    ],
)
def test_the_config_guard_reads_an_empty_cell_as_both_reports_do_c2499(tmp_path: Path, line: str) -> None:
    """B13 round 43 ruling 160, C2499 (privacy): report_config_lines read every value after `| ` as empty, so
    an install.out line `debug: config | ~/Projects/...` kept another build's render, which printed the path
    both reports cut. Through dbval's own dbempty, a value after `| ` is empty only before another debug head,
    so the guard fires exactly where setup_report._redact_lines cuts the line; the recut of this checkout's
    own render (other=0) leaves it byte for byte."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    fns = "\n".join(
        m.group(0)
        for m in re.finditer(
            r"(?ms)^DBCELL_AWK='.*?^'$|^report_config_lines\(\) \{\n.*?^\}$|" + _REPORT_CUTS_BLOCK, text
        )
    )
    out = tmp_path / "install.out"
    out.write_text(f"{line}\n", encoding="utf-8")
    run = f"set -euo pipefail\n{fns}\nCONFIG=\nINSTALL_OUT={shlex.quote(str(out))}\nreport_config_lines"
    cp = subprocess.run(
        [BASH32, "-c", run],
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert cp.returncode in (0, 1), cp.stderr
    red = setup_report.Redactor()
    red.add("home", "/Users/dana")
    python = setup_report._redact_lines(red, [line])
    assert (cp.returncode == 0) is (python != line), (line, python)
    report = tmp_path / "setup-report.md"
    report.write_text(f"# agentsync setup report\n\n{line}\n", encoding="utf-8")
    before = report.read_bytes()
    assert _report_recut_harness(tmp_path, report, other=0).returncode == 0
    assert report.read_bytes() == before, "this checkout's own render stays byte for byte"


@pytest.mark.parametrize("folders", [["Empty Project", "Project Alpha"], ["Project Alpha", "Empty Project"]])
def test_both_reports_hold_agentsyncs_own_verb_whole_c1425(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    folders: list[str],
) -> None:
    """B13 round 38 C1425 (ruling 126; makes worse True for a hyphen-edge rule, which leaves
    `empty-project-2026` in clear): a folder `Empty Project` matched across `exclude-empty project-alpha`,
    so the command read `exclude-<folder-1>-alpha`, with a word of `Project Alpha` in clear. Both reports hold
    agentsync's own verb whole after `agentsync `, its argument cut as its own value, the fuzzy edges kept."""
    said = "~/.local/bin/agentsync exclude-empty project-alpha; sync empty-project-notes"
    shell, python = _fuzzy_report(env, monkeypatch, folders, said)
    assert shell == python, (shell, python)
    assert re.fullmatch(
        r"~/\.local/bin/agentsync exclude-empty <folder-\d>; sync <folder-\d>-notes", shell
    ), shell
    assert "alpha" not in shell.lower(), shell


@pytest.mark.parametrize(
    ("folders", "said", "want"),
    [
        (
            ["Empty Project", "Project Alpha", "Exclude Empty Notes"],
            "~/.local/bin/agentsync exclude-empty project-alpha; agentsync sync project-alpha",
            r"~/\.local/bin/agentsync exclude-empty <folder-\d>; agentsync sync <folder-\d>",
        ),
        (
            ["Curate", "Project Alpha"],
            "agentsync curate-queue project-alpha",
            r"agentsync <folder-\d>-queue <folder-\d>",
        ),
    ],
)
def test_both_reports_take_keep_verbs_one_guard_c1625(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    folders: list[str],
    said: str,
    want: str,
) -> None:
    """B13 round 39 C1625 (ruling 133): the shell blocked a verb on every line once any pair began with it,
    so with a folder `Exclude Empty Notes` it held no `exclude-empty`, and `Empty Project` cut across the
    command, leaving `alpha` in clear. Both reports now take keep_verb's one per-position guard: no verb is
    held where a pair whose value begins with any verb matches right after `agentsync `."""
    shell, python = _fuzzy_report(env, monkeypatch, folders, said)
    assert shell == python, (shell, python)
    assert re.fullmatch(want, shell), shell
    assert "alpha" not in shell.lower(), shell


_C1626_ROWS = (
    # an unregistered tenant in a collapsed and an uncollapsed added-source note
    (
        "added source 'plans' to ~/agent-context/sources.toml "
        "(~/Library/CloudStorage/OneDrive-Fabrikam/Plans, stored as "
        "~/Library/CloudStorage/OneDrive-Fabrikam/Plans): kept",
        "added source 'plans' to ~/agent-context/sources.toml (~/Library/CloudStorage/<path>): kept",
    ),
    (
        "added source 'plans' to ~/agent-context/sources.toml "
        "(~/Library/CloudStorage/OneDrive-Fabrikam/Plans, stored as ~/Projects/Plans): kept",
        "added source 'plans' to ~/agent-context/sources.toml (~/Library/CloudStorage/<path>, stored as "
        "~/Projects/Plans): kept",
    ),
    # a --source-local argument for a missing folder, bare, with a %q-escaped blank, and quoted
    (
        "args=--source-local ~/Library/CloudStorage/OneDrive-Fabrikam/Plans --dry-run",
        "args=--source-local ~/Library/CloudStorage/<path> --dry-run",
    ),
    (
        "args=--source-local ~/Library/CloudStorage/OneDrive-Fabrikam\\ Partners/Plans; retried",
        "args=--source-local ~/Library/CloudStorage/<path>; retried",
    ),
    (
        "ran --source-local '~/Library/CloudStorage/OneDrive-Personal-Fabrikam/Plans' once",
        "ran --source-local '~/Library/CloudStorage/<path>' once",
    ),
    # kept: a provider registered from disk, one that names no organisation, and the app's own name
    (
        "ran --source-local '~/Library/CloudStorage/OneDrive-Contoso/Northwind Plans' once",
        "ran --source-local '~/Library/CloudStorage/OneDrive-<org-1>/<folder-1>' once",
    ),
    (
        "args=--source-local ~/Library/CloudStorage/OneDrive-Personal/Northwind Plans",
        "args=--source-local ~/Library/CloudStorage/OneDrive-Personal/<folder-1>",
    ),
    (
        "args=--source-local ~/Library/CloudStorage/Box-Box",
        "args=--source-local ~/Library/CloudStorage/Box-Box",
    ),
    # the lane's review: a not-a-folder name with blanks taken whole, a bare word ending at a quote, and an
    # escaped quote read through inside a double-quoted word
    (
        "usage error: --source-local is not an existing folder: "
        "~/Library/CloudStorage/OneDrive-Contoso/Acme Merger Plans (see --help); retried",
        "usage error: --source-local is not an existing folder: "
        "~/Library/CloudStorage/OneDrive-<org-1>/<path> (see --help); retried",
    ),
    (
        'ran "install.sh --source-local '
        '~/Library/CloudStorage/OneDrive-Contoso/Northwind\\ Plans" and failed',
        'ran "install.sh --source-local ~/Library/CloudStorage/OneDrive-<org-1>/<folder-1>" and failed',
    ),
    (
        '--source-local "~/Library/CloudStorage/OneDrive-Fabrikam/Acme \\"Q3\\" Plans" --dry-run',
        '--source-local "~/Library/CloudStorage/<path>" --dry-run',
    ),
    # B13 round 40 C1831 with C1881 (ruling 137): the = form, bare, double- and single-quoted, after an
    # unregistered tenant and after a registered one with an unconfigured folder
    (
        "ran --source-local=~/Library/CloudStorage/OneDrive-Fabrikam/Plans once",
        "ran --source-local=~/Library/CloudStorage/<path> once",
    ),
    (
        'ran --source-local="~/Library/CloudStorage/OneDrive-Fabrikam/Plans" once',
        'ran --source-local="~/Library/CloudStorage/<path>" once',
    ),
    (
        "args=--source-local='~/Library/CloudStorage/OneDrive-Fabrikam/Plans' --dry-run",
        "args=--source-local='~/Library/CloudStorage/<path>' --dry-run",
    ),
    (
        "ran --source-local=~/Library/CloudStorage/OneDrive-Contoso/Wombat\\ Plans once",
        "ran --source-local=~/Library/CloudStorage/OneDrive-<org-1>/<path> once",
    ),
    # C1832: add-source keeps every placeholder and cuts at its first unknown part, bare, double-quoted and
    # hyphenated; a configured folder's number stays
    (
        "ran agentsync add-source ~/Library/CloudStorage/OneDrive-Contoso/Wombat\\ Plans and it failed",
        "ran agentsync add-source ~/Library/CloudStorage/OneDrive-<org-1>/<path> and it failed",
    ),
    (
        'ran agentsync add-source "~/Library/CloudStorage/OneDrive-Contoso/Wombat Plans" and it failed',
        'ran agentsync add-source "~/Library/CloudStorage/OneDrive-<org-1>/<path>" and it failed',
    ),
    (
        "ran agentsync add-source ~/Library/CloudStorage/OneDrive-Contoso/Wombat-Plans and it failed",
        "ran agentsync add-source ~/Library/CloudStorage/OneDrive-<org-1>/<path> and it failed",
    ),
    (
        "ran agentsync add-source ~/Library/CloudStorage/OneDrive-Contoso/Northwind\\ Plans again",
        "ran agentsync add-source ~/Library/CloudStorage/OneDrive-<org-1>/<folder-1> again",
    ),
    # C1833: a refused name runs to the last refusal head, never to its first blank
    (
        "usage error: --source-local ~/Library/CloudStorage/OneDrive-Contoso/Wombat Plans: its name is step "
        "1's escaped form of another folder there",
        "usage error: --source-local ~/Library/CloudStorage/OneDrive-<org-1>/<path>: its name is step 1's "
        "escaped form of another folder there",
    ),
    (
        "usage error: --source-local ~/Library/CloudStorage/OneDrive-Contoso/Wombat Plans: this is a row "
        "step 1 marked x",
        "usage error: --source-local ~/Library/CloudStorage/OneDrive-<org-1>/<path>: this is a row step 1 "
        "marked x",
    ),
    (
        "usage error: --source-local ~/Library/CloudStorage/OneDrive-Fabrikam/Wombat Plans: its name is step "
        "1's escaped form of another folder there",
        "usage error: --source-local ~/Library/CloudStorage/<path>: its name is step 1's escaped form of "
        "another folder there",
    ),
)


@pytest.mark.parametrize(("said", "want"), _C1626_ROWS)
def test_both_reports_cut_an_unregistered_tenant_after_cloudstorage_c1626(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    said: str,
    want: str,
) -> None:
    """B13 round 39 C1626 (ruling 133; privacy): typed_note_cut kept every part right after CloudStorage, and
    the pair loop skips a CloudStorage --source-local argument, so an unregistered `OneDrive-Fabrikam` printed
    in a typed note and an args= line beside the public link. The provider rule (CUT_PROVIDER_KEPT, the
    table's) now runs in typed_note_cut's cut() and in a pass after the pairs over each `--source-local
    ~/Library/CloudStorage/` word, bare or quoted, a `\\ ` kept in it: both reports print the same line, a
    registered tenant, a domain naming no organisation and the app's own name untouched."""
    lib = Path(env["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso"
    (lib / "Northwind Plans").mkdir(parents=True)
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(local_source_table("s0", lib / "Northwind Plans"), encoding="utf-8")
    line = f"2026-10-12T10:00:01Z | step 2 | deviation | {said} | -"
    text = _shell_report(env, f"Attempt: 2026-10-12T10:00:00Z\nPrompt: v15\n{line}\n")
    [shell] = [ln for ln in text.splitlines() if " | step 2 | deviation | " in ln]
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    python = setup_report._redact_lines(red, [line])
    assert shell.split(" | deviation | ", 1)[1] == f"{want} | -", shell
    assert python.split(" | deviation | ", 1)[1] == f"{want} | -", python
    assert "Fabrikam" not in text


def test_the_shell_cuts_a_refused_name_under_a_configured_folder_whole_c1833(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 40 C1833: under a configured folder, a refused name with a blank printed its tail in the
    shell report (`<path> Plans:`); it runs to the last refusal head now. Since C1882 (with C1834, ruling 137,
    the same land) the agentsync report cuts this word at the provider folder too, so both reports print
    `OneDrive-<org-1>/<path>: its name is ...`, held equal here (B13 round 41 C2056)."""
    lib = Path(env["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso"
    (lib / "Northwind Plans").mkdir(parents=True)
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(local_source_table("s0", lib / "Northwind Plans"), encoding="utf-8")
    said = (
        "usage error: --source-local ~/Library/CloudStorage/OneDrive-Contoso/Northwind Plans/Wombat Plans: "
        "its name is step 1's escaped form of another folder there"
    )
    text = _shell_report(
        env,
        f"Attempt: 2026-10-12T10:00:00Z\nPrompt: v15\n"
        f"2026-10-12T10:00:01Z | step 2 | deviation | {said} | -\n",
    )
    [shell] = [ln for ln in text.splitlines() if " | step 2 | deviation | " in ln]
    assert "OneDrive-<org-1>/<path>: its name is step 1's escaped form" in shell, shell
    assert "Wombat" not in text and " Plans:" not in shell, shell
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    line = f"2026-10-12T10:00:01Z | step 2 | deviation | {said} | -"
    python = setup_report._redact_lines(red, [line])
    assert shell.split(" | deviation | ", 1)[1] == python.split(" | deviation | ", 1)[1], (shell, python)


def test_both_reports_number_a_configured_tenant_whose_provider_folder_is_gone_c1862(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 40 C1862 (ruling 137; privacy): the shell registered a tenant only from the provider folders
    on disk, so a configured source whose provider folder is gone (signed out) printed its tenant in clear on
    doctor's listable line and in free text, where the agentsync report, which registers every configured
    source's tenant from sources.toml, printed `OneDrive-<org-2>`. (A two-letter source id is no shell pair,
    an older difference, so the doctor line here names none.) The shell registers them too, after the
    disk walk, in config order; and a `~/Library/CloudStorage/<App>-<word>` word no value registers reads
    `~/Library/CloudStorage/<path>` in both, never numbered."""
    cs = Path(env["HOME"]) / "Library" / "CloudStorage"
    alder, cedar = cs / "OneDrive-Fabrikam" / "Alder", cs / "OneDrive-Contoso" / "Cedar"
    for d in (alder, cedar):
        d.mkdir(parents=True)
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(local_source_table("s0", alder) + local_source_table("s1", cedar), encoding="utf-8")
    (cs / "OneDrive-Fabrikam" / "Alder").rmdir()
    (cs / "OneDrive-Fabrikam").rmdir()
    said = [
        "doctor: ~/Library/CloudStorage/OneDrive-Fabrikam/Alder does not exist",
        "saw ~/Library/CloudStorage/OneDrive-Fabrikam/Alder, then",
        "saw ~/Library/CloudStorage/OneDrive-Northwind/Birch, then",
    ]
    rows = [f"2026-10-12T10:00:0{i}Z | step 2 | deviation | {s} | -" for i, s in enumerate(said, 1)]
    text = _shell_report(env, "Attempt: 2026-10-12T10:00:00Z\nPrompt: v15\n" + "\n".join(rows) + "\n")
    shell = [
        ln.split(" | deviation | ", 1)[1] for ln in text.splitlines() if " | step 2 | deviation | " in ln
    ]
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    python = [ln.split(" | deviation | ", 1)[1] for ln in setup_report._redact_lines(red, rows).splitlines()]
    assert shell == python, (shell, python)
    assert shell[0].startswith("doctor: ~/Library/CloudStorage/OneDrive-<org-2>/<folder-1>"), shell
    assert shell[2] == "saw ~/Library/CloudStorage/<path>, then | -", shell
    assert "Fabrikam" not in text and "Northwind" not in text


def test_both_reports_cut_a_refused_look_alike_at_its_provider_folder_c1601(
    env: dict[str, str],  # noqa: F811
) -> None:
    """B13 round 39 C1601 with C1656 (ruling 133): a refused look-alike --source-local folder under
    CloudStorage read `<path>` whole in the shell report's refusal line, `OneDrive-<org-2>/<folder-1>` on its
    install.log line, and `<org-2>/<path>` or `<org-2>/<folder-2>` in the agentsync report. Every such line
    in both reports now reads `~/Library/CloudStorage/OneDrive-<org-1>/<path>`, no list-only name numbered
    there, and the shell legend's <path> item names that form."""
    lib = Path(env["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso"
    (lib / "Zephyr\nQuarry").mkdir(parents=True)
    (lib / "Zephyr\\nQuarry").mkdir()
    assert install_sh(env, "--list-folders").returncode in (0, 3)
    refused = install_sh(env, "--source-local", str(lib / "Zephyr\\nQuarry"))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "its name is step 1's escaped form of another folder there" in refused.stderr, refused.stderr
    form = "~/Library/CloudStorage/OneDrive-<org-1>/<path>"
    reports = {"shell": report_path(env).read_text(encoding="utf-8")}
    assert "shell fallback" in refused.stdout or "Shell fallback" in reports["shell"], refused.stdout
    assert install_sh(_renderer_home(env, "checkout"), "--report-only").returncode == 0
    reports["agentsync"] = report_path(env).read_text(encoding="utf-8")
    for who, text in reports.items():
        assert "Zephyr" not in text and "Quarry" not in text, who
        said = [ln for ln in text.splitlines() if "--source-local ~/Library/CloudStorage/" in ln]
        assert said, (who, text)
        assert all(form in ln and "<folder-" not in ln for ln in said), (who, said)
    # the shell report's own record of the refusal (the agentsync report names only its exit here)
    own = f"--source-local {form}: its name is step 1's"
    assert [ln for ln in reports["shell"].splitlines() if own in ln], reports["shell"]


# ---- ruling 133's property test: both reports over one hostile name set, in every line form ----------------

_HOSTILE_CHARS = (
    [chr(c) for c in range(1, 32)]
    + ["\x7f", "\x85", "\u2028", "\u2029"]
    + ["\u00ad", "\u0890", "\U000110bd", "\U00013430", "\U0001d173"]
)
"""Ruling 133's characters, each between two made-up words: each C0 control, DEL, each break str.splitlines()
honours, and Cf characters safe_shown once kept (U+00AD, U+0890, U+110BD, U+13430, U+1D173)."""
_HOSTILE_WORDS = (
    "\u200bStatus",
    "\u2060Status",
    "\u200bDocuments",
    "\u2060Music",
    "\u200bCopilot",
    "Q3\x08Plans",
    "Curate Notes",
    "Exclude Empty Alder",
    "Café\x1bAlder",
)
"""Ruling 133's named cases: a zero-width or word joiner before a report word, a generic folder and a product
word, a control byte between two words, verb-led folders and a bash 3.2 UTF-8 `%q` word (Café with an ESC)."""


def _printf_q(word: str, locale: str) -> str:
    cp = subprocess.run(
        [BASH32, "-c", 'printf %q "$1"', "_", word],
        capture_output=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": locale, "LANG": locale},
    )
    return cp.stdout.decode("utf-8", "replace")


def _hostile_lines(
    n: int, name: str, locale: str, provider: str = "OneDrive-Contoso", home: str = "/Users/dana"
) -> list[str]:
    """Each line form the table names (B13 round 40 ruling 137: :func:`_table_rows` holds that every row has
    one), for the configured folder ``name`` (source s<n>), quoted under ``provider``: args= with the
    ``$'...'`` word mid-path (the word an agent might type; install.log's own form, the whole path through
    ``printf %q``, is ruling 138's pinned divergence when it is dollar-quoted, :func:`_divergent_lines`),
    ``--source-local=``, each refusal head, the not-a-folder line, a dry run's add-source word, the run
    header's ``--cwd `` and ``--cwd=``, the debug lines, add-source's echoed table in both DEL spellings
    (taken from config.local_source_table, never a hand copy), a typed note collapsed and not, a free-text
    and doctor's detail, doctor's fix, and step 1's list spelling, new and old (C1802)."""
    shown, old = _listed(name), _listed(name, old=True)
    root = f"~/Library/CloudStorage/{provider}"
    q = _printf_q(name, locale)
    ansi = setup_report._ansi_c_body(name, setup_report._DOCTOR_LETTERS)
    table = local_source_table(f"s{n}", Path(f"{home}/Library/CloudStorage/{provider}/{name}"))
    [echo] = [ln for ln in table.splitlines() if ln.startswith("path = ")]
    return [
        f"start install.sh args=--source-local {root}/{q}",
        f"I ran install.sh --source-local={root}/{q} first",
        *(f"usage error: --source-local {root}/{shown}: {head}x" for head in report_cuts.REFUSED_HEADS),
        f"usage error: --source-local is not an existing folder: {root}/{shown} (see --help)",
        f"[dry-run] ~/.local/bin/agentsync add-source {root}/{q}",
        f"# run=r{n} 2026-11-16T00:00:00Z install.sh --cwd {root}/{q} --debug",
        f"# run=r{n} 2026-11-16T00:00:00Z install.sh --cwd={root}/{q}",
        f"debug: cwd {root}/{shown}",
        f"debug: source checkout {root}/{shown}/agent-context-sync",
        f"debug: config {root}/{shown}/sources.toml",
        echo,
        *([echo.replace(report_cuts.DEL_ESCAPED[0], report_cuts.DEL_ESCAPED[1])] if "\x7f" in name else []),
        f"added source 'alpha' to ~/agent-context/sources.toml ({root}/{shown}, stored as {root}/{shown}): x",
        f"added source 'alpha' to ~/agent-context/sources.toml ({root}/{shown}, stored as ~/x/{shown}): x",
        f"saw {root}/{safe_text(name)}",
        f"[warn] source.alpha.listable — {root}/{shown} is empty",
        f"source-local: {root}/{old}",
        f"open ~/$'Library/CloudStorage/{provider}/{ansi}' in Finder once",
    ]


_R142_SHELL = True
"""Whether ruling 142's tests hold the shell report too. Lane RB's shell half sets it True in the one joint
land, so trunk never carries one half; on lane RA's branch alone they hold the agentsync report (B13 round 41
ruling 142, its Python half)."""

_R146_SHELL = True
"""Whether B13 round 42 rulings 146 to 149's rows hold the shell report too. Lane RB's shell half sets it True
in the one joint land, so trunk never carries one half; on lane RA's branch alone they hold the agentsync
report (the rulings' Python half), and ruling 142's test compares the shell's line only on its older rows."""


def _legacy_echo(path: str) -> str:
    """add-source's echoed ``path = ...`` line as config.toml_string wrote it before af21e6339: json.dumps
    with ensure_ascii off and DEL as ``\\u007F``, the line breaks raw (B13 round 42 C2320), spelled here apart
    from setup_report's helper."""
    return 'path = "' + json.dumps(path, ensure_ascii=False)[1:-1].replace("\x7f", "\\u007F") + '"'


def _r146_lines(
    n: int, name: str, locale: str, provider: str = "OneDrive-Contoso", home: str = "/Users/dana"
) -> list[str]:
    """B13 round 42 rulings 146 to 148's forms for the configured folder ``name`` (source s<n>), part of
    :func:`_r142_lines`: an empty debug value before another head and in the double-space form, the
    carried cwd kept (C2286 with C2242); an empty --cwd and not-a-folder name (C2242); and a quoted name
    for both whole-name heads, single- and double-quoted, run to its line's end and to install.sh's own
    end (C2234)."""
    shown = _listed(name)
    q = _printf_q(name, locale)
    out = _OUTSIDE.replace(" ", "\\ ")
    cwd_name = next(h for h, rule in report_cuts.PATH_HEADS if rule == "cwd_name")
    quoted = [
        line
        for head in (
            f"usage error: --source-local {setup_report.NOT_A_FOLDER_SAID}",
            f"usage error: {cwd_name}",
        )
        for mark in ("'", '"')
        for line in (
            f"{head}{mark}~/{_OUTSIDE}/{shown}{mark} and then I retried",
            f"{head}{mark}{_OUTSIDE}{mark} {shown} (see --help)",
        )
    ]
    return [
        f"debug: config | debug: cwd ~/{out}",
        f"debug: source checkout ~/{out}/{q}",
        f"debug: source | debug: cwd ~/{out}",
        f"debug: cwd | debug: cwd ~/{out}",
        f"debug: config  | debug: cwd ~/{out}",
        f"debug: cwd ~/{out}",
        "debug: cwd | debug: config ~/a",
        f"debug: source checkout ~/{out}/{q}",
        "debug: config ",
        "debug: cwd ",
        f"usage error: {cwd_name} (see --help)",
        f"usage error: --source-local {setup_report.NOT_A_FOLDER_SAID} (see --help)",
        *quoted,
    ]


_OUTSIDE = "Wombat Ledger"
"""A made-up private folder outside ~/Library/CloudStorage, in no config, with a blank (ruling 142's case of a
path outside the expected place)."""
_OTHER_HOME = f"/Users/olga/{_OUTSIDE}/Tailspin Northwind"
"""Another home's path with a blank and a later word holding a `/` (B13 round 43 C2468): both reports cut it
to ``/Users/<path> Northwind``, the other-home rule's residual word, whatever head it follows."""
_C2468_HEADS = (
    "--source-local=",
    "--source-local ",
    "'--source-local=",
    "--cwd=",
    "'--cwd=",
    "--cwd ",
    "add-source ",
)


def _r142_lines(
    n: int, name: str, locale: str, provider: str = "OneDrive-Contoso", home: str = "/Users/dana"
) -> list[str]:
    """B13 round 41 ruling 142's forms for the configured folder ``name`` (source s<n>): per head, a path
    outside CloudStorage, bare with ``\\ ``, single- and double-quoted (C2062 with C2031 and C2085); the --cwd
    usage error's whole name, absolute and relative (C2030); a path under --cwd and every debug head twice on
    a line (C2029); a config path outside the cwd (C2063); two heads on one line; a quoted refused and
    not-a-folder name with a blank, the rest of its line kept (C2027); a tenant with a blank before its ``/``
    (C2050 with C2032); and prose rows that pin the guard: a bare word after a head, add-source's own
    sentences and install.sh's --cwd usage words, which stay whole. R41-B1: add-source's echoed table with
    each line break config.toml_string wrote raw in each escaped spelling (report_cuts.TOML_BREAKS), the table
    split at LF only, never by splitlines(), which cuts it at U+2028; and the older writer's echo, the breaks
    raw (B13 round 42 C2320 with C2239). B13 round 42 C2275: install.sh's own two ``--cwd=`` forms outside
    CloudStorage. B13 round 43 C2466 (ruling 158, lifting ruling 148's "never the form whose quote wraps the
    whole argument"): a quote that wraps a whole ``--cwd``, ``--source-local`` or ``add-source`` argument, in
    the ``=`` and blank forms, single and double, outside CloudStorage and under it with an unconfigured name
    holding a blank, left open, and with a second head inside it. B13 round 43 C2468 (ruling 158): another
    home's path with a blank after each head (:data:`_C2468_HEADS`) and behind a registered org's volume,
    which the shell cut after its head cuts; and :func:`_r146_lines`."""
    shown = _listed(name)
    root = f"~/Library/CloudStorage/{provider}"
    q = _printf_q(name, locale)
    out = _OUTSIDE.replace(" ", "\\ ")
    free = safe_text(name)
    cwd_name = next(h for h, rule in report_cuts.PATH_HEADS if rule == "cwd_name")
    table = local_source_table(f"s{n}", Path(f"{home}/Library/CloudStorage/{provider}/{name}"))
    [echo] = [ln for ln in table.split("\n") if ln.startswith("path = ")]
    escaped = [
        echo.replace(ch, spelling)
        for ch in report_cuts.TOML_BREAKS
        if ch in name
        for spelling in report_cuts.toml_break_spellings(ch)
    ]
    # B13 round 42 C2320 with C2239 (ruling 147): the older writer's echo, its line breaks raw
    if any(ch in name for ch in report_cuts.TOML_BREAKS):
        escaped.append(_legacy_echo(f"{home}/Library/CloudStorage/{provider}/{name}"))
    return [
        *escaped,
        f"start install.sh args=--source-local ~/{out}/{q}",
        f"start install.sh args=--source-local '~/{_OUTSIDE}/{shown}' then",
        f'I ran install.sh --source-local="~/{_OUTSIDE}/{shown}" first',
        f"[dry-run] ~/.local/bin/agentsync add-source ~/{out}/{q}",
        f"usage error: --source-local is not an existing folder: ~/{_OUTSIDE}/{shown} (see --help)",
        f"usage error: --source-local ~/{_OUTSIDE}/{shown}: {report_cuts.REFUSED_HEADS[3]}x",
        f"usage error: {cwd_name}~/{_OUTSIDE}/{shown} (see --help)",
        f"usage error: {cwd_name}{_OUTSIDE} (see --help)",
        f"# run=s{n} 2026-11-17T00:00:00Z install.sh --cwd ~/{out} --debug --source-local ./{q}",
        f"# run=s{n} 2026-11-17T00:00:00Z install.sh --cwd=~/{out}/{q} --debug",  # B13 round 42 C2275
        f"start install.sh args=--cwd='~/{_OUTSIDE}/{shown}' --source-local ./x",  # B13 round 42 C2275
        # B13 round 43 C2466 (ruling 158): the quote wraps the whole argument
        f"start install.sh args='--cwd=~/{_OUTSIDE}/{shown}' x",
        f"start install.sh args='--cwd ~/{_OUTSIDE}/{shown}' x",
        f'start install.sh args="--cwd=~/{_OUTSIDE}/{shown}" x',
        f"start install.sh args='--source-local=~/{_OUTSIDE}/{shown}' x",
        f'start install.sh args="--source-local ~/{_OUTSIDE}/{shown}" x',
        f"start install.sh args='--source-local={root}/{_OUTSIDE}' x",
        f"I ran 'add-source ~/{_OUTSIDE}/{shown}' first",
        f"start install.sh args='--cwd=~/{_OUTSIDE}/{shown} then more",
        f"start install.sh args='--cwd ~/{_OUTSIDE} --debug --source-local ./{shown}' x",
        # B13 round 44 C2666 (ruling 173): a quote that opens a whole command before the head, an apostrophe
        # in prose before that quote, and a double-quoted whole command, each value to its quote's close
        f"I ran '~/.local/bin/agentsync add-source ~/{_OUTSIDE}/{shown}' first",
        f"I ran 'install.sh --cwd=~/{_OUTSIDE}/{shown}' first",
        f"Dana's run: '~/src/install.sh --cwd=~/{_OUTSIDE}/{shown}' first",
        f'I ran "install.sh --debug --source-local ~/{_OUTSIDE}/{shown}" first',
        # B13 round 43 C2468 (ruling 158): another home's path with a blank, a later word holding a `/`, after
        # each head and behind a registered org's volume, only the residual word (no `/`) in clear in both
        *(f"I ran {h}{_OTHER_HOME} x" for h in _C2468_HEADS),
        f"I ran --source-local=/Volumes/{provider.split('-', 1)[1]}{_OTHER_HOME} x",
        f"debug: cwd ~/{out} | debug: source checkout ~/{out}/{q}",
        f"debug: source checkout ~/{out}/{q} | debug: source tree ~/{out}/x",
        f"debug: cwd {root}/{shown} | debug: cwd ~/{out}",
        f"debug: config ~/{out}/a.toml | debug: config ~/{out}/{q}/b.toml",
        f"start install.sh args=--source-local {root}/{q} --source-local ~/{out}/{q}",
        f'usage error: --source-local "{root}/{shown}/{_OUTSIDE}": {report_cuts.REFUSED_HEADS[1]}x and more',
        f"usage error: --source-local is not an existing folder: '{root}/{_OUTSIDE}' (see --help); retried",
        f"saw ~/Library/CloudStorage/OneDrive-{_OUTSIDE}/{free}, then",
        f"saw ~/Library/CloudStorage/OneDrive-SharedLibraries-{_OUTSIDE}/A x/{free} done",
        "usage error: --source-local is not an existing folder: (see --help)",
        "I ran add-source again, then add-source --help",
        "Dana's note: add-source again, then add-source --help",  # C2666: an apostrophe and no path, whole
        "~/.local/bin/agentsync add-source failed after adding it; add-source opened past it",
        *(f"usage error: --cwd {usage} --debug (see --help)" for usage in report_cuts.CWD_USAGE),
        f"debug: config {report_cuts.CONFIG_DEFAULT}",
        *_r146_lines(n, name, locale, provider, home),
    ]


def _fragments(names: list[str]) -> set[str]:
    """The words of each name a report must not print: each word of the raw name split at every character
    that is no letter or digit, and each of its safe_text form, apart (B13 round 41 ruling 142: splitting only
    safe_text missed ``Alder29\\u001eBirch29``), but the report's own words."""
    words = {
        w
        for name in names
        for text in (name, safe_text(name))
        for w in re.split(r"[^A-Za-z0-9\u00c0-\u024f]+", text)
        if len(w) > 3
    }
    return words - {"Status", "Documents", "Music", "Copilot", "Curate", "Exclude", "Empty", "Notes", "Plans"}


def _table_rows() -> set[str]:
    """Every row of report_cuts' table a line form reads: each path head, each refusal head, each DEL
    spelling and the free-text provider head (B13 round 40 ruling 137)."""
    return {
        *(head for head, _rule in report_cuts.PATH_HEADS),
        *report_cuts.REFUSED_HEADS,
        *report_cuts.DEL_ESCAPED,
        report_cuts.FREE_PROVIDER_HEAD,
        *(f"--cwd {usage}" for usage in report_cuts.CWD_USAGE),  # B13 round 41 C2030
        report_cuts.CONFIG_DEFAULT,  # B13 round 41 C2063 with C2083
        *(sp for ch in report_cuts.TOML_BREAKS for sp in report_cuts.toml_break_spellings(ch)),  # R41-B1
    }


def test_every_row_of_the_table_is_a_case_of_the_property_test_ruling_137() -> None:
    """B13 round 40 ruling 137 (privacy; data integrity): ruling 133's table held the values both reports cut
    by but not the forms they parse, and the hand-kept forms drifted. Every argument form is now a row of the
    table, and the property test's lines come from those rows, so a row with no case fails here."""
    lines = [
        *_hostile_lines(0, "Alder\x7fBirch", "C", home="/Users/dana"),
        *_r142_lines(0, "Alder\x7fBirch", "C"),  # B13 round 41 ruling 142's forms
        *_r142_lines(1, "Alder\x85Birch\u2028Cedar\u2029Dace", "C"),  # R41-B1's escaped line breaks
    ]
    missing = {row for row in _table_rows() if not any(row in ln for ln in lines)}
    assert not missing, missing


def _both_reports(
    env: dict[str, str],  # noqa: F811
    folders: list[str],
    said: list[str],
    locale: str,
    *,
    shell: bool = True,
    provider: str = "OneDrive-Contoso",
    ids: list[str] | None = None,
    listed: list[str] | None = None,
    config: Path | None = None,
    cwd: Path | None = None,
    config_body: str | None = None,
    texts_out: list[str] | None = None,
    vanish: bool = False,
) -> tuple[list[str], list[str], bool, bool]:
    """Each of ``said``, a friction row of its own, as the shell report and this checkout's agentsync report
    print it, with ``folders`` configured under ``provider`` (ids ``ids``, else s<n>), install.sh under
    ``locale``; and each report's private-only verdict. Without ``shell``, the agentsync report alone (ruling
    137's Python half): the shell's lines are empty and its verdict False. B13 round 41 ruling 142's arms:
    ``listed``, folders made beside them and listed by ``install.sh --list-folders -a`` first, never
    configured (C2060); ``config``, sources.toml's place, read through AGENTSYNC_CONFIG relative to ``cwd``
    when it is relative, both reports run from ``cwd`` and install.sh with ``--cwd`` (C2063 with C2083),
    holding ``config_body`` in place of the tables, none at all when it is ``""``; ``texts_out`` takes the two
    whole reports; ``vanish``, each ``listed`` folder gone from disk once step 1 listed it, so only
    install.out's list names it (B13 round 44: both reports list CloudStorage, C2669)."""
    lib = _cloud(env) / provider
    lib.mkdir(parents=True, exist_ok=True)
    tables = []
    for n, name in enumerate(folders):
        (lib / name).mkdir(parents=True, exist_ok=True)
        # as written: config.toml_string escapes DEL itself since lane B's R39-B1 (B13 round 40 C1887)
        tables.append(local_source_table(ids[n] if ids else f"s{n}", lib / name))
    for name in listed or []:
        (lib / name).mkdir(parents=True, exist_ok=True)
    if config is None:
        _write_config(env, *tables)
    else:
        env = {**env, "AGENTSYNC_CONFIG": str(config)}
        at = config if config.is_absolute() else (cwd or Path(env["HOME"])) / config
        at.parent.mkdir(parents=True, exist_ok=True)
        if config_body != "":
            body = default_config_text() + "".join(tables) if config_body is None else config_body
            at.write_text(body, encoding="utf-8")
    if listed:
        assert install_sh({**env, "LC_ALL": locale, "LANG": locale}, "--list-folders", "-a").returncode in (
            0,
            3,
        )
        for name in listed if vanish else []:
            (lib / name).rmdir()
    rows = "".join(
        f"2026-10-10T00:00:{n % 60:02d}Z | step 1 | deviation | {ln} | -\n" for n, ln in enumerate(said)
    )
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(f"Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\n{rows}", encoding="utf-8")
    texts = [""]
    if shell:
        where = ("--cwd", str(cwd)) if cwd is not None else ()
        cp = install_sh({**env, "LC_ALL": locale, "LANG": locale}, *where, "--report-only")
        assert cp.returncode == 0 and "shell fallback" in cp.stdout, cp.stdout + cp.stderr
        texts = [report_path(env).read_text(encoding="utf-8")]
    # this checkout's own render, as `agentsync setup-report` writes it (install.sh would recut a render it
    # cannot stamp as its own build, ruling 126's other-build pass)
    out = Path(env["HOME"]) / "agentsync-report.md"
    cp = subprocess.run(
        [sys.executable, "-m", "agentsync", "setup-report", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**env, "LC_ALL": locale, "LANG": locale, "AGENTSYNC_OCR": "0", "AGENTSYNC_TM_EXCLUDE": "0"},
        stdin=subprocess.DEVNULL,
        cwd=cwd,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    texts.append(out.read_text(encoding="utf-8"))
    if texts_out is not None:
        texts_out[:] = texts
    cut = " | step 1 | deviation | "
    shell, python = (
        [ln.split(cut, 1)[1].removesuffix(" | -") for ln in text.splitlines() if cut in ln] for text in texts
    )
    return shell, python, "Private only:" in texts[0], "Private only:" in texts[1]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    "names",
    [
        *(
            [f"Alder{n:02d}{ch}Birch{n:02d}" for n, ch in enumerate(_HOSTILE_CHARS)][g : g + 6]
            for g in range(0, len(_HOSTILE_CHARS), 6)
        ),
        *([w] for w in _HOSTILE_WORDS),
    ],
)
@pytest.mark.parametrize("provider", ["OneDrive-Contoso", "OneDrive-Fabrikam", "OneDrive-Personal"])
def test_both_reports_print_one_line_over_a_hostile_name_set_ruling_133(
    env: dict[str, str],  # noqa: F811
    names: list[str],
    locale: str,
    provider: str,
) -> None:
    """B13 round 39 ruling 133 (privacy; data integrity): ruling 126's halves were ported by hand and drifted
    (C1625 to C1629, C1671, C1672, C1681, C1601). Both reports now cut by one table, and over one hostile name
    set, in every line form the table names (B13 round 40 ruling 137) but ruling 138's one divergence, which
    test_the_one_kept_divergence_is_pinned_in_both_reports_ruling_138 pins, under LC_ALL=C and en_US.UTF-8,
    the shell report and this checkout's agentsync report print the same line, give the same private-only
    verdict and print no fragment of a name, for the folders quoted under a registered, an unregistered and a
    generic-domain provider folder. safe_shown over every Cf the running Python knows is
    test_safe_shown_drops_every_format_character_the_running_python_knows_c1628_c1672 in
    test_install_oneshot.py."""
    if provider != "OneDrive-Contoso" and len(names) > 1:
        pytest.skip("each other provider folder runs over the named cases")
    said = [
        ln for n, name in enumerate(names) for ln in _hostile_lines(n, name, locale, provider, env["HOME"])
    ]
    shell, python, shell_private, python_private = _both_reports(env, names, said, locale)
    assert shell_private == python_private, (names, shell_private, python_private)
    assert len(shell) == len(python) == len(said), (len(shell), len(python), len(said))
    frags = {w for name in names for w in re.split(r"[^A-Za-z0-9]+", safe_text(name)) if len(w) > 3}
    frags -= {"Status", "Documents", "Music", "Copilot", "Curate", "Exclude", "Empty", "Notes", "Plans"}
    frags.add("Fabrikam")
    pairs = list(zip(shell, python, strict=True))
    # an unregistered tenant is cut by structure on every line, free text and doctor's detail included, by
    # the provider rule both reports run there (B13 round 40 C1862)
    leaks = [(a, b) for a, b in pairs for f in frags if f in a or f in b]
    assert not leaks, (len(leaks), leaks[:4])
    wrong = [(a, b) for a, b in pairs if a != b]
    assert not wrong, (len(wrong), wrong[:8])


def test_a_recut_of_another_builds_render_says_so_under_its_redaction_section_c1406(tmp_path: Path) -> None:
    """B13 round 38 C1406 (ruling 126): another build's render that this checkout's recut changed showed
    `/Users/<path>` and `<path>` its own legend neither names nor counts. One line right after its
    `Placeholders used:` line says so, with the count of cuts the passes made, the totals line unchanged;
    written once, never on this checkout's render, and never beside one already there."""
    report = tmp_path / "setup-report.md"
    older = (
        "# agentsync setup report\n\n## Friction\n\n"
        "saw /Users/dana.contoso/Fabrikam/notes.txt\n"
        "usage error: --source-local is not an existing folder: ~/Tailspin Merger (see --help)\n\n"
        "## Redaction\n\n3 replacements of 2 values (folder).\nPlaceholders used: <folder-N>.\n"
        "Send it: privately.\n"
    )
    report.write_text(older, encoding="utf-8")
    cp = _report_recut_harness(tmp_path, report)
    assert cp.returncode == 0, cp.stderr
    lines = report.read_text(encoding="utf-8").splitlines()
    at = lines.index("Placeholders used: <folder-N>.")
    assert lines[at + 1].startswith(
        "This checkout's installer cut this report again after the installed agentsync"
    ), lines
    assert lines[at + 1].endswith("these 2 cut(s) are not counted above."), lines[at + 1]
    assert lines[at - 1] == "3 replacements of 2 values (folder).", "the totals line is the render's own"
    assert "saw /Users/<path>" in lines and "Fabrikam" not in report.read_text(encoding="utf-8")
    once = report.read_bytes()
    assert _report_recut_harness(tmp_path, report).returncode == 0
    assert report.read_bytes() == once, "a second pass adds nothing"
    report.write_text(older, encoding="utf-8")
    assert _report_recut_harness(tmp_path, report, other=0).returncode == 0
    assert "This checkout's installer" not in report.read_text(encoding="utf-8"), "never on its own render"
    # B13 round 39 C1604 (ruling 135): a render that already held <path> (an older build's own exclude cut)
    # scopes this pass's <path> to its two kinds; one with none keeps the sentence above
    assert "this pass's <path>" not in lines[at + 1] and "any other <path>" not in lines[at + 1]
    report.write_text(
        older.replace(
            "## Redaction", "set exclude = [<path>] in [[source]]\nsaw /Users/olga/x\n\n## Redaction"
        ),
        encoding="utf-8",
    )
    cp = _report_recut_harness(tmp_path, report)
    assert cp.returncode == 0, cp.stderr
    scoped = report.read_text(encoding="utf-8").splitlines()
    note = scoped[scoped.index("Placeholders used: <folder-N>.") + 1]
    assert note.startswith(
        "This checkout's installer cut this report again after the installed agentsync wrote it: this pass's "
        "<path> stands for a path in another home, cut whole to /Users/<path>"
    ), note
    assert (
        "; any other <path> here was already in the render, the installed agentsync's own cut; "
        "these 3 cut(s) are not counted above."
    ) in note, note
    assert "set exclude = [<path>] in [[source]]" in scoped


_DIVERGENT_NAMES = [
    "Alder\x01Birch",
    "Alder\x7fBirch",
    "Alder\tBirch",
    "Alder\u2028Birch",
    "Caf\u00e9\x1bAlder",
]
"""Names bash 3.2's ``printf %q`` dollar-quotes: a C0 control, DEL, a tab, U+2028, an accent with an ESC."""


def _divergent_lines(name: str, locale: str, provider: str, home: str) -> list[str]:
    """Ruling 138's three forms of a bare ``printf %q`` word of the whole path: install.log's real args=
    (C1839), the = form and a dry run's add-source word (C1863 with C1864)."""
    q = _printf_q(f"{home}/Library/CloudStorage/{provider}/{name}", locale)
    return [
        f"start install.sh args=--source-local {q}",
        f"I ran install.sh --source-local={q} first",
        f"[dry-run] ~/.local/bin/agentsync add-source {q}",
    ]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("provider", ["OneDrive-Contoso", "OneDrive-Fabrikam", "OneDrive-Personal"])
def test_the_one_kept_divergence_is_pinned_in_both_reports_ruling_138(
    env: dict[str, str],  # noqa: F811
    locale: str,
    provider: str,
) -> None:
    """B13 round 40 ruling 138 (C1839, C1863 with C1864; makes worse True for C1863: no output changes): a
    bare ``$'...'`` word right after ``--source-local ``, ``--source-local=`` or ``add-source `` is cut whole
    to ``<path>`` by the shell report (ruling 134: it never decodes a body no pair covers) and decoded, cut by
    its head's rule and shown in double quotes by the agentsync report (round 17 U10; ruling 49). The property
    test once left the pair out; it is pinned here, for install.log's real form, under a registered, an
    unregistered and a generic-domain provider: neither line holds a name fragment, ``$'``, an octal escape or
    U+FFFD, and the verdicts match. A word ``%q`` writes with backslash escapes stays equal."""
    said = [ln for name in _DIVERGENT_NAMES for ln in _divergent_lines(name, locale, provider, env["HOME"])]
    shell, python, shell_private, python_private = _both_reports(env, _DIVERGENT_NAMES, said, locale)
    assert shell_private == python_private, (shell_private, python_private)
    assert len(shell) == len(python) == len(said), (len(shell), len(python))
    kept = r"~/Library/CloudStorage/(?:OneDrive-<org-\d+>|OneDrive-Personal)/<(?:folder-\d+|path)>"
    decoded = rf'"(?:{kept}|~/Library/CloudStorage/<path>)"'
    for (a, b), ln in zip(zip(shell[: len(said)], python[: len(said)], strict=True), said, strict=True):
        head = ln.split("$'", 1)[0]
        assert a == f"{head}<path>" + (" first" if head.endswith("=") else ""), (ln, a)
        assert re.fullmatch(re.escape(head) + decoded + (" first" if head.endswith("=") else ""), b), (ln, b)
        for text in (a, b):
            assert not re.search(r"Alder|Birch|Caf|\$'|\\[0-7]{3}|\ufffd", text), text
    # alone, as the property test runs a single name: a word %q writes with backslash escapes stays equal
    plain = _divergent_lines("Juniper Notes", locale, provider, env["HOME"])
    assert all("$'" not in ln for ln in plain), plain
    shell, python, shell_private, python_private = _both_reports(env, ["Juniper Notes"], plain, locale)
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))
    assert not any("Juniper" in ln for ln in shell), shell


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_cut_a_nested_source_local_word_at_its_provider_folder_c1882_c1834(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 40 C1882 with C1834 (ruling 137; data integrity): the shell report read a nested
    --source-local argument at its provider folder and the agentsync report at its first unknown part, so one
    install.log line showed two ways. Python moved to the shell's rule: a missing folder under Documents or
    under a configured folder, and a refused look-alike, read `OneDrive-<org-1>/<path>` in both reports; a
    configured folder under Documents keeps its `<folder-N>` in both. The refused name runs to the last head
    on its line in both (C1833, lane RB's shell half, in the same land)."""
    lib = f"{env['HOME']}/Library/CloudStorage/OneDrive-Contoso"
    said = [
        f"start install.sh args=--source-local {lib}/Documents/Zephyr\\ Quarry",
        f"start install.sh args=--source-local {lib}/Alder\\ Birch/Zephyr\\ Quarry",
        f"usage error: --source-local is not an existing folder: {lib}/Alder Birch/Zephyr Quarry"
        " (see --help)",
        f"usage error: --source-local {lib}/Documents/Zephyr Quarry: {report_cuts.REFUSED_HEADS[2]}x",
        f"start install.sh args=--source-local {lib}/Documents/Cedar\\ Notes",
    ]
    shell, python, shell_private, python_private = _both_reports(
        env, ["Alder Birch", "Documents/Cedar Notes"], said, locale
    )
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))
    org = "~/Library/CloudStorage/OneDrive-<org-1>"
    assert [ln.rsplit(" ", 1)[-1] if "args=" in ln else ln for ln in python[:2]] == [f"{org}/<path>"] * 2
    assert f"{org}/<path> (see --help)" in python[2] and f"{org}/<path>: " in python[3]
    # a configured folder's every part is a configured value, Documents included, so both number it
    assert re.search(rf"{re.escape(org)}/<folder-\d>/<folder-\d>$", python[4]), python[4]
    assert not any(w in ln for ln in shell + python for w in ("Zephyr", "Quarry", "Alder", "Cedar")), python


# ---- ruling 142: each head's rule over any path, both reports, both locales --------------------------

_C2083_MIXED = [
    "Alder01\x01Birch01",
    "Alder02\x7fBirch02",
    "Alder03\tBirch03",
    "Alder04\u2028Birch04",
    "Café\x1bAlder05",
    "Juniper Ledger06",
]
"""B13 round 41 C2083: five hostile names and a plain one, as one configured set."""


def _numbers(lines: list[str]) -> set[int]:
    return {int(x) for ln in lines for x in re.findall(r"<folder-(\d+)>", ln)}


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    "names",
    [
        _C2083_MIXED,
        ["Alder01\x01Birch01", "Juniper Ledger06", "Alder01 Birch01x"],  # a plain name like a hostile one
        ["Alder29\x1eBirch29", "Q3\x08Plans"],
        ["Alder07\u2028Birch07", "Alder08\x85Birch08", "Alder09\u2029Birch09"],  # R41-B1
        ["\u200bStatus"],
    ],
)
def test_both_reports_apply_each_heads_rule_to_any_path_ruling_142(
    env: dict[str, str],  # noqa: F811
    names: list[str],
    locale: str,
) -> None:
    """B13 round 41 ruling 142 (privacy; data integrity): ruling 137 made every argument form a row, but the
    shell applied the provider and first-unknown rules only under ~/Library/CloudStorage/ while the agentsync
    report cut every word, so a name elsewhere, or a tail after a provider, printed in clear (C2062 with C2031
    and C2085, C2084), a quoted name lost the rest of its line (C2027), only the first debug head of each kind
    was cut (C2029), the --cwd usage error's folder stayed (C2030) and a tenant with a blank kept its later
    words (C2050 with C2032). Over each head's case outside the expected place: no fragment of a name in
    either report, the prose rows kept as written, both reports one line with the same verdict, and the
    folders numbered exactly <folder-1> on (C2083)."""
    said = [ln for n, name in enumerate(names) for ln in _r142_lines(n, name, locale, home=env["HOME"])]
    shell, python, shell_private, python_private = _both_reports(env, names, said, locale, shell=_R142_SHELL)
    assert len(python) == len(said), (len(python), len(said))
    frags = _fragments(names) | {"Wombat", "Ledger", "Fabrikam"}
    # B13 round 42: the shell's half of rulings 146 to 148's rows is lane RB's (_R146_SHELL)
    new = {ln for n, name in enumerate(names) for ln in _r146_lines(n, name, locale, home=env["HOME"])}
    held_shell = [a for a, ln in zip(shell, said, strict=False) if _R146_SHELL or ln not in new]
    leaks = [ln for ln in [*python, *held_shell] for f in frags if f in ln]
    assert not leaks, (len(leaks), leaks[:4])
    prose = [
        ln for ln in said if "add-source again" in ln or "failed after adding" in ln or "--cwd needs" in ln
    ]
    assert prose and all(ln in python for ln in prose), [ln for ln in prose if ln not in python]
    assert f"debug: config {report_cuts.CONFIG_DEFAULT}" in python
    assert any(ln.endswith("x and more") for ln in python) and any(
        ln.endswith("(see --help); retried") for ln in python
    )
    assert _numbers(python) == set(range(1, len(_numbers(python)) + 1)) and _numbers(python), python
    if _R142_SHELL:
        held = [
            (a, b, ln) for a, b, ln in zip(shell, python, said, strict=True) if _R146_SHELL or ln not in new
        ]
        assert all(a == b for a, b, _ln in held) and shell_private == python_private, [
            (a, b) for a, b, _ln in held if a != b
        ][:8]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    "names", [_C2083_MIXED, ["Alder01\x01Birch01", "Juniper Ledger06", "Alder01 Birch01x"]]
)
def test_both_reports_number_a_mixed_set_one_to_n_c2083(
    env: dict[str, str],  # noqa: F811
    names: list[str],
    locale: str,
) -> None:
    """B13 round 41 C2083 (the verifier's test only: both reports already number this set alike, measured):
    ruling 133's property test ran sets of six only as consecutive hostile characters. Five hostile names and
    a plain one, and a plain name beside a hostile one it fuzzy-matches, over ruling 133's line forms: both
    reports print one line and number the folders exactly <folder-1> to <folder-N>."""
    said = [ln for n, name in enumerate(names) for ln in _hostile_lines(n, name, locale, home=env["HOME"])]
    shell, python, shell_private, python_private = _both_reports(env, names, said, locale)
    assert shell == python and shell_private == python_private, [
        (a, b) for a, b in zip(shell, python, strict=True) if a != b
    ][:8]
    assert _numbers(python) == set(range(1, len(names) + 1)), sorted(_numbers(python))
    # ruling 133's fragment check (safe_text's words): add-source's TOML echo writes U+2028 raw and both
    # reports' line readers split there, which ruling 142's raw-split check flags in both (lane RA's round 41
    # note, sent to the lead); the forms ruling 142 adds take the raw-split check
    frags = {w for name in names for w in re.split(r"[^A-Za-z0-9]+", safe_text(name)) if len(w) > 3}
    assert not [ln for ln in python for f in frags - {"Plans", "Notes"} if f in ln]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("arm", ["relative", "absolute", "missing", "invalid", "loading", "default", "cloud"])
def test_both_reports_cut_a_config_path_but_the_default_c2063_c2083(
    env: dict[str, str],  # noqa: F811
    arm: str,
    locale: str,
) -> None:
    """B13 round 41 C2063 with C2083 (ruling 142; privacy): a config path other than ~/agent-context/
    sources.toml printed in clear on the Configuration line, the loads line, both Not run lines and debug:
    config, in both reports. AGENTSYNC_CONFIG relative under --cwd, absolute outside it, missing, invalid TOML
    and loading: every line reads the ~-form's cut, `~/<path>`, never the folder; the default stays in
    clear. B13 round 42 C2231 (ruling 147): a config inside a configured folder under a registered provider
    folder is cut unredacted in both reports, `~/Library/CloudStorage/<path>`, as install.sh's config_shown
    cuts it, and the two reports' debug: config rows agree."""
    home = Path(env["HOME"])
    cwd = home / _OUTSIDE
    cwd.mkdir(parents=True, exist_ok=True)
    config = {
        "relative": Path("cfg/sources.toml"),
        "absolute": home / "Tailspin Merger" / "sources.toml",
        "missing": home / "Tailspin Merger" / "gone" / "sources.toml",
        "invalid": home / "Tailspin Merger" / "bad.toml",
        "loading": home / "Tailspin Merger" / "sources.toml",
        "default": None,
        "cloud": _cloud(env) / "OneDrive-Contoso" / "Juniper Notes" / "sources.toml",
    }[arm]
    said = [f"debug: config {cwd}/cfg/sources.toml", f"debug: cwd {cwd}"]
    if arm == "cloud":
        said.append(f"debug: config {config}")
    body = {"missing": "", "invalid": "not toml [[[\n"}.get(arm)
    texts: list[str] = []
    shell, python, _sp, _pp = _both_reports(
        env,
        ["Juniper Notes"],
        said,
        locale,
        shell=_R142_SHELL,
        config=config,
        cwd=cwd,
        config_body=body,
        texts_out=texts,
    )
    for text in texts[1:] if not _R142_SHELL else texts:
        assert "Wombat" not in text and "Ledger" not in text and "Tailspin" not in text, text
        [line] = [ln for ln in text.splitlines() if ln.startswith("- config: ")]
        if arm == "default":
            assert line.startswith(f"- config: {report_cuts.CONFIG_DEFAULT} ("), line
        elif arm == "cloud":
            assert line.startswith("- config: ~/Library/CloudStorage/<path> ("), line
            assert "Juniper" not in text and "Contoso" not in line, line
        else:
            assert line.startswith("- config: ~/<path> ("), line
    if _R142_SHELL:
        configs = [[ln for ln in t.splitlines() if ln.startswith("- config: ")] for t in texts]
        assert configs[0] == configs[1], configs
        if arm == "cloud":
            assert shell[-1] == python[-1], (shell, python)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    "docs",
    [
        "/Volumes/Zephyr Drive/Wombat/docs",
        "/Users/Shared/Zephyr Quarry/docs",
        "/Volumes/Zephyr Drive//Wombat/docs",
        "/Volumes/Zephyr Drive/Wombat/./docs",
        # B13 round 44 C2637 (ruling 172): beside the home, an inbox that links into it, a parent that does
        "<beside>/Zephyr Quarry/docs inbox-link",
        "<beside>/Zephyr Quarry/docs parent-link",
    ],
)
def test_both_reports_cut_the_inbox_beside_a_docs_repo_outside_the_home_c2430(
    env: dict[str, str],  # noqa: F811
    locale: str,
    docs: str,
) -> None:
    """B13 round 43 C2430 (ruling 157; privacy): with a docs repo outside the home, both reports printed the
    inbox beside it whole (`- inbox: not added (no source on /Volumes/Zephyr Drive/Wombat/inbox)`), the docs
    path's own cut one line above it no use. Both register `<parent>/inbox` whole after the four own folders:
    the Configuration lines and a friction row quoting the inbox print no part of the parent, and agree. A
    docs repo written with a `//` or a `/./` is the path pathlib reads, which both print: the shell report
    printed it and its inbox in clear (B13 round 43, lane RB's review).

    B13 round 44 C2637 (ruling 172; privacy): the agentsync report skipped the inbox whose path resolved
    into the home, and the shell report judged the parent's resolved path, so an inbox that is a link into
    the home, or a parent that is one, printed the typed path whole. Both register the typed
    `<parent>/inbox` whenever it lies outside the home, wherever it resolves."""
    beside = Path(env["HOME"]).parent  # outside the home, a folder a test can make
    docs, _, link = docs.replace("<beside>", str(beside)).partition(" ")
    if link == "inbox-link":
        (Path(env["HOME"]) / "Wombat Inbox").mkdir()
        Path(docs).parent.mkdir(parents=True)
        (Path(docs).parent / "inbox").symlink_to(Path(env["HOME"]) / "Wombat Inbox")
    elif link == "parent-link":
        (Path(env["HOME"]) / "Wombat Quarry").mkdir()
        Path(docs).parent.symlink_to(Path(env["HOME"]) / "Wombat Quarry")
    shown = str(Path(docs))
    inbox = str(Path(docs).parent / "inbox")
    config = Path(env["HOME"]) / "agent-context" / "sources.toml"
    body = f"[agentsync]\ndocs_repo = {toml_string(docs)}\n" + default_config_text()
    texts: list[str] = []
    shell, python, sp, pp = _both_reports(
        env,
        [],
        [f"saw {inbox}", f"docs at {shown} and {inbox}"],
        locale,
        shell=_R142_SHELL,
        config=config,
        config_body=body,
        texts_out=texts,
    )
    for text in texts[1:] if not _R142_SHELL else texts:
        leaks = [ln for ln in text.splitlines() if "Zephyr" in ln or "Wombat" in ln]
        assert not leaks, leaks
    assert python == ["saw <folder-2>", "docs at <folder-1> and <folder-2>"], python
    if _R142_SHELL:
        assert shell == python and sp == pp, (shell, python, sp, pp)
        conf = [[ln for ln in t.splitlines() if ln.startswith(("- docs repo: ", "- inbox: "))] for t in texts]
        # the shell prints these lines only through an installed agentsync (config_folder_lines), which this
        # stub harness has none of: where it prints them they are the agentsync report's (lane RB, C2430).
        # B13 round 45 ruling 194, C2810: beside the config, the shell's own inbox line says it was not
        # checked and why, never a path; it names no part of the parent either (the leak scan above holds it)
        own = [ln for ln in conf[0] if ln.startswith("- inbox: not checked (")]
        assert own == ["- inbox: not checked (agentsync is not installed)"], conf
        rest = [ln for ln in conf[0] if ln not in own]
        assert conf[1] and rest in ([], conf[1]), conf


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_judge_a_tenants_whole_run_in_free_text_ruling_146(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 42 ruling 146 (C2285 with C2235 and C2271; privacy; data integrity): the free-text keep test
    read a tenant's first word, so `OneDrive-Personal Secret/Plans` printed whole in both reports, and a
    not-kept part swallowed a later path. Every example of the ruling and C2050's rows, under both locales:
    the agentsync report prints each pinned line, and the shell report the same line (lane RB's half)."""
    said = [f"saw ~/Library/CloudStorage/{row}" for row, _want in R146_FREE_ROWS] + [
        "saw ~/Library/CloudStorage/OneDrive-Northwind Traders/Q3 Plans, then",
        "saw ~/Library/CloudStorage/OneDrive-Fabrikam Holdings/Q3 Plans/Draft x/y today",
        'saw "~/Library/CloudStorage/OneDrive-Fabrikam Holdings" and x/y',
        "saw ~/Library/CloudStorage/OneDrive-Fab Co): x/y",
    ]
    want = [f"saw ~/Library/CloudStorage/{w}" for _row, w in R146_FREE_ROWS] + [
        "saw ~/Library/CloudStorage/<path> Plans, then",
        "saw ~/Library/CloudStorage/<path> today",
        'saw "~/Library/CloudStorage/<path>" and x/y',
        "saw ~/Library/CloudStorage/<path>): x/y",
    ]
    shell, python, shell_private, python_private = _both_reports(
        env, ["Juniper Notes"], said, locale, shell=_R146_SHELL
    )
    assert python == want, [(a, b) for a, b in zip(python, want, strict=True) if a != b]
    if _R146_SHELL:
        assert shell == python and shell_private == python_private, [
            (a, b) for a, b in zip(shell, python, strict=True) if a != b
        ]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_the_second_kept_divergence_is_pinned_in_both_reports_c2265(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 42 C2265 (ruling 148, amending ruling 138; makes worse True for converging): install.log's
    relative --source-local word naming no CloudStorage entry keeps its ./, ../ or ./../ head in the agentsync
    report, cut from its first unknown part (./<folder-N> for a configured folder), where the shell report
    cuts the whole word. No output changes: the divergence is declared in both legends and pinned here, and
    neither report prints a name piece."""
    words = ["./Zephyr\\ Quarry", "../Zephyr\\ Quarry", "./../Zephyr\\ Quarry", "./Northwind\\ Q1"]
    args = " ".join(f"--source-local {w}" for w in words)
    start = "2026-10-10T00:00:00Z run=20261010T000000Z-4242"
    log = install_log_path(env)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        f"{start} start install.sh compat=5 commit=0123456789ab kind=checkout source=- args={args}\n"
        f"{start} end rc=0 seconds=57\n",
        encoding="utf-8",
    )
    texts: list[str] = []
    _both_reports(env, ["Northwind Q1"], ["ran it"], locale, texts_out=texts)
    shell, python = ([ln for ln in t.splitlines() if "args=--source-local" in ln] for t in texts)
    assert shell and python, texts
    for text in texts:
        assert "Zephyr" not in text and "Quarry" not in text and "Northwind" not in text, text
    assert all(ln.count("--source-local <path>") == 4 for ln in shell), shell
    tail = " ".join(f"--source-local {w}" for w in ("./<path>", "../<path>", "./../<path>", "./<folder-1>"))
    assert any(tail in ln for ln in python), python
    legend = " ".join(setup_report._LEGEND["path"].split())
    assert "shown with its ./ or ../ head and cut from its first unknown part" in legend
    # the shell half, as the shell report prints its legend (B13 round 43 C2437)
    shell_legend = " ".join(texts[0].split("\n## Redaction\n", 1)[1].split("\n\n")[0].split())
    assert (
        "a relative --source-local word on install.log's run header naming no CloudStorage folder is cut"
        " whole here, where the agentsync report shows it with its ./ or ../ head and cuts it from its first"
        " unknown part"
    ) in shell_legend, shell_legend


def _debug_path(home: str, path: str, locale: str) -> str:
    """install.sh's own debug_path over ``path``, run under /bin/bash (3.2 on macOS) with ``home`` and
    ``locale``: the ``printf %q`` form a ``debug:`` line carries (B13 round 42 C2311)."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    [fn] = re.findall(r"(?ms)^debug_path\(\) \{.*?^\}\n", script)
    cp = subprocess.run(
        [BASH32, "-c", fn + 'debug_path "$1"', "_", path],
        capture_output=True,
        text=True,
        errors="replace",  # as the report reads install.out: bash 3.2 writes a lead byte raw (B13 round 44)
        check=True,
        timeout=30,
        env={"HOME": home, "LC_ALL": locale, "LANG": locale, "PATH": "/usr/bin:/bin"},
    )
    return cp.stdout


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_cut_each_dollar_quoted_debug_value_whole_c2311(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 42 C2311: the debug heads compare each path in the `%q` form debug_path writes, never
    decoded, so under the Mac's locale a cwd bash 3.2 writes as `$'...'` never matches its source's
    prefix. The masking is no accident: each `$'...'` value is cut whole by the dollar-quote cut, under
    the cwd or not. Over real /bin/bash `debug_path` output, both locales: a non-ASCII cwd with its source
    and config paths, an ASCII cwd with a non-ASCII child and a name holding a newline; each `$'...'`
    value prints `<path>`, both reports print the same line, and no name piece shows."""
    home = env["HOME"]
    rows = []
    for cwd, child in (
        (f"{home}/Z\u00fcrich Wombat", "Ledger"),
        (f"{home}/Projects/Wombat", "Caf\u00e9 Ledger"),
        (f"{home}/Alder\nWombat", "Ledger"),
    ):
        rows += [
            f"debug: cwd {_debug_path(home, cwd, locale)}",
            f"debug: source checkout {_debug_path(home, f'{cwd}/{child}', locale)}",
            f"debug: config {_debug_path(home, f'{cwd}/{child}/sources.toml', locale)}",
        ]
    quoted = [ln for ln in rows if "$'" in ln]
    assert quoted, rows  # bash 3.2 writes each of these names as $'...' in at least one value
    shell, python, shell_private, python_private = _both_reports(env, ["Juniper Notes"], rows, locale)
    for said, shown in zip(rows, python, strict=True):
        if "$'" in said:
            assert "$'" not in shown and shown.endswith("<path>"), (said, shown)
    leaks = [ln for ln in [*python, *shell] for w in ("Wombat", "Ledger", "Alder", "rich", "Caf") if w in ln]
    assert not leaks, leaks
    assert shell == python and shell_private == python_private, [
        (a, b) for a, b in zip(shell, python, strict=True) if a != b
    ]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_cut_a_config_path_to_its_tokens_end_c2290(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 42 C2290 (ruling 147; privacy; makes worse True for a boundary-only match): the whole-config
    cut stopped at the literal, so a longer token printed its suffix, `-PrivateBackup` or `.bak`. Both
    reports cut to the path token's end on every line, a sentence-final `.` and every terminator kept. B13
    round 43 ruling 158 (C2526 with C2435, C2496): the token ends at the same byte in both, a no-break or an
    ideographic space and a printf-%q `\\x` inside it, `\\"` ending it (a line-final `\\`, which a friction
    cell never holds, is the Python-only test's row)."""
    home = Path(env["HOME"])
    cfg = home / "Tailspin Merger" / "sources.toml"
    tilde = "~/Tailspin Merger/sources.toml"
    said = [
        f"{cfg}-PrivateBackup",
        f"{tilde}.bak",
        f"{cfg}x/Wombat",
        f"saw {cfg}, then",
        f"saw ({cfg}) and '{cfg}'",
        f"saw {cfg}.",
        f"ConfigError: {cfg}: bad",
        # B13 round 43 C2526 with C2435 (ruling 158): one ASCII stop set, so a no-break or an ideographic
        # space sits inside the token in both reports
        f"{cfg}\u00a0PrivateBackup",
        f"{cfg}\u3000PrivateBackup",
        f"{cfg} then",
        # C2496 (ruling 158): a printf-%q escape sits inside the token, `\"` and a line-final `\` end it
        f"{cfg}-Private\\ Backup",
        f"saw {cfg}-Private\\ Backup there",
        f'{cfg}\\",',
        # lane RB's shell half (C2496): two escapes, a `.` before `\"` and an escaped `.` inside
        f"saw {tilde}-Private\\ Backup\\ Copy, then",
        f'saw {cfg}.\\" x',
        f"{cfg}-Private\\.Backup.",
    ]
    want = [
        "~/<path>",
        "~/<path>",
        "~/<path>",
        "saw ~/<path>, then",
        "saw (~/<path>) and '~/<path>'",
        "saw ~/<path>.",
        "ConfigError: ~/<path>: bad",
        "~/<path>",
        "~/<path>",
        "~/<path> then",
        "~/<path>",
        "saw ~/<path> there",
        '~/<path>\\",',
        "saw ~/<path>, then",
        'saw ~/<path>.\\" x',
        "~/<path>.",
    ]
    shell, python, _sp, _pp = _both_reports(
        env, ["Juniper Notes"], said, locale, shell=_R146_SHELL, config=cfg
    )
    assert python == want, python
    if _R146_SHELL:
        assert shell == python, shell


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    "leaf",
    [
        "Z\u00e9phyr Quarry",  # NFC
        unicodedata.normalize("NFD", "Z\u00e9phyr Quarry"),
        "Z\u00e9phyr\u00a0Quarry Ledger",  # an NBSP, which bash writes raw and str.isprintable() refuses
        "\U0001f600 Quarry",
        "Zephyr\tQuarry",
        "Zephyr Quarry",
    ],
)
def test_the_python_report_cuts_each_debug_path_word_of_a_pandoc_path_c2716(leaf: str, locale: str) -> None:
    """B13 round 44 ruling 171 (C2716 with C2713, C2697 and C2641; privacy): under a UTF-8 locale /bin/bash
    3.2's `printf %q` writes a non-ASCII name raw with its blank backslashed (`Z\u00e9phyr\\ Quarry`), and
    `_printf_q_forms` returned nothing for any path past printable ASCII, so the agentsync report printed the
    folder where the shell report, registering debug_path in its own locale, cut it. Over install.sh's real
    debug_path under both locales, home as `~` and not, NFC, NFD, NBSP, emoji and TAB leaves: each word reads
    `~/<path>`, as the shell report prints it, and no piece of the leaf shows."""
    home = str(Path.home())
    pandoc = f"{home}/Tools/{leaf}/pandoc"
    red = setup_report.Redactor()
    red.add("home", home)
    setup_report._add_pandoc_cut(red, Path(pandoc), raw=pandoc)
    word = _debug_path(home, pandoc, locale)
    plain = _debug_path("/nonexistent-home", pandoc, locale)  # the absolute word: no home to fold into `~`
    assert word != plain or word.startswith("$'"), (word, plain)
    for said, want in (
        (f"debug: pandoc {word} | x", "debug: pandoc ~/<path> | x"),
        (f"ran {plain} --version", "ran ~/<path> --version"),
    ):
        got = red.redact(said)
        assert got == want, (said, got)


_C2432_SPELLINGS = {
    "table": "[convert]\npandoc_path = {v}\n",
    "quoted header": '["convert"]\n"pandoc_path" = {v}\n',
    "literal header": "[ 'convert' ]\n'pandoc_path' = {v}\n",
    "dotted key": "^convert.pandoc_path = {v}\n",
    "quoted dotted key": "^\"convert\" . 'pandoc_path' = {v}\n",
    "inline table": "^convert = {{ pandoc_path = {v} }}\n",
    "triple literal": "[convert]\npandoc_path = '''{s}'''\n",
    "triple basic": '[convert]\npandoc_path = """\n{s}"""\n',
    "multi-line value": '[convert]\npandoc_path = """\n{s}\n"""\n',
    "padded": "[convert]\npandoc_path = '  {s}\t '\n",
    # B13 round 45 C2913: a line-ending backslash with blanks after it joins the next line, as tomllib reads
    # it ({h} the path up to its last /, {t} the rest)
    "joined multi-line": '[convert]\npandoc_path = """{h}\\  \t\n   {t}"""\n',
}
"""B13 round 44 ruling 171 (C2698): each TOML spelling of `[convert] pandoc_path` a person can write, `{v}`
the value as a one-line string and `{s}` the bare path; a leading `^` puts it above the tables, where a
top-level key must stand."""


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    ("leaf", "written", "spelling"),
    [
        ("Zephyr Quarry", None, "table"),
        ("O'Zephyr Quarry", None, "table"),
        ("O'Zephyr $Quarry", None, "table"),
        (None, None, "table"),
        ("Zephyr Quarry", "~/Tools//{leaf}/pandoc", "table"),
        ("Zephyr Quarry", "{home}/Tools/./{leaf}/pandoc", "table"),
        # B13 round 44 ruling 171 (C2716 with C2713, C2697 and C2641): a non-ASCII leaf with an NBSP, and a
        # leaf whose blank is a TAB, as a literal TAB and as a basic string's escape
        ("Z\u00e9phyr\u00a0Quarry Ledger", None, "table"),
        ("Zephyr\tQuarry", None, "table"),
        ("Zephyr\tQuarry", "basic", "table"),
        # B13 round 45 ruling 189 (C2880 with C2914): blanks that mix a TAB and a space, literal and basic
        ("Zephyr\tQuarry Ledger", None, "table"),
        ("Zephyr\tQuarry Ledger", "basic", "table"),
        ("Zephyr Quarry\tLedger", None, "table"),
        ("Zephyr Quarry\tLedger", "basic", "table"),
        # C2698: every TOML spelling of the key and its value
        *(("Zephyr Quarry", None, k) for k in _C2432_SPELLINGS if k != "table"),
    ],
)
def test_both_reports_cut_a_configured_pandoc_path_in_every_spelling_c2432(
    env: dict[str, str],  # noqa: F811
    leaf: str | None,
    written: str | None,
    spelling: str,
    locale: str,
) -> None:
    """B13 round 43 C2432 (ruling 157; privacy): the shell report registered no `[convert] pandoc_path`, so
    the pandoc line, doctor's FAIL and its fix printed the folder in clear where the agentsync report printed
    `~/<path>`. A pandoc under the home whose folder holds a blank (a literal TOML string), then a `'` (a
    basic one: doctor writes `~/"..."`), then a `'` and a `$` (doctor's `'\\''` splice) reads `~/<path>` in
    both reports, one line, in each spelling a printer writes: as written, absolute, `~/` and doctor's shell
    word (doctor._quoted of the part under the sandbox home); the shell report (bash 3.2) also cuts the
    spellings ruling 157 pairs that no printer writes for it, the shell words doctor did not pick. One under
    /opt/homebrew names no person and stays, in both locales. One written with a `//` or a `/./` (`written`)
    is the path pathlib reads, which every printer writes: the shell report printed it in clear (B13 round
    43, lane RB's review), and cuts the spelling as written too.

    B13 round 44 ruling 171 (C2716 with C2713, C2697 and C2641, C2698; privacy): debug_path's `%q` word, as a
    UTF-8 run and a C run each write it, reads `~/<path>` in both reports, where the agentsync report printed
    a UTF-8 run's `Z\u00e9phyr\\ Quarry` whole; a non-ASCII leaf with an NBSP and a leaf whose blank is a
    TAB (a literal TAB and a basic string's `\\t`) are cut alike; and every TOML spelling of the key (a
    quoted table header or key, a dotted key, a one-line inline table, triple-quoted strings, a multi-line
    value, a padded one) is read by both, its echo as written cut too.

    B13 round 45 ruling 189 (C2880 with C2914): a leaf whose blanks mix a TAB and a space matched neither of
    the shell's guesses (every blank a space, every blank a TAB), so its debug_path word read `<path>` in the
    shell report and `~/<path>` in the agentsync report; the shell reads the true value now (C2913's joined
    multi-line spelling too)."""
    from agentsync.ops import doctor  # noqa: PLC0415 - as setup_report imports it

    home = Path(env["HOME"])
    pandoc = home / "Tools" / leaf / "pandoc" if leaf else Path("/opt/homebrew/bin/pandoc")
    spelled = written.format(home=home, leaf=leaf) if written and written != "basic" else str(pandoc)
    value = f"'{spelled}'" if leaf and "'" not in leaf and written != "basic" else json.dumps(spelled)
    table = local_source_table("s0", _cloud(env) / "OneDrive-Contoso" / "Juniper Notes")
    head, _, tail = spelled.rpartition("/")
    fragment = _C2432_SPELLINGS[spelling].format(v=value, s=spelled, h=f"{head}/", t=tail)
    if fragment.startswith("^"):
        body = f"{fragment[1:]}{default_config_text()}{table}"
    else:
        body = f"{default_config_text()}{table}\n{fragment}"
    assert tomllib.loads(body)["convert"]["pandoc_path"].strip() == spelled, body
    rest = str(pandoc.relative_to(home)) if leaf else ""
    picked = f"~/{doctor._quoted(rest)}" if leaf else doctor._shell_path(pandoc)  # _shell_path under env HOME
    said = [
        f"[ok  ] pandoc — pandoc 3.1.9 at {pandoc}",
        f"[FAIL] pandoc — {f'~/{rest}' if leaf else pandoc} did not run",
        f"check or replace the pandoc at [convert] pandoc_path ({picked})",
        "[ok  ] pandoc — pandoc 3.1.9 at /opt/homebrew/bin/pandoc",
    ]
    if leaf:
        # ruling 171: debug_path's word as either locale writes it, read by both reports
        said += [f"debug: pandoc {_debug_path(str(home), str(pandoc), loc)}" for loc in ("en_US.UTF-8", "C")]
    if written or spelling == "padded":
        said.append(f"pandoc_path ({spelled if spelling != 'padded' else f'  {spelled}'})")
    splice = "~/'" + rest.replace("'", "'\\''") + "'"
    words = [f"~/'{rest}'", f'~/"{rest}"', splice] if leaf else []
    shell_only = [f"pandoc_path ({w})" for w in dict.fromkeys(words) if w != picked]
    shell, python, shell_private, python_private = _both_reports(
        env,
        ["Juniper Notes"],
        [*said, *shell_only],
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
    )
    n = len(said)
    assert shell[:n] == python[:n] and shell_private == python_private, [
        (a, b) for a, b in zip(shell, python, strict=True) if a != b
    ]
    if leaf is None:
        assert python == said, python
        return
    pieces = ("Zephyr", "Quarry", "phyr", "Ledger")
    leaks = [ln for ln in [*shell, *python[:n]] if any(w in ln for w in pieces)]
    assert not leaks, leaks
    assert python[:4] == [
        "[ok  ] pandoc — pandoc 3.1.9 at ~/<path>",
        "[FAIL] pandoc — ~/<path> did not run",
        "check or replace the pandoc at [convert] pandoc_path (~/<path>)",
        said[3],
    ], python
    echo = "pandoc_path (  ~/<path>)" if spelling == "padded" else "pandoc_path (~/<path>)"  # blanks kept
    assert python[4:n] == ["debug: pandoc ~/<path>", "debug: pandoc ~/<path>", *([echo] * (n - 6))], python
    assert shell[n:] == ["pandoc_path (~/<path>)"] * len(shell_only), shell


_UNPARSED_SAID = [
    "I added ~/Projects/Wombat Ledger as a source",
    "Sync of zephyr-plans stopped; folder ~/Development/ZephyrQuarry/notes was skipped.",
]
_UNPARSED_LINE = (
    "Private only: sources.toml does not load and is not readable as TOML, so a source id or folder it names "
    "may show in clear."
)
_UNPARSED_BODIES = {
    "unterminated path beside id = =": (
        '[[source]]\nid = = \nkind = "local"\npath = "~/Projects/Wombat Ledger\n'
        '\n[[source]]\nid = "zephyr-plans"\nkind = "local"\npath = "~/Development/ZephyrQuarry/notes"\n'
    ),
    "half-closed header and a garbage line": (
        '[[source]\nid = "zephyr-plans"\nkind = "local"\npath = "~/Development/ZephyrQuarry/notes"\n'
        "this line is no TOML\n"
    ),
    "a garbage line after whole tables": (
        '[[source]]\nid = "zephyr-plans"\nkind = "local"\npath = "~/Development/ZephyrQuarry/notes"\n'
        "this line is no TOML\n"
    ),
}
"""B13 round 45 ruling 188 (C2840 with C2915): sources.toml bodies tomllib does not parse."""


def _tool_python(home: str) -> None:
    """The installed agentsync's interpreter where install.sh's tool_python finds it: this test's Python."""
    py = Path(home) / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.symlink_to(sys.executable)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("python", ["tomllib", "none"])
@pytest.mark.parametrize("body", list(_UNPARSED_BODIES))
def test_both_reports_reach_one_verdict_on_a_config_that_does_not_parse_c2840_c2915(
    env: dict[str, str],  # noqa: F811
    body: str,
    python: str,
    locale: str,
) -> None:
    """B13 round 45 ruling 188, C2840 with C2915 (privacy): over a sources.toml tomllib does not parse, the
    agentsync report said it was private only and gave no issue link, while the shell report stayed public,
    its `Sync of zephyr-plans` row in clear under a `[[source]` header, and ended on the public link. The
    shell reads the file with tomllib through the installed agentsync's interpreter (``tomllib``) and prints
    setup_report.py's sentence word for word; where no interpreter has tomllib (``none``) the structural
    check decides alike. Each report says it once and neither carries the link."""
    if python == "tomllib":
        _tool_python(env["HOME"])
    texts: list[str] = []
    _, _, shell_private, python_private = _both_reports(
        env,
        [],
        _UNPARSED_SAID,
        locale,
        config=Path(env["HOME"]) / "agent-context" / "sources.toml",
        config_body=_UNPARSED_BODIES[body],
        texts_out=texts,
    )
    assert shell_private and python_private, texts
    for text in texts:
        assert text.splitlines().count(_UNPARSED_LINE) == 1, text
        assert "issues/new" not in text, text


@pytest.mark.parametrize("python", ["tomllib", "none"])
def test_a_config_that_parses_keeps_the_shell_reports_issue_link_c2915(
    env: dict[str, str],  # noqa: F811
    python: str,
) -> None:
    """B13 round 45 ruling 188, C2915: a well-formed sources.toml keeps the shell report public whether or
    not an interpreter with tomllib is there: no interpreter is never by itself a private-only verdict, which
    would drop the link from every fallback report on a Mac with no developer tools."""
    if python == "tomllib":
        _tool_python(env["HOME"])
    texts: list[str] = []
    _, _, shell_private, python_private = _both_reports(
        env, ["Juniper Notes"], ["the sync of Juniper Notes went on"], "C", texts_out=texts
    )
    assert not shell_private and not python_private, texts
    assert "issues/new" in texts[0] and _UNPARSED_LINE not in texts[0], texts[0]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_register_no_inbox_beside_a_docs_repo_under_the_root_c2430(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 43 C2430 (ruling 157; privacy), the guard lane RA's
    test_both_reports_cut_the_inbox_beside_a_docs_repo_outside_the_home_c2430 has no row for: a docs repo
    right under `/` registers no `/inbox`, which would cut agentsync's own `~/agent-context/inbox`, while the
    docs path itself stays registered whole (lane RA's measured deviation: skipping it printed the folder in
    clear). Both reports, one line, in both locales."""
    home = Path(env["HOME"])
    body = '[agentsync]\ndocs_repo = "/Zephyr"\n' + default_config_text()
    said = ["saw /Zephyr", "docs at /Zephyr and /inbox", "inbox: /inbox", f"{home}/agent-context/inbox"]
    shell, python, sp, pp = _both_reports(
        env, [], said, locale, config=home / "agent-context" / "sources.toml", config_body=body
    )
    assert python == [
        "saw <folder-1>",
        "docs at <folder-1> and /inbox",
        "inbox: /inbox",
        "~/agent-context/inbox",
    ], python
    assert shell == python and sp == pp, (shell, python)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    "names",
    [
        ["Alder29\x1eBirch29", "Alder30\x7fBirch30"],
        ["Alder31\tBirch31", "Q3\x08Plans"],
        ["Alder32\x1eBirch32", "Alder33\nBirch33"],
        # B13 round 46 C3114 with C3036: a name's own backslash before t or three octal digits
        ["Wombat\\tQuokka\x7f", "Wombat\\177Quokka\x7f", "Q3\\101\\tPlans"],
    ],
)
def test_the_agentsync_report_cuts_a_folder_step_1_listed_but_not_configured_c2873(
    env: dict[str, str],  # noqa: F811
    names: list[str],
    locale: str,
) -> None:
    """B13 round 45 C2873 (ruling 190; privacy: C2060's Python twin): the agentsync report registered step
    1's escaped spelling of a listed name alone, never the bytes it stands for, so a listed folder gone from
    disk printed its TOML, safe_text and split words in clear (6 and 2 lines), hidden by the strict xfail
    that holds the shell half. An escaped row's name is decoded and registered under one number, its
    escaped spelling an alias: the agentsync report holds no fragment and one line per row, in both locales,
    an RS and an LF name among them, asserted whatever the shell half does. B13 round 47 C3288 (ruling 208;
    privacy): and an escaped row's other reading's blank-joined and slug forms (`Alder32 Birch32`,
    `alder32-birch32`), compared case-blind, cut under the row's one number, where the LF reading, LF being no
    separator, registered none of them."""
    said = [ln for n, name in enumerate(names) for ln in _hostile_lines(n, name, locale, home=env["HOME"])]
    for (
        name
    ) in names:  # C3288: each name's words as its RS reading splits them, joined by a blank and slugged
        words = setup_report._tokens(name.replace("\n", "\x1e"))
        if len(words) > 1 and all(w.isprintable() for w in words):
            said += [f"the folder {' '.join(words)} is listed", f"slug {'-'.join(w.lower() for w in words)}"]
    _shell, python, _sp, _pp = _both_reports(
        env, ["Juniper Notes"], said, locale, shell=False, listed=names, vanish=True
    )
    assert len(python) == len(said), (len(python), len(said))
    leaks = [ln for ln in python for f in _fragments(names) if f.casefold() in ln.casefold()]
    assert not leaks, (len(leaks), leaks[:4])


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_cut_a_folder_step_1_marked_paused_or_retired_c3332(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 47 C3332 (privacy): step 1 marks a folder sources.toml holds paused or retired `[paused] ` or
    `[retired] `, so both install.out readers strip those marks too; a reader that kept one never registered
    the row (the shell reads a row only under the CloudStorage root, the agentsync report a path only past
    its marks), and a folder no longer configured or on disk printed in clear. Neither report prints the
    folder's words."""
    cs = _cloud(env) / "OneDrive-Contoso"
    out = Path(env["HOME"]) / "agent-context" / "setup" / "install.out"
    out.parent.mkdir(parents=True, mode=0o700)
    out.write_text(
        "# run=r1 2026-11-25T00:00:00Z install.sh --list-folders\n"
        f"[paused] {cs}/Kestrel Plans\n[retired] {cs}/Wombat Ledger/Quokka Drafts\n",
        encoding="utf-8",
    )
    said = [
        f"the person named {cs}/Kestrel Plans",
        "the person named Kestrel Plans and Quokka Drafts",
        f"step 1 listed {cs}/Wombat Ledger/Quokka Drafts",
    ]
    shell, python, _sp, _pp = _both_reports(env, ["Juniper Notes"], said, locale)
    assert len(shell) == len(python) == len(said), (shell, python)
    leaks = [ln for ln in shell + python for w in ("Kestrel", "Wombat", "Quokka") if w in ln]
    assert not leaks, leaks


def test_a_listed_escaped_name_is_decoded_under_one_number_and_the_old_form_left_c2873() -> None:
    """B13 round 45 C2873 (ruling 190): only a row that opened with an escaped mark is decoded, `\\ooo` per
    byte, `\\t`, `\\r` and `\\n` as both LF and 0x1E; a twin-marked or plain row keeps its backslash as the
    name's own; the old `?` spelling (escq) is lossy and never decoded, a known limit. The decoded name and
    its escaped spelling read as one placeholder."""
    assert setup_report.listed_name_decodings("Alder29\\nBirch29") == [
        "Alder29\nBirch29",
        "Alder29\x1eBirch29",
    ]
    # B13 round 46 C3114 with C3036: the all-decoded reading first, then the ones that keep some escapes
    q3 = setup_report.listed_name_decodings("Q3\\010Plans\\tx\\ry")
    assert q3[0] == "Q3\x08Plans\tx\ry" and "Q3\x08Plans\\tx\ry" in q3 and "Q3\\010Plans\\tx\\ry" not in q3
    assert setup_report.listed_name_decodings("Caf\\303\\251")[0] == "Caf\u00e9"
    assert setup_report.listed_name_decodings("Alder30?Birch30") == []  # the old form: a known limit
    assert setup_report.listed_name_decodings("Plain Folder") == []
    home = "/Users/dana"
    root = f"{home}/Library/CloudStorage/OneDrive-Contoso"
    marks = setup_report._LIST_MARKS
    text = (
        "# run=r1 2026-11-23T00:00:00Z install.sh --list-folders\n"
        f"{marks[0]}{root}/Alder29\\nBirch29\n"
        f"{marks[1]}{root}/Wombat\\nLedger\n"
        f"{marks[3]}{root}/Alder30?Birch30\n"
        f"{root}/Back\\tslash\n"
    )
    _found, rows = setup_report.listed_folder_rows(text, home)
    assert [(name, escaped) for _p, _d, name, escaped in rows] == [
        ("Alder29\\nBirch29", True),
        ("Wombat\\nLedger", False),
        ("Alder30?Birch30", True),
        ("Back\\tslash", False),
    ]
    red = setup_report.Redactor()
    red.add("home", home)
    entries = [(p, d, n) for p, d, n, _e in rows]
    decoded = {i: setup_report.listed_name_decodings(n) for i, (_p, _d, n, e) in enumerate(rows) if e}
    setup_report._add_listed(red, entries, decoded=decoded)
    one = red._known("Alder29\nBirch29")
    assert one is not None and red._known("Alder29\x1eBirch29") == one == red._known("Alder29\\nBirch29")
    assert red._known("Wombat\\nLedger") not in (None, one) and red._known("Wombat\nLedger") is None


@pytest.mark.parametrize("plain_first", [True, False])
def test_a_plain_row_keeps_its_literal_name_beside_an_escaped_row_spelled_alike_c3082(
    plain_first: bool,
) -> None:
    """B13 round 46 C3082 (ruling 200; privacy): `decoded` was keyed by name, so a plain row whose folder is
    literally named `Project\\177Ledger` was decoded for another provider's escaped row printed the same way,
    and an escaped row listed first aliased that spelling exactly, so the plain folder's fuzzy forms were
    never registered. Each row is decoded by its own flag and the escaped spellings are aliased last: the
    literal and the decoded name take two placeholders, and every spelling of each is cut, in both orders."""
    home = "/Users/dana"
    cs = f"{home}/Library/CloudStorage"
    plain = f"{cs}/OneDrive-Contoso/Project\\177Ledger\n"
    escaped = f"{setup_report._LIST_MARKS[0]}{cs}/OneDrive-Fabrikam/Project\\177Ledger\n"
    text = "# run=r1 2026-11-24T00:00:00Z install.sh --list-folders\n" + (
        plain + escaped if plain_first else escaped + plain
    )
    _found, rows = setup_report.listed_folder_rows(text, home)
    red = setup_report.Redactor()
    red.add("home", home)
    setup_report._add_listed(
        red,
        [(p, d, n) for p, d, n, _e in rows],
        decoded={i: setup_report.listed_name_decodings(n) for i, (_p, _d, n, e) in enumerate(rows) if e},
    )
    literal, decoded = red._known("Project\\177Ledger"), red._known("Project\x7fLedger")
    assert literal is not None and decoded is not None and literal != decoded, (literal, decoded)
    for spelling in ("project-177-ledger", "Project 177 Ledger", "Project177Ledger", "Project\x7fLedger"):
        assert red.redact(f"saw {spelling} there") != f"saw {spelling} there", spelling
    numbers = [red._known(n) for n in ("Project\\177Ledger", "Project\x7fLedger")]
    assert numbers == (["<folder-1>", "<folder-2>"] if plain_first else ["<folder-2>", "<folder-1>"]), numbers


def test_an_escaped_name_is_read_every_way_it_allows_within_the_cap_c3114_c3036() -> None:
    """B13 round 46 C3114 with C3036 (ruling 200; privacy; makes worse True for an unbounded reading):
    LIST_ESC_AWK writes a name's own backslash as itself, so `\\101` or `\\t` in a row may be the name's own
    characters. The readings that keep some escapes as written follow the all-decoded one, both readings of
    `\\n`, each once and never the written spelling; under one number in `_add_listed`; past the cap of 6
    only the single-kept readings are added, and a long name answers at once."""
    assert setup_report.listed_name_decodings("Q3\\101\\tPlans") == [
        "Q3A\tPlans",
        "Q3\\101\tPlans",
        "Q3A\\tPlans",
    ]
    both = setup_report.listed_name_decodings("Wombat\\nQuokka\\177")
    assert both[:2] == ["Wombat\nQuokka\x7f", "Wombat\x1eQuokka\x7f"] and "Wombat\\nQuokka\x7f" in both
    red = setup_report.Redactor()
    setup_report._add_listed(
        red,
        [("OneDrive-Contoso", 2, "Q3\\101\\tPlans")],
        decoded={0: setup_report.listed_name_decodings("Q3\\101\\tPlans")},
    )
    assert (
        red._known("Q3\\101\tPlans") == red._known("Q3\\101\\tPlans") == red._known("Q3A\tPlans") is not None
    )
    long = "Kestrel\\t" * 85
    t0 = time.monotonic()
    readings = setup_report.listed_name_decodings(long)
    assert time.monotonic() - t0 < 1.0 and len(readings) == 86, len(readings)
    assert readings[0] == "Kestrel\t" * 85 and long not in readings


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    "names",
    [
        ["Alder29\x1eBirch29", "Alder30\x7fBirch30"],
        ["Alder31\tBirch31", "Q3\x08Plans"],
        ["Wombat\\tQuokka\x7f", "Wombat\\177Quokka\x7f", "Q3\\101\\tPlans"],  # B13 round 46 C3114
    ],
)
@pytest.mark.xfail(
    _R142_SHELL,
    strict=True,
    reason="C2060 unbuilt: install.sh's decode of a listed part awaits operator decision c0777e12a523",
)
def test_both_reports_cut_a_folder_step_1_listed_but_not_configured_ruling_142(
    env: dict[str, str],  # noqa: F811
    names: list[str],
    locale: str,
) -> None:
    """B13 round 41 ruling 142's listed-only arm (C2060's case; privacy): `_both_reports` configured every
    name it tested, so a folder step 1 listed but the config does not name, spelled with escaped bytes, was
    never a case. Each name is listed by `--list-folders -a`, one other plain folder configured, a 0x1E name
    among them (step 1 spells it `\\n`): no fragment of a name, each raw split word and safe_text's apart, in
    either report, both reports one line and one verdict. Since B13 round 44 C2669 both reports list
    CloudStorage, which gives a folder on disk its own bytes, so each name is gone from disk once listed
    (`vanish`): install.out's list alone names it, the case C2060's decode is for. Since B13 round 45 C2873
    (ruling 190) the agentsync report's half is asserted unconditionally
    (test_the_agentsync_report_cuts_a_folder_step_1_listed_but_not_configured_c2873), so this strict xfail
    holds the shell half alone: its leaks and the two reports' parity."""
    said = [ln for n, name in enumerate(names) for ln in _hostile_lines(n, name, locale, home=env["HOME"])]
    shell, python, shell_private, python_private = _both_reports(
        env, ["Juniper Notes"], said, locale, shell=_R142_SHELL, listed=names, vanish=True
    )
    assert len(shell) == len(said), (len(shell), len(said))
    leaks = [ln for ln in shell for f in _fragments(names) if f in ln]
    assert not leaks, (len(leaks), leaks[:4])
    if _R142_SHELL:
        assert shell == python and shell_private == python_private, [
            (a, b) for a, b in zip(shell, python, strict=True) if a != b
        ][:8]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    "provider", ["GoogleDrive-dana@example.com", "OneDrive-SharedLibraries-dana@example.com"]
)
def test_both_reports_read_a_googledrive_address_tenant_and_a_short_id_alike_c2071(
    env: dict[str, str],  # noqa: F811
    locale: str,
    provider: str,
) -> None:
    """B13 round 41 C2071 (ruling 142; makes worse True for registering every short id as a plain word): a
    `GoogleDrive-<address>` tenant read `GoogleDrive-<org-N>` in the shell report and `<email-N>` in the
    agentsync report, and a 2-letter source id stayed in clear in one and was cut in the other. Configured,
    unconfigured and gone folders under `My Drive`, the not-a-folder line, each id's listable warning and free
    text: no address, no folder name, and, with the shell half, one line and one verdict. B13 round 42 C2240
    (ruling 149): the source id `it` cuts the pronoun as well, as <source-N>, which the last line pins; only a
    folder `IT` with no such id keeps it (test_install_oneshot.py's case). Keeping the pronoun here would
    print the id in clear. B13 round 42 C2288 with C2241: a shared library named with an address reads
    `OneDrive-SharedLibraries-<org-N>` in both. B13 round 43 ruling 159, C2471: the shell numbers the library
    `<library-N>` too, so for both providers the two reports print each line alike."""
    head = provider.removesuffix("dana@example.com")
    cs = f"~/Library/CloudStorage/{provider}"
    said = [
        f"start install.sh args=--source-local {cs}/My\\ Drive/Juniper\\ Notes",
        f"start install.sh args=--source-local {cs}/My\\ Drive/Zephyr",
        f"usage error: --source-local is not an existing folder: {cs}/My Drive/Zephyr (see --help)",
        *(f"[warn] source.{sid}.listable — {sid} is empty" for sid in ("qx", "hr", "it")),
        "the HR folder and the IT folder, and it is ok",
    ]
    shell, python, shell_private, python_private = _both_reports(
        env,
        ["My Drive/Juniper Notes", "My Drive/HR", "My Drive/IT"],
        said,
        locale,
        shell=_R142_SHELL,
        provider=provider,
        ids=["qx", "hr", "it"],
    )
    assert len(python) == len(said), (len(python), len(said))
    assert not [
        ln for ln in python for w in ("dana", "example", "Juniper", "Zephyr", "<email-") if w in ln
    ], python
    assert f"~/Library/CloudStorage/{head}<org-1>/" in python[0], python[0]
    assert f"{head}<org-1>/" in python[1] and python[1].endswith("<path>"), python[1]
    assert not [ln for ln in python[3:] for w in (".qx.", ".hr.", " HR ", " IT ") if w in ln], python
    assert python_private, "a 2-letter id or folder that is a word makes the report private only where cut"
    if head.endswith("SharedLibraries-"):  # My Drive is the library, so HR and IT are folders 2 and 3
        assert python[-1] == "the <folder-2> folder and the <folder-3> folder, and <source-2> is ok", python[
            -1
        ]
    else:
        assert python[-1] == "the <folder-3> folder and the <folder-4> folder, and <source-2> is ok", python[
            -1
        ]
    if _R142_SHELL:
        assert any("<org-1>" in a for a in shell), shell
        assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("arm", ["configured", "listed", "vanished"])
def test_both_reports_number_a_sharepoint_library_and_a_same_name_folder_alike_c2471(
    env: dict[str, str],  # noqa: F811
    locale: str,
    arm: str,
) -> None:
    """B13 round 43 ruling 159, C2471: the first folder under a OneDrive-SharedLibraries-<org> folder is a
    SharePoint library, `<library-N>` in the agentsync report, and the shell numbered it `<folder-N>` from the
    folders' counter, so the two reports read it, and every later folder, apart. The shell now numbers it from
    a counter of its own where the agentsync report does: a configured library, one step 1's list gives at
    depth 2, and a vanished source's, whose add-back folder only the sync's refusal names to the shell. A
    library and a folder of one name read the placeholder the first registration gave, in both reports:
    `Cedar Hub` and `Alder Notes` are each a library under one provider and a folder under the other."""
    home = Path(env["HOME"])
    plain, shared = (_cloud(env) / p for p in ("OneDrive-Contoso", "OneDrive-SharedLibraries-Contoso"))
    run = {**env, "LC_ALL": locale, "LANG": locale, "AGENTSYNC_OCR": "0", "AGENTSYNC_TM_EXCLUDE": "0"}
    first = [("s0", plain / "Alder Notes")]
    if arm == "configured":  # the library first, then a folder of its name; a folder first, then a library
        first += [("s1", shared / "Cedar Hub" / "Birch Plans"), ("s2", shared / "Alder Notes" / "Dune Files")]
    last = [("s3", plain / "Cedar Hub")]
    gone = shared / "Elm Yard" / "Fern Docs"
    for _sid, path in [*first, *last, ("fern-docs", gone)]:
        path.mkdir(parents=True, exist_ok=True)
        (path / "notes.txt").write_text("notes\n", encoding="utf-8")
    cfg = home / "agent-context" / "sources.toml"
    body = default_config_text() + "".join(local_source_table(sid, path) for sid, path in [*first, *last])
    if arm == "vanished":  # synced, then its [[source]] block removed: the manifest keeps its folder
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(body + local_source_table("fern-docs", gone), encoding="utf-8")
        sync = [sys.executable, "-m", "agentsync", "sync", "--config", str(cfg)]
        done = subprocess.run(sync, capture_output=True, text=True, check=False, timeout=300, env=run)
        assert done.returncode == 0, done.stdout + done.stderr
        cfg.write_text(body, encoding="utf-8")
        refused = subprocess.run(sync, capture_output=True, text=True, check=False, timeout=300, env=run)
        assert refused.returncode == 78, refused.stdout + refused.stderr
        [line] = [ln for ln in refused.stderr.splitlines() if ln.startswith("configuration error: ")]
        assert "'fern-docs'" in line and "Fern Docs" in line, line
        out = home / "agent-context" / "setup" / "install.out"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            f.write(f"{line}\n")
    rels = ["Cedar Hub/Birch Plans", "Alder Notes/Dune Files"] if arm == "configured" else []
    lead = "picked ~/Library/CloudStorage/OneDrive-SharedLibraries-"
    said = [f"{lead}Contoso/{r}" for r in [*rels, "Elm Yard/Fern Docs"]]
    said += ["the Cedar Hub library, the Alder Notes folder and Elm Yard"]
    shell, python, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        provider="OneDrive-SharedLibraries-Contoso",
        listed=None if arm == "vanished" else ["Elm Yard/Fern Docs"],
        config=cfg,
        config_body=body,
    )
    assert len(python) == len(said), python
    assert not [ln for ln in python for w in ("Cedar", "Alder", "Elm", "Fern", "Birch", "Dune") if w in ln], (
        python
    )
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))
    lead += "<org-1>/"
    words = "the {} library, the <folder-1> folder and {}"
    if arm == "configured":
        assert python[0] == f"{lead}<library-1>/<folder-2>", python[0]
        assert python[1] == f"{lead}<folder-1>/<folder-3>", python[1]
        assert python[2] == f"{lead}<library-2>/<folder-4>", python[2]
        assert python[3] == words.format("<library-1>", "<library-2>"), python[3]
    else:  # Cedar Hub configured as a folder before the library is seen
        assert python[0] == f"{lead}<library-1>/<folder-3>", python[0]
        assert python[1] == words.format("<folder-2>", "<library-1>"), python[1]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_cut_a_two_letter_sharepoint_library_in_any_case_c2471(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 43 C2471: the agentsync report applies the two-letter-word rule (C2071) to a folder and a
    source id, never a library, which it registers fuzzily, so in any case: a library `IT` is cut where the
    pronoun `it` stands too. The shell sent a library through short_pair, as a folder: it kept the pronoun."""
    lead = "picked ~/Library/CloudStorage/OneDrive-SharedLibraries-Contoso/"
    said = [f"{lead}IT/Birch Plans", "it is the IT library, and Qx and qx"]
    shell, python, shell_private, python_private = _both_reports(
        env, ["IT/Birch Plans", "Qx/Dune Files"], said, locale, provider="OneDrive-SharedLibraries-Contoso"
    )
    assert python == [
        f"{lead.replace('Contoso', '<org-1>')}<library-1>/<folder-1>",
        "<library-1> is the <library-1> library, and <library-2> and <library-2>",
    ], python
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(("library", "private"), [("Status", True), ("Documents", False), ("IT", False)])
def test_a_sharepoint_library_named_like_a_report_word_makes_both_reports_private_c2471(
    env: dict[str, str],  # noqa: F811
    locale: str,
    library: str,
    private: bool,
) -> None:
    """B13 round 43 C2471: a library named like a word the report prints (`Status`) reads back where its
    `<library-N>` stands, so the agentsync report is private only, as for a folder; a generic one
    (`Documents`) is not, nor a two-letter one (`IT`), which the two-letter-word rule never covers for a
    library. The shell judged only `<folder-N>` and `<org-N>` placeholders, so its library left it public."""
    lead = "picked ~/Library/CloudStorage/OneDrive-SharedLibraries-Contoso/"
    said = [f"{lead}{library}/Birch Plans", f"the {library} library"]
    shell, python, shell_private, python_private = _both_reports(
        env, [f"{library}/Birch Plans"], said, locale, provider="OneDrive-SharedLibraries-Contoso"
    )
    assert python_private is private, python
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_number_a_cloudstorage_folder_on_disk_alike_c2669(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 44 ruling 172, C2669: a folder on disk under ~/Library/CloudStorage, neither configured nor
    listed by step 1, that a friction line quotes: the agentsync report lists CloudStorage at depth 2-3 and
    numbers it, the shell report printed it in clear and stayed public. The shell lists it as Python does, in
    Python's order (provider, each depth-2 name then its depth-3 names, each level by its bytes), so a
    sibling `Kestrel Quarry B` (a blank sorts before `/`) and a nested name read the same numbers in both."""
    cs = _cloud(env) / "OneDrive-Contoso"
    for d in ("Kestrel Quarry/Zephyr Plans", "Kestrel Quarry B/Alder Notes", "Kestrel Quarry/Birch Files"):
        (cs / d).mkdir(parents=True)
    said = [
        "picked ~/Library/CloudStorage/OneDrive-Contoso/Kestrel Quarry/Zephyr Plans",
        "the person named the Kestrel Quarry folder, then Kestrel Quarry B and Alder Notes and Birch Files",
    ]
    shell, python, shell_private, python_private = _both_reports(env, ["Q3 Launch"], said, locale)
    assert not any(w in ln for ln in python for w in ("Kestrel", "Zephyr", "Alder", "Birch")), python
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


@pytest.mark.parametrize("listed", [False, True], ids=["no list", "a list run"])
def test_an_unfinished_cloudstorage_listing_makes_the_shell_report_private_c2669(
    env: dict[str, str],  # noqa: F811
    tmp_path: Path,
    listed: bool,
) -> None:
    """B13 round 44 ruling 172, C2669: a CloudStorage listing that runs out of its 3 s, with no folder list of
    step 1 in install.out, makes the shell report private only, as the agentsync report's listing_unread
    does; with a list run in install.out, its folders stand in the listing's place and the report stays
    public. A find that never returns stands in for a provider macOS keeps waiting."""
    cs = _cloud(env) / "OneDrive-Contoso"
    (cs / "Kestrel Quarry" / "Zephyr Plans").mkdir(parents=True)
    if listed:
        assert install_sh(env, "--list-folders", "-a").returncode in (0, 3)
    slow = tmp_path / "slowbin"
    slow.mkdir()
    (slow / "find").write_text(
        '#!/bin/bash\ncase " $* " in *CloudStorage*" -print0 "*) sleep 30 ;; esac\nexec /usr/bin/find "$@"\n',
        encoding="utf-8",
    )
    (slow / "find").chmod(0o755)
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(
        "Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\n"
        "2026-10-10T00:00:01Z | step 1 | deviation | the person named a folder | -\n",
        encoding="utf-8",
    )
    cp = install_sh({**env, "PATH": f"{slow}:{env['PATH']}"}, "--report-only")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    text = report_path(env).read_text(encoding="utf-8")
    said = "Private only: the listing of ~/Library/CloudStorage for redaction did not finish"
    assert (said in text) is not listed, [ln for ln in text.splitlines() if "Private" in ln]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("arm", ["id it", "folder IT/it", "folder IT/IT"])
def test_a_two_letter_word_on_a_library_makes_both_reports_private_c2670(
    env: dict[str, str],  # noqa: F811
    locale: str,
    arm: str,
) -> None:
    """B13 round 44 ruling 172, C2670: a source id `it`, or a folder `it` or `IT`, under library IT shares
    its `<library-1>`, and the agentsync report marks that two-letter word short, so it is private only where
    the placeholder shows; the shell exempted every `<library-N>` from the two-letter rule and stayed public,
    and gave id `it` a `<source-1>` of its own, so a second id read one apart. The shell now registers a
    library's own folded value whatever its length (hidden_by's library flag) and judges a `<library-N>` a
    two-letter word that is no library landed on (lib_short); the library alone stays public."""
    lead = "picked ~/Library/CloudStorage/OneDrive-SharedLibraries-Contoso/"
    if arm == "id it":
        folders, ids = ["IT/Birch Plans", "IT/Dune Files"], ["it", "qx7"]
        said = [f"{lead}IT/Birch Plans", "it was the IT team, and source it is ok, and source qx7 too"]
    else:
        leaf = arm.rpartition("/")[2]
        folders, ids = [f"IT/{leaf}"], None
        said = [f"{lead}IT/{leaf}", "it was the IT team"]
    shell, python, shell_private, python_private = _both_reports(
        env, folders, said, locale, provider="OneDrive-SharedLibraries-Contoso", ids=ids
    )
    assert python_private is True, python
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_number_a_library_only_install_log_names_alike_c2471(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 43 C2471: a library on disk under OneDrive-SharedLibraries-<org>, never configured or listed,
    that only install.log's --source-local words name is `<library-N>` in the agentsync report, whose
    CloudStorage listing registers it at depth 2, and the shell, which numbered a late path's parts as folders
    only, read it `<folder-N>`."""
    home = Path(env["HOME"])
    (_cloud(env) / "OneDrive-SharedLibraries-Contoso" / "Elm Yard" / "Fern Docs").mkdir(parents=True)
    log = home / "agent-context" / "setup" / "install.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "run=20261010T010000Z-1 start install.sh compat=10 commit=- kind=- source=- args=--source-local "
        "~/Library/CloudStorage/OneDrive-SharedLibraries-Contoso/Elm\\ Yard/Fern\\ Docs\n",
        encoding="utf-8",
    )
    lead = "picked ~/Library/CloudStorage/OneDrive-SharedLibraries-Contoso/"
    said = [f"{lead}Cedar Hub/Birch Plans", f"{lead}Elm Yard/Fern Docs", "the Elm Yard library and Fern Docs"]
    shell, python, shell_private, python_private = _both_reports(
        env, ["Cedar Hub/Birch Plans"], said, locale, provider="OneDrive-SharedLibraries-Contoso"
    )
    lead = lead.replace("Contoso", "<org-1>")
    assert python == [
        f"{lead}<library-1>/<folder-1>",
        f"{lead}<library-2>/<folder-2>",
        "the <library-2> library and <folder-2>",
    ], python
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


def _hold(env: dict[str, str], *scopes: str) -> None:  # noqa: F811
    """``scopes`` held, as status's HOLD: line reads them from the heartbeat."""
    state = Path(env["HOME"]) / "Library" / "Application Support" / "agentsync"
    state.mkdir(parents=True, exist_ok=True)
    (state / "heartbeat.json").write_text(json.dumps({"holds": list(scopes)}), encoding="utf-8")


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    ("sid", "private"),
    [
        ("it", True),
        ("hr", False),
        ("qx", False),
        ("q-3", False),
        ("a-b", False),
        ("q-x", False),
        ("i-t", True),
    ],
)
def test_both_reports_cut_a_two_character_id_in_any_case_one_id_each_c2287_c2312(
    env: dict[str, str],  # noqa: F811
    locale: str,
    sid: str,
    private: bool,
) -> None:
    """B13 round 42 C2287 with C2232 and C2312 (ruling 149; privacy): a 2-character source id is a whole word
    in any case in both reports, quoted as written, in capitals and capitalized, in free text and on doctor's
    vanished-sources line; one id a report each, so the private-only verdict is the id's own: a word id
    (`it`) makes the report private only where its placeholder shows, `hr` and `qx` leave it public, the id
    still cut. The shell half is lane RB's (_R146_SHELL). B13 round 43 ruling 159, C2434: an id 2 characters
    long without its `-` (`q-3`, `a-b`, `q-x`) is one too, as the agentsync report's _norm reads it, where
    the shell measured it as written and printed `Q-3` in clear; `i-t` makes the report private only."""
    said = [
        f"the {sid} source, {sid.upper()} and {sid.capitalize()} too",
        f"[FAIL] config.vanished_sources — {sid}, reports-2 have files on record",
    ]
    shell, python, shell_private, python_private = _both_reports(
        env, ["My Drive/Juniper Notes"], said, locale, shell=_R146_SHELL, ids=[sid]
    )
    assert python[0] == "the <source-1> source, <source-1> and <source-1> too", python
    assert python_private is private, (python_private, python)
    if _R146_SHELL:
        assert shell == python and shell_private == python_private, (shell, python)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("entry", ["hold-scope", "vanished"])
@pytest.mark.parametrize("sid", ["qx", "q-3", "a-b", "q-x", "i-t"])
def test_both_reports_cut_a_two_character_hold_scope_or_vanished_id_in_any_case_c2287(
    env: dict[str, str],  # noqa: F811
    locale: str,
    entry: str,
    sid: str,
) -> None:
    """B13 round 42 ruling 149, C2287 with C2232 (privacy): the shell's vanished-source and hold-scope loops
    registered an id through add_pair alone, whose floor is 3 characters, so a 2-character vanished id or hold
    scope printed in clear in the shell report. Each loop now calls short_pair after add_pair with the same
    <source-N>: a whole word in any case. A hold scope is read by both reports (one line each); a vanished id
    quoted on install.out's configuration error is read by the shell from that line, the agentsync report
    from the manifest, so its rows are the shell's alone. B13 round 43 ruling 159, C2434: one 2 characters
    long without its `-` (`q-3`, `a-b`, `q-x`, `i-t`) too, as _norm reads it."""
    home = Path(env["HOME"])
    if entry == "hold-scope":
        _hold(env, sid)
    else:
        out = home / "agent-context" / "setup" / "install.out"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            f"configuration error: source '{sid}' (3 files) is mirrored but no longer in sources.toml\n",
            encoding="utf-8",
        )
    said = [f"waiting on {sid} now", f"waiting on {sid.upper()} now", f"waiting on {sid.capitalize()} now"]
    shell, python, shell_private, python_private = _both_reports(env, ["Juniper Notes"], said, locale)
    assert len(shell) == len(said), shell
    assert not [ln for ln in shell if sid in ln.lower()], shell
    assert len(set(shell)) == 1 and "<source-" in shell[0], shell
    if entry == "hold-scope":
        assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    ("folders", "ids"),
    [
        (["Alpha Plan", "Beta Plan", "Gamma Plan"], ["contoso", "quillon", "brindle"]),
        (["Alder\x01Birch", "Beta Plan"], ["alderbirch", "quillon"]),
        (["Alpha Plan", "Beta Plan", "Gamma Plan"], ["x", "quillon", "brindle"]),
        (["Spruce Ledger", "Maple Ledger", "Gamma Plan"], ["maple-ledger", "quillon", "brindle"]),
    ],
    ids=["org", "escaped", "one-character", "another-folders-slug"],
)
def test_both_reports_number_an_id_another_value_hides_alike_c2233(
    env: dict[str, str],  # noqa: F811
    locale: str,
    folders: list[str],
    ids: list[str],
) -> None:
    """B13 round 42 ruling 149, C2233: the shell's id loop counted an id whose spelling another value already
    holds (an organisation, a folder whose name holds a TOML escape, another configured folder's slug) and a
    1-character id, so every later id read one <source-N> apart from the agentsync report's. The loop now
    finds the placeholder that hides an id first, skips an id under 2 characters and numbers only on a miss:
    one line each in both reports."""
    said = [f"waiting on {sid} now" for sid in ids]
    shell, python, shell_private, python_private = _both_reports(env, folders, said, locale, ids=ids)
    assert len(python) == len(said), python
    assert not [ln for ln in python for sid in ids if len(sid) > 1 and f" {sid} " in ln], python
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize(
    "holds",
    [
        ["b", "quillon"],
        ["contoso", "quillon", "wombat-matter"],
        ["juniper-notes", "quillon", "wombat-matter"],
        ["a", "quillon", "wombat-matter"],
    ],
    ids=["one-character-first", "org", "another-folders-slug", "one-character"],
)
def test_both_reports_number_a_hold_scope_another_value_hides_alike_c2433_c2501(
    env: dict[str, str],  # noqa: F811
    locale: str,
    holds: list[str],
) -> None:
    """B13 round 43 ruling 159, C2501 with C2433: the shell's hold-scope loop numbered every scope, a
    1-character one and one an organisation or a folder's slug already holds, so each later scope read one
    <source-N> apart from the agentsync report's (and a folder's slug read <source-N>, not its <folder-N>).
    It now skips a scope under 2 characters and keeps the placeholder id_known finds, as the configured ids
    do: one line each in both reports."""
    _hold(env, *holds)
    said = [f"waiting on {scope} now" for scope in holds]
    shell, python, shell_private, python_private = _both_reports(env, ["Juniper Notes"], said, locale)
    assert len(python) == len(said), python
    assert not [ln for ln in python for scope in holds if len(scope) > 1 and f" {scope} " in ln], python
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_number_a_vanished_id_and_its_folder_before_a_hold_scope_c2433_c2501(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 43 ruling 159, C2501 with C2433: three sources synced, then their [[source]] blocks removed,
    so the manifest holds their ids (the agentsync report reads it) and the sync's refusal quotes them, with
    the add-back folder of the one whose id is its folder's slug, on install.out (the shell report reads it).
    The shell numbered the 1-character id `b`, so every later id read one <source-N> apart, and registered the
    add-back folder after the hold scopes, so a scope spelling it (`betaplan`) read <source-N> where the
    agentsync report reads its <folder-N>. The add-back folder lies below step 1's list (depth 4), which both
    reports read, so only the add-back registers it."""
    home = Path(env["HOME"])
    lib = _cloud(env) / "OneDrive-Contoso"
    run = {**env, "LC_ALL": locale, "LANG": locale, "AGENTSYNC_OCR": "0", "AGENTSYNC_TM_EXCLUDE": "0"}
    tables = []
    for sid, name in (
        ("s0", "Juniper Notes"),
        ("b", "Alpha Plan"),
        ("beta-plan", "Deep/Er/Beta Plan"),
        ("quillon", "Gamma"),
    ):
        (lib / name).mkdir(parents=True)
        (lib / name / "notes.txt").write_text("notes\n", encoding="utf-8")
        tables.append(local_source_table(sid, lib / name))
    cfg = _write_config(env, *tables)

    def sync() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "agentsync", "sync", "--config", str(cfg)],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
            env=run,
            stdin=subprocess.DEVNULL,
        )

    first = sync()
    assert first.returncode == 0, first.stdout + first.stderr
    _write_config(env, tables[0])
    refused = sync()
    assert refused.returncode == 78, refused.stdout + refused.stderr
    [line] = [ln for ln in refused.stderr.splitlines() if ln.startswith("configuration error: ")]
    assert "'b'" in line and "'quillon'" in line and "Beta Plan" in line, line
    assert install_sh(run, "--list-folders", "-a").returncode in (0, 3)
    out = home / "agent-context" / "setup" / "install.out"
    with out.open("a", encoding="utf-8") as f:
        f.write(f"{line}\n")
    # the heartbeat's list, read only while no hold record is there (the sync wrote one)
    (home / "Library" / "Application Support" / "agentsync" / "hold-record.json").unlink(missing_ok=True)
    _hold(env, "betaplan", "wombat-matter")
    said = [
        "waiting on quillon now",
        "waiting on beta-plan now",
        "waiting on betaplan now",
        "Beta Plan moved",
        "waiting on wombat-matter now",
    ]
    shell, python, shell_private, python_private = _both_reports(env, ["Juniper Notes"], said, locale)
    assert len(python) == len(said), python
    assert not [ln for ln in python for w in ("quillon", "beta", "Beta", "wombat") if w in ln], python
    assert python[2] == python[3].replace(" moved", "").replace("<", "waiting on <", 1) + " now", python
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_number_a_folder_install_log_alone_names_after_the_rest_c2202(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 42 ruling 149, C2202 (makes worse True for numbering a product word in both): the shell
    numbered install.log's --source-local folders (a refused run's missing one, a $'..' one) before step 1's
    listed rows and the folders outside CloudStorage, which the agentsync report numbers and install.log's
    words never are, so a configured folder outside CloudStorage read another <folder-N> in each report. Those
    names now come after every value the agentsync report numbers: one line each, and `GitHub Copilot CLI`
    stays readable with a listed `Copilot` folder."""
    home = Path(env["HOME"])
    lib = _cloud(env) / "OneDrive-Contoso"
    (home / "Kestrel Media").mkdir(parents=True)
    start = "2026-10-10T00:00:00Z run=20261010T000000Z-4242"
    gone = "~/Library/CloudStorage/OneDrive-Contoso/Kestrel\\ Gone"
    log = install_log_path(env)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        f"{start} start install.sh compat=5 commit=0123456789ab kind=checkout source=- args="
        f"--source-local {gone}"
        " --source-local $'~/Library/CloudStorage/OneDrive-Contoso/Kestrel\\tTab'\n"
        f"{start} end rc=2 seconds=1\n",
        encoding="utf-8",
    )
    body = (
        default_config_text()
        + local_source_table("alpha", lib / "Project Alpha")
        + local_source_table("kestrel-media", home / "Kestrel Media")
    )
    said = [
        "Project Alpha moved",
        "~/Kestrel Media is fine",
        "Project Beta is listed",
        "GitHub Copilot CLI ran",
    ]
    shell, python, shell_private, python_private = _both_reports(
        env,
        ["Project Alpha"],
        said,
        locale,
        listed=["Project Beta", "Copilot", "Q3\x08Plans"],
        config=home / "agent-context" / "sources.toml",
        config_body=body,
    )
    assert len(python) == len(said), python
    assert not [ln for ln in python for w in ("Kestrel", "Alpha", "Beta") if w in ln], python
    assert python[-1] == "GitHub Copilot CLI ran", python[-1]
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_number_install_log_cloudstorage_parts_under_one_skip_c2668(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 44 C2668 (ruling 172; privacy; makes worse True for numbering every part as the shell did):
    install.log's --source-local words under ~/Library/CloudStorage that no listing or config names were
    numbered by the shell report alone, so the agentsync report printed `Kestrel Quarry/Zephyr Plans` whole,
    while the shell numbered `Copilot` and `Documents` from install.log (`GitHub <folder-2> CLI`). Both
    register each part below the provider after every value they number, under report_cuts' one skip (a
    generic folder, a coding agent's product word): an off-disk folder reads `<folder-N>/<folder-M>` in both,
    and `GitHub Copilot CLI` and `Documents` stay readable."""
    start = "2026-10-10T00:00:00Z run=20261010T000000Z-4242"
    cs = "~/Library/CloudStorage/OneDrive-Contoso"
    words = [f"{cs}/Kestrel\\ Quarry/Zephyr\\ Plans", f"{cs}/Copilot", f"{cs}/Kestrel\\ Quarry/Copilot"]
    words.append(f"{cs}/Documents/Wombat\\ Drafts")
    log = install_log_path(env)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        f"{start} start install.sh compat=5 commit=0123456789ab kind=checkout source=- args="
        + " ".join(f"--source-local {w}" for w in words)
        + f"\n{start} end rc=2 seconds=1\n",
        encoding="utf-8",
    )
    said = [
        f"picked {cs}/Kestrel Quarry/Zephyr Plans",
        "the Kestrel Quarry folder was gone",
        "GitHub Copilot CLI ran",
        "the Documents folder and my Wombat Drafts",
    ]
    shell, python, shell_private, python_private = _both_reports(env, ["Project Alpha"], said, locale)
    assert not [ln for ln in python for w in ("Kestrel", "Zephyr", "Quarry") if w in ln], python
    assert re.fullmatch(
        r"picked ~/Library/CloudStorage/OneDrive-<org-1>/<folder-\d+>/<folder-\d+>", python[0]
    )
    assert re.fullmatch(r"the <folder-\d+> folder was gone", python[1]), python
    assert python[2] == "GitHub Copilot CLI ran", python  # a product word, never numbered
    assert re.fullmatch(r"the Documents folder and my <folder-\d+>", python[3]), python  # generic: kept
    assert not python_private, python
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_read_a_logged_word_whole_and_number_it_in_install_shs_order_c2939_c2912(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 45 C2939 with C2912 (ruling 189; privacy): the agentsync report ended a bare install.log word
    at `\\s`, which matches the NBSP a UTF-8 run's printf %q writes raw, so `Kestrel<NBSP>Quarry/Zephyr Plans`
    was cut to `Kestrel` and `Quarry` and `Zephyr Plans` printed in clear where the shell numbered them; and
    it numbered the log-only folders in log order, where install.sh sorts the args lines and reads the
    `$'..'` words after the bare ones, so two runs' folders took opposite numbers. A word ends at a space,
    tab or newline, and both reports number in install.sh's order, under C and UTF-8 alike."""
    home, cs = env["HOME"], "Library/CloudStorage/OneDrive-Contoso"
    names = ["Zebra Wombat", "Alder Kestrel", "Kestrel\u00a0Quarry/Zephyr Plans"]  # a $'..' word under C
    log = install_log_path(env)
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for n, name in enumerate(names):  # log order: Zebra first
        start = f"2026-11-23T10:00:0{n}Z run=20261123T100000Z-{4240 + n}"
        word = _printf_q(f"{home}/{cs}/{name}", locale)
        lines += [
            f"{start} start install.sh compat=41 kind=checkout source=- args=--source-local {word}",
            f"{start} end rc=2 seconds=1",
        ]
    log.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    said = [
        "first Zebra Wombat",
        "second Alder Kestrel",
        "the Kestrel\u00a0Quarry folder",
        "my Zephyr Plans folder",
    ]
    shell, python, shell_private, python_private = _both_reports(env, ["Project Alpha"], said, locale)
    leaks = [ln for ln in python for w in ("Quarry", "Zephyr", "Zebra", "Alder") if w in ln]
    assert not leaks, python
    assert shell == python and shell_private == python_private, list(zip(shell, python, strict=True))
    first, second = (int(m.group(1)) for ln in python[:2] if (m := re.search(r"<folder-(\d+)>", ln)))
    assert second < first, python  # `Alder\ Kestrel`'s args line sorts before `Zebra\ Wombat`'s


def test_a_logged_word_ends_only_where_bash_ends_it_c2939(tmp_path: Path) -> None:
    """B13 round 45 C2939 (ruling 189): `_logged_cloud_folders` reads a UTF-8 run's raw NBSP inside the word,
    as bash's `read -a` does, and ends it at a space or tab."""
    log = tmp_path / "install.log"
    start = "2026-11-23T10:00:00Z run=20261123T100000Z-1"
    word = "~/Library/CloudStorage/OneDrive-Contoso/Kestrel\u00a0Quarry/Zephyr\\ Plans"
    log.write_text(
        f"{start} start install.sh compat=41 args=--source-local {word}\t--debug\n{start} end rc=0\n",
        encoding="utf-8",
    )
    assert setup_report._logged_cloud_folders(log) == [(["Kestrel\u00a0Quarry", "Zephyr Plans"], False)]


def test_the_loop_line_and_install_sh_say_one_no_folder_next_c2601(
    real_env: dict[str, str],  # noqa: F811
    wheel: Path,  # noqa: F811
) -> None:
    """B13 round 44 ruling 179 (C2601 with C2638), the twins fed one run: a bare install.sh, then the same
    command over the inbox-only config it made, ends on install.sh's `no folder is synced yet:` NEXT (round 43
    ruling 163, C2401), and the setup report install.sh writes at that exit said status's skill-missing NEXT
    on its Loop line. Both now say the no-folder words of the one constant, the Loop line with
    install.sh's path kept whole and no skill-missing words."""
    home_env = real_env
    assert install_sh(home_env, str(wheel), timeout=300).returncode == 2
    again = install_sh(home_env, str(wheel), timeout=300)
    assert again.returncode == 0, again.stdout + again.stderr
    said = [ln for ln in again.stdout.splitlines() if ln.startswith("NEXT: ")][-1]
    words = setup_report.NO_FOLDER_NEXT.split("{install_sh}", 1)[0]
    assert said.startswith(f"NEXT: {words}{INSTALL_SH} --list-folders"), said
    [loop] = [
        ln
        for ln in report_path(home_env).read_text(encoding="utf-8").splitlines()
        if ln.startswith("- Loop: ")
    ]
    kept = setup_report.INSTALL_SH  # the report keeps the README checkout's path whole, whatever $SELF is
    want = setup_report.NO_FOLDER_NEXT.format(install_sh=kept, rerun=kept)
    assert f"NEXT: {want}" in loop and "skill is missing" not in loop, loop
    assert (
        said.replace(str(INSTALL_SH), kept).split(" then run: ", 1)[0]
        == f"NEXT: {want}".split(" then run: ")[0]
    )


def test_one_real_no_folder_runs_next_is_the_loop_lines_c2946(
    real_env: dict[str, str],  # noqa: F811
    wheel: Path,  # noqa: F811
) -> None:
    """B13 round 45 C2946 with C2801, C2836 and C2932 (ruling 192), the twins fed one run: a real
    `install.sh --no-folder` on a fresh home ends 0 on the person's-choice NEXT, and the setup report it
    writes at that exit said `no folder is synced yet: ... choose the folders to sync` on its Loop line. Both
    now say NO_FOLDER_CHOSEN_NEXT's words, the Loop line with install.sh's path kept whole."""
    done = install_sh(real_env, str(wheel), "--no-folder", timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    said = [ln for ln in done.stdout.splitlines() if ln.startswith("NEXT: ")][-1]
    assert said.startswith(f"NEXT: {setup_report.NO_FOLDER_CHOSEN}, so this Mac syncs none"), said
    [loop] = [
        ln
        for ln in report_path(real_env).read_text(encoding="utf-8").splitlines()
        if ln.startswith("- Loop: ")
    ]
    kept = setup_report.INSTALL_SH
    want = setup_report.NO_FOLDER_CHOSEN_NEXT.format(rerun=kept)
    # B13 round 48 C3546 (ruling 227): status's skill rule yields to a completed none, so the Loop line is
    # status's own choice NEXT (loop.NO_FOLDER_CHOSEN_LOOP_NEXT, kept by _CHOSEN_LOOP_OPENER), never a patched
    # skill-missing line; its opening words are install.sh's
    assert f"NEXT: {loop_module.NO_FOLDER_CHOSEN_LOOP_NEXT}" in loop, loop
    assert "no folder is synced yet" not in loop and "choose the folders" not in loop, loop
    assert (
        said.replace(str(INSTALL_SH), kept).split(" the run is: ", 1)[0]
        == f"NEXT: {want}".split(" the run is: ")[0]
    )


_PROJECTS_OUTSIDE = "~/Projects/Zephyr Quarry"
"""A private folder outside ~/Library/CloudStorage/ that no config names (B13 round 41 ruling 142)."""


def _outside_lines(home: str) -> list[str]:
    """Each head's word outside CloudStorage (C2062 with C2031 and C2085): bare with ``\\ ``, single- and
    double-quoted after ``--source-local ``, ``--source-local=`` and ``add-source ``, the not-a-folder line
    and a refusal head; then the prose rows the guard keeps whole, as _source_local_shown leaves them."""
    bare, word = _PROJECTS_OUTSIDE.replace(" ", "\\ "), _PROJECTS_OUTSIDE
    return [
        *(
            f"{lead}{head}{form} x"
            for lead, head in (
                ("start install.sh args=", "--source-local "),
                ("I ran install.sh ", "--source-local="),
                ("[dry-run] ~/.local/bin/agentsync ", "add-source "),
            )
            for form in (bare, f"'{word}'", f'"{word}"')
        ),
        f"start install.sh args=--source-local {home}/Projects/Zephyr\\ Quarry",
        f"usage error: --source-local is not an existing folder: {word} (see --help)",
        f"usage error: --source-local {word}: {report_cuts.REFUSED_HEADS[0]}x",
        "--source-local is not an existing folder: ... was the line",
        "I ran --source-local Wombat and add-source again",
        "add-source --help prints the usage",
        "~/.local/bin/agentsync add-source failed after adding it (see the error above)",
        "the add-source opened past it",
    ]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_cut_a_word_outside_cloudstorage_after_every_head_c2062(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 41 ruling 142, C2062 with C2031 and C2085 (privacy; data integrity; makes worse True each for
    the unguarded cut): the shell report cut a word after ``--source-local``, ``--source-local=``,
    ``add-source`` or the not-a-folder head only under ~/Library/CloudStorage/, so a private folder anywhere
    else printed in clear while the agentsync report read ``~/Projects/<path>``. Each head's rule now reads
    any path in both reports, and a bare word with no slash, backslash or quote stays prose in both, so
    `--source-local is not an existing folder: ` keeps its head and install.sh's own add-source words stay
    whole."""
    said = _outside_lines(env["HOME"])
    shell, python, shell_private, python_private = _both_reports(env, ["Spruce Ledger"], said, locale)
    assert shell_private == python_private, (shell_private, python_private)
    assert len(shell) == len(python) == len(said), (len(shell), len(python))
    assert shell == python, [(a, b) for a, b in zip(shell, python, strict=True) if a != b]
    assert not any(w in ln for ln in shell for w in ("Zephyr", "Quarry")), shell
    assert shell[-4:] == said[-4:], "prose keeps every word"
    # the guard keeps the not-a-folder head after `--source-local `, and its name is cut as always
    assert shell[-5] == "--source-local is not an existing folder: <path>", shell[-5]
    assert all("~/Projects/<path>" in ln for ln in shell[:-5]), shell


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_read_a_listed_only_folder_outside_cloudstorage_alike_c2062(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 41 ruling 142, C2062's residual: a folder only step 1's list names is not known to the
    provider rule (setup_report.Redactor.listed_only), so `--source-local ~/Projects/Cedar Lodge/x` is cut at
    its <folder-N> in both reports, while add-source's first-unknown rule keeps that placeholder; the shell's
    cut() kept every placeholder."""
    lib = _cloud(env) / "OneDrive-Contoso"
    (lib / "Cedar Lodge").mkdir(parents=True)
    assert install_sh(env, "--list-folders").returncode in (0, 3)
    said = [
        "start install.sh args=--source-local ~/Projects/Cedar\\ Lodge/x",
        "start install.sh args=--source-local ~/Projects/Cedar\\ Lodge",
        "[dry-run] ~/.local/bin/agentsync add-source ~/Projects/Cedar\\ Lodge/x",
        "usage error: --source-local is not an existing folder: ~/Projects/Cedar Lodge (see --help)",
    ]
    shell, python, shell_private, python_private = _both_reports(env, ["Spruce Ledger"], said, locale)
    assert shell_private == python_private, (shell_private, python_private)
    assert shell == python, [(a, b) for a, b in zip(shell, python, strict=True) if a != b]
    assert shell[0].endswith("--source-local ~/Projects/<path>") and shell[1].endswith("~/Projects/<path>")
    assert re.search(r"add-source ~/Projects/<folder-\d+>/<path>$", shell[2]), shell[2]
    assert not any("Cedar" in ln or "Lodge" in ln for ln in shell), shell


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_cut_every_debug_head_on_a_line_c2029(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 41 ruling 142, C2029 (privacy; data integrity): the shell cut only the first `debug: cwd`,
    `debug: source` or `debug: config` head of each kind on a line, so a second one printed its project
    folder, and the agentsync report took a line's cwd only from its first debug head. Every head on a line is
    cut in both; a source or config path is cut under the cwd an earlier line left or under any `debug: cwd`
    on its own line, and the next line compares with the line's last."""
    kl, nd, fp = "~/Projects/Kestrel\\ Ledger", "~/Projects/Northwind\\ Deal", "~/Projects/Fabrikam\\ Plan"
    said = [
        f"debug: cwd {kl}",
        f"debug: config {kl}/a.toml | debug: config {kl}/b.toml",
        f"debug: source checkout {kl}/s1 | debug: source checkout {kl}/s2",
        f"debug: cwd {kl} | debug: cwd {nd}",
        f"debug: config {nd}/c.toml | debug: config ~/y.toml",
        f"debug: config ~/x.toml | debug: cwd {fp}",
        f"debug: config {fp}/c.toml",
    ]
    shell, python, shell_private, python_private = _both_reports(env, ["Spruce Ledger"], said, locale)
    assert shell_private == python_private, (shell_private, python_private)
    assert not any(w in ln for ln in shell + python for w in ("Kestrel", "Northwind", "Fabrikam")), (
        shell,
        python,
    )
    assert shell == python, [(a, b) for a, b in zip(shell, python, strict=True) if a != b]
    assert shell[1] == "debug: config ~/Projects/<path> | debug: config ~/Projects/<path>", shell[1]
    assert shell[5] == "debug: config ~/<path> | debug: cwd ~/Projects/<path>", shell[5]  # C2063: always


# B13 round 46 rulings 198 and 199 (privacy; data integrity): lane RA's Python halves and the rows both
# reports share, which lane RB's shell halves turn green in one joint land.


def test_a_logged_flag_word_inside_an_escaped_operand_is_no_flag_c3104(tmp_path: Path) -> None:
    """B13 round 46 C3104 (ruling 199; privacy): an unanchored match began at the escaped space inside
    `~/Project\\ --source-local`, took the real `--source-local` for its operand and never read RealFolder,
    which bash's `read -a` finds. The args split as read -a splits them, then the word after each flag token;
    a `$'..'` word after such an operand is read by install.sh's grep pattern."""
    log = tmp_path / "install.log"
    start = "2026-11-24T10:00:00Z run=20261124T100000Z-1"
    cs = "~/Library/CloudStorage/OneDrive-Contoso"
    for word, want in (
        (f"{cs}/RealFolder", [(["RealFolder"], False)]),
        (f"$'{cs}/Wombat\\'s Plans'", [(["Wombat's Plans"], False)]),
    ):
        log.write_text(
            f"{start} start install.sh compat=42 args=--launcher ~/Project\\ --source-local --source-local "
            f"{word}\n{start} end rc=0\n",
            encoding="utf-8",
        )
        assert setup_report._logged_cloud_folders(log) == want, word


def test_an_unparsed_config_pairs_each_id_with_its_path_c3056() -> None:
    """B13 round 46 C3056 (ruling 199; privacy): the scan of a sources.toml that does not parse read ids
    only, so `~/Development/KestrelAlder/notes` printed in clear under every header. Each [[source]] table's
    id is paired with its path, the key bare or quoted, a basic value read as TOML reads it and a literal one
    raw; an id that is no kept source id leaves the path, and a table with neither is skipped."""
    text = (
        '[[source]]\nid = "kestrel-alder"\npath = "~/Development/Kestrel\\u0041lder/notes"\n'
        "[[ 'source' ]]\n\"id\" = 'kestrel-birch'\n'path' = '~/Development/Kestrel\\Birch'  # raw\n"
        '[[ "source" ]]\nid = "setup"\npath = "~/Development/KestrelCedar"\n'
        '[[source]]\nkind = "local"\n[convert]\npath = "~/Development/Nowhere"\nid = = \n'
    )
    assert setup_report._scanned_sources(text) == [
        ("kestrel-alder", "~/Development/KestrelAlder/notes"),
        ("kestrel-birch", "~/Development/Kestrel\\Birch"),
        ("", "~/Development/KestrelCedar"),
    ]
    crlf = '[[source]]\r\nid = "kestrel-alder"\r\npath = "~/Development/KestrelAlder"\r\nbad =\r\n'
    assert setup_report._scanned_sources(crlf) == [
        ("kestrel-alder", "~/Development/KestrelAlder")
    ]  # review row


_R199_HEADERS = {"bare": "[[source]]", "basic": '[[ "source" ]]', "literal": "[['source']]"}


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("python", ["tomllib", "none"])
@pytest.mark.parametrize("parsed", ["loaded", "unparsed"])
@pytest.mark.parametrize("quote", ['"', "'"])
@pytest.mark.parametrize("header", list(_R199_HEADERS))
def test_both_reports_cut_a_source_under_every_header_and_quote_c3056(
    env: dict[str, str],  # noqa: F811
    header: str,
    quote: str,
    parsed: str,
    python: str,
    locale: str,
) -> None:
    """B13 round 46 C3056 with C3034 and C3083 (ruling 199; privacy): under a quoted header or a literal id
    the shell printed `Sync of kestrel-alder stopped` and the literal path in clear, in a report public when
    the file loads, and the agentsync report printed the path of a file that does not parse. Both read every
    header spelling and both quotes, loaded or not, with or without tomllib: neither shows the id or a part
    of the path, both print the same rows and each reaches the same verdict."""
    if python == "tomllib":
        _tool_python(env["HOME"])
    (Path(env["HOME"]) / "Development" / "KestrelAlder" / "notes").mkdir(parents=True)
    q = quote
    body = (
        default_config_text()
        + f'\n{_R199_HEADERS[header]}\nid = {q}kestrel-alder{q}\nkind = "local"\n'
        + f"path = {q}~/Development/KestrelAlder/notes{q}\n"
        + ("this line is no TOML\n" if parsed == "unparsed" else "")
    )
    said = ["Sync of kestrel-alder stopped; folder ~/Development/KestrelAlder/notes was skipped."]
    texts: list[str] = []
    shell, python_rows, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=Path(env["HOME"]) / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if "kestrel" in ln.casefold()]
        assert not leaks, (name, leaks)
    assert python_private == (parsed == "unparsed"), texts[1]
    assert shell == python_rows and shell_private == python_private, (shell, python_rows)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("field", ["source path", "source leaf", "pandoc_path", "docs_repo"])
def test_both_reports_cut_a_tilde_name_value_beside_a_valid_source_c3099(
    env: dict[str, str],  # noqa: F811
    field: str,
    locale: str,
) -> None:
    """B13 round 46 C3099 with C3033, C3037 and C3110 (ruling 198; privacy): a `~contoso` value in
    sources.toml raised inside the agentsync report's redactor, which then cut only the home and went out
    public with every folder in clear. Each report cuts the value whole, its head and its folder parts,
    beside a valid source that keeps its own placeholder: no `contoso`, `Zephyr` or `Quarry`, the same rows
    and one verdict, public. B13 round 47 C3285 (ruling 207; privacy): and the value's parent, quoted, reads
    `~<user>/<path>`, never `~<user> Quarry` (the head's cut ran to the blank)."""
    home = Path(env["HOME"])
    juniper = _cloud(env) / "OneDrive-Contoso" / "Juniper Notes"
    juniper.mkdir(parents=True)
    raw = {
        "source path": "~contoso/Zephyr Quarry",
        "source leaf": "~contoso/Zephyr Quarry/leaf",
        "pandoc_path": "~contoso/Zephyr Quarry/pandoc",
        "docs_repo": "~contoso/Zephyr Quarry/docs",
    }[field]
    top = f"[agentsync]\ndocs_repo = {toml_string(raw)}\n" if field == "docs_repo" else ""
    table = f'\n[[source]]\nid = "extra-notes"\nkind = "local"\npath = {toml_string(raw)}\n'
    extra = {
        "source path": table,
        "source leaf": table,
        "pandoc_path": f"\n[convert]\npandoc_path = {toml_string(raw)}\n",
        "docs_repo": "",
    }[field]
    body = top + default_config_text() + local_source_table("juniper", juniper) + extra
    said = [f"I set {raw} in sources.toml", "the sync of Juniper Notes went on", f"{raw} names another home"]
    if field != "source path":  # C3285: the parent the value lies in
        said.append("I set ~contoso/Zephyr Quarry in sources.toml")
    texts: list[str] = []
    shell, python, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if re.search("(?i)contoso|zephyr|quarry", ln)]
        assert not leaks, (name, leaks)
    assert python[0] == "I set ~<user>/<path> in sources.toml" and re.fullmatch(
        r"the sync of <folder-\d+> went on", python[1]
    ), python
    assert python[3:] == ([] if field == "source path" else ["I set ~<user>/<path> in sources.toml"]), python
    assert not python_private, texts[1]
    assert shell == python and shell_private == python_private, (shell, python)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_cut_an_unconfigured_tilde_login_path_whole_c3224(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 47 C3224 (ruling 207; privacy): a friction line naming another account's `~login/` path that
    sources.toml no longer holds printed in clear in both reports, where a `/Users/<login>/` path is cut by
    its shape. Both cut a `~login/` head that no login character precedes, not this account's, whole as the
    `/Users/` rule cuts (a word after an unquoted blank kept, C946's limit), and leave `HEAD~1` and `~/x`."""
    juniper = _cloud(env) / "OneDrive-Contoso" / "Juniper Notes"
    juniper.mkdir(parents=True)
    body = default_config_text() + local_source_table("juniper", juniper)
    said = [
        "I set ~dana/Kestrel Notes in sources.toml",
        "~dana/Kestrel Notes names another home",
        "I set ~contoso/Zephyr Quarry in sources.toml",
        "I set '~dana/Kestrel Notes' in sources.toml",
        "HEAD~1 is the commit before",
        "~/x is mine",
    ]
    home = Path(env["HOME"])
    texts: list[str] = []
    shell, python, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    assert python == [
        "I set ~<user>/<path> Notes in sources.toml",
        "~<user>/<path> Notes names another home",
        "I set ~<user>/<path> Quarry in sources.toml",
        "I set '~<user>/<path>' in sources.toml",
        "HEAD~1 is the commit before",
        "~/x is mine",
    ], python
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if re.search("(?i)dana|kestrel|zephyr", ln)]
        assert not leaks, (name, leaks)
    assert not python_private, texts[1]
    assert shell == python and shell_private == python_private, (shell, python)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("head", ["~jd", "~a", "~JD", "~it", "~me"])
def test_both_reports_cut_a_short_tilde_name_head_alike_c3247(
    env: dict[str, str],  # noqa: F811
    head: str,
    locale: str,
) -> None:
    """B13 round 47 C3247 (ruling 207; privacy; makes worse True for a 3-character floor or short_pair's
    any-case flag): a refused `~name` value of one or two characters was cut by the agentsync report only,
    and `~it` cut the word `it` across the report. Both cut the head anchored on its `~`, a two-letter name
    as written past the report's own words and the two-letter word list (`me` joins it), and leave common
    words alone: the same rows and one verdict, public."""
    juniper = _cloud(env) / "OneDrive-Contoso" / "Juniper Notes"
    juniper.mkdir(parents=True)
    raw = f"{head}/Zephyr Quarry"
    body = (
        default_config_text()
        + local_source_table("juniper", juniper)
        + f'\n[[source]]\nid = "extra-notes"\nkind = "local"\npath = {toml_string(raw)}\n'
    )
    name = head[1:]
    said = [f"I set {head} in sources.toml", f"the account {name} owns it", "tell me if it works"]
    home = Path(env["HOME"])
    texts: list[str] = []
    shell, python, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    cut = len(name) >= 2 and name.lower() not in setup_report._TWO_LETTER_WORDS
    assert python == [
        "I set ~<user> in sources.toml",
        f"the account {'<user>' if cut else name} owns it",
        "tell me if it works",
    ], python
    assert not python_private, texts[1]
    assert shell == python and shell_private == python_private, (shell, python)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("parsed", ["parsed", "unparsed"])
def test_both_reports_cut_a_source_id_agentsync_refuses_c3223(
    env: dict[str, str],  # noqa: F811
    parsed: str,
    locale: str,
) -> None:
    """B13 round 47 C3223 (ruling 207; privacy): a literal-string id agentsync refuses (`'Contoso Quarry'`,
    no source-id match) was never registered by the agentsync report, which printed `<org-1> Quarry`, where
    the shell numbers every id it reads. Both cut it whole, in config order, parsed or not, and an id that is
    one of the report's own words (`'Status'`) is still not registered."""
    home = Path(env["HOME"])
    juniper = _cloud(env) / "OneDrive-Contoso" / "Juniper Notes"
    juniper.mkdir(parents=True)
    quarry = home / "Projects" / "Quarry Ledger"
    quarry.mkdir(parents=True)
    body = (
        default_config_text()
        + local_source_table("juniper", juniper)
        + f'\n[[ "source" ]]\nid = \'Contoso Quarry\'\nkind = "local"\npath = {toml_string(str(quarry))}\n'
        + '\n[[source]]\nid = \'Status\'\nkind = "local"\npath = "~/Projects/Nimbus Den"\n'
        + ("%%% not toml\n" if parsed == "unparsed" else "")
    )
    said = ["the source Contoso Quarry was listed by status", "the Status line is fine"]
    texts: list[str] = []
    shell, python, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if "quarry" in ln.casefold()]
        assert not leaks, (name, leaks)
    assert re.fullmatch(r"the source <(?:source|folder)-\d+> was listed by status", python[0]), python
    assert python[1] == "the Status line is fine", python
    assert shell == python and shell_private == python_private, (shell, python)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("python", ["tomllib", "none"])
@pytest.mark.parametrize("parsed", ["loaded", "unparsed"])
@pytest.mark.parametrize("id_line", ["no id", "id = 7"])
def test_both_reports_cut_a_tilde_name_path_of_a_source_with_no_string_id_c3244(
    env: dict[str, str],  # noqa: F811
    id_line: str,
    parsed: str,
    python: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 207, C3244 (privacy): the shell's refused-path loop read only tables with a string
    id, so a `~contoso/Zephyr Quarry` path in a table with no id, or `id = 7`, printed in clear in a public
    shell report while the agentsync report cut it. Both cut it, with or without tomllib, loaded or not: no
    `contoso`, `Zephyr` or `Quarry`, the same rows, one verdict, and the later source keeps its number."""
    if python == "tomllib":
        _tool_python(env["HOME"])
    home = Path(env["HOME"])
    juniper = _cloud(env) / "OneDrive-Contoso" / "Juniper Notes"
    juniper.mkdir(parents=True)
    raw = "~contoso/Zephyr Quarry"
    extra = "" if id_line == "no id" else f"{id_line}\n"
    body = (
        default_config_text()
        + local_source_table("juniper", juniper)
        + f'\n[[source]]\n{extra}kind = "local"\npath = {toml_string(raw)}\n'
        + '\n[[source]]\nid = "quokka-ledger"\nkind = "local"\npath = "~/Development/QuokkaNotes"\n'
        + ("this line is no TOML\n" if parsed == "unparsed" else "")
    )
    said = [f"I set {raw} in sources.toml", f"{raw} names another home", "Sync of quokka-ledger stopped"]
    texts: list[str] = []
    shell, python_rows, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if re.search("(?i)contoso|zephyr|quarry|quokka", ln)]
        assert not leaks, (name, leaks)
    assert python_rows[0] == "I set ~<user>/<path> in sources.toml", python_rows
    assert python_rows[2] == "Sync of <source-2> stopped", python_rows
    assert python_private == (parsed == "unparsed"), texts[1]
    assert shell == python_rows and shell_private == python_private, (shell, python_rows)


_C3303_PATHS = {
    # written path in sources.toml, the value both reports read, the words neither may print
    "uXXXX head": ("~k\\u0065strel/Zephyr Quarry", "~kestrel/Zephyr Quarry", "kestrel|zephyr|quarry"),
    "u001c before the head": (
        "\\u001c~kestrel/Zephyr Quarry",
        "~kestrel/Zephyr Quarry",
        "kestrel|zephyr|quarry",
    ),
    "u0085 in the head": (
        "~ali\\u0085ce/Zephyr Quarry",
        "~ali\x85ce/Zephyr Quarry",
        "ali\x85ce|zephyr|quarry",
    ),
    "u001c in the head": (
        "~ali\\u001cce/Zephyr Quarry",
        "~ali\x1cce/Zephyr Quarry",
        "ali\x1cce|zephyr|quarry",
    ),
}


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("python", ["tomllib", "none"])
@pytest.mark.parametrize("parsed", ["loaded", "unparsed"])
@pytest.mark.parametrize("case", list(_C3303_PATHS))
def test_both_reports_cut_a_refused_source_path_written_with_an_escape_c3303(
    env: dict[str, str],  # noqa: F811
    case: str,
    parsed: str,
    python: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 207, C3303 (privacy): the shell's fallback scan read a basic source path as
    written, so `~k\\u0065strel/Zephyr Quarry` was registered escaped and `kestrel` printed in clear, in a
    public shell report where no interpreter answers. One tbasic decoder reads it as tomllib does: neither
    report shows the name or a folder, with or without tomllib, loaded or not, the same rows, one verdict."""
    if python == "tomllib":
        _tool_python(env["HOME"])
    home = Path(env["HOME"])
    juniper = _cloud(env) / "OneDrive-Contoso" / "Juniper Notes"
    juniper.mkdir(parents=True)
    written, value, words = _C3303_PATHS[case]
    body = (
        default_config_text()
        + local_source_table("juniper", juniper)
        + f'\n[[source]]\nid = "extra-notes"\nkind = "local"\npath = "{written}"\n'
        + ("this line is no TOML\n" if parsed == "unparsed" else "")
    )
    said = [
        f"I set {value} in sources.toml",
        "the sync of Juniper Notes went on",
        f"{value} names another home",
    ]
    texts: list[str] = []
    shell, python_rows, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if re.search(f"(?i){words}", ln)]
        assert not leaks, (name, leaks)
    assert python_rows[0] == "I set ~<user>/<path> in sources.toml", python_rows
    assert python_private == (parsed == "unparsed"), texts[1]
    assert shell == python_rows and shell_private == python_private, (shell, python_rows)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("python", ["tomllib", "none"])
@pytest.mark.parametrize("second", ['id = "kestrel-birch"', "\"id\" = 'kestrel-birch'"])
def test_both_reports_read_the_first_of_a_repeated_id_and_go_private_c3246(
    env: dict[str, str],  # noqa: F811
    second: str,
    python: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 207, C3246 (privacy): a `[[source]]` table holding `id` twice, which tomllib
    refuses, passed the shell's structural check where no interpreter answers, so the shell report stayed
    public, cut only the last id and printed `Sync of kestrel-alder stopped`, while the agentsync report
    went private only and cut the first. Both now cut the first id alike, print the same rows and each says
    private only, with or without tomllib."""
    if python == "tomllib":
        _tool_python(env["HOME"])
    body = (
        default_config_text()
        + f'\n[[source]]\nid = "kestrel-alder"\n{second}\nkind = "local"\npath = "~/Development/WombatDocs"\n'
    )
    said = ["Sync of kestrel-alder stopped", "Sync of kestrel-birch stopped"]
    texts: list[str] = []
    shell, python_rows, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=Path(env["HOME"]) / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if re.search("(?i)kestrel-alder|wombat", ln)]
        assert not leaks, (name, leaks)
    assert python_rows[0] == "Sync of <source-1> stopped", python_rows
    assert python_private, texts[1]
    assert shell == python_rows and shell_private == python_private, (shell, python_rows)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("python", ["tomllib", "none"])
@pytest.mark.parametrize("escape", ["\\t", "\\n"])
def test_both_reports_cut_an_id_whose_path_holds_a_tab_or_lf_c3243_c3302(
    env: dict[str, str],  # noqa: F811
    escape: str,
    python: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 207, C3243 with C3302 (privacy): tomllib reads a source path's `\\t` or `\\n`
    escape as a real TAB or LF, and the shell's tomllib records dropped that whole table, so with an
    interpreter the shell printed `Sync of wombat-ledger stopped` in a public report and numbered the next
    id `<source-1>` where the agentsync report numbers it `<source-2>`. Neither report shows the id, both
    print the same rows, with or without tomllib, and each reaches the same verdict, public."""
    if python == "tomllib":
        _tool_python(env["HOME"])
    body = (
        default_config_text()
        + f'\n[[source]]\nid = "wombat-ledger"\nkind = "local"\npath = "~/Development/Kestrel{escape}Alder"\n'
        + '\n[[source]]\nid = "quokka-notes"\nkind = "local"\npath = "~/Development/JuniperLedger"\n'
    )
    said = ["Sync of wombat-ledger stopped", "Sync of quokka-notes stopped"]
    texts: list[str] = []
    shell, python_rows, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=Path(env["HOME"]) / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if re.search("(?i)wombat|quokka|kestrel|juniper", ln)]
        assert not leaks, (name, leaks)
    assert python_rows == ["Sync of <source-1> stopped", "Sync of <source-2> stopped"], python_rows
    assert not python_private, texts[1]
    assert shell == python_rows and shell_private == python_private, (shell, python_rows)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("target", ["locked", "documents"])
def test_both_reports_cut_where_the_inbox_links_outside_agent_context_c3055(
    env: dict[str, str],  # noqa: F811
    target: str,
    locale: str,
) -> None:
    """B13 round 46 C3055 (ruling 199; privacy; makes worse True for registering every source's resolved
    path): the agentsync report took the inbox link's resolved target for agentsync's own, so doctor's FAIL
    on a link into ~/Locked Contoso/inner, a folder at mode 000, printed it in clear in a public report. The
    target outside ~/agent-context is registered through the own-folder filter after the own folders: the
    locked one is cut and the report stays public, and a link to ~/Documents/inbox registers no `inbox`, so
    the report's own `inbox` words stay and it stays public."""
    home = Path(env["HOME"])
    inner = home / ("Locked Contoso/inner" if target == "locked" else "Documents/inbox")
    inner.mkdir(parents=True)
    (home / "agent-context").mkdir(exist_ok=True)
    (home / "agent-context" / "inbox").symlink_to(inner)
    juniper = _cloud(env) / "OneDrive-Fabrikam" / "Juniper Notes"
    juniper.mkdir(parents=True)
    body = (
        default_config_text()
        + local_source_table("juniper", juniper)
        + '\n[[source]]\nid = "inbox"\nkind = "inbox"\npath = "~/agent-context/inbox"\n'
    )
    shown = "~/Locked Contoso/inner" if target == "locked" else "~/Documents/inbox"
    said = [f"the inbox links to {shown}", "the inbox is a link"]
    texts: list[str] = []
    if target == "locked":
        inner.parent.chmod(0)
    try:
        shell, python, shell_private, python_private = _both_reports(
            env,
            [],
            said,
            locale,
            config=home / "agent-context" / "sources.toml",
            config_body=body,
            texts_out=texts,
        )
    finally:
        inner.parent.chmod(0o755)
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if "Locked" in ln]
        assert not leaks, (name, leaks)
    if target == "locked":
        assert re.fullmatch(
            r"the inbox links to ~/<folder-\d+>(/inner)?|the inbox links to <folder-\d+>", python[0]
        )
    else:
        assert python == said, python
    assert not python_private, texts[1]
    assert shell == python and shell_private == python_private, (shell, python)


def _r206_home(home: Path, case: str) -> tuple[str, list[str], str]:
    """B13 round 47 ruling 206's rows: lay ``case``'s kept inbox link out under ``home`` and return the extra
    sources.toml tables, the friction rows and the regex of a word that must not print in either report."""
    ac = home / "agent-context"
    ac.mkdir(exist_ok=True)
    inbox = ac / "inbox"
    extra, said, leak = "", ["the sync of Juniper Notes went on"], "(?i)juniper|fabrikam"
    if case == "self":
        inbox.symlink_to("inbox")
    elif case == "chain":
        (ac / "a").symlink_to("b")
        (ac / "b").symlink_to("a")
        inbox.symlink_to("a")
    elif case == "loop-locked":
        (home / "Locked Contoso").mkdir()
        (home / "Locked Contoso" / "x").symlink_to(inbox)
        inbox.symlink_to(home / "Locked Contoso" / "x")
        said, leak = [*said, "the inbox links to ~/Locked Contoso/x"], f"{leak}|locked|contoso"
    elif case == "self-target":
        (home / "Documents").mkdir()
        (home / "Documents" / "Wombat").symlink_to("Wombat")
        inbox.symlink_to(home / "Documents" / "Wombat")
        said, leak = [*said, "the inbox links to ~/Documents/Wombat"], f"{leak}|wombat"
    elif case == "relative":
        (home / "Locked Contoso" / "inner").mkdir(parents=True)
        inbox.symlink_to("../Locked Contoso/inner")
        said, leak = [*said, "the inbox links to ~/Locked Contoso/inner"], f"{leak}|locked|contoso"
    elif case == "link-to-link":
        (home / "Wombat Den").mkdir()
        (home / "Wombat Door").symlink_to(home / "Wombat Den")
        inbox.symlink_to(home / "Wombat Door")
        said, leak = [*said, "the inbox links to ~/Wombat Den"], f"{leak}|wombat den"
    elif case == "linked-folder":  # a loop whose first hop's own folder is a link: that folder resolved
        (home / "Kestrel Real").mkdir()
        (home / "Kestrel Alias").symlink_to(home / "Kestrel Real")
        (home / "Kestrel Real" / "x").symlink_to(inbox)
        inbox.symlink_to(home / "Kestrel Alias" / "x")
        said, leak = [*said, "the inbox links to ~/Kestrel Real/x"], f"{leak}|kestrel"
    elif case in ("desktop", "home"):
        target = home / "Desktop" if case == "desktop" else home
        (target / "Kestrel Plans").mkdir(parents=True)
        inbox.symlink_to(target)
        extra = local_source_table("kestrel-plans", target / "Kestrel Plans")
        shown = "~/Desktop/Kestrel Plans" if case == "desktop" else "~/Kestrel Plans"
        said, leak = [*said, f"the sync of {shown} went on", "Kestrel Plans is mine"], f"{leak}|kestrel"
    elif case == "hub":
        (home / "Kestrel Hub" / "Kestrel Plans").mkdir(parents=True)
        inbox.symlink_to(home / "Kestrel Hub")
        extra = local_source_table("kestrel-plans", home / "Kestrel Hub" / "Kestrel Plans")
        said, leak = [*said, "the sync of ~/Kestrel Hub/Kestrel Plans went on"], f"{leak}|kestrel hub"
    return extra, said, leak


_R206_CASES = (
    "self",
    "chain",
    "loop-locked",
    "self-target",
    "relative",
    "link-to-link",
    "linked-folder",
    "desktop",
    "home",
    "hub",
)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("case", _R206_CASES)
def test_both_reports_resolve_the_kept_inbox_link_alike_ruling_206(
    env: dict[str, str],  # noqa: F811
    case: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 206 (C3210 with C3284, C3242, C3245; privacy; data integrity): the agentsync
    report's redactor raised on a looping inbox link (Path.resolve()'s RuntimeError on 3.11) and went out
    private only with every folder in clear, the shell followed one hop, and an inbox linked to the home, a
    container or a folder holding a configured project hid that project from both cuts. Both reports take the
    entry's realpath, a loop's first hop's target (that hop's folder resolved, its last part kept), skip a
    home or container target and register a folder below a kept target through the own-folder filter: no
    word of a linked or configured folder prints, the agentsync report stays public, and the two print the
    same rows with one verdict."""
    home = Path(env["HOME"])
    extra, said, leak = _r206_home(home, case)
    juniper = _cloud(env) / "OneDrive-Fabrikam" / "Juniper Notes"
    juniper.mkdir(parents=True)
    body = (
        default_config_text()
        + local_source_table("juniper", juniper)
        + '\n[[source]]\nid = "inbox"\nkind = "inbox"\npath = "~/agent-context/inbox"\n'
        + extra
    )
    texts: list[str] = []
    shell, python, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if re.search(leak, ln)]
        assert not leaks, (name, leaks)
    assert "redaction facts could not be read" not in texts[1], texts[1]
    assert "/private~" not in texts[1] and not python_private, texts[1]
    assert re.fullmatch(r"the sync of <folder-\d+> went on", python[0]), python
    assert shell == python and shell_private == python_private, (shell, python)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("beside", ["default", "configured"])
def test_both_reports_read_an_unparsed_config_with_the_default_docs_repo_c3324(
    env: dict[str, str],  # noqa: F811
    beside: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 206 (C3324; privacy): over a sources.toml that does not parse, the agentsync
    report reads the default docs repo and its kept inbox link, and the shell read the configured docs repo's
    beside from the half-read file. Both read the default docs repo, with the link beside each docs repo:
    the same rows and one verdict, private only (the file does not parse)."""
    home = Path(env["HOME"])
    (home / "Locked Contoso" / "inner").mkdir(parents=True)
    ac = home / ("agent-context" if beside == "default" else "Wombat Docs")
    ac.mkdir(exist_ok=True)
    (ac / "inbox").symlink_to(home / "Locked Contoso" / "inner")
    body = '[agentsync]\ndocs_repo = "~/Wombat Docs/docs"\n%%% not toml\n'
    said = ["the inbox links to ~/Locked Contoso/inner"]
    texts: list[str] = []
    shell, python, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    assert python_private, texts[1]
    assert shell == python and shell_private == python_private, (shell, python)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("python", ["tomllib", "none"])
@pytest.mark.parametrize("escape", ["\\t", "\\n"])
def test_both_reports_keep_a_source_whose_path_holds_a_tab_or_newline_c3243(
    env: dict[str, str],  # noqa: F811
    escape: str,
    python: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 207 (C3243 with C3302; privacy): the shell's tomllib records dropped a whole
    source whose path held a TAB or LF, so its id printed in clear in a public shell report. Both keep the
    record: neither report shows `wombat`, both print the same rows, so each `<source-N>` agrees, and both
    reach one verdict."""
    if python == "tomllib":
        _tool_python(env["HOME"])
    home = Path(env["HOME"])
    juniper = _cloud(env) / "OneDrive-Contoso" / "Juniper Notes"
    juniper.mkdir(parents=True)
    body = (
        default_config_text()
        + local_source_table("juniper", juniper)
        + f'\n[[source]]\nid = "wombat-den"\nkind = "local"\npath = "~/Projects/Wombat{escape}Den"\n'
    )
    said = ["source 'wombat-den' stopped", "source 'juniper' stopped"]
    texts: list[str] = []
    shell, python_rows, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if "wombat" in ln.casefold()]
        assert not leaks, (name, leaks)
    if escape == "\\n":
        # the shell's line reader gives an LF in a path as a space, so its folder's fuzzy key is the id's
        # and the id takes that folder's number, where python's fuzzy add skips a value holding a line
        # break: the id is cut in both, its number's kind alone differs (lane RA's note (a), measured at
        # RB's land gate)
        assert re.fullmatch(r"source '<(folder|source)-2>' stopped", shell[0]), shell
        assert shell[1:] == python_rows[1:] and shell_private == python_private, (shell, python_rows)
        return
    assert shell == python_rows and shell_private == python_private, (shell, python_rows)


_C3244_TABLES = {
    "no id": '\n[[source]]\nkind = "local"\npath = "~contoso/Zephyr Quarry"\n',
    "id = 7": '\n[[source]]\nid = 7\nkind = "local"\npath = "~contoso/Zephyr Quarry"\n',
}


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("python", ["tomllib", "none"])
@pytest.mark.parametrize("parsed", ["loaded", "unparsed"])
@pytest.mark.parametrize("table", list(_C3244_TABLES))
def test_both_reports_cut_a_tilde_name_path_in_a_table_with_no_string_id_c3244(
    env: dict[str, str],  # noqa: F811
    table: str,
    parsed: str,
    python: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 207 (C3244; privacy; makes worse True for id-less id records): a `~name` path in a
    `[[source]]` table with no string id was cut by the agentsync report alone, as the shell's refused loop
    read the id records. The shell's refused loop reads one path per table whatever its id: no `contoso`,
    `zephyr` or `quarry` in either report, the same rows and one verdict, and the valid source's numbers
    unshifted."""
    if python == "tomllib":
        _tool_python(env["HOME"])
    home = Path(env["HOME"])
    juniper = _cloud(env) / "OneDrive-Fabrikam" / "Juniper Notes"
    juniper.mkdir(parents=True)
    body = (
        default_config_text()
        + local_source_table("juniper", juniper)
        + _C3244_TABLES[table]
        + ("this line is no TOML\n" if parsed == "unparsed" else "")
    )
    said = [
        "I set ~contoso/Zephyr Quarry in sources.toml",
        "the sync of Juniper Notes went on",
        "juniper stopped",
    ]
    texts: list[str] = []
    shell, python_rows, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if re.search("(?i)contoso|zephyr|quarry", ln)]
        assert not leaks, (name, leaks)
    assert python_rows == [
        "I set ~<user>/<path> in sources.toml",
        "the sync of <folder-1> went on",
        "<source-1> stopped",
    ], python_rows
    assert shell == python_rows and shell_private == python_private, (shell, python_rows)


_C3246_BODIES = {
    "id/id": 'id = "kestrel-alder"\nid = "wombat-den"\n',
    'id/"id"': 'id = "kestrel-alder"\n"id" = "wombat-den"\n',
    "valid": 'id = "kestrel-alder"\n',
}


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("ids", list(_C3246_BODIES))
def test_both_reports_call_a_repeated_key_unparsed_and_take_the_first_id_c3246(
    env: dict[str, str],  # noqa: F811
    ids: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 207 (C3246; privacy): with no interpreter, a repeated `id` key passed the shell's
    structural check, which kept the last id, so its report stayed public with the first id in clear. A
    repeated whole key (`id`, `"id"` and `'id'` one key) is unparsed there as tomllib says, and both take a
    table's first value: the same rows and one verdict, private only over a repeated key, public over the
    valid file."""
    home = Path(env["HOME"])
    (home / "Development" / "KestrelAlder" / "notes").mkdir(parents=True)
    (home / "Development" / "Plain Notes").mkdir(parents=True)
    body = (
        default_config_text()
        + "\n[[source]]\n"
        + _C3246_BODIES[ids]
        + 'kind = "local"\npath = "~/Development/KestrelAlder/notes"\n'
        + '\n[[source]]\nid = "plain-notes"\nkind = "local"\npath = "~/Development/Plain Notes"\n'
    )
    said = ["Sync of kestrel-alder stopped", "Sync of plain-notes stopped"]
    texts: list[str] = []
    shell, python_rows, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if "kestrel" in ln.casefold()]
        assert not leaks, (name, leaks)
    assert python_private == (ids != "valid"), texts[1]
    assert shell == python_rows and shell_private == python_private, (shell, python_rows)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("python", ["tomllib", "none"])
@pytest.mark.parametrize("parsed", ["loaded", "unparsed"])
def test_both_reports_decode_a_basic_string_escape_in_a_refused_head_c3303(
    env: dict[str, str],  # noqa: F811
    parsed: str,
    python: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 207 (C3303; privacy): the shell's fallback scanner kept a TOML escape in a
    refused path (`~\\u006bestrel/Zephyr Quarry`), so the account name it decodes to was never cut. Both read
    the basic string as TOML does: no `kestrel` in either report, the same rows and one verdict."""
    if python == "tomllib":
        _tool_python(env["HOME"])
    home = Path(env["HOME"])
    juniper = _cloud(env) / "OneDrive-Fabrikam" / "Juniper Notes"
    juniper.mkdir(parents=True)
    body = (
        default_config_text()
        + local_source_table("juniper", juniper)
        + '\n[[source]]\nid = "extra-notes"\nkind = "local"\npath = "~\\u006bestrel/Zephyr Quarry"\n'
        + ("this line is no TOML\n" if parsed == "unparsed" else "")
    )
    said = ["I set ~kestrel/Zephyr Quarry in sources.toml", "the account kestrel owns it"]
    texts: list[str] = []
    shell, python_rows, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for name, text in zip(("shell", "agentsync"), texts, strict=True):
        leaks = [ln for ln in text.splitlines() if re.search("(?i)kestrel|zephyr|quarry", ln)]
        assert not leaks, (name, leaks)
    assert shell == python_rows and shell_private == python_private, (shell, python_rows)


@pytest.mark.parametrize(
    ("locale", "ch"),
    [("en_US.UTF-8", "\\u0085"), ("C", "\\u0085"), ("C", "\\u3000"), ("en_US.UTF-8", "\\u001c")],
)
def test_both_reports_judge_a_unicode_blank_in_a_refused_head_alike_c3304(
    env: dict[str, str],  # noqa: F811
    ch: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 207 (C3304): the shell judged a refused head's blank by its locale, so
    `~ali<U+0085>ce/x` was cut alone in one report and not the other. Both judge it as Python's isspace does,
    under any locale: the whole value cut, the head that holds a blank never alone, the same rows and one
    verdict."""
    home = Path(env["HOME"])
    juniper = _cloud(env) / "OneDrive-Fabrikam" / "Juniper Notes"
    juniper.mkdir(parents=True)
    body = (
        f'[agentsync]\ndocs_repo = "~ali{ch}ce/x"\n'
        + default_config_text()
        + local_source_table("juniper", juniper)
    )
    shown = "~ali" + chr(int(ch[2:], 16)) + "ce"
    # the head and its name alone, which only a shell that judged the blank by its locale cut (lane RB)
    said = [
        f"I set {shown}/x in sources.toml",
        "the sync of Juniper Notes went on",
        f"{shown} and {shown[1:]} alone",
    ]
    texts: list[str] = []
    shell, python_rows, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    assert python_rows[1].startswith("the sync of <folder-"), python_rows
    assert shell == python_rows and shell_private == python_private, (shell, python_rows)


_C3289_SPELLINGS = ("Kestrel Alder", "Kestrel_Alder", "KESTREL ALDER", "kestrel-alder")


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("arm", ["configured", "joined", "vanished"])
def test_both_reports_cut_a_project_folders_fuzzy_spellings_alike_c3289(
    env: dict[str, str],  # noqa: F811
    arm: str,
    locale: str,
) -> None:
    """B13 round 47 ruling 207 (C3289; privacy): the shell registered a configured folder outside
    CloudStorage as written only, so `Kestrel Alder` and its other spellings of `KestrelAlder` printed in
    clear where the agentsync report cuts them fuzzily. The configured-sources loop takes fuzzy_flag's flag:
    every spelling cut in both. A joined name (one whose letters spell a report word) and a vanished source's
    folder stay literal in both: the same rows and one verdict."""
    home = Path(env["HOME"])
    name = "InStall" if arm == "joined" else "KestrelAlder"
    folder = home / "Development" / name
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("notes\n", encoding="utf-8")
    juniper = _cloud(env) / "OneDrive-Fabrikam" / "Juniper Notes"
    juniper.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    base = default_config_text() + local_source_table("juniper", juniper)
    body = base + local_source_table("kestrel-den", folder)
    if arm == "vanished":  # synced, then its [[source]] block removed: the manifest keeps its folder
        run = {**env, "LC_ALL": locale, "LANG": locale, "AGENTSYNC_OCR": "0", "AGENTSYNC_TM_EXCLUDE": "0"}
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(body, encoding="utf-8")
        sync = [sys.executable, "-m", "agentsync", "sync", "--config", str(cfg)]
        done = subprocess.run(sync, capture_output=True, text=True, check=False, timeout=300, env=run)
        assert done.returncode == 0, done.stdout + done.stderr
        body = base
    said = (
        [f"{w} is mine" for w in _C3289_SPELLINGS]
        if arm != "joined"
        else ["InStall is mine", "In Stall is mine", "the install went on"]
    )
    texts: list[str] = []
    shell, python_rows, shell_private, python_private = _both_reports(
        env, [], said, locale, config=cfg, config_body=body, texts_out=texts
    )
    if arm == "configured":
        assert all(re.fullmatch(r"<folder-\d+> is mine", ln) for ln in python_rows), python_rows
    assert shell == python_rows, (shell, python_rows)
    # joined: the shell's private verdict for a report word's <folder-N> ("the <folder-2> went on") predates
    # C3289 (red with or without its hunk), so only that arm's verdict is left out here (lane RB)
    assert arm == "joined" or shell_private == python_private, (shell_private, python_private)


# ---- B13 round 47 ruling 206: the kept inbox's link, resolved alike in both reports ------------------------

_INBOX_LINK_BODY = (
    default_config_text() + '\n[[source]]\nid = "inbox"\nkind = "inbox"\npath = "~/agent-context/inbox"\n'
)
_C3245_ROWS = [
    "self-link",
    "chain",
    "loop-locked",
    "documents-self-link",
    "relative",
    "link-to-link",
    "folder-link",
]
_C3245_LOOPS = [
    "self-link",
    "chain",
    "loop-locked",
    "documents-self-link",
]  # pathlib's resolve() raises on these


def _inbox_link_row(home: Path, row: str) -> tuple[list[str], list[str], Path | None]:
    """C3245's ``row`` under ``home``: ~/agent-context/inbox a link that loops, chains or is relative. The
    friction lines naming its target as doctor prints it, the words neither report may print, and the folder
    held at mode 000 for the run (None for none)."""
    ac = home / "agent-context"
    ac.mkdir(exist_ok=True)
    inbox = ac / "inbox"
    locked = home / "Locked Contoso"
    if row == "self-link":
        inbox.symlink_to("inbox")
        return ["the inbox is a link to itself"], [], None
    if row == "chain":  # inbox -> a -> b -> a
        alpha, beta = home / "Wombat Alpha", home / "Wombat Beta"
        inbox.symlink_to(alpha)
        alpha.symlink_to(beta)
        beta.symlink_to(alpha)
        return ["the inbox links to ~/Wombat Alpha"], ["Wombat", "Alpha"], None
    if row == "loop-locked":  # inbox -> ~/Locked Contoso/x -> inbox
        locked.mkdir()
        inbox.symlink_to(locked / "x")
        (locked / "x").symlink_to(inbox)
        return ["the inbox links to ~/Locked Contoso/x"], ["Locked"], None
    if row == "documents-self-link":
        juniper = home / "Documents" / "Juniper"
        juniper.parent.mkdir(parents=True, exist_ok=True)
        juniper.symlink_to("Juniper")
        inbox.symlink_to(juniper)
        return ["the inbox links to ~/Documents/Juniper"], ["Juniper"], None
    (locked / "inner").mkdir(parents=True)
    if row == "relative":
        inbox.symlink_to("../Locked Contoso/inner")
    elif row == "link-to-link":
        (ac / "inbox2").symlink_to(locked / "inner")
        inbox.symlink_to("inbox2")
    else:  # folder-link: a relative link whose own folder is a link, read against its physical folder
        deep = home / "Real Spot" / "deep"
        deep.mkdir(parents=True)
        (home / "Quokka Hop").symlink_to(deep)
        (deep / "x").symlink_to("../../Locked Contoso/inner")
        inbox.symlink_to(home / "Quokka Hop" / "x")
    return ["the inbox links to ~/Locked Contoso/inner"], ["Locked"], locked


def _resolved(home: str, path: str) -> str:
    """install.sh's own resolved() over ``path``, run under /bin/bash (3.2 on macOS) with ``home``, bounded:
    a loop must return, never hang (B13 round 47 C3245)."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    [fn] = re.findall(r"(?ms)^\tresolved_into\(\) \{.*?^\tresolved\(\) \{.*?^\t\}\n", script)
    cp = subprocess.run(
        [BASH32, "-c", fn + 'resolved "$1"', "_", path],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        env={"HOME": home, "LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
    )
    return cp.stdout.removesuffix("\n")


@pytest.mark.parametrize("row", [*_C3245_ROWS, "cap", "mutual"])
def test_install_resolves_the_inbox_link_as_realpath_reads_it_c3245(tmp_path: Path, row: str) -> None:
    """B13 round 47 C3245 (ruling 206; privacy): resolved() read one link and never normalized it, so a
    relative `../Locked Contoso/inner` matched the folder beside the docs repo and was skipped as agentsync's
    own, and a link to a link stopped after one hop. It walks as os.path.realpath does, each `cd -P`, a
    relative target against the link's resolved folder; on a loop, or past 40 hops, the first hop's target
    with its folder resolved. Two links that each name the other's folder return too."""
    home = tmp_path / "home"
    home.mkdir()
    real = Path(os.path.realpath(home))
    inbox = home / "agent-context" / "inbox"
    locked = None
    if row == "cap":  # 45 links in a ring: the cap, not the seen set, ends the walk
        (home / "agent-context").mkdir()
        inbox.symlink_to(home / "ring0")
        for n in range(45):
            (home / f"ring{n}").symlink_to(home / f"ring{(n + 1) % 45}")
    elif row == "mutual":
        (home / "agent-context").mkdir()
        (home / "a").symlink_to(home / "b" / "x")
        (home / "b").symlink_to(home / "a" / "x")
        inbox.symlink_to(home / "a")
    else:
        _said, _secrets, locked = _inbox_link_row(home, row)
    expected = {
        "self-link": real / "agent-context" / "inbox",
        "chain": real / "Wombat Alpha",
        "loop-locked": real / "Locked Contoso" / "x",
        "documents-self-link": real / "Documents" / "Juniper",
        "cap": real / "ring0",
    }.get(row, real / "Locked Contoso" / "inner")
    if locked is not None:
        locked.chmod(0)
    try:
        got = _resolved(str(home), str(inbox))
    finally:
        if locked is not None:
            locked.chmod(0o755)
    if row == "mutual":
        assert got.startswith(f"{real}/"), got
    else:
        assert got == str(expected), (got, expected)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("row", _C3245_LOOPS)
def test_the_shell_report_cuts_a_looped_inbox_links_target_c3245(
    env: dict[str, str],  # noqa: F811
    row: str,
    locale: str,
) -> None:
    """B13 round 47 C3245 (ruling 206), the shell half alone (both reports: lane RA's
    test_both_reports_resolve_the_kept_inbox_link_alike_ruling_206): the shell report holds no word of the
    first hop's target, cuts it and stays public, and a self-link (agentsync's own) prints as said."""
    home = Path(env["HOME"])
    said, secrets, _locked = _inbox_link_row(home, row)
    config = home / "agent-context" / "sources.toml"
    config.write_text(_INBOX_LINK_BODY, encoding="utf-8")
    rows = "".join(
        f"2026-10-10T00:00:{n:02d}Z | step 1 | deviation | {ln} | -\n" for n, ln in enumerate(said)
    )
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(f"Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\n{rows}", encoding="utf-8")
    cp = install_sh(
        {**env, "AGENTSYNC_CONFIG": str(config), "LC_ALL": locale, "LANG": locale}, "--report-only"
    )
    assert cp.returncode == 0 and "shell fallback" in cp.stdout, cp.stdout + cp.stderr
    text = report_path(env).read_text(encoding="utf-8")
    leaks = [ln for ln in text.splitlines() if any(w in ln for w in secrets)]
    assert not leaks, leaks
    assert "Private only:" not in text, text
    cut = " | step 1 | deviation | "
    printed = [ln.split(cut, 1)[1].removesuffix(" | -") for ln in text.splitlines() if cut in ln]
    if secrets:
        assert re.fullmatch(r"the inbox links to ~/(Documents/)?<folder-\d+>(/x)?", printed[0]), printed
    else:
        assert printed == said, printed


_C3242_ROWS = ["desktop", "home", "hub"]


def _inbox_over_projects(home: Path, row: str) -> tuple[str, list[str]]:
    """C3242's ``row`` under ``home``: ~/agent-context/inbox a link to ~/Desktop, to the home or to the
    non-container ~/Kestrel Hub, with a local source `Kestrel Plans` inside that target. sources.toml's text
    and the friction lines naming the source."""
    ac = home / "agent-context"
    ac.mkdir(exist_ok=True)
    target = {"desktop": home / "Desktop", "home": home, "hub": home / "Kestrel Hub"}[row]
    plans = target / "Kestrel Plans"
    plans.mkdir(parents=True)
    (ac / "inbox").symlink_to(target)
    body = _INBOX_LINK_BODY + local_source_table("kestrel-plans", plans)
    # below a kept target the folder is cut as an own folder is, its path from the first part that names
    # something and that part alone, so the leaf alone is said only where it is that path
    said = [f"I picked {plans}"] + ([] if row == "hub" else ["the sync of Kestrel Plans went on"])
    return body, said


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("row", _C3242_ROWS)
def test_the_shell_report_cuts_a_project_under_the_inbox_links_target_c3242(
    env: dict[str, str],  # noqa: F811
    row: str,
    locale: str,
) -> None:
    """B13 round 47 C3242 (ruling 206; privacy), the shell half alone: an inbox linked to ~/Desktop or to the
    home is no inbox target, so `Kestrel Plans` configured there is cut as any project and the report stays
    public; one linked to ~/Kestrel Hub keeps its target, and the project below it is registered through the
    own-folder filter, never skipped."""
    home = Path(env["HOME"])
    body, said = _inbox_over_projects(home, row)
    config = home / "agent-context" / "sources.toml"
    config.write_text(body, encoding="utf-8")
    rows = "".join(
        f"2026-10-10T00:00:{n:02d}Z | step 1 | deviation | {ln} | -\n" for n, ln in enumerate(said)
    )
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(f"Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\n{rows}", encoding="utf-8")
    cp = install_sh(
        {**env, "AGENTSYNC_CONFIG": str(config), "LC_ALL": locale, "LANG": locale}, "--report-only"
    )
    assert cp.returncode == 0 and "shell fallback" in cp.stdout, cp.stdout + cp.stderr
    text = report_path(env).read_text(encoding="utf-8")
    leaks = [ln for ln in text.splitlines() if "Kestrel" in ln or "Plans" in ln]
    assert not leaks, leaks
    assert "Private only:" not in text, text


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_both_reports_read_the_folder_after_a_launcher_named_like_the_flag_c3104(
    env: dict[str, str],  # noqa: F811
    locale: str,
) -> None:
    """B13 round 46 C3104 (ruling 199; privacy): a launcher named `Project --source-local` is logged as
    `~/Project\\ --source-local`; the agentsync report's unanchored match began at its escaped space, took the
    real `--source-local` for its operand and printed the logged folder in clear, where the shell's `read -a`
    reads it. Both read the folder after the real flag and print the same rows."""
    log = install_log_path(env)
    log.parent.mkdir(parents=True, exist_ok=True)
    start = "2026-11-24T10:00:00Z run=20261124T100000Z-4242"
    log.write_text(
        f"{start} start install.sh compat=42 kind=checkout source=- args="
        "--launcher ~/Project\\ --source-local --source-local "
        "~/Library/CloudStorage/OneDrive-Contoso/Kestrel\\ Ledger\n"
        f"{start} end rc=2 seconds=1\n",
        encoding="utf-8",
    )
    said = ["picked the Kestrel Ledger folder"]
    shell, python, shell_private, python_private = _both_reports(env, ["Project Alpha"], said, locale)
    assert re.fullmatch(r"picked the <folder-\d+> folder", python[0]), python
    assert shell == python and shell_private == python_private, (shell, python)


# ---- B13 round 46 lane RB: ruling 199's shell halves -------------------------------------------------------


def _git_remote(repo: Path, url: str) -> None:
    """A checkout at ``repo`` whose .git/config names ``url`` as origin."""
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {url}\n', encoding="utf-8")


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("unknown_key", [False, True])
def test_both_reports_read_the_configured_docs_repos_remote_c3054(
    env: dict[str, str],  # noqa: F811
    locale: str,
    unknown_key: bool,
) -> None:
    """B13 round 46 ruling 199, C3054 (privacy): the shell read only ~/agent-context/docs's git remotes, so a
    docs repo moved by `docs_repo` printed its remote's host and team path in clear in a public shell report
    while the agentsync report cut them. The shell reads the configured docs repo in place of the default,
    never beside it: a leftover ~/agent-context/docs naming another host moves no number, and both reports
    print one line, a loaded config and one with an unknown key (read raw) alike."""
    home = Path(env["HOME"])
    _git_remote(home / "Work" / "kb" / "docs", "https://git.wombatcorp.example/kestrel-team/kb.git")
    _git_remote(home / "agent-context" / "docs", "https://git.quokka-labs.example/zephyr/kb.git")
    config = home / "agent-context" / "sources.toml"
    extra = "bogus = 1\n" if unknown_key else ""
    body = f'{extra}[agentsync]\ndocs_repo = "~/Work/kb/docs"\n' + default_config_text()
    texts: list[str] = []
    said = ["fatal: unable to access 'https://git.wombatcorp.example/kestrel-team/kb.git/': 403"]
    shell, python, sp, pp = _both_reports(
        env, [], said, locale, config=config, config_body=body, texts_out=texts
    )
    for text in texts:
        leaks = [ln for ln in text.splitlines() if re.search(r"(?i)wombat|kestrel", ln)]
        assert not leaks, leaks
    assert python == ["fatal: unable to access 'https://<proxy-1>/<proxy-2>/': 403"], python
    assert (shell, sp) == (python, pp), (shell, python, sp, pp)


@pytest.mark.parametrize("ch", ["\u3000", "\u65e5\u672c", "\u0085"], ids=["U+3000", "nihon", "U+0085"])
def test_both_reports_cut_a_folder_install_sh_logged_in_the_c_form_ruling_203(
    env: dict[str, str],  # noqa: F811
    wheel: Path,  # noqa: F811
    ch: str,
) -> None:
    """B13 round 46 ruling 203 (C3080): install.log's args= wrote a --source-local folder holding U+3000, a
    CJK name or U+0085 with a lone lead byte under UTF-8, which the agentsync report read as U+FFFD and placed
    as another folder. q_word writes it in the C form, every byte past ASCII an octal escape: both reports
    read the logged folder back to its name and cut it, under either locale, with one verdict. B13 round 47
    C3330: the shell writer is asserted to have run, and each report's args= row is checked."""
    folder = _cloud(env) / "OneDrive-Contoso" / f"Kestrel{ch}Ledger"
    folder.mkdir(parents=True)
    rc, out, err = _install_bytes(
        {**env, "STUB_UV_INSTALL_RC": "2"}, str(wheel), "--source-local", str(folder)
    )
    assert rc == 1, out + err
    log = install_log_path(env).read_bytes().decode("utf-8")
    assert "Kestrel\\" in log, log
    texts = [report_path(env).read_text(encoding="utf-8")]
    # B13 round 47 C3330: the shell writer ran (an `or` passed on the agentsync render alone), and each
    # report's args= row is checked by its own writer's form
    assert "- written by: scripts/install.sh shell fallback (" in texts[0], texts[0][:400]
    assert "(shell fallback:" in out.decode("utf-8"), out
    for locale in ("C", "en_US.UTF-8"):
        dest = Path(env["HOME"]) / f"agentsync-report-{locale}.md"
        cp = subprocess.run(
            [sys.executable, "-m", "agentsync", "setup-report", "--out", str(dest)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env={**env, "LC_ALL": locale, "LANG": locale, "AGENTSYNC_OCR": "0", "AGENTSYNC_TM_EXCLUDE": "0"},
            stdin=subprocess.DEVNULL,
        )
        assert cp.returncode == 0, cp.stdout + cp.stderr
        texts.append(dest.read_text(encoding="utf-8"))
    for text in texts:
        leaks = [ln for ln in text.splitlines() if "Kestrel" in ln or "Ledger" in ln]
        assert not leaks, leaks
    for n, text in enumerate(texts):
        row = [ln for ln in text.splitlines() if " start install.sh " in ln and " args=" in ln]
        assert len(row) == 1, (n, row)
        if n == 0:
            assert row[0].endswith(" --source-local <path>"), row
        else:
            shown = r' --source-local "~/Library/CloudStorage/OneDrive-<org-\d+>/<path>"$'
            assert re.search(shown, row[0]), row
    assert len({"Private only:" in t for t in texts}) == 1, [t[-600:] for t in texts]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("name", ["Kestrel Vault", "Kestrel\x01Vault"])
def test_both_reports_cut_a_folder_doctor_writes_unquoted_in_prose_c3008(
    env: dict[str, str],  # noqa: F811
    name: str,
    locale: str,
) -> None:
    """B13 round 46 C3008 (lane B's doctor._prose_path; this row is lane RA's): doctor's prose now writes a
    folder unquoted (`search access to ~/Kestrel Vault, a folder above ~/Kestrel Vault/drop`) unless
    arm_local._LIST_ESCAPED_RE matches, then the `$'..'` form, where it wrote `~/'Kestrel Vault'`; command
    words keep the quote. Both reports still cut the folder in each form, a blank and a control character
    among them, print the same rows and reach one verdict. B13 round 47 C3315: the words are doctor's own,
    built by doctor._prose_path and _shell_path under the test HOME, where the row hand-spelled
    `~/$'Kestrel\\001Vault'/drop`, a spelling doctor never writes (kept as a last row, an agent's own); the
    rows are counted and the escaped form's are pinned."""
    home = Path(env["HOME"])
    drop = home / name / "drop"
    drop.mkdir(parents=True)
    body = default_config_text() + local_source_table("vault-drop", drop)
    escaped = arm_local._LIST_ESCAPED_RE.search(name) is not None
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", env["HOME"])
        above, root = doctor._prose_path(home / name), doctor._prose_path(drop)
        quoted = doctor._shell_path(home / name)
    if escaped:  # doctor's $'..' word holds the whole path below the home, never a raw control character
        assert root == "~/$'Kestrel\\001Vault/drop'" and above == quoted == "~/$'Kestrel\\001Vault'", root
    said = [
        f"the person gives this account search access to {above}, a folder above {root} that refuses "
        f"it one, then run the same command again; chmod u+rwx {quoted} gives it",
        f"if {root} is meant to be empty, set state = paused",
        *(["the agent wrote ~/$'Kestrel\\001Vault'/drop itself, not doctor"] if escaped else []),
    ]
    texts: list[str] = []
    shell, python, shell_private, python_private = _both_reports(
        env,
        [],
        said,
        locale,
        config=home / "agent-context" / "sources.toml",
        config_body=body,
        texts_out=texts,
    )
    for report, rows in (("shell", shell), ("agentsync", python)):
        leaks = [ln for ln in rows if "Kestrel" in ln or "Vault" in ln]
        assert not leaks, (report, leaks)
    assert len(shell) == len(python) == len(said), (shell, python)
    if escaped:
        assert "if ~/<path> is meant to be empty, set state = paused" in python, python
    assert shell == python and shell_private == python_private, (shell, python)
