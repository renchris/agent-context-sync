"""The setup report's redaction, in both reports: ``agentsync setup-report`` (``setup_report.Redactor`` and
``_build_redactor``) and ``scripts/install.sh``'s shell fallback (``redaction_pairs``, ``redact_stream``).
Every case is tested in both, with made-up names (B13 round 3, CONTRACTS §16.46)."""

from __future__ import annotations

import re
import shlex
import subprocess
import unicodedata
from pathlib import Path

import pytest

from agentsync import cli, net, setup_report
from agentsync.config import load_config
from agentsync.text import safe_text
from test_install_oneshot import (  # noqa: F401  (fixture)
    _C1253_ROWS,
    _C1285_ROWS,
    BASH32,
    INSTALL_SH,
    _fuzzy_report,
    _shell_cut_stdout,
    _stub_id,
    _write_exe,
    env,
    install_sh,
    report_path,
)
from test_setup_report import (  # noqa: F401  (fixtures)
    _REAL_FULL_NAME,
    _RULING_102_LEAKS,
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
    assert "project-alpha" not in text and "<source-1>" not in text, friction
    assert 'id = "<folder-1>"' in friction and "exclude-empty <folder-1> |" in friction, friction
    assert "OneDrive-<org-1>/<folder-1>; <source-3> kept" in friction, "not a cloud folder: its own number"
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
    assert "| <folder-1> and <source-2> |" in friction, friction


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
        assert re.search(r"OneDrive-<org-1>/<folder-\d+>\n", text), text


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
            r"^(?:CUT_FIXED|CUT_DRIVE|CUT_HOME|REFUSED_HEADS|RECUT_NOTE)=.*?$",
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


def _r126_lines(n: int, name: str) -> list[str]:
    """Each line form ruling 126 names, for the folder ``name`` (source s<n>) under OneDrive-Contoso: the
    source-local line, doctor's listable detail and fix, each refusal head, the not-a-folder line and a
    friction row in safe_text's spelling."""
    shown = setup_report._LIST_ESCAPED_RE.sub(lambda m: setup_report._LIST_ESCAPES.get(m.group(), "?"), name)
    root = "~/Library/CloudStorage/OneDrive-Contoso"
    ansi = setup_report._ansi_c_body(name, setup_report._DOCTOR_LETTERS)
    return [
        f"source-local: {root}/{shown}",
        f"[warn] source.alpha.listable — {root}/{shown} is empty",
        f"open ~/$'Library/CloudStorage/OneDrive-Contoso/{ansi}' in Finder once",
        *(f"usage error: --source-local {root}/{shown}: {head}x" for head in setup_report._REFUSED_HEADS),
        f"usage error: --source-local is not an existing folder: {root}/{shown} (see --help)",
        f"saw {root}/{safe_text(name)}",
    ]


@pytest.mark.parametrize("group", range(0, len(_R126_CHARS), 12))
def test_both_reports_print_a_folder_name_in_no_spelling_ruling_126(
    env: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    group: int,
) -> None:
    """B13 round 38 ruling 126 (C1414 with C1439, C1432; privacy; data integrity): install.sh's source-local
    line printed a folder's name raw, doctor's listable detail and fix printed spellings no alias knew, and
    safe_text's spelling (a format character dropped) was no pair, so a name holding a control, a break or a
    hidden character printed in clear. Each proof name, configured, goes through the shell report and the
    agentsync report in every line form, its list spelling, its `$'...'` word and its safe_text spelling: no
    fragment of any name prints, and both reports print the same line."""
    names = [f"Alder{n:02d}{ch}Birch{n:02d}" for n, ch in enumerate(_R126_CHARS)][group : group + 12]
    said = " ;; ".join(line for n, name in enumerate(names) for line in _r126_lines(n, name))
    shell, python = _fuzzy_report(env, monkeypatch, names, said)
    leaks = [(who, line) for who, got in (("shell", shell), ("python", python)) for line in got.split(" ;; ")]
    leaks = [(who, line) for who, line in leaks if re.search("Alder|Birch", line)]
    assert not leaks, (len(leaks), leaks[:3], sorted({(w, x[x.index("Alder") :][:7]) for w, x in leaks}))
    # the shell cuts every `$'...'` word whole, before its pairs, so doctor's fix reads `open ~/<path> ...`
    # there, where the agentsync report keeps the word's quotes around its placeholders
    pairs = list(zip(shell.split(" ;; "), python.split(" ;; "), strict=True))
    fixes = [(a, b) for a, b in pairs if a.startswith("open ")]
    assert fixes and all(a == "open ~/<path> in Finder once" for a, _b in fixes), fixes
    assert all(
        re.fullmatch(r"open ~/\$'Library/CloudStorage/OneDrive-<org-1>/<folder-\d+>' in Finder once", b)
        for _a, b in fixes
    )
    wrong = [(a, b) for a, b in pairs if a != b and not a.startswith("open ")]
    assert not wrong, (len(wrong), wrong[:6])


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
