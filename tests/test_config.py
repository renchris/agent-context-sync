from __future__ import annotations

import contextlib
import dataclasses
import os
import re
import signal
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from agentsync import policy as policy_mod
from agentsync.config import (
    DEFAULT_EXCLUDES,
    MIN_PAGE_BYTES,
    NOT_A_FILE,
    RETIRE_ADVICE,
    SOURCE_ID_RE,
    BreakerConfig,
    Config,
    ConvertConfig,
    append_to_config,
    canonical_source_root,
    default_config_text,
    derive_source_id,
    ensure_inbox,
    inbox_source_table,
    load_config,
    local_source_table,
    parse_config,
    parse_size,
    read_config_bytes,
    set_source_exclude,
    toml_string,
)
from agentsync.errors import ConfigError
from agentsync.governance import load_governance, parse_governance
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
    """KISS K15 moved the Graph examples out of the template into docs/deploy "What needs IT"; added to a
    config as that section says, they load: a new config takes the whole block, a pre-K15 config (which
    already has a ``[graph]`` table) takes ``client_id`` and ``tenant`` into that table, then the
    ``[[source]]`` tables."""
    readme = (Path(__file__).parents[1] / "docs" / "deploy" / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n## What needs IT\n", 1)[1].split("\n## ", 1)[0]
    (block,) = re.findall(r"```toml\n(.*?)```", section, flags=re.S)
    assert "already has a `[graph]` table: put `client_id`\nand `tenant` in that table" in section
    graph_table, sources = block.split("\n\n", 1)
    assert graph_table.startswith("[graph]\n") and "[graph]" not in sources
    keys = "".join(
        line + "\n" for line in graph_table.splitlines() if re.match(r"(client_id|tenant) = ", line)
    )
    folder = tmp_path / "Projects"
    folder.mkdir()
    local = local_source_table("projects", folder) + "\n"
    with pytest.raises(ConfigError):  # appending the whole block to an old config declares [graph] twice
        parse(_PRE_K15_TEMPLATE + local + block, tmp_path)
    old = _PRE_K15_TEMPLATE.replace('tenant = "organizations"', keys + '# tenant = "organizations"')
    for text in (default_config_text() + local + block, old + local + sources):
        cfg = parse(text, tmp_path)
        assert cfg.graph.client_id is not None and cfg.graph.tenant not in ("organizations", "common")
        graph = cfg.sources[1:]
        assert [s.kind for s in graph] == [
            SourceKind.GRAPH_DRIVE,
            SourceKind.GRAPH_MAIL,
            SourceKind.GRAPH_TEAMS,
        ]
        assert all(s.state is SourceState.PAUSED for s in graph)
        assert cfg.source("finance-library").folder == "/Shared Documents/FY26"


_PRE_K15_TEMPLATE = """\
# agentsync — sources.toml
#
# The in-scope set: nothing else in agentsync names a cloud location.  Edit, then run `agentsync doctor`.
# Every key below shows its default; delete a line to keep the default.

[agentsync]
docs_repo = "~/agent-context/docs"                    # its own git repo, OUTSIDE ~/Library/CloudStorage
state_dir = "~/Library/Application Support/agentsync"  # manifest, cursors (0600), lock, heartbeat
cache_dir = "~/Library/Caches/agentsync"              # converter cache, rebuildable, never in git
log_dir = "~/Library/Logs/agentsync"                  # launchd agent logs
reconcile_interval_s = 3600                           # full enumeration cadence (hourly at <= 1e5 items)
poll_interval_s = 300                                 # delta poll cadence
tombstone_reap_days = 180
# principal = "you@example.com"                       # which signed-in identity this mirror is a view of

[graph]
# client_id = "00000000-0000-0000-0000-000000000000"  # Entra app registration (single-tenant public client)
#                                                     # unset = no Graph arms; local sources still work
tenant = "organizations"                              # Graph NEEDS your tenant id (GUID) or verified domain:
#                                                     # organizations/common are refused (AADSTS50194)
# scopes = ["Files.Read.All", "Sites.Read.All", "Mail.Read", "User.Read"]
company = "agentsync"                                 # User-Agent: NONISV|<company>|agentsync/<version>
# cloud = "global"                                    # global | usgov | usgov-dod | china (default: base_url)
# broker = true                                       # sign in via the macOS broker (Company Portal) first
# allow_device_code = false                           # last-resort device-code sign-in, only if IT allows it

# [network]
#   proxy = "http://proxy.example.com:8080"           # default: HTTPS_PROXY, then the macOS manual proxy;
#                                                     # "direct" ignores both.  PAC/WPAD are not evaluated.

# [policy]                                            # sensitivity-label gate (C15 section 4)
#   exclude_label_ids = ["00000000-0000-0000-0000-000000000000"]   # label GUIDs never converted
#   exclude_label_names = ["Highly Confidential"]
#   refuse_unlabelled = false

# [governance]                                        # retention, purge, legal hold (C15 section 7)
#   history_days = 30                                 # compact-history squashes older mirror history
#   allow_remote = false                              # every clone is a copy no purge can reach
#   hold = false                                      # legal/records hold: suspends purge and compaction
#   archive = false                                   # true keeps everything: deleted pages in docs/archive/,
#                                                     # a snapshot/<date> tag per checkpoint, no compaction;
#                                                     # agentsync purge still erases

[breaker]                                             # deletion circuit breaker, per source
fraction = 0.20                                       # trip when deletions > max(fraction * live rows, floor)
floor = 25
hold_days = 7

[convert]
xlsx_stream_threshold_bytes = "20MB"                  # larger workbooks get a schema + sample page + CSV
max_rows_per_sheet = 5000
# pandoc_path = "/opt/homebrew/bin/pandoc"            # default: pypandoc_binary's bundled pandoc

# ---- sources -------------------------------------------------------------------------------------------
# One [[source]] per scope.  id: lowercase letters, digits and '-'; it names docs/mirror/<id>/.
# state: "paused" | "live" | "retired" (retired needs retired_reason).  Budgets are per cycle.

# A folder inside the OneDrive / SharePoint sync client (no IT involvement needed):
# [[source]]
# id = "onedrive-projects"
# kind = "local"
# path = "~/Library/CloudStorage/OneDrive-Contoso/Projects"
# sentinel = "README.txt"                             # positive control: must exist, or the walk is 'unknown'
# include = []                                        # empty = everything
# exclude = ["~$*", "*.tmp", ".~lock.*#", ".DS_Store", "._*"]
# max_materialise_bytes = "1GiB"                      # download budget per cycle: online-only files only
# max_files = 5000

# A manual drag-and-drop inbox:
# [[source]]
# id = "inbox"
# kind = "inbox"
# path = "~/agent-context/inbox"
# quiescence_s = 60                                   # skip files still being written

# A SharePoint document library or OneDrive via Graph delta (needs [graph] client_id):
# [[source]]
# id = "finance-library"
# kind = "graph_drive"
# site = "contoso.sharepoint.com:/sites/finance"      # or: drive_id = "b!..." ; or: drive_id = "me"
# folder = "/Shared Documents/FY26"                   # subtree filter, "/" = whole drive
# state = "paused"                                    # first pass is a full enumeration; flip to live

# An Outlook mail folder via Graph message delta:
# [[source]]
# id = "mail-projects"
# kind = "graph_mail"
# mailbox = "me"                                      # or a shared mailbox UPN (adds Mail.Read.Shared)
# folder = "Inbox"                                    # well-known name or folder id

# A Teams channel's messages via channel delta (ChannelMessage.Read.All needs admin consent):
# [[source]]
# id = "team-acme-general"
# kind = "graph_teams"
# team_id = "..."
# channel_id = "19:...@thread.tacv2"
"""
"""The sources.toml template before KISS K15 (2026-10-04), verbatim (``_TEMPLATE`` in
``git show e49e212^:src/agentsync/config.py``): every older config started as this text, so line 22 is the
line their status WARN names."""


def test_old_config_shapes_load(tmp_path: Path) -> None:
    """KISS K15: the previous full template, and the live shape an operator's config has grown into, load
    with the same values the short template gives; ``[graph] company`` is recorded (status warns) and nothing
    else changes.  principal, cadence_s and launchd_label_prefix stay parsed, and so do the keys a real
    operator config carries: an uncommented ``[governance] archive`` and a source's exclude, sentinel and
    max_materialise_bytes."""
    old = parse(_PRE_K15_TEMPLATE, tmp_path)
    new = parse(default_config_text(), tmp_path)
    assert old.graph_company_line == 22  # company = "agentsync"
    assert dataclasses.replace(old, graph_company_line=None) == new
    assert new.graph_company_line is None
    folder = tmp_path / "Projects"
    folder.mkdir()
    live = (
        _PRE_K15_TEMPLATE.replace('# principal = "you@example.com"', 'principal = "ada@contoso.com"')
        .replace('company = "agentsync"', 'company = "Contoso"')
        .replace("reap_days = 180", 'reap_days = 180\nlaunchd_label_prefix = "com.contoso.as"')
        .replace("# [governance]", "[governance]")
        .replace("#   archive = false", "archive = true")
        + local_source_table("projects", folder)
        + "cadence_s = 600\n"
        + 'exclude = ["*.tmp", "~$*"]\nsentinel = "README.txt"\nmax_materialise_bytes = "1GB"\n'
        + inbox_source_table("inbox", tmp_path / "inbox")
    )
    cfg = parse(live, tmp_path)
    assert cfg.principal == "ada@contoso.com" and cfg.launchd_label_prefix == "com.contoso.as"
    assert cfg.source("projects").cadence_s == 600 and [s.kind for s in cfg.sources] == [
        SourceKind.LOCAL,
        SourceKind.INBOX,
    ]
    projects = cfg.source("projects")
    assert projects.exclude == ("*.tmp", "~$*") and projects.sentinel == "README.txt"
    assert projects.max_materialise_bytes == 1000**3
    assert parse_governance(tomllib.loads(live)).archive is True
    assert cfg.graph_company_line == 23  # one line further down: launchd_label_prefix was added above it
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


def test_convert_recordings_is_one_boolean_key_that_defaults_to_on(tmp_path: Path) -> None:
    """Spec section 6 and lead decision L1: recordings are converted unless the key turns them off."""
    assert parse("", tmp_path).convert.recordings is True
    assert parse("[convert]\nrecordings = false\n", tmp_path).convert.recordings is False
    with pytest.raises(ConfigError, match=r"\[convert\]: 'recordings' must be true or false, got 'off'"):
        parse('[convert]\nrecordings = "off"\n', tmp_path)
    with pytest.raises(ConfigError, match="unknown key"):
        parse("[convert]\nrecording = false\n", tmp_path)


def test_convert_ocr_is_one_boolean_key_that_defaults_to_on(tmp_path: Path) -> None:
    """Decision D2: the one switch a LaunchAgent run sees too.  Languages and page caps are constants."""
    assert parse("", tmp_path).convert.ocr is True
    assert parse("[convert]\nocr = false\n", tmp_path).convert.ocr is False
    with pytest.raises(ConfigError, match=r"\[convert\]: 'ocr' must be true or false, got 'off'"):
        parse('[convert]\nocr = "off"\n', tmp_path)
    for dropped in ('ocr_languages = ["en-US"]', "ocr_max_pages = 7"):
        with pytest.raises(ConfigError, match="unknown key"):
            parse(f"[convert]\n{dropped}\n", tmp_path)


def test_a_page_cap_under_8_kib_is_refused_at_load_and_the_default_and_8kib_load_c1886(
    tmp_path: Path,
) -> None:
    """B13 round 40 C1886 with C1888 (ruling 140): parse_size took any size, and below 2,048 bytes the CSV
    table's 1,024-byte budget floor made a 1,346-byte page at caps up to 1,345, so `max_page_bytes` was no
    hard cap. A [convert] max_page_bytes under MIN_PAGE_BYTES (8 KiB) is a ConfigError at load naming the
    minimum; the default and `8KiB` load as before. A ConvertConfig built in code is not checked."""
    assert MIN_PAGE_BYTES == 8192
    assert parse("", tmp_path).convert.max_page_bytes == 1_000_000
    assert parse('[convert]\nmax_page_bytes = "8KiB"\n', tmp_path).convert.max_page_bytes == 8192
    assert parse("[convert]\nmax_page_bytes = 8192\n", tmp_path).convert.max_page_bytes == 8192
    for low in ("0", "1024", "2000", "8191", '"1KiB"'):
        got = r"\[convert\]: max_page_bytes must be at least 8 KiB \(8192\), got \d+$"
        with pytest.raises(ConfigError, match=got):
            parse(f"[convert]\nmax_page_bytes = {low}\n", tmp_path)
    assert ConvertConfig(max_page_bytes=400).max_page_bytes == 400


def test_sample_config_fixture(sample_config: Config, local_source_dir: Path) -> None:
    src = sample_config.source("local-fixture")
    assert src.kind is SourceKind.LOCAL
    assert src.path == local_source_dir
    assert src.max_materialise_bytes == 50_000_000
    assert src.exclude == DEFAULT_EXCLUDES
    assert sample_config.layout.mirror == sample_config.docs_repo / "mirror"
    assert sample_config.state_paths.db.name == "manifest.sqlite"


_GOOD_CONFIG = "# Contoso sources.toml\n[governance]\narchive = false\n"


def _config_entry(tmp_path: Path, kind: str) -> Path:
    """sources.toml as one kind of entry a config reader can meet (B13 round 47's entry-kind rule)."""
    good = tmp_path / "real.toml"
    good.write_text(_GOOD_CONFIG, encoding="utf-8")
    cfg = tmp_path / "sources.toml"
    if kind == "file":
        cfg.write_text(_GOOD_CONFIG, encoding="utf-8")
    elif kind == "link to a file":
        cfg.symlink_to(good)
    elif kind == "folder":
        cfg.mkdir()
    elif kind == "fifo":
        os.mkfifo(cfg)
    elif kind == "link to a fifo":
        os.mkfifo(tmp_path / "pipe")
        cfg.symlink_to(tmp_path / "pipe")
    elif kind == "dangling link":
        cfg.symlink_to(tmp_path / "gone.toml")
    elif kind == "device":
        cfg.symlink_to("/dev/null")
    return cfg


@contextlib.contextmanager
def _bounded(seconds: int) -> Iterator[None]:
    """Fail, never hang, if a read blocks (a plain open of a FIFO waits for a writer forever)."""

    def hung(*_: object) -> None:
        raise TimeoutError("a config read blocked")

    old = signal.signal(signal.SIGALRM, hung)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


_READERS: dict[str, Callable[[Path], Any]] = {
    "load_config": load_config,
    "append_to_config": lambda p: append_to_config(p, "# kept\n"),
    "set_source_exclude": lambda p: set_source_exclude(p, "contoso", ()),
    "load_governance": load_governance,
    "policy": lambda p: policy_mod._policy_from_file(p, required=False),
}


@pytest.mark.parametrize("reader", sorted(_READERS))
@pytest.mark.parametrize(
    "kind", ["file", "link to a file", "folder", "fifo", "link to a fifo", "dangling link", "device"]
)
def test_every_config_reader_returns_on_every_kind_of_entry_c3307(
    tmp_path: Path, reader: str, kind: str
) -> None:
    """B13 round 47 C3307 with C3251, C3254 and C3290: load_config, append_to_config, the exclude editor,
    load_governance and policy's _policy_from_file read sources.toml with a plain open, which waits forever
    on a FIFO, so status, sync, add-source and setup-report all hung. Every one reads through
    config.read_config_bytes, so each returns or raises ConfigError on every kind of entry, under a timeout;
    a FIFO, a link to one and a device are `not a file`, a folder keeps `Is a directory`, and a link to a real
    file reads as the file."""
    cfg = _config_entry(tmp_path, kind)
    with _bounded(10):
        try:
            _READERS[reader](cfg)
        except (ConfigError, IsADirectoryError) as exc:  # load_governance's folder stays the open's own error
            got: str | None = str(exc)
        else:
            got = None
    if kind in ("fifo", "link to a fifo", "device"):
        assert got is not None and NOT_A_FILE in got, got  # policy keeps its own `cannot read the ... table`
    elif kind == "folder":
        assert got is not None and "Is a directory" in got, got
    elif kind == "dangling link":
        assert reader in ("load_governance", "policy") or (got is not None and "not found" in got), got
    elif reader != "set_source_exclude":  # the file holds no source contoso, so the editor refuses it
        assert got is None, got


def test_read_config_bytes_follows_a_link_and_refuses_a_fifo_by_its_descriptor_c3307(tmp_path: Path) -> None:
    """B13 round 47 C3307 (C3254: links followed, never lstat): a symlinked sources.toml is a working
    setup."""
    assert read_config_bytes(_config_entry(tmp_path, "link to a file")) == _GOOD_CONFIG.encode()
    (tmp_path / "f").mkdir()
    with _bounded(10), pytest.raises(OSError, match="not a file"):
        read_config_bytes(_config_entry(tmp_path / "f", "fifo"))


@pytest.mark.parametrize(
    "kind",
    [
        "folder",
        "file",
        "link to a folder",
        "link to a file",
        "dangling link",
        "link to itself",
        "absolute link to itself",
        "two-link loop",
        "link through a file",
        "fifo",
        "folder this account cannot search",
        "link into a folder this account cannot search",
    ],
)
def test_canonical_source_root_gives_one_answer_from_every_spelling_c3300(tmp_path: Path, kind: str) -> None:
    """B13 round 47 C3300 (ruling 210): canonical_source_root stopped a loop at a fixed depth, so from a home
    reached through a link (``/tmp`` is ``/private/tmp``) it spent a level on that link and returned another
    link of the loop than from the canonical spelling, and every reader's path match failed. It stops at the
    first repeated link, as posixpath.realpath does, never Path.resolve() (RuntimeError on 3.11 and 3.12):
    over every kind of entry, under a timeout, both spellings give realpath's answer and nothing is raised."""
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "home-link").symlink_to(home)
    entry = home / "inbox"
    locked: Path | None = None
    if kind == "folder":
        entry.mkdir()
    elif kind == "file":
        entry.write_text("contoso\n", encoding="utf-8")
    elif kind == "link to a folder":
        (home / "drop").mkdir()
        entry.symlink_to(home / "drop")
    elif kind in ("link to a file", "link through a file"):
        (home / "note.txt").write_text("x\n", encoding="utf-8")
        entry.symlink_to(home / "note.txt" / ("sub" if kind == "link through a file" else ""))
    elif kind == "dangling link":
        entry.symlink_to(home / "missing")
    elif kind == "link to itself":
        entry.symlink_to("inbox")
    elif kind == "absolute link to itself":
        entry.symlink_to(tmp_path / "home-link" / "inbox")
    elif kind == "two-link loop":
        (home / "loop").symlink_to(tmp_path / "home-link" / "inbox")
        entry.symlink_to(tmp_path / "home-link" / "loop")
    elif kind == "fifo":
        os.mkfifo(entry)
    elif kind == "folder this account cannot search":
        entry.mkdir()
        locked = entry
    else:
        (home / "locked" / "sub").mkdir(parents=True)
        entry.symlink_to(home / "locked" / "sub")
        locked = home / "locked"
    if locked is not None:
        locked.chmod(0)
    try:
        if locked is not None and os.access(locked, os.X_OK):
            pytest.skip("this account searches a mode-0 folder")
        with _bounded(10):
            want = Path(os.path.realpath(entry))
            for typed in (entry, tmp_path / "home-link" / "inbox"):
                assert canonical_source_root(typed) == want, typed
    finally:
        if locked is not None:
            locked.chmod(0o700)


def test_load_config_missing_file_names_add_source(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"run `~/\.local/bin/agentsync add-source <folder>` to create it"):
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


_C3333_BLANKS = pytest.mark.parametrize("blank", ["\u3000", " ", "\u00a0"], ids=["u3000", "space", "nbsp"])


@_C3333_BLANKS
def test_a_stored_path_keeps_the_blank_its_writer_wrote_c3333(tmp_path: Path, blank: str) -> None:
    """B13 round 47 C3333 (ruling 209; data integrity): the loader read every path through ``_str``, which
    strips, so a source path ending in U+3000, U+00A0 or a space loaded as its sibling without the blank and a
    sync mirrored the sibling's files. A path key keeps the bytes its writer wrote, both siblings present; a
    blank-only value still counts as missing."""
    work = tmp_path / "w"
    for name in ("Project", "docs", "state", "cache"):
        (work / name).mkdir(parents=True)
        (work / f"{name}{blank}").mkdir()
    for name in ("pandoc", f"pandoc{blank}"):
        (work / name).write_text("#!/bin/sh\n", encoding="utf-8")
    text = (
        f"[agentsync]\ndocs_repo = {toml_string(str(work / f'docs{blank}'))}\n"
        f"state_dir = {toml_string(str(work / f'state{blank}'))}\n"
        f"cache_dir = {toml_string(str(work / f'cache{blank}'))}\n"
        f"[convert]\npandoc_path = {toml_string(str(work / f'pandoc{blank}'))}\n"
        f"[[source]]\nid = 'p'\nkind = 'local'\npath = {toml_string(str(work / f'Project{blank}'))}\n"
    )
    cfg = parse(text, tmp_path)
    assert cfg.sources[0].path == canonical_source_root(work / f"Project{blank}")
    assert cfg.docs_repo == work / f"docs{blank}"
    assert cfg.state_dir == work / f"state{blank}"
    assert cfg.cache_dir == work / f"cache{blank}"
    assert cfg.convert.pandoc_path == work / f"pandoc{blank}"
    blank_only = parse(
        f"[agentsync]\ndocs_repo = '{blank}'\n[convert]\npandoc_path = '{blank}{blank}'\n", tmp_path
    )
    assert blank_only.convert.pandoc_path is None
    assert blank_only.docs_repo == parse("", tmp_path).docs_repo
    with pytest.raises(ConfigError, match="missing required key 'path'"):
        parse(f"[[source]]\nid = 'p'\nkind = 'local'\npath = '{blank}'\n", tmp_path)


@_C3333_BLANKS
@pytest.mark.parametrize(
    ("table", "key", "make"),
    [
        ("[[source]]\nid = 'p'\nkind = 'local'\n", "path", "dir"),
        ("[agentsync]\n", "docs_repo", "dir"),
        ("[agentsync]\n", "state_dir", "dir"),
        ("[convert]\n", "pandoc_path", "file"),
    ],
)
def test_a_stored_path_absent_whose_stripped_spelling_exists_names_both_c3333(
    tmp_path: Path, blank: str, table: str, key: str, make: str
) -> None:
    """B13 round 47 C3333 (ruling 209): a config written before the fix may hold a blank the old loader
    stripped; when the path as written is absent and its stripped spelling exists, the load stops on a
    ConfigError naming the key and both spellings (repr, so the blank shows), never picking one."""
    target = tmp_path / "w" / "Thing"
    target.parent.mkdir()
    if make == "dir":
        target.mkdir()
    else:
        target.write_text("#!/bin/sh\n", encoding="utf-8")
    written = f"{target}{blank}"
    with pytest.raises(ConfigError) as caught:
        parse(f"{table}{key} = {toml_string(written)}\n", tmp_path)
    message = str(caught.value)
    assert f"{key!r} {written!r}" in message and repr(str(target)) in message, message
    assert "edit sources.toml" in message, message


def test_the_retire_advice_names_a_source_block_the_loader_accepts(tmp_path: Path) -> None:
    """F97 (B13 round 5): the cycle's vanished-source refusal and doctor's shared-content line said to set
    state = "retired" alone, which the loader refuses (a retired source needs retired_reason).  The advice's
    own assignments, pasted into a [[source]] block, parse as a retired source."""
    settings = re.findall(r'(\w+) = ("[^"]*")', RETIRE_ADVICE)
    assert [k for k, _ in settings] == ["state", "retired_reason"]
    block = "".join(f"{k} = {v}\n" for k, v in settings)
    cfg = parse(f"[[source]]\nid = 'a'\nkind = 'local'\npath = '/tmp/x'\n{block}", tmp_path)
    assert cfg.sources[0].state is SourceState.RETIRED


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


def _case_insensitive(folder: Path) -> bool:
    """Whether ``folder``'s volume takes a name in any case (APFS's default on a Mac)."""
    probe = folder / "case-probe-x"
    probe.mkdir()
    try:
        return (folder / "case-probe-X").exists() and probe.samefile(folder / "case-probe-X")
    finally:
        probe.rmdir()


def test_ensure_inbox_takes_a_source_on_the_folder_in_another_case_as_the_inbox(tmp_path: Path) -> None:
    """B13 round 42 C2318: a source on ``Inbox`` is on the folder ``inbox`` on a case-insensitive Mac, so
    ensure_inbox adds no second source for it; compared lexically, it added ``inbox`` beside ``Inbox`` and the
    one folder became two live sources mirroring the same files."""
    if not _case_insensitive(tmp_path):
        pytest.skip("the tmp volume is case-sensitive: Inbox and inbox are two folders there")
    upper = tmp_path / "ctx" / "Inbox"
    upper.mkdir(parents=True)
    cfg_path = _ctx_config(tmp_path, inbox_source_table("contoso-inbox", canonical_source_root(upper)))
    before = cfg_path.read_text(encoding="utf-8")
    config, added = ensure_inbox(cfg_path)
    assert added is None, added
    assert [(s.id, s.kind) for s in config.sources] == [("contoso-inbox", SourceKind.INBOX)]
    assert cfg_path.read_text(encoding="utf-8") == before


def test_ensure_inbox_keeps_two_folders_that_differ_only_in_case_two_where_the_disk_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2318's other side: on a case-sensitive volume ``Inbox`` and ``inbox`` are two folders, and the file
    system says so (samefile False, or an OSError for one that is gone): the kept folder gets its own
    source."""
    upper = tmp_path / "ctx" / "Inbox"
    upper.mkdir(parents=True)
    for answer in (False, OSError("gone")):

        def samefile(self: Path, other: object, answer: object = answer) -> bool:
            if isinstance(answer, OSError):
                raise answer
            return bool(answer)

        monkeypatch.setattr(Path, "samefile", samefile)
        cfg_path = _ctx_config(tmp_path, inbox_source_table("contoso-inbox", canonical_source_root(upper)))
        added = ensure_inbox(cfg_path)[1]
        assert added is not None and added.id == "inbox" and added.kind is SourceKind.INBOX
        assert [s.id for s in load_config(cfg_path).sources] == ["contoso-inbox", "inbox"]


def test_ensure_inbox_adds_the_named_inbox_beside_other_inboxes(tmp_path: Path) -> None:
    """Field report 2026-10-05: a Mac with hand-added inbox sources elsewhere still gets
    ~/agent-context/inbox, the folder every guide names; no folder is ever configured twice."""
    elsewhere = tmp_path / "drop"
    elsewhere.mkdir()
    paused = inbox_source_table("drop", elsewhere).replace(
        'kind = "inbox"\n', 'kind = "inbox"\nstate = "paused"\n'
    )
    cfg_path = _ctx_config(tmp_path, paused)
    added = ensure_inbox(cfg_path)[1]
    inbox = tmp_path / "ctx" / "inbox"
    assert added is not None and added.kind is SourceKind.INBOX and added.path == inbox.resolve()
    assert ensure_inbox(cfg_path)[1] is None  # the second run adds nothing
    assert [s.path for s in load_config(cfg_path).sources] == [elsewhere.resolve(), inbox.resolve()]

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
