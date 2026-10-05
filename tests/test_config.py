from __future__ import annotations

from pathlib import Path

import pytest

from agentsync.config import (
    DEFAULT_EXCLUDES,
    SOURCE_ID_RE,
    TEAMS_SCOPE,
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
    line = 'docs_repo = "~/agent-context/docs"'
    text = default_config_text().replace(line, f'docs_repo = "{tmp_path / "ctx" / "docs"}"', 1)
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
