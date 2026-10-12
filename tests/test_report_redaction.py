"""The setup report's redaction, in both reports: ``agentsync setup-report`` (``setup_report.Redactor`` and
``_build_redactor``) and ``scripts/install.sh``'s shell fallback (``redaction_pairs``, ``redact_stream``).
Every case is tested in both, with made-up names (B13 round 3, CONTRACTS §16.46)."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

from agentsync import cli, setup_report
from test_install_oneshot import (  # noqa: F401  (fixture)
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
