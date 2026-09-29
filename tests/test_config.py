from __future__ import annotations

from pathlib import Path

import pytest

from agentsync.config import (
    DEFAULT_EXCLUDES,
    TEAMS_SCOPE,
    BreakerConfig,
    Config,
    default_config_text,
    load_config,
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


def test_template_examples_all_parse_when_uncommented(tmp_path: Path) -> None:
    lines = []
    for line in default_config_text().splitlines():
        stripped = line.lstrip()
        if stripped.startswith("# ") and ("=" in stripped or stripped.startswith("# [[source]]")):
            candidate = stripped[2:]
            if candidate.startswith(
                (
                    "[[source]]",
                    "id",
                    "kind",
                    "path",
                    "site",
                    "folder",
                    "state",
                    "mailbox",
                    "team_id",
                    "channel_id",
                    "include",
                    "exclude",
                    "max_",
                    "sentinel",
                    "quiescence_s",
                    "client_id",
                )
            ):
                lines.append(candidate)
                continue
        lines.append(line)
    cfg = parse("\n".join(lines), tmp_path)
    kinds = [s.kind for s in cfg.sources]
    assert kinds == [
        SourceKind.LOCAL,
        SourceKind.INBOX,
        SourceKind.GRAPH_DRIVE,
        SourceKind.GRAPH_MAIL,
        SourceKind.GRAPH_TEAMS,
    ]
    drive = cfg.source("finance-library")
    assert drive.state is SourceState.PAUSED
    assert drive.folder == "/Shared Documents/FY26"
    assert cfg.source("onedrive-projects").max_materialise_bytes == 1024**3
    assert TEAMS_SCOPE in cfg.graph_scopes()


def test_sample_config_fixture(sample_config: Config, local_source_dir: Path) -> None:
    src = sample_config.source("local-fixture")
    assert src.kind is SourceKind.LOCAL
    assert src.path == local_source_dir
    assert src.max_materialise_bytes == 50_000_000
    assert src.exclude == DEFAULT_EXCLUDES
    assert sample_config.layout.mirror == sample_config.docs_repo / "mirror"
    assert sample_config.state_paths.db.name == "manifest.sqlite"


def test_load_config_missing_file_names_init(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="agentsync init"):
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
