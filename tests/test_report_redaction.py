"""The setup report's redaction, in both reports: ``agentsync setup-report`` (``setup_report.Redactor`` and
``_build_redactor``) and ``scripts/install.sh``'s shell fallback (``redaction_pairs``, ``redact_stream``).
Every case is tested in both, with made-up names (B13 round 3, CONTRACTS §16.46)."""

from __future__ import annotations

import re
import subprocess
import unicodedata
from pathlib import Path

import pytest

from agentsync import cli, net, setup_report
from agentsync.config import load_config
from test_install_oneshot import (  # noqa: F401  (fixture)
    BASH32,
    INSTALL_SH,
    _stub_id,
    _write_exe,
    env,
    install_sh,
    report_path,
)
from test_setup_report import (  # noqa: F401  (fixtures)
    _REAL_FULL_NAME,
    V5_HAPPY,
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
    assert red.redact(NAME_LINES[2]) == "~/<name>-notes and /Users/<name>/x"


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
    assert "| ~/<name>-notes and /Users/<name>/x |" in text


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
    assert "a configured source id (<source-N>" in text


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
