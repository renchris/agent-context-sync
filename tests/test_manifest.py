"""SQLite manifest: pragmas, migrations, atomic transactions, items, outputs, tombstones, cursors, export."""

from __future__ import annotations

import json
import logging
import sqlite3
import stat
import unicodedata
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from agentsync import manifest as manifest_mod
from agentsync.config import SourceConfig
from agentsync.errors import ConfigError, ManifestSchemaError
from agentsync.manifest import (
    MANIFEST_SCHEMA_VERSION,
    DependsRow,
    Manifest,
    OutputRow,
    TombstoneRow,
    cursor_fingerprint,
)
from agentsync.model import (
    CycleMode,
    OutputStatus,
    PassKind,
    RemoteHashes,
    RowState,
    SourceItem,
    SourceKind,
    SourceState,
    Verdict,
)

H = "a" * 64
H2 = "b" * 64
H3 = "c" * 64
SECRET_CURSOR = "https://graph.microsoft.com/v1.0/drives/x/root/delta?token=SUPERSECRETTOKEN"


def local_source(sid: str = "src", path: str = "/tmp/src", **kw: object) -> SourceConfig:
    return SourceConfig(id=sid, kind=SourceKind.LOCAL, path=Path(path), **kw)  # type: ignore[arg-type]


def item(
    stable_id: str = "vol:1",
    rel_path: str = "a/file.docx",
    *,
    source_id: str = "src",
    size: int = 10,
    mtime_ns: int = 1_000,
    ctime_ns: int = 1_000,
    **kw: object,
) -> SourceItem:
    return SourceItem(
        source_id=source_id,
        stable_id=stable_id,
        rel_path=rel_path,
        name=rel_path.rsplit("/", 1)[-1],
        size=size,
        mtime_ns=mtime_ns,
        ctime_ns=ctime_ns,
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture
def db_path(tmp_state_dir: Path) -> Path:
    return tmp_state_dir / "manifest.sqlite"


@pytest.fixture
def m(db_path: Path) -> Iterator[Manifest]:
    man = Manifest(db_path)
    man.sync_sources([local_source()])
    yield man
    man.close()


def output(
    path: str, unit: str = "whole", *, stable_id: str = "vol:1", sha: str | None = H, **kw: object
) -> OutputRow:
    fields: dict[str, object] = {
        "output_path": path,
        "source_id": "src",
        "stable_id": stable_id,
        "unit_id": unit,
        "action_key": "k-" + unit,
        "rendered_sha256": sha,
        "page_sha256": H3,
        "converter_id": "pandoc-gfm",
        "converter_version": "3.9",
        "options_hash": "sha256:" + H,
        "status": OutputStatus.OK,
        "built_run": 1,
    }
    fields.update(kw)
    return OutputRow(**fields)  # type: ignore[arg-type]


# ---- opening, pragmas, schema ------------------------------------------------------------------------------


def test_creates_db_0600_with_wal_full_sync_and_foreign_keys(db_path: Path) -> None:
    with Manifest(db_path) as man:
        assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
        conn = man._db
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert man.get_meta("manifest_schema_version") == str(MANIFEST_SCHEMA_VERSION)
        assert man.get_meta("key_schema_version") == "1"
        assert man.applied_migrations() == [(1, "initial schema (CONTRACTS.md section 5)")]


def test_existing_file_permissions_are_tightened(db_path: Path) -> None:
    Manifest(db_path).close()
    db_path.chmod(0o644)
    Manifest(db_path).close()
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_every_contract_table_exists(m: Manifest) -> None:
    names = {r[0] for r in m._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in (
        "meta",
        "sources",
        "items",
        "cursors",
        "cache",
        "outputs",
        "tombstones",
        "runs",
        "run_sources",
        "depends",
        "schema_migrations",
    ):
        assert table in names


def test_reopen_keeps_data_and_is_idempotent(db_path: Path) -> None:
    with Manifest(db_path) as man:
        man.sync_sources([local_source()])
        man.set_meta("tree_sha", "t1")
    with Manifest(db_path) as man:
        assert man.tree_sha() == "t1"
        assert man.get_source("src") is not None
        assert len(man.applied_migrations()) == 1


def test_schema_version_mismatch_raises_never_rederives(db_path: Path) -> None:
    with Manifest(db_path) as man:
        man.set_meta("manifest_schema_version", str(MANIFEST_SCHEMA_VERSION + 1))
    with pytest.raises(ManifestSchemaError, match="newer"):
        Manifest(db_path)
    with pytest.raises(ManifestSchemaError, match="newer than this build"):
        Manifest.migrate(db_path)


def _meta(path: Path, key: str) -> str:
    conn = sqlite3.connect(path)
    try:
        return str(conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()[0])
    finally:
        conn.close()


def test_older_schema_version_migrates_on_open_after_a_backup(db_path: Path) -> None:
    with Manifest(db_path) as man:
        man.set_meta("manifest_schema_version", "0")
    with Manifest(db_path) as man:
        assert man.get_meta("manifest_schema_version") == str(MANIFEST_SCHEMA_VERSION)
        backup = man.migration_backup
    assert backup == db_path.with_name(f"manifest.sqlite.pre-v{MANIFEST_SCHEMA_VERSION}")
    assert backup.is_file() and stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert _meta(backup, "manifest_schema_version") == "0"  # the copy is the database before the step
    assert manifest_mod.migration_backups(db_path) == [backup]
    with Manifest(db_path) as man:  # already current: no second copy, the first is left alone
        assert man.migration_backup is None
    assert _meta(backup, "manifest_schema_version") == "0"


def test_open_runs_a_new_schema_step_in_one_transaction(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Manifest(db_path).close()
    step2 = manifest_mod._Migration(2, "add items.note", "ALTER TABLE items ADD COLUMN note TEXT;")
    monkeypatch.setattr(manifest_mod, "_MIGRATIONS", (*manifest_mod._MIGRATIONS, step2))
    monkeypatch.setattr(manifest_mod, "MANIFEST_SCHEMA_VERSION", 2)
    with Manifest(db_path) as man:
        assert [v for v, _ in man.applied_migrations()] == [1, 2]
        assert "note" in {r[1] for r in man._db.execute("PRAGMA table_info(items)")}
        assert man.migration_backup == db_path.with_name("manifest.sqlite.pre-v2")
    assert _meta(db_path.with_name("manifest.sqlite.pre-v2"), "manifest_schema_version") == "1"


def test_failed_open_migration_rolls_back_and_drops_the_copy(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Manifest(db_path).close()
    bad = manifest_mod._Migration(2, "broken step", "ALTER TABLE nosuchtable ADD COLUMN y TEXT;")
    monkeypatch.setattr(manifest_mod, "_MIGRATIONS", (*manifest_mod._MIGRATIONS, bad))
    monkeypatch.setattr(manifest_mod, "MANIFEST_SCHEMA_VERSION", 2)
    with pytest.raises(sqlite3.OperationalError):
        Manifest(db_path)
    assert _meta(db_path, "manifest_schema_version") == "1"
    assert manifest_mod.migration_backups(db_path) == []


def test_migration_copy_is_excluded_from_backups_and_never_left_partial(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Manifest(db_path) as man:
        man.set_meta("key_schema_version", "0")
    excluded: list[Path] = []
    monkeypatch.setattr(manifest_mod.tm_exclude, "exclude_new_file", lambda p: excluded.append(p) or True)
    partial = db_path.with_name(f"manifest.sqlite.pre-v{MANIFEST_SCHEMA_VERSION}.partial")
    partial.write_bytes(b"truncated by a killed copy")  # a leftover is replaced, never trips the copy
    assert partial in manifest_mod.migration_backups(db_path)  # so purge and the TM list reach it too
    with Manifest(db_path) as man:
        backup = man.migration_backup
    assert backup is not None and excluded == [partial]  # excluded before the copy is written, then renamed
    assert not partial.exists() and _meta(backup, "key_schema_version") == "0"
    assert manifest_mod.migration_backups(db_path) == [backup]


def test_newer_key_schema_refuses_on_open(db_path: Path) -> None:
    with Manifest(db_path) as man:
        man.set_meta("key_schema_version", "999")
    with pytest.raises(ManifestSchemaError, match="newer"):
        Manifest(db_path)
    assert manifest_mod.migration_backups(db_path) == []


def test_key_schema_version_mismatch_migrates_on_open_and_reindexes_cache(db_path: Path) -> None:
    with Manifest(db_path) as man:
        man.record_cache(
            "k1",
            converter_id="c",
            converter_version="1",
            options_hash="o",
            canonical_sha256=H,
            status="ok",
            unit_count=1,
            size=10,
            run_id=1,
        )
        man.set_meta("key_schema_version", "0")
    with Manifest(db_path) as man:
        assert man.get_meta("key_schema_version") == "1"
        assert man._db.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
        assert man.migration_backup is not None
    assert _meta(man.migration_backup, "key_schema_version") == "0"
    assert Manifest.migrate(db_path) == []


def test_not_a_manifest_raises(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE other (x)")
    conn.commit()
    conn.close()
    with pytest.raises(ManifestSchemaError, match="no meta"):
        Manifest(db_path)


def test_missing_meta_version_raises(db_path: Path) -> None:
    with Manifest(db_path) as man:
        man._db.execute("DELETE FROM meta WHERE key = 'manifest_schema_version'")
    with pytest.raises(ManifestSchemaError, match="missing"):
        Manifest(db_path)


def test_migrate_applies_new_steps_in_one_transaction(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Manifest(db_path).close()
    step2 = manifest_mod._Migration(2, "add items.note", "ALTER TABLE items ADD COLUMN note TEXT;")
    monkeypatch.setattr(manifest_mod, "_MIGRATIONS", (*manifest_mod._MIGRATIONS, step2))
    monkeypatch.setattr(manifest_mod, "MANIFEST_SCHEMA_VERSION", 2)
    assert Manifest.migrate(db_path) == [2]  # the explicit form takes the same pre-v<N> copy (install.sh)
    assert manifest_mod.migration_backups(db_path) == [db_path.with_name("manifest.sqlite.pre-v2")]
    assert _meta(db_path.with_name("manifest.sqlite.pre-v2"), "manifest_schema_version") == "1"
    with Manifest(db_path) as man:
        assert man.get_meta("manifest_schema_version") == "2"
        assert [v for v, _ in man.applied_migrations()] == [1, 2]
        cols = {r[1] for r in man._db.execute("PRAGMA table_info(items)")}
        assert "note" in cols
    assert Manifest.migrate(db_path) == []


def test_failed_migration_rolls_back_everything(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Manifest(db_path).close()
    good = manifest_mod._Migration(2, "ok step", "CREATE TABLE extra (x INTEGER);")
    bad = manifest_mod._Migration(3, "broken step", "ALTER TABLE nosuchtable ADD COLUMN y TEXT;")
    monkeypatch.setattr(manifest_mod, "_MIGRATIONS", (*manifest_mod._MIGRATIONS, good, bad))
    monkeypatch.setattr(manifest_mod, "MANIFEST_SCHEMA_VERSION", 3)
    with pytest.raises(sqlite3.OperationalError):
        Manifest.migrate(db_path)
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT value FROM meta WHERE key='manifest_schema_version'").fetchone()[0] == "1"
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='extra'").fetchone() is None
    conn.close()


def test_migrate_creates_missing_db(tmp_path: Path) -> None:
    path = tmp_path / "new" / "manifest.sqlite"
    assert Manifest.migrate(path) == [1]
    Manifest(path).close()


def test_split_sql_handles_the_whole_schema() -> None:
    statements = manifest_mod._split_sql(manifest_mod.SCHEMA_SQL)
    assert sum(s.startswith("CREATE TABLE") for s in statements) == 10
    assert all(sqlite3.complete_statement(s) for s in statements)


def test_close_is_idempotent_and_blocks_use(db_path: Path) -> None:
    man = Manifest(db_path)
    man.close()
    man.close()
    with pytest.raises(sqlite3.ProgrammingError):
        man.get_meta("x")


# ---- transactions ------------------------------------------------------------------------------------------


def test_transaction_commits_items_and_cursor_together(m: Manifest) -> None:
    run = m.begin_run(CycleMode.POLL, host="h", pid=1)
    with m.transaction():
        m.upsert_observed(item(), run_id=run, verdict=Verdict.CREATED, state=RowState.LIVE)
        m.stage_cursor("src", SECRET_CURSOR, run)
    assert m.get_item("src", "vol:1") is not None
    cur = m.get_cursor("src")
    assert cur is not None and cur.pending == SECRET_CURSOR and cur.current is None


def test_transaction_rolls_back_items_and_cursor_together(m: Manifest) -> None:
    run = m.begin_run(CycleMode.POLL, host="h", pid=1)
    with pytest.raises(RuntimeError, match="boom"), m.transaction():
        m.upsert_observed(item(), run_id=run, verdict=Verdict.CREATED, state=RowState.LIVE)
        m.stage_cursor("src", SECRET_CURSOR, run)
        m.rekey("src", "vol:1", "vol:2")  # a nested internal savepoint must not leak a commit
        raise RuntimeError("boom")
    assert m.get_item("src", "vol:1") is None
    assert m.get_item("src", "vol:2") is None
    assert m.get_cursor("src") is None


def test_transaction_is_not_reentrant(m: Manifest) -> None:
    with m.transaction():
        with pytest.raises(RuntimeError, match="re-entrant"), m.transaction():
            pass
        m.set_meta("k", "v")
    assert m.get_meta("k") == "v"


def test_internal_savepoint_failure_rolls_back_only_itself(m: Manifest) -> None:
    run = m.begin_run(CycleMode.POLL, host="h", pid=1)
    with m.transaction():
        m.upsert_observed(item(), run_id=run, verdict=Verdict.CREATED, state=RowState.LIVE)
        with pytest.raises(sqlite3.IntegrityError):
            m.replace_outputs(
                "src", "vol:1", [output("mirror/src/a.md"), output("mirror/src/a.md", "sheet:1")]
            )
    assert m.get_item("src", "vol:1") is not None
    assert m.outputs_for("src", "vol:1") == []


def test_writes_are_visible_to_a_second_connection(m: Manifest, db_path: Path) -> None:
    run = m.begin_run(CycleMode.POLL, host="h", pid=1)
    m.upsert_observed(item(), run_id=run, verdict=Verdict.CREATED, state=RowState.LIVE)
    other = sqlite3.connect(db_path)
    assert other.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    other.close()


# ---- runs --------------------------------------------------------------------------------------------------


def test_runs_are_monotonic_and_finish_sets_written_at(m: Manifest) -> None:
    assert m.written_at_ns() == 0
    r1 = m.begin_run(CycleMode.POLL, host="h", pid=1)
    r2 = m.begin_run(CycleMode.RECONCILE, host="h", pid=2)
    assert r2 > r1
    m.finish_run(r1, status="ok", commit_sha="abc", counts={"b": 2, "a": 1})
    assert m.written_at_ns() > 0
    assert m.last_runs() == [(r2, "reconcile", "running", None), (r1, "poll", "ok", "abc")]
    assert m.last_runs(1) == [(r2, "reconcile", "running", None)]
    counts = m._db.execute("SELECT counts_json FROM runs WHERE run_id = ?", (r1,)).fetchone()[0]
    assert counts == '{"a":1,"b":2}'
    with pytest.raises(ValueError, match="run status"):
        m.finish_run(r2, status="weird", commit_sha=None, counts={})
    with pytest.raises(KeyError):
        m.finish_run(999, status="ok", commit_sha=None, counts={})


def test_record_source_pass_upserts(m: Manifest) -> None:
    run = m.begin_run(CycleMode.POLL, host="h", pid=1)
    m.record_source_pass(
        run,
        "src",
        pass_kind=PassKind.FULL,
        enumeration_complete=True,
        cursor_reset=False,
        counts={"created": 3},
    )
    m.record_source_pass(
        run,
        "src",
        pass_kind=None,
        enumeration_complete=False,
        cursor_reset=True,
        counts={},
        skipped_reason="network down",
        error="x",
    )
    rows = m._db.execute("SELECT * FROM run_sources").fetchall()
    assert len(rows) == 1
    assert rows[0]["pass_kind"] is None and rows[0]["skipped_reason"] == "network down"
    assert rows[0]["cursor_reset"] == 1


def test_written_at_ns_garbage_is_treated_as_unset(m: Manifest) -> None:
    m.set_meta("written_at_ns", "nope")
    assert m.written_at_ns() == 0


# ---- sources -----------------------------------------------------------------------------------------------


def test_sync_sources_insert_state_and_fingerprint_changes(m: Manifest) -> None:
    run = m.begin_run(CycleMode.RECONCILE, host="h", pid=1)
    m.set_enumeration_complete("src", True, run)
    m.stage_cursor("src", SECRET_CURSOR, run)
    m.promote_cursors(run, "2026-09-29T00:00:00Z")
    assert m.sync_sources([local_source()]) == []
    assert m.sync_sources([local_source(state=SourceState.PAUSED)]) == []
    src = m.get_source("src")
    assert src is not None and src.config_state is SourceState.PAUSED and src.enumeration_complete
    assert m.get_cursor("src") is not None
    assert m.sync_sources([local_source(path="/tmp/elsewhere")]) == ["src"]
    src = m.get_source("src")
    assert src is not None and not src.enumeration_complete and not src.baseline_complete
    assert m.get_cursor("src") is None


@pytest.mark.parametrize(
    "change",
    [{"include": ("*.docx",)}, {"exclude": ()}, {"principal": "someone@example.com"}],
)
def test_scope_fingerprint_covers_globs_and_principal(m: Manifest, change: dict[str, object]) -> None:
    assert m.sync_sources([local_source("src", "/tmp/src", **change)]) == ["src"]


def test_scope_fingerprint_ignores_non_scope_keys(m: Manifest) -> None:
    assert m.sync_sources([local_source(max_files=7, cadence_s=999, sentinel="README.txt")]) == []


def test_sync_sources_kind_change_and_duplicates_raise(m: Manifest) -> None:
    inbox = SourceConfig(id="src", kind=SourceKind.INBOX, path=Path("/tmp/src"))
    with pytest.raises(ConfigError, match="immutable"):
        m.sync_sources([inbox])
    with pytest.raises(ConfigError, match="duplicate"):
        m.sync_sources([local_source(), local_source()])


def test_sync_sources_is_atomic(m: Manifest) -> None:
    other = local_source("other")
    inbox = SourceConfig(id="src", kind=SourceKind.INBOX, path=Path("/tmp/src"))
    with pytest.raises(ConfigError):
        m.sync_sources([other, inbox])
    assert m.get_source("other") is None


def test_enumeration_flags_success_and_auth(m: Manifest) -> None:
    run = m.begin_run(CycleMode.RECONCILE, host="h", pid=1)
    m.set_enumeration_complete("src", True, run)
    src = m.get_source("src")
    assert src is not None and src.enumeration_complete and src.baseline_complete
    assert src.last_full_run_id == run
    m.set_enumeration_complete("src", False, run + 1)
    src = m.get_source("src")
    assert src is not None and not src.enumeration_complete and src.baseline_complete
    assert src.last_full_run_id == run
    m.set_last_success("src", run)
    m.set_auth_state("REAUTH_REQUIRED", ["src"])
    src = m.get_source("src")
    assert src is not None and src.last_success_run_id == run and src.auth_state == "REAUTH_REQUIRED"
    with pytest.raises(ValueError, match="auth_state"):
        m.set_auth_state("bad", ["src"])
    with pytest.raises(KeyError):
        m.set_last_success("nope", run)


def test_breaker_lifecycle(m: Manifest) -> None:
    assert not m.breaker_active("src", "2026-09-29T00:00:00Z")
    m.trip_breaker("src", candidates=40, tripped_at="2026-09-29T00:00:00Z", until="2026-10-06T00:00:00Z")
    assert m.breaker_active("src", "2026-10-01T12:00:00Z")
    assert m.breaker_active("src", "2026-10-05T23:59:59+00:00")
    assert not m.breaker_active("src", "2026-10-06T00:00:00Z")
    src = m.get_source("src")
    assert src is not None and src.breaker_candidates == 40
    m.clear_breaker("src")
    assert not m.breaker_active("src", "2026-10-01T00:00:00Z")
    assert not m.breaker_active("unknown", "2026-10-01T00:00:00Z")
    with pytest.raises(ValueError):
        m.trip_breaker("src", candidates=1, tripped_at="yesterday", until="2026-10-06T00:00:00Z")


# ---- items -------------------------------------------------------------------------------------------------


def test_upsert_inserts_all_observed_fields(m: Manifest) -> None:
    it = item(
        gen_count=5,
        ino=77,
        mode=0o100644,
        created_ns=5,
        dataless=False,
        etag="e1",
        ctag="c1",
        remote_hashes=RemoteHashes(quickxor="qx", sha1="s1", sha256="s256"),
        parent_id="p",
        content_type="application/x",
        extra={"z": 1, "a": "b"},
    )
    m.upsert_observed(it, run_id=3, verdict=Verdict.CREATED, state=RowState.LIVE)
    row = m.get_item("src", "vol:1")
    assert row is not None
    assert (row.size, row.mtime_ns, row.ctime_ns, row.ino, row.mode, row.gen_count) == (
        10,
        1000,
        1000,
        77,
        0o100644,
        5,
    )
    assert (row.quickxor, row.sha1_remote, row.sha256_remote, row.etag, row.ctag) == (
        "qx",
        "s1",
        "s256",
        "e1",
        "c1",
    )
    assert row.first_seen_run == row.last_seen_run == 3
    assert row.last_verdict is Verdict.CREATED and row.state is RowState.LIVE
    assert row.content_sha256 is None and row.canonical_parts == ()
    assert dict(row.extra) == {"a": "b", "z": 1}
    stored = m._db.execute("SELECT extra_json FROM items").fetchone()[0]
    assert stored == '{"a":"b","z":1}'


def test_upsert_rename_records_prev_path_and_keeps_content(m: Manifest) -> None:
    m.upsert_observed(item(), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.set_content(
        "src", "vol:1", content_sha256=H, canonical_sha256=H2, canonical_method="bytes@1", canonical_parts=[]
    )
    m.upsert_observed(
        item(rel_path="b/renamed.docx"), run_id=2, verdict=Verdict.METADATA_ONLY, state=RowState.LIVE
    )
    row = m.get_item("src", "vol:1")
    assert row is not None
    assert (row.rel_path, row.prev_path, row.name) == ("b/renamed.docx", "a/file.docx", "renamed.docx")
    assert row.content_sha256 == H and row.canonical_sha256 == H2
    assert row.first_seen_run == 1 and row.last_seen_run == 2
    m.upsert_observed(
        item(rel_path="b/renamed.docx"), run_id=3, verdict=Verdict.UNCHANGED, state=RowState.LIVE
    )
    row = m.get_item("src", "vol:1")
    assert row is not None and row.prev_path == "a/file.docx"  # sticky until the next rename


def test_upsert_normalises_nfc(m: Manifest) -> None:
    nfd = unicodedata.normalize("NFD", "café/Résumé.docx")
    m.upsert_observed(item(rel_path=nfd), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    row = m.get_item("src", "vol:1")
    assert row is not None and row.rel_path == unicodedata.normalize("NFC", "café/Résumé.docx")
    assert m.item_by_path("src", nfd) is not None


def test_upsert_deleted_touches_only_verdict_state_and_seen(m: Manifest) -> None:
    m.upsert_observed(item(etag="e1"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    gone = item(rel_path="", size=0, mtime_ns=0, ctime_ns=0, deleted=True)
    m.upsert_observed(gone, run_id=2, verdict=Verdict.DELETED, state=RowState.TOMBSTONE)
    row = m.get_item("src", "vol:1")
    assert row is not None
    assert (row.rel_path, row.size, row.mtime_ns, row.etag) == ("a/file.docx", 10, 1000, "e1")
    assert row.state is RowState.TOMBSTONE and row.last_verdict is Verdict.DELETED and row.last_seen_run == 2


def test_upsert_dataless_keeps_last_materialised_h0(m: Manifest) -> None:
    m.upsert_observed(item(gen_count=4, ino=9), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    placeholder = item(size=99, mtime_ns=5_000, ctime_ns=5_000, gen_count=7, ino=9, dataless=True)
    m.upsert_observed(placeholder, run_id=2, verdict=Verdict.DATALESS, state=RowState.DATALESS)
    row = m.get_item("src", "vol:1")
    assert row is not None and row.dataless and row.state is RowState.DATALESS
    assert (row.size, row.mtime_ns, row.gen_count) == (10, 1000, 4)
    m.upsert_observed(
        replace(placeholder, dataless=False), run_id=3, verdict=Verdict.MAYBE_CHANGED, state=RowState.LIVE
    )
    row = m.get_item("src", "vol:1")
    assert row is not None and (row.size, row.mtime_ns, row.gen_count, row.dataless) == (99, 5000, 7, False)


def test_upsert_state_change_clears_stale_reason(m: Manifest) -> None:
    m.upsert_observed(item(), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.set_state("src", "vol:1", RowState.QUARANTINED, "password-protected")
    m.upsert_observed(item(), run_id=2, verdict=Verdict.UNCHANGED, state=RowState.QUARANTINED)
    row = m.get_item("src", "vol:1")
    assert row is not None and row.state_reason == "password-protected"
    m.upsert_observed(item(), run_id=3, verdict=Verdict.UNCHANGED, state=RowState.LIVE)
    row = m.get_item("src", "vol:1")
    assert row is not None and row.state_reason is None


def test_upsert_requires_known_source(m: Manifest) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        m.upsert_observed(item(source_id="ghost"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)


def test_set_content_verdict_state_validate(m: Manifest) -> None:
    m.upsert_observed(item(), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.set_content(
        "src",
        "vol:1",
        content_sha256=H,
        canonical_sha256=H2,
        canonical_method="ooxml-parts@1",
        canonical_parts=[("word/document.xml", H3), ("[Content_Types].xml", H)],
    )
    row = m.get_item("src", "vol:1")
    assert row is not None
    assert row.canonical_parts == (("[Content_Types].xml", H), ("word/document.xml", H3))
    m.set_verdict("src", "vol:1", Verdict.DEFERRED)
    m.set_state("src", "vol:1", RowState.REFUSED, "no converter for .xyz")
    row = m.get_item("src", "vol:1")
    assert row is not None and row.last_verdict is Verdict.DEFERRED and row.state is RowState.REFUSED
    with pytest.raises(ValueError, match="64 lowercase hex"):
        m.set_content(
            "src", "vol:1", content_sha256="ABC", canonical_sha256=H, canonical_method="m", canonical_parts=[]
        )
    with pytest.raises(KeyError):
        m.set_verdict("src", "missing", Verdict.ERROR)
    with pytest.raises(KeyError):
        m.set_state("src", "missing", RowState.LIVE, None)


def _seed(m: Manifest, specs: list[tuple[str, RowState, Verdict | None, bool]], run: int = 1) -> None:
    for sid, state, verdict, is_dir in specs:
        m.upsert_observed(
            item(sid, f"p/{sid}", is_dir=is_dir),
            run_id=run,
            verdict=verdict or Verdict.UNCHANGED,
            state=state,
        )


def test_pending_work_selects_unpublished_rows(m: Manifest) -> None:
    _seed(
        m,
        [
            ("a-created", RowState.LIVE, Verdict.CREATED, False),
            ("b-maybe", RowState.LIVE, Verdict.MAYBE_CHANGED, False),
            ("c-changed", RowState.LIVE, Verdict.CHANGED, False),
            ("d-deferred", RowState.LIVE, Verdict.DEFERRED, False),
            ("e-error", RowState.QUARANTINED, Verdict.ERROR, False),
            ("f-unchanged", RowState.LIVE, Verdict.UNCHANGED, False),
            ("g-touched", RowState.LIVE, Verdict.TOUCHED_NOT_CHANGED, False),
            ("h-dataless-new", RowState.DATALESS, Verdict.DATALESS, False),
            ("i-dataless-had", RowState.DATALESS, Verdict.DATALESS, False),
            ("j-tomb", RowState.TOMBSTONE, Verdict.CHANGED, False),
            ("k-dir", RowState.LIVE, Verdict.CREATED, True),
            ("l-meta", RowState.LIVE, Verdict.METADATA_ONLY, False),
            ("m-out", RowState.LIVE, Verdict.OUTPUT_UNCHANGED, False),
        ],
    )
    m.set_content(
        "src",
        "i-dataless-had",
        content_sha256=H,
        canonical_sha256=H,
        canonical_method="bytes@1",
        canonical_parts=[],
    )
    ids = [r.stable_id for r in m.pending_work("src")]
    assert ids == ["a-created", "b-maybe", "c-changed", "d-deferred", "e-error", "h-dataless-new"]


def test_unseen_live_and_live_count(m: Manifest) -> None:
    _seed(
        m,
        [
            ("a", RowState.LIVE, None, False),
            ("b", RowState.DATALESS, None, False),
            ("c", RowState.QUARANTINED, None, False),
            ("d", RowState.REFUSED, None, False),
            ("e", RowState.TOMBSTONE, None, False),
            ("f", RowState.LIVE, None, True),
        ],
        run=1,
    )
    _seed(m, [("g", RowState.LIVE, None, False)], run=2)
    assert [r.stable_id for r in m.unseen_live("src", 2)] == ["a", "b", "c", "d"]
    assert m.unseen_live("src", 1) == []
    assert m.live_count("src") == 3  # a, b, g (files in live|dataless)
    assert [r.stable_id for r in m.iter_items("src", states=[RowState.TOMBSTONE, RowState.REFUSED])] == [
        "d",
        "e",
    ]
    assert [r.stable_id for r in m.iter_items("src")] == list("abcdefg")
    assert list(m.iter_items("src", states=[])) == []


def test_item_by_path_skips_tombstones(m: Manifest) -> None:
    _seed(m, [("old", RowState.TOMBSTONE, None, False)])
    m.upsert_observed(item("new", "p/old"), run_id=2, verdict=Verdict.CREATED, state=RowState.LIVE)
    row = m.item_by_path("src", "p/old")
    assert row is not None and row.stable_id == "new"
    assert m.item_by_path("src", "P/OLD") is None  # case-sensitive by contract
    assert m.item_by_path("src", "nothing") is None


def test_tree_lookup(m: Manifest) -> None:
    m.upsert_observed(
        item("dir1", "Docs", is_dir=True), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE
    )
    m.upsert_observed(
        item("f1", "Docs/x.txt", parent_id="dir1"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE
    )
    look = m.tree_lookup("src")
    assert look("f1") == ("dir1", "x.txt")
    assert look("dir1") == (None, "Docs")
    assert look("nope") is None


def test_find_by_canonical_across_sources(m: Manifest) -> None:
    m.sync_sources([local_source(), local_source("inbox", "/tmp/inbox")])
    for src, sid, state in (
        ("src", "a", RowState.LIVE),
        ("inbox", "b", RowState.LIVE),
        ("src", "c", RowState.TOMBSTONE),
    ):
        m.upsert_observed(
            item(sid, f"x/{sid}", source_id=src), run_id=1, verdict=Verdict.CREATED, state=state
        )
        m.set_content(
            src, sid, content_sha256=H, canonical_sha256=H2, canonical_method="bytes@1", canonical_parts=[]
        )
    assert [(r.source_id, r.stable_id) for r in m.find_by_canonical(H2)] == [("inbox", "b"), ("src", "a")]
    assert m.find_by_canonical(H3) == []


# ---- rekey (safe-save continuity) --------------------------------------------------------------------------


def _published_old(m: Manifest) -> None:
    m.upsert_observed(item("vol:1", "a/Budget.xlsx"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.set_content(
        "src",
        "vol:1",
        content_sha256=H,
        canonical_sha256=H2,
        canonical_method="ooxml-parts@1",
        canonical_parts=[("xl/workbook.xml", H3)],
    )
    m.replace_outputs(
        "src",
        "vol:1",
        [
            output("mirror/src/a/budget.xlsx.d/00-index.md", "index"),
            output("mirror/src/a/budget.xlsx.d/01-q3.md", "sheet:1"),
        ],
    )
    m.add_tombstone(
        TombstoneRow(
            "mirror/src/a/budget.xlsx.d/02-old.md",
            "src",
            "vol:1",
            "sheet:2",
            "2026-09-01",
            1,
            H,
            "c0ffee",
            "2027-03-01",
            "unit-removed",
        )
    )


def test_rekey_after_upsert_moves_content_outputs_tombstones(m: Manifest) -> None:
    _published_old(m)
    m.upsert_observed(
        item("vol:2", "a/Budget.xlsx"), run_id=2, verdict=Verdict.MAYBE_CHANGED, state=RowState.LIVE
    )
    m.rekey("src", "vol:1", "vol:2")
    assert m.get_item("src", "vol:1") is None
    row = m.get_item("src", "vol:2")
    assert row is not None
    assert (row.content_sha256, row.canonical_sha256, row.first_seen_run) == (H, H2, 1)
    assert row.canonical_parts == (("xl/workbook.xml", H3),)
    assert row.last_verdict is Verdict.MAYBE_CHANGED and row.last_seen_run == 2
    assert [o.unit_id for o in m.outputs_for("src", "vol:2")] == ["index", "sheet:1"]
    assert m.outputs_for("src", "vol:1") == []
    tomb = m.get_tombstone("mirror/src/a/budget.xlsx.d/02-old.md")
    assert tomb is not None and tomb.stable_id == "vol:2"


def test_rekey_before_upsert_renames_the_row(m: Manifest) -> None:
    _published_old(m)
    m.rekey("src", "vol:1", "vol:2")
    row = m.get_item("src", "vol:2")
    assert row is not None and row.content_sha256 == H and row.first_seen_run == 1
    assert len(m.outputs_for("src", "vol:2")) == 2


def test_rekey_carries_quarantine_and_reparents_children(m: Manifest) -> None:
    m.upsert_observed(item("d1", "dir", is_dir=True), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.upsert_observed(
        item("c1", "dir/x", parent_id="d1"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE
    )
    m.set_state("src", "d1", RowState.QUARANTINED, "checked-out")
    m.upsert_observed(item("d2", "dir", is_dir=True), run_id=2, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.rekey("src", "d1", "d2")
    row = m.get_item("src", "d2")
    assert row is not None and row.state is RowState.QUARANTINED and row.state_reason == "checked-out"
    child = m.get_item("src", "c1")
    assert child is not None and child.parent_id == "d2"


def test_rekey_missing_old_raises_and_same_id_is_noop(m: Manifest) -> None:
    with pytest.raises(KeyError):
        m.rekey("src", "nope", "x")
    m.rekey("src", "same", "same")


# ---- rederive_paths ----------------------------------------------------------------------------------------


def _tree(m: Manifest, rows: list[tuple[str, str | None, str, str, bool]]) -> None:
    for sid, parent, name, rel, is_dir in rows:
        it = SourceItem(
            source_id="src",
            stable_id=sid,
            rel_path=rel,
            name=name,
            size=0,
            mtime_ns=0,
            ctime_ns=0,
            is_dir=is_dir,
            parent_id=parent,
        )
        m.upsert_observed(it, run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)


def test_rederive_paths_after_folder_rename_touches_only_the_subtree(m: Manifest) -> None:
    _tree(
        m,
        [
            ("root", None, "root", "", True),
            ("fin", "root", "Finance", "Finance", True),
            ("q3", "fin", "Q3", "Finance/Q3", True),
            ("f1", "q3", "budget.xlsx", "Finance/Q3/budget.xlsx", False),
            ("f2", "fin", "plan.docx", "Finance/plan.docx", False),
            ("f3", "root", "other.txt", "other.txt", False),
        ],
    )
    assert m.rederive_paths("src", None) == []
    # delta reports ONE record for the renamed folder; its descendants carry no path
    m._db.execute("UPDATE items SET name = 'Money' WHERE stable_id = 'fin'")
    changes = m.rederive_paths("src", None)
    assert changes == [
        ("f1", "Finance/Q3/budget.xlsx", "Money/Q3/budget.xlsx"),
        ("f2", "Finance/plan.docx", "Money/plan.docx"),
        ("fin", "Finance", "Money"),
        ("q3", "Finance/Q3", "Money/Q3"),
    ]
    f1 = m.get_item("src", "f1")
    assert (
        f1 is not None and f1.rel_path == "Money/Q3/budget.xlsx" and f1.prev_path == "Finance/Q3/budget.xlsx"
    )
    other = m.get_item("src", "f3")
    assert other is not None and other.prev_path is None


def test_rederive_paths_under_a_scope_root_and_unknown_parents(m: Manifest) -> None:
    _tree(
        m,
        [
            ("scope", "driveroot", "Shared", "", True),
            ("a", "scope", "a.txt", "wrong/a.txt", False),
            ("orphan", "ghost", "o.txt", "o.txt", False),
            ("outside", None, "x.txt", "x.txt", False),
        ],
    )
    changes = m.rederive_paths("src", "scope")
    assert changes == [("a", "wrong/a.txt", "a.txt")]


def test_rederive_paths_survives_a_cycle(m: Manifest, caplog: pytest.LogCaptureFixture) -> None:
    _tree(m, [("x", "y", "x", "x", True), ("y", "x", "y", "y", True), ("f", "x", "f", "f", False)])
    with caplog.at_level(logging.WARNING, logger="agentsync.manifest"):
        assert m.rederive_paths("src", None) == []
    assert "cycle" in caplog.text


# ---- outputs, cache ----------------------------------------------------------------------------------------


def test_replace_outputs_returns_removed_units(m: Manifest) -> None:
    first = [
        output("mirror/src/b.xlsx.d/00-index.md", "index"),
        output("mirror/src/b.xlsx.d/01-a.md", "sheet:1"),
        output("mirror/src/b.xlsx.d/02-b.md", "sheet:2"),
    ]
    assert m.replace_outputs("src", "vol:1", first) == []
    second = [
        output("mirror/src/b.xlsx.d/00-index.md", "index", sha=H2),
        output("mirror/src/b.xlsx.d/01-a.md", "sheet:1"),
    ]
    removed = m.replace_outputs("src", "vol:1", second)
    assert [o.unit_id for o in removed] == ["sheet:2"]
    assert [(o.unit_id, o.rendered_sha256) for o in m.outputs_for("src", "vol:1")] == [
        ("index", H2),
        ("sheet:1", H),
    ]
    with pytest.raises(ValueError, match="belongs to"):
        m.replace_outputs("src", "vol:1", [output("mirror/src/z.md", stable_id="other")])
    with pytest.raises(ValueError, match="duplicate unit_id"):
        m.replace_outputs("src", "vol:1", [output("mirror/src/1.md"), output("mirror/src/2.md")])


def test_output_path_is_owned_by_one_item(m: Manifest) -> None:
    m.replace_outputs("src", "vol:1", [output("mirror/src/a.md")])
    with pytest.raises(sqlite3.IntegrityError):
        m.replace_outputs("src", "vol:2", [output("mirror/src/a.md", stable_id="vol:2")])
    assert [o.output_path for o in m.outputs_for("src", "vol:1")] == ["mirror/src/a.md"]


def test_output_by_path_case_insensitive_nfc(m: Manifest) -> None:
    path = unicodedata.normalize("NFC", "mirror/src/Café Notes.docx.md")
    m.replace_outputs("src", "vol:1", [output(path)])
    assert m.output_by_path(path) is not None
    hit = m.output_by_path(unicodedata.normalize("NFD", "MIRROR/SRC/CAFÉ NOTES.DOCX.MD"))
    assert hit is not None and hit.output_path == path
    assert m.output_by_path("mirror/src/other.md") is None


def test_live_action_keys_and_iter_outputs(m: Manifest) -> None:
    m.replace_outputs("src", "vol:1", [output("mirror/src/b.md"), output("mirror/src/a.md", "sheet:1")])
    m.replace_outputs(
        "src",
        "vol:2",
        [output("mirror/src/c.md", stable_id="vol:2", status=OutputStatus.TOMBSTONE, action_key="dead")],
    )
    assert m.live_action_keys() == {"k-whole", "k-sheet:1"}
    assert [o.output_path for o in m.iter_outputs()] == [
        "mirror/src/a.md",
        "mirror/src/b.md",
        "mirror/src/c.md",
    ]
    assert list(m.iter_outputs(source_id="none")) == []


def test_record_cache_is_write_once_and_touches_last_used(m: Manifest) -> None:
    kw = {
        "converter_id": "c",
        "converter_version": "1",
        "options_hash": "o",
        "canonical_sha256": H,
        "status": "ok",
        "unit_count": 1,
        "size": 10,
    }
    assert m.cache_last_used("k") is None, "no row yet"
    m.record_cache("k", run_id=1, **kw)  # type: ignore[arg-type]
    assert m.cache_last_used("k") == 1
    m.record_cache("k", run_id=5, **{**kw, "size": 999})  # type: ignore[arg-type]
    m.record_cache("k", run_id=3, **kw)  # type: ignore[arg-type]
    r = m._db.execute("SELECT created_run, last_used_run, bytes FROM cache WHERE action_key='k'").fetchone()
    assert tuple(r) == (1, 5, 10)
    assert m.cache_last_used("k") == 5 and m.cache_last_used("other") is None


# ---- tombstones --------------------------------------------------------------------------------------------


def test_tombstones_due_and_validation(m: Manifest) -> None:
    for path, reap in (
        ("mirror/src/b.md", "2027-01-02"),
        ("mirror/src/a.md", "2027-01-01"),
        ("mirror/src/c.md", "2027-06-01"),
    ):
        m.add_tombstone(
            TombstoneRow(path, "src", "vol:1", "whole", "2026-07-01", 1, H, None, reap, "deleted-upstream")
        )
    assert [t.output_path for t in m.tombstones_due("2027-01-02")] == ["mirror/src/a.md", "mirror/src/b.md"]
    assert m.tombstones_due("2026-12-31") == []
    assert [t.output_path for t in m.iter_tombstones()] == [
        "mirror/src/a.md",
        "mirror/src/b.md",
        "mirror/src/c.md",
    ]
    m.remove_tombstone("mirror/src/a.md")
    assert m.get_tombstone("mirror/src/a.md") is None
    replaced = TombstoneRow(
        "mirror/src/b.md", "src", "vol:1", "whole", "2026-08-01", 2, H, "abc", "2027-02-01", "moved"
    )
    m.add_tombstone(replaced)
    assert m.get_tombstone("mirror/src/b.md") == replaced
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        m.add_tombstone(replace(replaced, reap_after="2027-2-1"))
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        m.tombstones_due("2027-01-02T00:00:00Z")


# ---- cursors -----------------------------------------------------------------------------------------------


def test_cursor_stage_promote_only_after_commit(m: Manifest) -> None:
    r1 = m.begin_run(CycleMode.POLL, host="h", pid=1)
    m.stage_cursor("src", "cursor-1", r1)
    cur = m.get_cursor("src")
    assert cur is not None and (cur.current, cur.pending, cur.pending_run_id) == (None, "cursor-1", r1)
    assert m.promote_cursors(r1 + 1, "2026-09-29T00:00:00Z") == []
    assert m.promote_cursors(r1, "2026-09-29T00:00:00Z") == ["src"]
    cur = m.get_cursor("src")
    assert cur is not None
    assert (cur.current, cur.current_set_at, cur.pending, cur.pending_run_id) == (
        "cursor-1",
        "2026-09-29T00:00:00Z",
        None,
        None,
    )
    r2 = m.begin_run(CycleMode.POLL, host="h", pid=1)
    m.stage_cursor("src", "cursor-2", r2)
    m.discard_pending(r2)
    cur = m.get_cursor("src")
    assert cur is not None and cur.current == "cursor-1" and cur.pending is None
    assert m.promote_cursors(r2, "2026-09-29T01:00:00Z") == []


def test_stage_none_never_erases_current(m: Manifest) -> None:
    r1 = m.begin_run(CycleMode.POLL, host="h", pid=1)
    m.stage_cursor("src", "cursor-1", r1)
    m.promote_cursors(r1, "2026-09-29T00:00:00Z")
    r2 = m.begin_run(CycleMode.POLL, host="h", pid=1)
    m.stage_cursor("src", None, r2)
    assert m.promote_cursors(r2, "2026-09-29T01:00:00Z") == []
    cur = m.get_cursor("src")
    assert cur is not None and cur.current == "cursor-1"


def test_page_link_and_drop_cursor(m: Manifest) -> None:
    m.set_page_link("src", "https://next?$skiptoken=abc")
    cur = m.get_cursor("src")
    assert cur is not None and cur.page_link == "https://next?$skiptoken=abc" and cur.current is None
    m.stage_cursor("src", "p", 1)
    cur = m.get_cursor("src")
    assert cur is not None and cur.page_link is not None  # staging keeps the resume point
    m.set_page_link("src", None)
    m.drop_cursor("src")
    assert m.get_cursor("src") is None


def test_cursor_values_never_logged(m: Manifest, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="agentsync"):
        run = m.begin_run(CycleMode.POLL, host="h", pid=1)
        m.stage_cursor("src", SECRET_CURSOR, run)
        m.set_page_link("src", SECRET_CURSOR)
        m.promote_cursors(run, "2026-09-29T00:00:00Z")
        m.sync_sources([local_source(path="/moved")])
    assert "SUPERSECRETTOKEN" not in caplog.text


def test_cursor_fingerprint() -> None:
    fp = cursor_fingerprint(SECRET_CURSOR)
    assert len(fp) == 12 and all(c in "0123456789abcdef" for c in fp)
    assert "SUPERSECRET" not in fp
    assert fp == cursor_fingerprint(SECRET_CURSOR) != cursor_fingerprint(SECRET_CURSOR + "x")
    assert cursor_fingerprint(None) == "-"


# ---- depends -----------------------------------------------------------------------------------------------


def test_replace_depends_and_pages_citing(m: Manifest) -> None:
    rows = [
        DependsRow("topics/b.md", "mirror/src/x.md", H, "primary"),
        DependsRow("topics/a.md", "mirror/src/x.md", H, "corroborating"),
        DependsRow("topics/a.md", "mirror/src/y.md", H2, "primary"),
    ]
    m.replace_depends(rows)
    assert m.pages_citing("mirror/src/x.md") == ["topics/a.md", "topics/b.md"]
    m.replace_depends([rows[0], DependsRow("topics/b.md", "mirror/src/x.md", H2, "corroborating")])
    assert m.pages_citing("mirror/src/y.md") == []
    stored = m._db.execute("SELECT pinned_sha FROM depends").fetchall()
    assert [r[0] for r in stored] == [H]  # duplicate (page, source): first in sorted order wins
    m.replace_depends([])
    assert m.pages_citing("mirror/src/x.md") == []


# ---- export ------------------------------------------------------------------------------------------------


def test_export_shard_is_deterministic_and_carries_nothing_volatile(m: Manifest) -> None:
    m.upsert_observed(item("z", "b/é.docx", etag="e"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.upsert_observed(item("a", "a/x.txt"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.upsert_observed(item("d", "dir", is_dir=True), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.upsert_observed(item("t", "gone.md"), run_id=1, verdict=Verdict.CREATED, state=RowState.TOMBSTONE)
    m.set_state("src", "a", RowState.REFUSED, "no converter for .txt")
    m.replace_outputs("src", "z", [output("mirror/src/b/e.docx.md", stable_id="z", sha=H2)])
    m.replace_outputs("src", "a", [output("mirror/src/a/x.txt.md", "whole", stable_id="a", sha=None)])
    lines = m.export_shard("src")
    assert [json.loads(line)["stable_id"] for line in lines] == ["a", "t", "z"]
    assert lines[2] == (
        '{"outputs":[{"path":"mirror/src/b/e.docx.md","rendered_sha256":"' + H2 + '","unit_id":"whole"}],'
        '"rel_path":"b/é.docx","source_id":"src","stable_id":"z","state":"live","state_reason":null}'
    )
    assert json.loads(lines[0])["state_reason"] == "no converter for .txt"
    assert json.loads(lines[1]) == {
        "outputs": [],
        "rel_path": "gone.md",
        "source_id": "src",
        "stable_id": "t",
        "state": "tombstone",
        "state_reason": None,
    }
    # a no-op observation (new run, re-stamped H0) produces a byte-identical shard
    m.upsert_observed(
        item("z", "b/é.docx", etag="e", mtime_ns=99), run_id=2, verdict=Verdict.UNCHANGED, state=RowState.LIVE
    )
    m.set_content("src", "z", content_sha256=H, canonical_sha256=H3, canonical_method="m", canonical_parts=[])
    assert m.export_shard("src") == lines
    assert m.export_shard("other") == []


def test_tree_sha_roundtrip(m: Manifest) -> None:
    assert m.tree_sha() is None
    m.set_tree_sha("deadbeef")
    assert m.tree_sha() == "deadbeef"


def test_upsert_resurrecting_a_tombstone_forgets_content_hashes(m: Manifest) -> None:
    m.upsert_observed(item(), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.set_content(
        "src",
        "vol:1",
        content_sha256=H,
        canonical_sha256=H2,
        canonical_method="bytes@1",
        canonical_parts=[("p", H3)],
    )
    m.upsert_observed(item(), run_id=2, verdict=Verdict.UNCHANGED, state=RowState.TOMBSTONE)
    row = m.get_item("src", "vol:1")
    assert row is not None and row.canonical_sha256 == H2  # tombstoning itself keeps them
    m.upsert_observed(item(), run_id=3, verdict=Verdict.MAYBE_CHANGED, state=RowState.LIVE)
    row = m.get_item("src", "vol:1")
    assert row is not None and row.state is RowState.LIVE
    assert (row.content_sha256, row.canonical_sha256, row.canonical_method, row.canonical_parts) == (
        None,
        None,
        None,
        (),
    )


# ---------------------------------------------------------------------------------------------------------
# batched pass writes, the H0 observation index, counters (perf work; semantics must not move)
# ---------------------------------------------------------------------------------------------------------


def _all_rows(m: Manifest) -> list[tuple[object, ...]]:
    return [tuple(r) for r in m._db.execute("SELECT * FROM items ORDER BY source_id, stable_id").fetchall()]


def _seed_for_upserts(man: Manifest) -> None:
    man.upsert_observed(item("keep", "a/keep.docx"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    man.upsert_observed(item("mv", "a/old.docx"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    man.upsert_observed(
        item("dl", "a/dl.docx", gen_count=4, ino=9), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE
    )
    man.upsert_observed(item("tomb", "a/t.docx"), run_id=1, verdict=Verdict.CREATED, state=RowState.TOMBSTONE)
    man.set_content(
        "src", "tomb", content_sha256=H, canonical_sha256=H2, canonical_method="m", canonical_parts=[]
    )
    man.upsert_observed(item("gone", "a/g.docx"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    man.upsert_observed(item("q", "a/q.docx"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    man.set_state("src", "q", RowState.QUARANTINED, "encrypted")


def _pass_observations() -> list[tuple[SourceItem, Verdict, RowState]]:
    return [
        (item("new", "b/new.txt", extra={"dedup_name": "new.txt"}), Verdict.CREATED, RowState.LIVE),
        (item("keep", "a/keep.docx", mtime_ns=5), Verdict.MAYBE_CHANGED, RowState.LIVE),
        (item("mv", "a/renamed.docx"), Verdict.METADATA_ONLY, RowState.LIVE),
        (item("dl", "a/dl.docx", dataless=True, mtime_ns=77), Verdict.DATALESS, RowState.DATALESS),
        (item("tomb", "a/t.docx"), Verdict.MAYBE_CHANGED, RowState.LIVE),
        (item("gone", "a/g.docx", deleted=True), Verdict.DELETED, RowState.TOMBSTONE),
        (item("q", "a/q.docx"), Verdict.UNCHANGED, RowState.QUARANTINED),
        (item("keep", "a/keep2.docx", mtime_ns=6), Verdict.MAYBE_CHANGED, RowState.LIVE),  # listed twice
    ]


def test_upsert_observed_many_equals_one_by_one(tmp_path: Path) -> None:
    one, many = Manifest(tmp_path / "one.sqlite"), Manifest(tmp_path / "many.sqlite")
    for man in (one, many):
        man.sync_sources([local_source()])
        _seed_for_upserts(man)
    obs = _pass_observations()
    for it, verdict, state in obs:
        one.upsert_observed(it, run_id=2, verdict=verdict, state=state)
    before = {r.stable_id: r for r in many.iter_items("src")}
    with many.transaction():
        many.upsert_observed_many(obs, run_id=2, existing={k: v for k, v in before.items() if k != "mv"})
    assert _all_rows(many) == _all_rows(one)
    row = many.get_item("src", "keep")
    assert row is not None and row.rel_path == "a/keep2.docx" and row.prev_path == "a/keep.docx"
    tomb = many.get_item("src", "tomb")
    assert tomb is not None and tomb.content_sha256 is None  # reappearance forgot the hashes
    one.close()
    many.close()


def test_upsert_observed_many_outside_a_transaction_is_atomic(m: Manifest) -> None:
    m.upsert_observed_many([], run_id=1)  # nothing to do
    bad = [(item("ok", "a/ok.md"), Verdict.CREATED, RowState.LIVE), (item("x", "a/x.md", source_id="ghost"),
           Verdict.CREATED, RowState.LIVE)]  # fmt: skip
    with pytest.raises(sqlite3.IntegrityError):
        m.upsert_observed_many(bad, run_id=1)
    assert m.get_item("src", "ok") is None  # rolled back as one unit


def test_touch_observed_stamps_only_the_pass_columns(m: Manifest) -> None:
    m.upsert_observed(item("a", "a.md", gen_count=3), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.upsert_observed(item("b", "b.md"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.set_verdict("src", "a", Verdict.OUTPUT_UNCHANGED)
    before = {r.stable_id: r for r in m.iter_items("src")}
    assert m.touch_observed("src", ["a", "a", "nope"], run_id=7) == 1
    assert m.touch_observed("src", [], run_id=8) == 0
    after = {r.stable_id: r for r in m.iter_items("src")}
    assert after["a"] == replace(before["a"], last_seen_run=7, last_verdict=Verdict.UNCHANGED)
    assert after["b"] == before["b"]  # not listed: untouched


def test_observation_index_matches_item_rows(m: Manifest) -> None:
    m.upsert_observed(
        item("a", "x/é.md", etag="e", gen_count=2, ino=5, mode=0o100644, created_ns=3, extra={"k": "v"},
             remote_hashes=RemoteHashes(quickxor="q", sha1="s1", sha256="s2"), content_type="text/markdown"),
        run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE,
    )  # fmt: skip
    m.upsert_observed(item("d", "x", is_dir=True), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.upsert_observed(
        item("t", "t.md", dataless=True), run_id=1, verdict=Verdict.DATALESS, state=RowState.TOMBSTONE
    )
    m.set_content("src", "a", content_sha256=H, canonical_sha256=H2, canonical_method="m", canonical_parts=[])
    index = m.observation_index("src")
    assert sorted(index) == ["a", "d", "t"]
    for sid, light in index.items():
        full = m.get_item("src", sid)
        assert full is not None
        for name in light._fields:
            assert getattr(light, name) == getattr(full, name), name
        assert type(light.state) is RowState and type(light.is_dir) is bool and type(light.dataless) is bool
    assert index["a"].extra == {"k": "v"} and index["a"].last_verdict is Verdict.CREATED
    assert m.observation_index("other") == {}


def test_get_items_batches_beyond_the_parameter_chunk(m: Manifest) -> None:
    ids = [f"vol:{i:05d}" for i in range(manifest_mod._ID_CHUNK * 2 + 5)]
    m.upsert_observed_many([(item(i, f"f/{i}.md"), Verdict.CREATED, RowState.LIVE) for i in ids], run_id=1)
    got = m.get_items("src", [*ids, "missing", ids[0]])
    assert sorted(got) == ids and got[ids[7]] == m.get_item("src", ids[7])
    assert m.get_items("src", []) == {}


def test_state_counts_and_find_by_size(m: Manifest) -> None:
    m.sync_sources([local_source(), local_source("inbox", "/tmp/inbox")])
    m.upsert_observed(item("a", "a.md", size=5), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.upsert_observed(item("b", "b.md", size=5), run_id=1, verdict=Verdict.CREATED, state=RowState.DATALESS)
    m.upsert_observed(item("c", "c.md", size=5), run_id=1, verdict=Verdict.CREATED, state=RowState.TOMBSTONE)
    m.upsert_observed(
        item("d", "d", size=5, is_dir=True), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE
    )
    m.upsert_observed(
        item("e", "e.md", size=6, source_id="inbox"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE
    )
    m.set_verdict("src", "a", Verdict.DEFERRED)
    counts, deferred = m.state_counts("src")
    assert counts == {RowState.LIVE: 1, RowState.DATALESS: 1, RowState.TOMBSTONE: 1} and deferred == 1
    assert m.state_counts("nobody") == ({}, 0)
    assert [(r.source_id, r.stable_id) for r in m.find_by_size(5)] == [("src", "a"), ("src", "b")]
    assert [r.stable_id for r in m.find_by_size(6)] == ["e"] and m.find_by_size(7) == []


@pytest.mark.parametrize(
    "text",
    [
        "plain",
        "é/ünïcødé",
        'quote " and \\ backslash',
        "tab\tnewline\nbell\x07 del\x7f",
        chr(0x2028) + chr(0x2029),
        "😀/x",
    ],
)
def test_export_shard_lines_are_byte_identical_to_json_dumps(m: Manifest, text: str) -> None:
    m.upsert_observed(item("s", text), run_id=1, verdict=Verdict.CREATED, state=RowState.DATALESS)
    m.set_state("src", "s", RowState.DATALESS, text)
    m.replace_outputs("src", "s", [output(f"mirror/{text}.md", stable_id="s", sha=None),
                                  output("mirror/b.md", "index", stable_id="s")])  # fmt: skip
    (line,) = m.export_shard("src")
    reference = {
        "outputs": [
            {"path": "mirror/b.md", "rendered_sha256": H, "unit_id": "index"},
            {"path": f"mirror/{text}.md", "rendered_sha256": None, "unit_id": "whole"},
        ],
        "rel_path": text,
        "source_id": "src",
        "stable_id": "s",
        "state": "live",
        "state_reason": text,
    }
    assert line == json.dumps(reference, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def test_touch_observed_writes_the_verdict_only_where_it_changes(m: Manifest) -> None:
    for sid in ("a", "b"):
        m.upsert_observed(item(sid, f"{sid}.md"), run_id=1, verdict=Verdict.CREATED, state=RowState.LIVE)
    m.set_verdict("src", "a", Verdict.OUTPUT_UNCHANGED)
    m.set_verdict("src", "b", Verdict.TOUCHED_NOT_CHANGED)
    assert m.touch_observed("src", ["a", "b"], run_id=5, verdict_changed=["b", "zz"]) == 2
    rows = {r.stable_id: r for r in m.iter_items("src")}
    assert (rows["a"].last_seen_run, rows["a"].last_verdict) == (5, Verdict.OUTPUT_UNCHANGED)
    assert (rows["b"].last_seen_run, rows["b"].last_verdict) == (5, Verdict.UNCHANGED)
    assert m.touch_observed("src", ["a"], run_id=6, verdict_changed=[]) == 1
    assert m.get_item("src", "a").last_verdict is Verdict.OUTPUT_UNCHANGED  # type: ignore[union-attr]


# ---- files to read again (CONTRACTS.md 16.27) --------------------------------------------------------------

OLD, NEW = "2.0.0+pdfium-1", "2.1.0+pdfium-1"
NO_TEXT = "no text layer"
NO_PNG = "no converter for .png"
PDF_PAGE = ("pdf", OLD, None, ".pdf")
PDF_STUB = ("pdf", OLD, NO_TEXT, ".pdf")
PNG_STUB = ("none", "0", NO_PNG, ".png")


def _mirrored(
    m: Manifest,
    sid: str,
    name: str,
    *,
    made: tuple[str | None, str | None] = ("pdf", OLD),
    cached: tuple[str, str] | None = ("pdf", OLD),
    status: OutputStatus = OutputStatus.OK,
    state: RowState = RowState.LIVE,
    reason: str | None = None,
    verdict: Verdict = Verdict.UNCHANGED,
    run: int = 5,
    **observed: object,
) -> None:
    """One file with one page: ``made`` is what its output row names, ``cached`` what the cache row of its
    action key names (None: no such row)."""
    m.upsert_observed(item(sid, f"p/{name}", **observed), run_id=run, verdict=verdict, state=state)
    if reason is not None:
        m.set_state("src", sid, state, reason)
    key = None if made[0] is None else f"key-{sid}"
    page = output(
        f"mirror/src/{sid}.md",
        stable_id=sid,
        action_key=key,
        status=status,
        converter_id=made[0],
        converter_version=made[1],
    )
    m.replace_outputs("src", sid, [page])
    if cached is not None and key is not None:
        m.record_cache(
            key,
            converter_id=cached[0],
            converter_version=cached[1],
            options_hash="o",
            canonical_sha256=H,
            status="ok" if status is OutputStatus.OK else "unreadable",
            unit_count=1,
            size=1,
            run_id=run,
        )


def _candidates(m: Manifest, targets: list[tuple[str, str, str | None, str]], **kw: object) -> list[str]:
    kw.setdefault("seen_run", 5)
    kw.setdefault("limit", 50)
    return [r.stable_id for r in m.reread_candidates("src", targets, **kw)]  # type: ignore[arg-type]


def test_produced_by_names_each_converter_version_and_stub_reason_once(m: Manifest) -> None:
    """One query, no row decoded: what made the pages of the files on this Mac.  The cache row of a page's
    action key has the last word (the H2 cutoff moves the key and nothing else), the output row stands in
    where it is gone, and a stub counts only when it is its item's own state."""
    stub = {"status": OutputStatus.QUARANTINED, "state": RowState.QUARANTINED}
    refusal = {"status": OutputStatus.REFUSED, "state": RowState.REFUSED}
    _mirrored(m, "a", "one.pdf")
    _mirrored(m, "b", "two.pdf")
    _mirrored(m, "c", "cut off.pdf", cached=("pdf", NEW))
    _mirrored(m, "d", "no cache row.pdf", made=("pdf", "1.9.0"), cached=None)
    _mirrored(m, "e", "scan.pdf", reason=NO_TEXT, **stub)
    _mirrored(m, "f", "plan.png", made=("none", "0"), cached=None, reason=NO_PNG, **refusal)
    _mirrored(m, "g", "labelled.pdf", reason="refused: excluded label", **refusal)
    _mirrored(
        m, "h", "online.pdf", made=("pdf", "0.1.0"), cached=None, dataless=True, state=RowState.DATALESS
    )
    _mirrored(m, "i", "gone.pdf", made=("pdf", "0.2.0"), cached=None, state=RowState.TOMBSTONE)
    _mirrored(
        m,
        "j",
        "failed.pdf",
        made=("pdf", "0.3.0"),
        cached=None,
        status=OutputStatus.FAILED,
        state=RowState.QUARANTINED,
        reason="conversion failed",
    )
    _mirrored(
        m,
        "k",
        "copy.pdf",
        made=("pdf", "0.4.0"),
        cached=None,
        status=OutputStatus.REFUSED,
        state=RowState.QUARANTINED,
        reason="duplicate-of drive",
    )
    _mirrored(m, "l", "key.pdf", made=(None, None), cached=None, reason="contains a credential", **stub)
    _mirrored(
        m, "n", "online scan.pdf", made=("pdf", "0.5.0"), cached=None, reason=NO_TEXT, dataless=True, **stub
    )
    m.upsert_observed(item("m", "p", is_dir=True), run_id=5, verdict=Verdict.UNCHANGED, state=RowState.LIVE)
    assert m.produced_by("src") == [
        ("none", "0", NO_PNG),
        ("pdf", "1.9.0", None),
        ("pdf", OLD, None),
        ("pdf", OLD, NO_TEXT),
        ("pdf", OLD, "refused: excluded label"),
        ("pdf", NEW, None),
    ]
    assert m.produced_by("another") == []


def test_reread_candidates_are_files_this_pass_listed_unchanged_on_this_mac(m: Manifest) -> None:
    stub = {"status": OutputStatus.QUARANTINED, "state": RowState.QUARANTINED}
    refusal = {"status": OutputStatus.REFUSED, "state": RowState.REFUSED}
    _mirrored(m, "a", "one.pdf")
    _mirrored(m, "b", "UPPER.PDF")
    _mirrored(m, "c", "no cache row.pdf", cached=None)
    _mirrored(m, "d", "scan.pdf", reason=NO_TEXT, **stub)
    _mirrored(m, "e", "plan.png", made=("none", "0"), cached=None, reason=NO_PNG, **refusal)
    # Not one of them: each is what the pass, the Mac or the page says no to.
    _mirrored(m, "n-pending", "pending.pdf", verdict=Verdict.MAYBE_CHANGED)
    _mirrored(m, "n-deferred", "deferred.pdf", verdict=Verdict.DEFERRED)
    _mirrored(m, "n-deleted", "deleted.pdf", verdict=Verdict.DELETED)
    _mirrored(m, "n-touched", "touched.pdf", verdict=Verdict.TOUCHED_NOT_CHANGED)
    _mirrored(m, "n-unlisted", "unlisted.pdf", run=4)
    _mirrored(m, "n-online", "online.pdf", dataless=True, state=RowState.DATALESS)
    # A stub keeps its state while its file is online-only: only the row's own flag says where the file is.
    _mirrored(m, "n-online-scan", "online scan.pdf", reason=NO_TEXT, dataless=True, **stub)
    _mirrored(
        m,
        "n-online-image",
        "online.png",
        made=("none", "0"),
        cached=None,
        reason=NO_PNG,
        dataless=True,
        **refusal,
    )
    _mirrored(m, "n-current", "current.pdf", cached=("pdf", NEW))
    _mirrored(m, "n-gone", "gone.pdf", state=RowState.TOMBSTONE)
    _mirrored(m, "n-encrypted", "locked.pdf", reason="encrypted-pdf", **stub)
    _mirrored(m, "n-labelled", "labelled.pdf", reason="refused: excluded label", **refusal)
    _mirrored(
        m, "n-failed", "failed.pdf", status=OutputStatus.FAILED, state=RowState.QUARANTINED, reason=NO_TEXT
    )
    _mirrored(m, "n-other-type", "notes.pdf.txt")
    _mirrored(m, "n-bare", ".pdf")
    _mirrored(
        m, "n-mp4", "clip.mp4", made=("none", "0"), cached=None, reason="no converter for .mp4", **refusal
    )
    m.upsert_observed(
        item("n-dir", "p/folder.pdf", is_dir=True), run_id=5, verdict=Verdict.UNCHANGED, state=RowState.LIVE
    )
    targets = [PDF_PAGE, PDF_STUB, PNG_STUB]
    assert _candidates(m, targets) == ["a", "b", "c", "d", "e"]
    assert _candidates(m, [PDF_PAGE]) == ["a", "b", "c"] and _candidates(m, [PDF_STUB]) == ["d"]
    assert _candidates(m, [PNG_STUB]) == ["e"] and _candidates(m, []) == []
    assert _candidates(m, [("pdf", NEW, None, ".pdf")]) == ["n-current"]
    assert _candidates(m, targets, seen_run=4) == ["n-unlisted"]
    # In stable-id order, a page at a time, never the ids the caller has had.
    assert _candidates(m, targets, limit=2) == ["a", "b"]
    assert _candidates(m, targets, limit=2, after="b") == ["c", "d"]
    assert _candidates(m, targets, after="e") == []
    assert _candidates(m, targets, skip=["a", "d", "zz"]) == ["b", "c", "e"]
    [row] = m.reread_candidates("src", [PDF_STUB], seen_run=5, limit=1)
    assert (row.rel_path, row.state, row.state_reason) == ("p/scan.pdf", RowState.QUARANTINED, NO_TEXT)
    assert (
        _candidates(m, targets, seen_run=6) == []
        and m.reread_candidates("other", targets, seen_run=5, limit=9) == []
    )


def test_reread_left_counts_what_a_pass_could_not_reach_and_nothing_that_is_not_on_this_mac(
    m: Manifest,
) -> None:
    """The cycle asks this before it stops looking.  A pending row and a row the pass did not list are still
    to come; an online-only file, a tombstone and a current page are not."""
    stub = {"status": OutputStatus.QUARANTINED, "state": RowState.QUARANTINED}
    _mirrored(m, "online", "online.pdf", dataless=True, state=RowState.DATALESS)
    _mirrored(m, "online-scan", "online scan.pdf", reason=NO_TEXT, dataless=True, **stub)
    _mirrored(m, "gone", "gone.pdf", state=RowState.TOMBSTONE)
    _mirrored(m, "current", "current.pdf", cached=("pdf", NEW))
    assert not m.reread_left("src", [PDF_PAGE, PDF_STUB]) and not m.reread_left("src", [])
    _mirrored(m, "pending", "pending.pdf", verdict=Verdict.ERROR)
    _mirrored(m, "unlisted", "unlisted.pdf", run=2)
    assert m.reread_left("src", [PDF_PAGE]) and _candidates(m, [PDF_PAGE]) == []
    assert m.reread_left("src", [PDF_PAGE], skip=["pending"])
    assert not m.reread_left("src", [PDF_PAGE], skip=["pending", "unlisted"])
    # The same files as a number, for the run record: what is left, the files given up left out.
    assert m.reread_count("src", [PDF_PAGE]) == 2 and m.reread_count("src", [PDF_PAGE], skip=["pending"]) == 1
    assert m.reread_count("src", [PDF_STUB, PNG_STUB]) == 0 and m.reread_count("src", []) == 0
    assert m.reread_count("other", [PDF_PAGE]) == 0
    assert not m.reread_left("src", [PDF_STUB, PNG_STUB]) and not m.reread_left("other", [PDF_PAGE])
    # The work queue asks about the one file it has in hand, whatever its verdict.
    assert m.reread_left("src", [PDF_PAGE], only="pending") and m.reread_left(
        "src", [PDF_PAGE], only="unlisted"
    )
    assert not m.reread_left("src", [PDF_PAGE], only="current"), "its cache row says the new version"
    assert not m.reread_left("src", [PDF_PAGE], only="online") and not m.reread_left(
        "src", [PDF_PAGE], only="x"
    )
    assert not m.reread_left("src", [PDF_PAGE], only="pending", skip=["pending"])
    assert not m.reread_left("src", [PDF_STUB], only="pending") and not m.reread_left(
        "src", [], only="pending"
    )


def test_reread_targets_past_one_statement_give_each_file_once(
    m: Manifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Targets are bound values, a few hundred a statement.  A file two statements name comes back once."""
    monkeypatch.setattr(manifest_mod, "_REREAD_TARGETS", 2)
    for n in range(5):
        _mirrored(m, f"f{n}", f"file {n}.pdf", made=("pdf", f"1.{n}.0"), cached=None)
    targets: list[tuple[str, str, str | None, str]] = [("pdf", f"1.{n}.0", None, ".pdf") for n in range(5)]
    targets += [("pdf", "1.0.0", None, "0.pdf"), ("pdf", "1.4.0", None, "e 4.pdf")]  # f0 and f4 a second time
    assert _candidates(m, targets) == [f"f{n}" for n in range(5)]
    assert _candidates(m, targets, limit=3) == ["f0", "f1", "f2"]
    assert _candidates(m, targets, limit=3, after="f2") == ["f3", "f4"]
    assert m.reread_left("src", targets) and not m.reread_left(
        "src", targets, skip=[f"f{n}" for n in range(5)]
    )
    assert m.reread_count("src", targets) == 5 and m.reread_count("src", targets, skip=["f0", "f4"]) == 3


def test_last_reread_counts_are_those_of_the_newest_run_that_says_what_it_looked_for(m: Manifest) -> None:
    """``loop.next_step`` words its re-read note from the newest run that brought a re-read record up to
    date.  A later run that looked at nothing (a ``materialise PATH`` run, a run that failed before its
    sources) has no ``reread_for`` and is passed over."""

    def ran(status: str, counts: dict[str, int]) -> None:
        run = m.begin_run(CycleMode.POLL, host="h", pid=1)
        m.finish_run(run, status=status, commit_sha=None, counts=counts)

    assert m.last_reread_counts() is None
    looked = {"converted": 0, "reread": 12, "reread_for": 7, "reread_left": 30}
    ran("ok", {"converted": 4})
    ran("ok", looked)
    assert m.last_reread_counts() == looked
    ran("failed", {"converted": 1})
    m.begin_run(CycleMode.POLL, host="h", pid=1)  # still running: its record is empty
    assert m.last_reread_counts() == looked
