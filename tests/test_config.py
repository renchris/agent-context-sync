from __future__ import annotations

import dataclasses
import re
import tomllib
from pathlib import Path

import pytest

from agentsync.config import (
    DEFAULT_EXCLUDES,
    SOURCE_ID_RE,
    BreakerConfig,
    Config,
    append_to_config,
    default_config_text,
    derive_source_id,
    ensure_inbox,
    inbox_source_table,
    load_config,
    local_source_table,
    parse_config,
    parse_size,
)
from agentsync.errors import ConfigError
from agentsync.model import SourceKind, SourceState


def parse(text: str, tmp_path: Path) -> Config:
    return parse_config(text, config_path=tmp_path / "sources.toml")


def test_template_parses_with_defaults(tmp_path: Path) -> None:
    cfg = parse(default_config_text(), tmp_path)
    home = Path.home()
    assert cfg.docs_repo == home / "agent-context" / "docs"
    assert cfg.state_dir == home / "Library" / "Application Support" / "agentsync"
    assert cfg.sources == ()
    assert cfg.graph.client_id is None
    assert cfg.graph.tenant == "organizations"
    assert cfg.breaker == BreakerConfig(0.20, 25, 7)
    assert cfg.convert.xlsx_stream_threshold_bytes == 20_000_000
    assert cfg.reconcile_interval_s == 3600


def test_template_has_only_sources(tmp_path: Path) -> None:
    """KISS K15: one header line and a commented ``[governance] archive`` line; every other key keeps its
    default, and add-source appends only ``[[source]]`` tables."""
    text = default_config_text()
    lines = text.splitlines()
    assert lines[0].startswith("# agentsync sources.toml") and "add-source" in lines[0]
    assert [ln for ln in lines if ln and not ln.startswith("#")] == []
    assert "# [governance]" in lines and any(ln.startswith("# archive = true") for ln in lines)
    for gone in ("[agentsync]", "[graph]", "[breaker]", "[convert]", "[network]", "[policy]", "tenant"):
        assert gone not in text, gone
    on = text.replace("# [governance]", "[governance]").replace("# archive = true", "archive = true")
    assert tomllib.loads(on) == {"governance": {"archive": True}}
    folder = tmp_path / "Projects"
    folder.mkdir()
    full = text + local_source_table("projects", folder) + inbox_source_table("inbox", tmp_path / "inbox")
    assert list(tomllib.loads(full)) == ["source"]
    assert [s.kind for s in parse(full, tmp_path).sources] == [SourceKind.LOCAL, SourceKind.INBOX]


def test_graph_examples_in_the_deploy_readme_parse(tmp_path: Path) -> None:
    """KISS K15 moved the Graph examples out of the template into docs/deploy "What needs IT"; appended to a
    config as that section says, they load."""
    readme = (Path(__file__).parents[1] / "docs" / "deploy" / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n## What needs IT\n", 1)[1].split("\n## ", 1)[0]
    (block,) = re.findall(r"```toml\n(.*?)```", section, flags=re.S)
    folder = tmp_path / "Projects"
    folder.mkdir()
    cfg = parse(default_config_text() + local_source_table("projects", folder) + "\n" + block, tmp_path)
    assert cfg.graph.client_id is not None and cfg.graph.tenant not in ("organizations", "common")
    graph = cfg.sources[1:]
    assert [s.kind for s in graph] == [SourceKind.GRAPH_DRIVE, SourceKind.GRAPH_MAIL, SourceKind.GRAPH_TEAMS]
    assert all(s.state is SourceState.PAUSED for s in graph)
    assert cfg.source("finance-library").folder == "/Shared Documents/FY26"


_PRE_K15_TEMPLATE = """\
[agentsync]
docs_repo = "~/agent-context/docs"
state_dir = "~/Library/Application Support/agentsync"
cache_dir = "~/Library/Caches/agentsync"
log_dir = "~/Library/Logs/agentsync"
reconcile_interval_s = 3600
poll_interval_s = 300
tombstone_reap_days = 180
# principal = "you@example.com"

[graph]
# client_id = "00000000-0000-0000-0000-000000000000"
tenant = "organizations"
company = "agentsync"                                 # User-Agent: NONISV|<company>|agentsync/<version>

# [governance]                                        # retention, purge, legal hold (C15 section 7)
#   archive = false

[breaker]                                             # deletion circuit breaker, per source
fraction = 0.20
floor = 25
hold_days = 7

[convert]
xlsx_stream_threshold_bytes = "20MB"
max_rows_per_sheet = 5000
"""
"""The live lines of the sources.toml template before KISS K15 (2026-10-04): every older config has them."""


def test_old_config_shapes_load(tmp_path: Path) -> None:
    """KISS K15: the previous full template, and the live shape an operator's config has grown into, load
    with the same values the short template gives; ``[graph] company`` is recorded (status warns) and nothing
    else changes.  principal, cadence_s and launchd_label_prefix stay parsed."""
    old = parse(_PRE_K15_TEMPLATE, tmp_path)
    new = parse(default_config_text(), tmp_path)
    assert old.graph_company_line == 14  # company = "agentsync"
    assert dataclasses.replace(old, graph_company_line=None) == new
    assert new.graph_company_line is None
    folder = tmp_path / "Projects"
    folder.mkdir()
    live = (
        _PRE_K15_TEMPLATE.replace('# principal = "you@example.com"', 'principal = "ada@contoso.com"')
        .replace('company = "agentsync"', 'company = "Contoso"')
        .replace("reap_days = 180", 'reap_days = 180\nlaunchd_label_prefix = "com.contoso.as"')
        + local_source_table("projects", folder)
        + "cadence_s = 600\n"
        + inbox_source_table("inbox", tmp_path / "inbox")
    )
    cfg = parse(live, tmp_path)
    assert cfg.principal == "ada@contoso.com" and cfg.launchd_label_prefix == "com.contoso.as"
    assert cfg.source("projects").cadence_s == 600 and [s.kind for s in cfg.sources] == [
        SourceKind.LOCAL,
        SourceKind.INBOX,
    ]
    assert cfg.graph_company_line == 15  # one line further down: launchd_label_prefix was added above it
    assert not hasattr(cfg.graph, "company")


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ('[graph]\ntenant = "x"\n  company = "A"\n', 3),
        ('[ "graph" ]  # g\ncompany="A"\n', 2),
        ('graph.company = "A"\n', 1),
        ('[[source]]\nid = "a"\nkind = "inbox"\npath = "/tmp/i"\n\n[graph]\ncompany = "A"\n', 7),
        ('graph = { company = "A" }\n', 0),
        ('[graph]\ntenant = "x"\n', None),
    ],
)
def test_graph_company_line_names_the_line_to_delete(text: str, line: int | None, tmp_path: Path) -> None:
    assert parse(text, tmp_path).graph_company_line == line


def test_sample_config_fixture(sample_config: Config, local_source_dir: Path) -> None:
    src = sample_config.source("local-fixture")
    assert src.kind is SourceKind.LOCAL
    assert src.path == local_source_dir
    assert src.max_materialise_bytes == 50_000_000
    assert src.exclude == DEFAULT_EXCLUDES
    assert sample_config.layout.mirror == sample_config.docs_repo / "mirror"
    assert sample_config.state_paths.db.name == "manifest.sqlite"


def test_load_config_missing_file_names_add_source(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"run `agentsync add-source <folder>` to create it"):
        load_config(tmp_path / "nope.toml")


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("[agentsync]\ndocs_repoo = '/x'\n", "unknown key"),
        ("[[source]]\nid = 'Bad_ID'\nkind = 'local'\npath = '/tmp/x'\n", "id must match"),
        ("[[source]]\nid = 'a'\nkind = 'ftp'\n", "kind must be one of"),
        ("[[source]]\nid = 'a'\nkind = 'local'\n", "missing required key 'path'"),
        ("[[source]]\nid = 'a'\nkind = 'local'\npath = '/tmp/x'\ndrive_id = 'me'\n", "unknown key"),
        (
            "[[source]]\nid = 'a'\nkind = 'local'\npath = '/tmp/a'\n[[source]]\nid = 'a'\nkind = 'local'\n"
            "path = '/tmp/b'\n",
            "duplicate source id",
        ),
        ("[[source]]\nid = 'd'\nkind = 'graph_drive'\nstate = 'paused'\n", "exactly one of"),
        ("[[source]]\nid = 'd'\nkind = 'graph_drive'\ndrive_id = 'me'\n", "client_id is"),
        ("[[source]]\nid = 'd'\nkind = 'local'\npath = '/tmp/x'\nstate = 'retired'\n", "retired_reason"),
        ("[agentsync]\ndocs_repo = '~/Library/CloudStorage/OneDrive-X/docs'\n", "CloudStorage"),
        ("[breaker]\nfraction = 1.5\n", "fraction"),
        ("not toml [", "not valid TOML"),
        ("[[source]]\nid = 'a'\nkind = 'local'\npath = '/tmp/x'\nsentinel = '../up'\n", "sentinel"),
    ],
)
def test_invalid_configs_have_clear_errors(tmp_path: Path, text: str, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        parse(text, tmp_path)


def test_source_inside_docs_repo_rejected(tmp_path: Path) -> None:
    text = (
        f"[agentsync]\ndocs_repo = '{tmp_path}/docs'\n"
        f"[[source]]\nid = 'a'\nkind = 'local'\npath = '{tmp_path}/docs/x'\n"
    )
    with pytest.raises(ConfigError, match="inside docs_repo"):
        parse(text, tmp_path)


def test_paused_graph_source_without_client_id_is_allowed(tmp_path: Path) -> None:
    cfg = parse("[[source]]\nid = 'd'\nkind = 'graph_drive'\ndrive_id = 'me'\nstate = 'paused'\n", tmp_path)
    assert cfg.live_sources() == ()
    assert cfg.source("d").folder == "/"


def test_shared_mailbox_adds_scope(tmp_path: Path) -> None:
    cfg = parse(
        "[graph]\nclient_id = 'x'\n[[source]]\nid = 'm'\nkind = 'graph_mail'\nfolder = 'Inbox'\n"
        "mailbox = 'shared@example.com'\n",
        tmp_path,
    )
    assert "Mail.Read.Shared" in cfg.graph_scopes()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (1024, 1024), ("1KB", 1000), ("1KiB", 1024), ("1.5 GB", 1_500_000_000), ("20MB", 20_000_000)],
)
def test_parse_size(value: object, expected: int) -> None:
    assert parse_size(value, where="x") == expected


@pytest.mark.parametrize("value", [-1, True, "lots", 1.5])
def test_parse_size_rejects(value: object) -> None:
    with pytest.raises(ConfigError):
        parse_size(value, where="x")


def test_breaker_threshold_is_floored_and_scoped() -> None:
    b = BreakerConfig()
    assert b.threshold(40) == 25  # a 40-row pilot does not trip on three real deletions
    assert b.threshold(1000) == 200


@pytest.mark.parametrize(
    ("name", "taken", "expected"),
    [
        ("Projects", set(), "projects"),
        ("My Projects (FY26)", set(), "my-projects-fy26"),
        ("Projects", {"projects"}, "projects-2"),
        ("Projects", {"projects", "projects-2"}, "projects-3"),
        ("!!!", set(), "local"),
        ("Ünïcode Déck", set(), "n-code-d-ck"),
    ],
)
def test_derive_source_id_is_deterministic_and_unique(name: str, taken: set[str], expected: str) -> None:
    sid = derive_source_id(Path("/x") / name, taken)
    assert sid == expected and SOURCE_ID_RE.match(sid)
    assert derive_source_id(Path("/x") / name, taken) == sid  # same input, same id


def test_local_source_table_uses_the_init_defaults(tmp_path: Path) -> None:
    folder = tmp_path / 'we"ird \\ name'
    folder.mkdir()
    cfg = parse(default_config_text() + local_source_table("weird", folder), tmp_path)
    (src,) = cfg.sources
    assert src.id == "weird" and src.kind is SourceKind.LOCAL and src.state is SourceState.LIVE
    assert src.path == folder and src.sentinel is None
    assert src.exclude == DEFAULT_EXCLUDES and src.max_materialise_bytes == 1024**3 and src.max_files == 5000


def test_append_to_config_keeps_every_byte_and_validates_first(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sources.toml"
    original = (
        default_config_text() + '# my own comment, kept\n[network]\nproxy = "direct"'
    )  # no final newline
    cfg_path.write_text(original, encoding="utf-8")
    cfg_path.chmod(0o640)
    folder = tmp_path / "Projects"
    folder.mkdir()
    cfg = append_to_config(cfg_path, local_source_table("projects", folder))
    text = cfg_path.read_text(encoding="utf-8")
    assert text.startswith(original + "\n") and "# my own comment, kept" in text
    assert [s.id for s in cfg.sources] == ["projects"] and cfg.network.proxy == "direct"
    assert load_config(cfg_path) == cfg
    assert cfg_path.stat().st_mode & 0o777 == 0o640  # the file's mode is kept
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Projects", "sources.toml"]  # no temp file left

    with pytest.raises(ConfigError, match="duplicate source id"):
        append_to_config(cfg_path, local_source_table("projects", folder))
    assert cfg_path.read_text(encoding="utf-8") == text  # an invalid result is never written
    with pytest.raises(ConfigError, match="not found"):
        append_to_config(tmp_path / "missing.toml", local_source_table("x", folder))


def _ctx_config(tmp_path: Path, extra: str = "") -> Path:
    """A template sources.toml whose docs repo is ``tmp_path/ctx/docs`` (the inbox goes to ``ctx/inbox``)."""
    cfg_path = tmp_path / "ctx" / "sources.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    text = f'[agentsync]\ndocs_repo = "{tmp_path / "ctx" / "docs"}"\n\n' + default_config_text()
    cfg_path.write_text(text + extra, encoding="utf-8")
    return cfg_path


def test_ensure_inbox_adds_one_inbox_beside_the_docs_repo(tmp_path: Path) -> None:
    cfg_path = _ctx_config(tmp_path)
    before = cfg_path.read_text(encoding="utf-8")
    config, added = ensure_inbox(cfg_path)
    inbox = tmp_path / "ctx" / "inbox"
    assert inbox.is_dir() and inbox.stat().st_mode & 0o777 == 0o700
    assert added is not None and added.id == "inbox" and added.kind is SourceKind.INBOX and added.is_live
    assert added.path == inbox.resolve() and config.sources == (added,)
    text = cfg_path.read_text(encoding="utf-8")
    assert text == before + inbox_source_table("inbox", inbox.resolve())
    assert ensure_inbox(cfg_path) == (config, None)  # idempotent: one kind = "inbox", nothing written
    assert cfg_path.read_text(encoding="utf-8") == text


def test_ensure_inbox_counts_any_inbox_and_never_configures_a_folder_twice(tmp_path: Path) -> None:
    elsewhere = tmp_path / "drop"
    elsewhere.mkdir()
    paused = inbox_source_table("drop", elsewhere).replace(
        'kind = "inbox"\n', 'kind = "inbox"\nstate = "paused"\n'
    )
    cfg_path = _ctx_config(tmp_path, paused)
    assert ensure_inbox(cfg_path)[1] is None  # an inbox in any state counts
    assert not (tmp_path / "ctx" / "inbox").exists()

    inbox = tmp_path / "ctx" / "inbox"
    inbox.mkdir()
    cfg_path = _ctx_config(tmp_path, local_source_table("inbox", inbox))
    assert ensure_inbox(cfg_path)[1] is None  # the folder is already a (local) source
    assert [s.kind for s in load_config(cfg_path).sources] == [SourceKind.LOCAL]

    other = tmp_path / "other"
    other.mkdir()
    cfg_path = _ctx_config(tmp_path, local_source_table("inbox", other))
    added = ensure_inbox(cfg_path)[1]
    assert added is not None and added.id == "inbox-2"  # the id "inbox" is taken

    inbox.rmdir()
    inbox.write_text("not a folder", encoding="utf-8")
    cfg_path = _ctx_config(tmp_path)
    with pytest.raises(FileExistsError):
        ensure_inbox(cfg_path)
    assert load_config(cfg_path).sources == ()
    with pytest.raises(ConfigError, match="not found"):
        ensure_inbox(tmp_path / "missing.toml")
