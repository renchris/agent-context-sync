"""``agentsync it-request``: docs/deploy/it-request.md rendered into an email draft for this Mac (judge
finding J10). Nothing here runs ``id``, ``system_profiler`` or reads the real home folder: facts are injected,
and the CLI tests run with HOME in a temporary folder and a stubbed command runner."""

from __future__ import annotations

import json
import re
import stat
import tomllib
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from agentsync import cli, it_request
from agentsync.it_request import MacFacts, Template

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / it_request.PAGE_REL
ENTRA = ROOT / it_request.ENTRA_REL
FACTS = MacFacts(
    requester_name="Jane Doe",
    serial="C02XY0000000",
    arch="arm64",
    orgs=("Contoso",),
    arms="local: 2 sources",
    today=date(2026, 10, 1),
)


def template() -> Template:
    return Template(PAGE.read_text(encoding="utf-8"), ENTRA.read_text(encoding="utf-8"), "test")


def test_fillers_are_exactly_the_command_rows_of_the_page_table() -> None:
    """The page's table is the specification: every row whose source is a command is filled, and nothing
    else is (a new command row without a filler, or a filler for a person's field, fails here)."""
    table = it_request.placeholder_table(PAGE.read_text(encoding="utf-8"))
    assert {p.name for p in table if p.by_command} == set(it_request.FILLERS)
    assert {p.name for p in table if p.by_person} == {
        "<it-contact>",
        "<requester-upn>",
        "<team>",
        "<wanted-by>",
    }
    assert {p.name for p in table if p.by_it} == {"<it-owner>", "<tenant-id>", "<app-client-id>"}


def test_draft_lists_the_open_fields_first_and_fills_the_rest() -> None:
    draft = it_request.render(template(), FACTS)
    lines = draft.text.splitlines()
    assert lines[0] == "You fill: <it-contact>, <requester-upn>, <team>, <wanted-by>"
    assert lines[1] == "IT fills (leave them as they are): <it-owner>, <tenant-id>, <app-client-id>"
    assert draft.you_fill == ["<it-contact>", "<requester-upn>", "<team>", "<wanted-by>"]
    assert draft.filled == {
        "<requester-name>": "Jane Doe",
        "<serial>": "C02XY0000000",
        "<arch>": "arm64",
        "<org>": "Contoso",
        "<arms-today>": "local: 2 sources",
        "<date>": "2026-10-01",
    }
    assert "nothing has been sent" in draft.text
    head = draft.text.split("\n---\n", 1)[0]
    assert "Filled from this Mac: requester-name = Jane Doe, serial = C02XY0000000," in head
    for name in draft.filled:
        assert name not in head, (
            f"{name}: a filled value is named without its placeholder, so no count sees it open"
        )
    to = lines.index("To: <it-contact>")
    assert lines[to + 1] == ("Subject: agentsync on a managed Mac: Entra app registration and admin consent")
    assert lines[to + 2] == ""
    assert (
        "**Requester:** Jane Doe, `<requester-upn>`, `<team>`. **Device:** C02XY0000000 (arm64 Mac)." in lines
    )
    assert "**Written:** 2026-10-01. **Wanted by:** `<wanted-by>`." in lines
    assert "(on this Mac: local: 2 sources)" in " ".join(lines)
    for name in draft.filled:
        assert name not in draft.text.split("\n---\n", 1)[1], f"{name} left in the body"
    body = draft.text.split("\n---\n", 1)[1]
    assert "| Placeholder | Meaning |" not in body, "the placeholder table is not part of the email"
    assert "## After IT replies" not in body, "the operator section is not part of the email"
    assert "**To:**" not in body


def test_draft_links_are_absolute_and_the_manifest_is_filled_json() -> None:
    body = it_request.render(template(), FACTS).text.split("\n---\n", 1)[1]
    fences = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
    targets = re.findall(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)\)", fences.sub("", body))
    assert targets, "the page has links"
    assert all(t.startswith("https://") for t in targets), targets
    assert f"{it_request.BLOB_BASE}docs/deploy/entra-app.json" in targets
    assert f"{it_request.BLOB_BASE}docs/deploy/mdm/README.md" in targets
    assert f"{it_request.BLOB_BASE}docs/design/receipts/verify/C15-corporate-controls.md" in targets
    for t in targets:
        path = t.removeprefix(it_request.BLOB_BASE).partition("#")[0]
        if t.startswith(it_request.BLOB_BASE):
            assert (ROOT / path).exists(), t
    [block] = re.findall(r"^```json\n(.*?)^```", body, re.MULTILINE | re.DOTALL)
    app: dict[str, Any] = json.loads(block)
    source: dict[str, Any] = json.loads(ENTRA.read_text(encoding="utf-8"))
    assert app["displayName"] == "agentsync (Contoso)"
    assert "Owner: <team>." in app["notes"], "the team is the person's to fill"
    assert app["requiredResourceAccess"] == source["requiredResourceAccess"]
    assert "byte-identical" not in body, "a filled copy is not byte-identical to entra-app.json"
    assert "with this request's values filled in" in body


def test_values_are_json_escaped_inside_the_manifest() -> None:
    facts = MacFacts(orgs=('Contoso "EU" \\ Ltd',), today=date(2026, 10, 1))
    body = it_request.render(template(), facts).text.split("\n---\n", 1)[1]
    [block] = re.findall(r"^```json\n(.*?)^```", body, re.MULTILINE | re.DOTALL)
    assert json.loads(block)["displayName"] == 'agentsync (Contoso "EU" \\ Ltd)'


def test_unknown_values_stay_open_and_two_organisations_are_the_persons_choice() -> None:
    facts = MacFacts(orgs=("Contoso", "Fabrikam"), today=date(2026, 10, 1))
    draft = it_request.render(template(), facts)
    assert draft.you_fill == [
        "<it-contact>",
        "<requester-name>",
        "<requester-upn>",
        "<team>",
        "<serial>",
        "<arch>",
        "<org>",
        "<arms-today>",
        "<wanted-by>",
    ]
    assert draft.notes == ["<org>: this Mac syncs more than one organisation (Contoso, Fabrikam); pick one"]
    assert draft.notes[0] in draft.text.split("\n---\n", 1)[0]


def test_find_orgs_prefers_the_configured_folders(tmp_path: Path) -> None:
    cloud = tmp_path / "CloudStorage"
    for name in ("OneDrive-Personal", "OneDrive-Fabrikam", "OneDrive-SharedLibraries-Contoso", "Dropbox"):
        (cloud / name).mkdir(parents=True)
    assert it_request.find_orgs(cloud) == ("Fabrikam", "Contoso")
    src = cloud / "OneDrive-SharedLibraries-Contoso" / "Team - Documents"
    assert it_request.find_orgs(cloud, [src]) == ("Contoso",)
    assert it_request.find_orgs(tmp_path / "missing") == ()
    assert it_request.org_of("OneDrive-Personal") is None and it_request.org_of("iCloud Drive") is None


def test_gather_facts_reads_only_what_the_table_names(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(argv: Any) -> str | None:
        calls.append(list(argv))
        if argv[0] == "id":
            return "Jane Doe\n"
        return "Hardware:\n\n    Hardware Overview:\n      Serial Number (system): C02XY0000000\n"

    (tmp_path / "OneDrive-Contoso").mkdir()
    facts = it_request.gather_facts(
        kinds=["local", "local", "inbox"], cloud_root=tmp_path, run=run, today=date(2026, 10, 1)
    )
    assert calls == [["id", "-F"], ["system_profiler", "SPHardwareDataType"]]
    assert (facts.requester_name, facts.serial, facts.org) == ("Jane Doe", "C02XY0000000", "Contoso")
    assert facts.arms == "local: 2 sources, inbox: 1 source"
    assert it_request.gather_facts(kinds=None, cloud_root=tmp_path, run=lambda _a: None).arms is None
    assert it_request.describe_kinds([]) == "no sources configured yet"


def test_template_comes_from_the_checkout_else_the_packaged_copy(tmp_path: Path) -> None:
    got = it_request.load_template([tmp_path / "nothing", ROOT])
    assert got.page == PAGE.read_text(encoding="utf-8") and got.origin == str(PAGE)
    assert ROOT in it_request.checkout_candidates()


def test_the_wheel_ships_the_page_and_manifest() -> None:
    """A wheel installed without its checkout still renders: pyproject force-includes both files where
    load_template's packaged fallback reads them."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    for rel, name in ((it_request.PAGE_REL, "it-request.md"), (it_request.ENTRA_REL, "entra-app.json")):
        assert include[rel] == f"agentsync/{it_request.PACKAGE_DATA_DIR}/{name}"


def test_write_draft_is_0600_and_keeps_a_different_earlier_draft(tmp_path: Path) -> None:
    out = tmp_path / "ctx" / "draft.md"
    assert it_request.write_draft(out, "one\n") is None
    assert stat.S_IMODE(out.stat().st_mode) == 0o600 and out.read_text() == "one\n"
    assert it_request.write_draft(out, "one\n") is None, "an identical draft is left as it is"
    backup = it_request.write_draft(out, "two\n", now=datetime(2026, 10, 1, 9, 30))
    assert backup == tmp_path / "ctx" / "draft.md.20261001T093000.bak"
    assert backup.read_text() == "one\n" and out.read_text() == "two\n"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_refused_location_is_canonical(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(repo)
    assert it_request.refused_location(repo / "x.md", [repo]) == repo
    assert it_request.refused_location(link / "docs" / "x.md", [repo]) == repo
    assert it_request.refused_location(tmp_path / "x.md", [repo]) is None


# ---- the CLI --------------------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    (h / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("AGENTSYNC_CONFIG", raising=False)

    def run(argv: Any) -> str | None:
        return "Jane Doe\n" if argv[0] == "id" else "Serial Number (system): C02XY0000000\n"

    monkeypatch.setattr(it_request, "run_command", run)
    return h


def _config(home: Path) -> Path:
    folder = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Projects"
    config = home / "agent-context" / "sources.toml"
    assert cli.main(["init", "--config", str(config), "--source-local", str(folder)]) == 0
    return config


def test_cli_writes_the_draft_and_prints_the_open_fields(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(home)
    capsys.readouterr()
    out = home / "agent-context" / "it-request-draft.md"
    assert cli.main(["it-request", "--out", str(out), "--config", str(config)]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0].startswith(f"wrote {out} (mode 0600;") and "nothing was sent" in printed[0]
    assert "You fill: <it-contact>, <requester-upn>, <team>, <wanted-by>" in printed
    text = out.read_text(encoding="utf-8")
    assert text.startswith("You fill: <it-contact>, <requester-upn>, <team>, <wanted-by>\n")
    assert "org = Contoso" in text and "arms-today = local: 1 source" in text
    assert "requester-name = Jane Doe" in text and "serial = C02XY0000000" in text
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_cli_without_sources_toml_says_none_configured(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = home / "draft.md"
    assert cli.main(["it-request", "--out", str(out)]) == 0
    assert "arms-today = no sources configured yet" in out.read_text(encoding="utf-8")


def test_cli_refuses_the_checkout_and_the_docs_repo(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(home)
    capsys.readouterr()
    for out in (ROOT / "it-request-draft.md", home / "agent-context" / "docs" / "draft.md"):
        assert cli.main(["it-request", "--out", str(out), "--config", str(config)]) == 2
        assert "is inside" in capsys.readouterr().err
        assert not out.exists()


def test_cli_requires_out() -> None:
    assert cli.main(["it-request"]) == 2
