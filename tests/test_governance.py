"""governance: purge, history compaction, holds, remote policy, Time Machine exclusions, offboarding.

Every git test runs real git on repos under tmp_path.  The only Keychain writes are items with service
``agentsync-test`` that the test deletes; no launchd job is ever created or removed (a fake uninstaller).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentsync import gitops
from agentsync import governance as gv
from agentsync import manifest as manifest_mod
from agentsync.config import Config
from agentsync.convert.pieces import PieceStore
from agentsync.errors import ConfigError
from agentsync.frontmatter import MirrorFrontmatter, render_mirror_page
from agentsync.graph import auth
from agentsync.manifest import Manifest, TombstoneRow
from agentsync.model import PageStatus
from agentsync.ops import doctor
from agentsync.ops.launchd import plist_path
from agentsync.publish import render_tombstone
from agentsync.text import plural

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------


def git(repo: Path, *args: str, date: str | None = None, check: bool = True) -> str:
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args}: {proc.stderr}")
    return proc.stdout


def commit_all(repo: Path, msg: str, date: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", msg, date=date)
    return git(repo, "rev-parse", "HEAD").strip()


def write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def page(source_id: str, stable_id: str, source_path: str, body: str) -> str:
    fm = MirrorFrontmatter(
        source_kind="local",
        source_id=source_id,
        stable_id=stable_id,
        source_path=source_path,
        status=PageStatus.CURRENT,
        content_sha256=sha("c" + body),
        canonical_sha256=sha("k" + body),
        rendered_sha256=sha(body),
        converter="text-plain@1",
        options_hash="sha256:" + "0" * 64,
        summary="s",
        tokens_estimate=1,
    )
    return render_mirror_page(fm, body + "\n")


def all_blobs(repo: Path) -> dict[str, str]:
    """blob sha -> first path, over every object reachable from any ref."""
    names: dict[str, str] = {}
    for line in git(repo, "rev-list", "--objects", "--all").splitlines():
        s, _, p = line.partition(" ")
        names.setdefault(s, p)
    kinds = subprocess.run(  # one git for every object's type, not one per object
        ["git", "-C", str(repo), "cat-file", "--batch-check=%(objectname) %(objecttype)"],
        input="".join(f"{s}\n" for s in names),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {s: names[s] for s, kind in (line.split() for line in kinds.splitlines()) if kind == "blob"}


def exists(repo: Path, obj: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", obj], check=False).returncode == 0


def make_config(tmp_path: Path, repo: Path, state: Path, extra_toml: str = "") -> Config:
    cfg_path = tmp_path / "agent-context" / "sources.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(extra_toml, encoding="utf-8")
    return Config(
        config_path=cfg_path,
        docs_repo=repo,
        state_dir=state,
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        launchd_label_prefix="com.agentsync-test",
    )


def cache_entry(cache: Path, key: str, body_sha: str) -> Path:
    d = cache / key[:2] / key
    write(d / "result.json", json.dumps({"units": [{"body_sha256": body_sha}]}))
    write(d / "unit-0.md", "cached body")
    return d


class World:
    """A docs repo with 5 commits, a manifest, a converter cache, logs and an ignored STATE.md."""

    def __init__(self, tmp_path: Path, repo: Path, state: Path, extra_toml: str = "") -> None:
        self.repo = repo
        self.config = make_config(tmp_path, repo, state, extra_toml)
        self.state = state
        write(repo / ".gitignore", "_sync/STATE.md\n")
        # c1: three items; the secret page has a sidecar
        write(repo / "mirror/src/secret.docx.md", page("src", "S1", "hr/secret.docx", "SECRET-ALPHA-1"))
        write(repo / "mirror/src/secret.docx.files/img.png", b"PNG-SECRET-SIDECAR")
        write(repo / "mirror/src/keep.md", page("src", "K1", "keep.txt", "KEEP-BODY"))
        write(repo / "mirror/src/gone.md", page("src", "G1", "gone.txt", "GONE-BODY"))
        shard = [
            {"outputs": [], "rel_path": "gone.txt", "source_id": "src", "stable_id": "G1", "state": "live"},
            {"outputs": [], "rel_path": "keep.txt", "source_id": "src", "stable_id": "K1", "state": "live"},
            {
                "outputs": [],
                "rel_path": "hr/secret.docx",
                "source_id": "src",
                "stable_id": "S1",
                "state": "live",
            },
        ]
        write(repo / "_manifest/src.jsonl", "".join(json.dumps(o, sort_keys=True) + "\n" for o in shard))
        write(
            repo / "CHANGELOG/2026-06.md",
            "# CHANGELOG 2026-06\n\n## 2026-06-01 · run 1 · sync: 3a\n\n"
            "- A `mirror/src/gone.md`\n- A `mirror/src/keep.md`\n- A `mirror/src/secret.docx.md`\n",
        )
        write(repo / "_sync/QUARANTINE.tsv", "source_id\tpath\treason\nsrc\thr/secret.docx\tencrypted\n")
        write(
            repo / "INDEX.md",
            "# Index\n\n- [secret](mirror/src/secret.docx.md): x\n- [keep](mirror/src/keep.md): y\n",
        )
        self.c1 = commit_all(repo, "sync: 3a 0m 0r 0d src", "2026-06-01T10:00:00Z")
        # c2: secret changes
        write(repo / "mirror/src/secret.docx.md", page("src", "S1", "hr/secret.docx", "SECRET-ALPHA-2"))
        self.c2 = commit_all(repo, "sync: 0a 1m 0r 0d src", "2026-06-10T10:00:00Z")
        # c3: secret renamed upstream (same stable id, new path) and changed again
        (repo / "mirror/src/secret.docx.md").unlink()
        (repo / "mirror/src/secret.docx.files/img.png").unlink()
        write(
            repo / "mirror/src/renamed-secret.docx.md",
            page("src", "S1", "hr/renamed-secret.docx", "SECRET-ALPHA-3"),
        )
        with (repo / "CHANGELOG/2026-06.md").open("a", encoding="utf-8") as fh:
            fh.write(
                "\n## 2026-06-15 · run 3\n\n"
                "- R `mirror/src/renamed-secret.docx.md` ← `mirror/src/secret.docx.md`\n"
            )
        self.c3 = commit_all(repo, "sync: 0a 0m 1r 0d src", "2026-06-15T10:00:00Z")
        # c4: G1 deleted upstream -> tombstone pointing at c3
        self.tomb = TombstoneRow(
            output_path="mirror/src/gone.md",
            source_id="src",
            stable_id="G1",
            unit_id="whole",
            deleted_at="2026-06-20",
            deleted_run=4,
            last_rendered_sha256=sha("GONE-BODY"),
            last_commit=self.c3,
            reap_after="2026-12-17",
            reason="deleted-upstream",
        )
        write(
            repo / "mirror/src/gone.md",
            render_tombstone(self.tomb, title="gone", source_kind="local", source_path="gone.txt"),
        )
        self.c4 = commit_all(repo, "sync: 0a 0m 0r 1d src", "2026-06-20T10:00:00Z")
        # c5: a curated page citing the secret page
        write(repo / "topics/overview.md", "# Overview\n\nSee ../mirror/src/renamed-secret.docx.md\n")
        self.c5 = commit_all(repo, "curate: overview", "2026-06-21T10:00:00Z")
        write(repo / "_sync/STATE.md", "state\n- last change mirror/src/renamed-secret.docx.md\n- ok\n")
        self.secret_blobs = {s for s, p in all_blobs(repo).items() if "secret" in p}
        self._manifest()
        self._cache_and_logs(tmp_path)

    def _manifest(self) -> None:
        db = self.config.state_paths.db
        m = Manifest(db)
        m.set_tree_sha(git(self.repo, "rev-parse", "HEAD^{tree}").strip())
        m.close()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO sources (source_id, kind, config_state, config_fingerprint) "
            "VALUES ('src','local','live','x')"
        )
        for sid, rel, state, canon in (
            ("S1", "hr/renamed-secret.docx", "live", "CANON_S"),
            ("K1", "keep.txt", "live", "CANON_K"),
            ("G1", "gone.txt", "tombstone", None),
        ):
            conn.execute(
                "INSERT INTO items (source_id, stable_id, name, rel_path, state, first_seen_run, "
                "last_seen_run, "
                "canonical_sha256) VALUES ('src', ?, ?, ?, ?, 1, 1, ?)",
                (sid, rel.rsplit("/", 1)[-1], rel, state, canon),
            )
        self.key_s, self.key_k, self.key_old = "a" * 64, "b" * 64, "c" * 64
        for path, sid, key, status in (
            ("mirror/src/renamed-secret.docx.md", "S1", self.key_s, "ok"),
            ("mirror/src/keep.md", "K1", self.key_k, "ok"),
            ("mirror/src/gone.md", "G1", None, "tombstone"),
        ):
            conn.execute(
                "INSERT INTO outputs (output_path, source_id, stable_id, unit_id, action_key, "
                "rendered_sha256, "
                "status, built_run) VALUES (?, 'src', ?, 'whole', ?, ?, ?, 1)",
                (path, sid, key, sha(sid), status),
            )
        t = self.tomb
        conn.execute(
            "INSERT INTO tombstones VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                t.output_path,
                t.source_id,
                t.stable_id,
                t.unit_id,
                t.deleted_at,
                t.deleted_run,
                t.last_rendered_sha256,
                t.last_commit,
                t.reap_after,
                t.reason,
            ),
        )
        for key, canon in ((self.key_s, "CANON_S"), (self.key_k, "CANON_K")):
            conn.execute(
                "INSERT INTO cache VALUES (?, 'text-plain', '1', 'sha256:0', ?, 'ok', 1, 10, 1, 1)",
                (key, canon),
            )
        conn.execute(
            "INSERT INTO depends VALUES "
            "('topics/overview.md', 'mirror/src/renamed-secret.docx.md', ?, 'primary')",
            ("0" * 64,),
        )
        conn.execute(
            "INSERT INTO runs (run_id, mode, started_at, host, pid, commit_sha) "
            "VALUES (3, 'poll', 'x', 'h', 1, ?)",
            (self.c3,),
        )
        conn.execute(
            "INSERT INTO run_sources (run_id, source_id, error) VALUES "
            "(3, 'src', 'conversion failed: mirror/src/secret.docx.md')"
        )
        conn.commit()
        conn.close()

    def _cache_and_logs(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        self.entry_s = cache_entry(cache, self.key_s, sha("whatever"))
        self.entry_k = cache_entry(cache, self.key_k, sha("KEEP-BODY"))
        # an older conversion of the secret, found only by its body hash (from the c2 page frontmatter)
        self.entry_old = cache_entry(cache, self.key_old, sha("SECRET-ALPHA-2"))
        write(
            tmp_path / "logs" / "com.agentsync-test.poll.out.log",
            "start\n"
            "converted hr/renamed-secret.docx -> mirror/src/renamed-secret.docx.md\n"
            "converted keep.txt\n",
        )


@pytest.fixture
def world(tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path) -> World:
    return World(tmp_path, tmp_docs_repo, tmp_state_dir)


# ---------------------------------------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------------------------------------


def test_parse_governance_defaults_and_validation() -> None:
    cfg = gv.parse_governance({})
    assert cfg.history_days == 30 and cfg.allow_remote is False and cfg.hold is False
    assert cfg.purge_on_upstream_delete is True
    got = gv.parse_governance(
        {"governance": {"history_days": 7, "hold": True, "hold_reason": "case 42", "hold_owner": "legal"}}
    )
    assert (got.history_days, got.hold, got.hold_reason) == (7, True, "case 42")
    with pytest.raises(ConfigError, match="hold_reason"):
        gv.parse_governance({"governance": {"hold": True}})
    with pytest.raises(ConfigError, match="remote_url_prefixes"):
        gv.parse_governance({"governance": {"allow_remote": True}})
    with pytest.raises(ConfigError, match="unknown key"):
        gv.parse_governance({"governance": {"histroy_days": 3}})
    with pytest.raises(ConfigError, match="history_days"):
        gv.parse_governance({"governance": {"history_days": 0}})


def test_archive_turns_off_upstream_purge_and_refuses_compaction(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    assert gv.parse_governance({}).archive is False  # the public default
    got = gv.parse_governance({"governance": {"archive": True}})
    assert got.archive is True and got.purge_on_upstream_delete is False
    assert gv.parse_governance({"governance": {"archive": True, "purge_on_upstream_delete": False}}).archive
    with pytest.raises(ConfigError, match=r"archive.*purge_on_upstream_delete"):
        gv.parse_governance({"governance": {"archive": True, "purge_on_upstream_delete": True}})
    with pytest.raises(ConfigError, match="archive"):
        gv.parse_governance({"governance": {"archive": "yes"}})
    cfg = make_config(tmp_path, tmp_docs_repo, tmp_state_dir, "[governance]\narchive = true\n")
    assert gv.compaction_state(tmp_docs_repo, got) == ("ok", gv.ARCHIVE_KEEPS_HISTORY)
    with pytest.raises(gv.GovernanceError, match="archive on: history is kept"):
        gv.compact_history(cfg, 0)
    with pytest.raises(gv.GovernanceError, match="archive on"):
        gv.compact_history(cfg, dry_run=True)


def test_load_governance_reads_sources_toml(tmp_path: Path) -> None:
    p = tmp_path / "sources.toml"
    assert gv.load_governance(p) == gv.GovernanceConfig()
    p.write_text(
        '[governance]\nallow_remote = true\nremote_url_prefixes = ["https://dev.azure.com/contoso/"]\n'
    )
    assert gv.load_governance(p).remote_url_prefixes == ("https://dev.azure.com/contoso/",)


def test_keychain_service_matches_graph_auth() -> None:
    assert gv.KEYCHAIN_SERVICE == auth.KEYCHAIN_SERVICE


def test_selector_parse() -> None:
    assert gv.PurgeSelector.parse("id=abc:12").stable_id == "abc:12"
    assert gv.PurgeSelector.parse("path=hr/**").path_glob == "hr/**"
    assert gv.PurgeSelector.parse("docs=mirror/x/*.md", source_id="x").docs_glob == "mirror/x/*.md"
    assert gv.PurgeSelector.parse("hr/*.docx").path_glob == "hr/*.docx"
    assert gv.PurgeSelector.parse("01ABCDEF").stable_id == "01ABCDEF"
    with pytest.raises(gv.GovernanceError):
        gv.PurgeSelector(stable_id="a", path_glob="b")


# ---------------------------------------------------------------------------------------------------------
# purge (C15 req 37, 38, 41)
# ---------------------------------------------------------------------------------------------------------


def test_purge_by_stable_id_removes_every_copy_and_verifies(world: World) -> None:
    repo = world.repo
    assert world.secret_blobs and all(exists(repo, b) for b in world.secret_blobs)
    dates_before = git(repo, "log", "--format=%ad %s", "--date=raw", "--all")
    rep = gv.purge(
        world.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.ERASURE_REQUEST, now=NOW
    )

    assert rep.verified, rep
    assert rep.items == (("src", "S1"),)
    assert {"mirror/src/secret.docx.md", "mirror/src/renamed-secret.docx.md"} <= set(rep.docs_paths)
    # req 37: every blob that held the item is unreadable afterwards
    for b in world.secret_blobs:
        assert not exists(repo, b), b
    history = git(repo, "log", "--all", "-p", "--", ".", ":(exclude)topics")
    assert "SECRET-ALPHA" not in history and "PNG-SECRET" not in history
    assert "secret.docx" not in history  # CHANGELOG, INDEX, shard and QUARANTINE lines scrubbed everywhere
    names = git(repo, "log", "--all", "--format=", "--name-only")
    assert "secret" not in names.replace("topics/overview.md", "")
    # the rest of history is intact, same shape and dates
    assert git(repo, "log", "--format=%ad %s", "--date=raw", "--all") == dates_before
    assert "KEEP-BODY" in (repo / "mirror/src/keep.md").read_text()
    assert git(repo, "status", "--porcelain") == ""
    assert not (repo / "mirror/src/renamed-secret.docx.md").exists()
    assert "keep.txt" in (repo / "_manifest/src.jsonl").read_text()
    assert "S1" not in (repo / "_manifest/src.jsonl").read_text()
    # reflogs expired
    assert git(repo, "reflog", "--all").strip() == ""
    # the other item's tombstone hint was remapped to the rewritten commit and still recovers content
    tomb = (repo / "mirror/src/gone.md").read_text()
    new_c3 = next(line.split()[1] for line in tomb.splitlines() if line.startswith("last_commit: "))
    assert new_c3 != world.c3 and exists(repo, new_c3)
    assert "GONE-BODY" in git(repo, "show", f"{new_c3}:mirror/src/gone.md")
    assert f"git show {new_c3}:" in tomb
    # ignored STATE.md scrubbed, logs scrubbed
    assert "renamed-secret" not in (repo / "_sync/STATE.md").read_text()
    log_text = (world.config.log_dir / "com.agentsync-test.poll.out.log").read_text()
    assert "secret" not in log_text and "converted keep.txt" in log_text
    # manifest rows, cache rows and blobs
    conn = sqlite3.connect(world.config.state_paths.db)
    try:
        assert conn.execute("SELECT stable_id FROM items ORDER BY stable_id").fetchall() == [("G1",), ("K1",)]
        assert conn.execute("SELECT COUNT(*) FROM outputs WHERE stable_id='S1'").fetchone() == (0,)
        assert conn.execute("SELECT action_key FROM cache").fetchall() == [(world.key_k,)]
        assert conn.execute("SELECT COUNT(*) FROM depends").fetchone() == (0,)
        assert conn.execute("SELECT error FROM run_sources").fetchone() == ("[purged]",)
        assert conn.execute("SELECT last_commit FROM tombstones").fetchone() == (new_c3,)
        tree = conn.execute("SELECT value FROM meta WHERE key='tree_sha'").fetchone()[0]
    finally:
        conn.close()
    assert tree == git(repo, "rev-parse", "HEAD^{tree}").strip()
    assert not world.entry_s.exists() and not world.entry_old.exists() and world.entry_k.exists()
    # curated page citing it is reported, not silently edited
    assert rep.citing_pages == ("topics/overview.md",)
    assert rep.remote == "no-remote"
    # req 41: audit line with source id, hashes, time, reason; no content or path text
    audit = gv.read_audit(world.state)
    purge_lines = [a for a in audit if a["action"] == "purge"]
    assert purge_lines == [
        {
            "action": "purge",
            "at": "2026-09-29T12:00:00.000000Z",  # to the microsecond (B13 round 30 C401)
            "path_sha256": sha("hr/renamed-secret.docx"),
            "reason": "erasure-request",
            "source_id": "src",
            "stable_id_sha256": sha("S1"),
        }
    ]
    raw = gv.audit_path(world.state).read_text()
    assert "secret" not in raw.lower() and "SECRET" not in raw
    # never re-synced: suppression list
    assert gv.load_suppressions(world.state).matches("src", "S1", "anything")
    assert not gv.load_suppressions(world.state).matches("src", "K1", "keep.txt")


def test_purge_by_source_path_glob_follows_renames(world: World) -> None:
    rep = gv.purge(
        world.config,
        gv.PurgeSelector.parse("path=hr/renamed-*"),
        reason=gv.PurgeReason.DLP_REMEDIATION,
        now=NOW,
    )
    assert rep.verified and rep.items == (("src", "S1"),)
    for b in world.secret_blobs:
        assert not exists(world.repo, b)
    assert gv.load_suppressions(world.state).matches("src", "other", "hr/renamed-x")


def test_purge_after_an_auto_migration_leaves_no_trace_under_the_state_dir(world: World) -> None:
    db = world.config.state_paths.db
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value = '0' WHERE key = 'key_schema_version'")
    conn.commit()
    conn.close()
    with Manifest(db) as man:  # any open migrates (status, a cycle, purge itself)
        backup = man.migration_backup
    assert backup is not None and b"renamed-secret" in backup.read_bytes()
    rep = gv.purge(
        world.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.ERASURE_REQUEST, now=NOW
    )
    assert rep.verified, rep
    assert not backup.exists()
    suppressions = world.state / "governance" / "suppressed.json"  # keeps the path on purpose: never re-sync
    for path in sorted(world.config.state_paths.root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            assert b"SECRET-ALPHA" not in data, path
            assert path == suppressions or b"renamed-secret" not in data, path


def test_purge_of_a_recording_removes_its_keyframes_cache_entry_and_stored_pieces(world: World) -> None:
    """spec section 4, "Purge of the item": the piece store folder of the recording's canonical hash goes
    with its pages, keyframe sidecars and cache entry; another recording's pieces stay."""
    repo, rec, other, key = world.repo, sha("recording bytes"), sha("other recording"), "d" * 64
    d = "mirror/src/weekly-sync.mp4.d"
    write(repo / f"{d}/00-index.md", page("src", "R1", "Weekly sync.mp4", "RECORDING-INDEX"))
    write(repo / f"{d}/01-t000000.md", page("src", "R1", "Weekly sync.mp4", "RECORDING-WINDOW"))
    write(repo / f"{d}/01-t000000.files/t000148.jpg", b"\xff\xd8\xff\xe0KEYFRAME")
    commit_all(repo, "sync: 1a 0m 0r 0d src", "2026-06-22T10:00:00Z")
    frame_blobs = {s for s, p in all_blobs(repo).items() if p.endswith(".jpg")}
    conn = sqlite3.connect(world.config.state_paths.db)
    conn.execute(
        "INSERT INTO items (source_id, stable_id, name, rel_path, state, first_seen_run, last_seen_run, "
        "canonical_sha256) VALUES ('src', 'R1', 'Weekly sync.mp4', 'Weekly sync.mp4', 'live', 1, 1, ?)",
        (rec,),
    )
    for unit_id, path in (("index", f"{d}/00-index.md"), ("window:1", f"{d}/01-t000000.md")):
        conn.execute(
            "INSERT INTO outputs (output_path, source_id, stable_id, unit_id, action_key, rendered_sha256, "
            "status, built_run) VALUES (?, 'src', 'R1', ?, ?, ?, 'ok', 1)",
            (path, unit_id, key, sha(unit_id)),
        )
    conn.execute(
        "INSERT INTO cache VALUES (?, 'recording-av', '1', 'sha256:0', ?, 'ok', 1, 10, 1, 1)", (key, rec)
    )
    conn.commit()
    conn.close()
    entry = cache_entry(world.config.cache_dir, key, sha("RECORDING-WINDOW"))
    store = PieceStore.under(world.config.cache_dir)
    store.save(rec, 0, "k", b"piece")
    store.set_progress(rec, done_ms=300_000, total_ms=600_000)
    store.save(other, 0, "k", b"piece")

    dry = gv.purge(
        world.config, gv.PurgeSelector(stable_id="R1"), reason=gv.PurgeReason.OPERATOR, dry_run=True
    )
    assert dry.cache_entries_removed == 2 and store.pending() == [rec, other]
    rep = gv.purge(world.config, gv.PurgeSelector(stable_id="R1"), reason=gv.PurgeReason.OPERATOR, now=NOW)
    assert rep.verified, rep
    assert rep.cache_entries_removed == 2  # the cache entry and the recording's piece folder
    assert frame_blobs and not any(exists(repo, b) for b in frame_blobs)
    assert not (repo / d).exists() and not entry.exists()
    assert store.pending() == [other]
    assert world.entry_k.exists()


def test_purge_removes_the_pieces_of_a_recording_staged_under_a_hash_its_row_does_not_hold(
    world: World,
) -> None:
    """Spec 4.1: a first read killed in its transaction leaves the row without a hash while its pieces stay;
    purge finds them by the hash the recording record (``recording:<source>``) says it was staged under."""
    staged, other = sha("killed recording"), sha("other recording")
    conn = sqlite3.connect(world.config.state_paths.db)
    conn.execute(
        "INSERT INTO items (source_id, stable_id, name, rel_path, state, first_seen_run, last_seen_run) "
        "VALUES ('src', 'R2', 'Weekly sync.mp4', 'Weekly sync.mp4', 'live', 1, 1)"
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('recording:src', ?)",
        (json.dumps({"staged": {"R2": staged, "R3": other}}),),
    )
    conn.commit()
    conn.close()
    store = PieceStore.under(world.config.cache_dir)
    for digest in (staged, other):
        store.save(digest, 0, "k", b"piece")
    rep = gv.purge(world.config, gv.PurgeSelector(stable_id="R2"), reason=gv.PurgeReason.OPERATOR, now=NOW)
    assert rep.verified, rep
    assert store.pending() == [other]


def test_purge_by_docs_glob(world: World) -> None:
    keep_blobs = {s for s, p in all_blobs(world.repo).items() if p == "mirror/src/keep.md"}
    rep = gv.purge(
        world.config, gv.PurgeSelector(docs_glob="mirror/src/keep.md"), reason=gv.PurgeReason.OPERATOR
    )
    assert rep.verified and rep.items == (("src", "K1"),)
    assert keep_blobs and not any(exists(world.repo, b) for b in keep_blobs)
    assert (world.repo / "mirror/src/renamed-secret.docx.md").exists()


def test_purge_upstream_deleted_is_not_suppressed(world: World) -> None:
    rep = gv.purge(world.config, gv.PurgeSelector(stable_id="G1"), reason=gv.PurgeReason.UPSTREAM_DELETED)
    assert rep.verified
    assert not (
        world.repo / "mirror/src/gone.md"
    ).exists()  # the tombstone and its recovery hint are gone too
    assert not gv.load_suppressions(world.state).matches("src", "G1", "gone.txt")


def test_purge_dry_run_writes_nothing(world: World) -> None:
    head = git(world.repo, "rev-parse", "HEAD")
    db_before = world.config.state_paths.db.read_bytes()
    rep = gv.purge(
        world.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.OPERATOR, dry_run=True
    )
    assert rep.dry_run and not rep.verified
    assert "mirror/src/secret.docx.md" in rep.docs_paths and rep.cache_entries_removed == 2
    assert git(world.repo, "rev-parse", "HEAD") == head
    assert all(exists(world.repo, b) for b in world.secret_blobs)
    assert world.config.state_paths.db.read_bytes() == db_before
    assert gv.read_audit(world.state) == []


def test_purge_dry_run_of_an_id_that_names_nothing_says_no_commit_would_be_rewritten(world: World) -> None:
    """Field report 2026-10-08: a queued id with no row and no page read "N commit(s) would be rewritten"."""
    commits = len(git(world.repo, "rev-list", "HEAD").split())
    nothing = gv.purge(
        world.config,
        gv.PurgeSelector(stable_id="NEVER-A-ROW", source_id="src"),
        reason=gv.PurgeReason.UPSTREAM_DELETED,
        dry_run=True,
    )
    assert nothing.items == () and nothing.docs_paths == () and nothing.blobs_targeted == 0
    assert nothing.notes == ("nothing is targeted: no commit would be rewritten",)
    named = gv.purge(
        world.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.OPERATOR, dry_run=True
    )
    assert named.blobs_targeted > 0
    # B13 round 17 U71: a known count needs no "(s)", in the purge command's own lines too (T32)
    assert named.notes == (f"up to {plural(commits, 'commit')} would be rewritten",)
    assert commits > 1 and "commits" in named.notes[0]
    # an outputs row with no items row and no page: no blob, but the docs path is still a needle
    conn = sqlite3.connect(world.config.state_paths.db)
    conn.execute(
        "INSERT INTO outputs (output_path, source_id, stable_id, unit_id, action_key, rendered_sha256, "
        "status, built_run) VALUES ('mirror/src/phantom.md','src','P1','whole',NULL,'x','ok',1)"
    )
    conn.commit()
    conn.close()
    phantom = gv.purge(
        world.config,
        gv.PurgeSelector(docs_glob="mirror/src/phantom.md"),
        reason=gv.PurgeReason.OPERATOR,
        dry_run=True,
    )
    assert phantom.items == () and phantom.blobs_targeted == 0
    assert phantom.notes == (f"up to {plural(commits, 'commit')} would be rewritten",)


def test_purge_reports_survivor_when_content_is_copied_elsewhere(world: World) -> None:
    secret_page = git(world.repo, "show", f"{world.c3}:mirror/src/renamed-secret.docx.md")
    write(world.repo / "topics/copy.md", secret_page)  # identical bytes outside mirror/: same blob
    commit_all(world.repo, "curate: copy", "2026-06-22T10:00:00Z")
    rep = gv.purge(world.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.OPERATOR)
    assert not rep.verified and len(rep.survivors) == 1
    assert any("topics/copy.md" in n for n in rep.notes)


def test_purge_cleans_a_linked_published_worktree(world: World, tmp_path: Path) -> None:
    pub = tmp_path / "published"
    git(world.repo, "worktree", "add", "-q", "--detach", str(pub), "HEAD")
    git(world.repo, "tag", "published", "HEAD")
    rep = gv.purge(world.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.OPERATOR)
    assert rep.verified
    assert not (pub / "mirror/src/renamed-secret.docx.md").exists()
    assert git(pub, "rev-parse", "HEAD") == git(world.repo, "rev-parse", "main")
    assert git(world.repo, "rev-parse", "published") == git(world.repo, "rev-parse", "main")


def test_purge_refuses_a_dirty_tree(world: World) -> None:
    write(world.repo / "mirror/src/keep.md", "edited\n")
    with pytest.raises(gv.GovernanceError, match="uncommitted"):
        gv.purge(world.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.OPERATOR)


def test_purge_and_compaction_refused_under_config_hold(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    w = World(
        tmp_path,
        tmp_docs_repo,
        tmp_state_dir,
        '[governance]\nhold = true\nhold_reason = "eDiscovery case 7"\nhold_owner = "legal@contoso"\n',
    )
    with pytest.raises(gv.HoldActiveError, match="eDiscovery case 7"):
        gv.purge(w.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.OPERATOR)
    with pytest.raises(gv.HoldActiveError, match="legal@contoso"):
        gv.compact_history(w.config, 1, now=NOW)
    assert all(exists(w.repo, b) for b in w.secret_blobs)


def test_scoped_state_hold_blocks_only_its_source(world: World) -> None:
    gov = gv.GovernanceConfig()
    gv.set_hold(world.state, "other", reason="matter 9", owner="records", now=NOW)
    assert gv.hold_state_lines(world.state, gov)[2].startswith("- HOLD `other`: matter 9")
    gv.set_hold(world.state, "src", reason="matter 10", owner="records", now=NOW)
    with pytest.raises(gv.HoldActiveError, match="matter 10"):
        gv.purge(world.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.OPERATOR, gov=gov)
    with pytest.raises(gv.HoldActiveError):
        gv.compact_history(world.config, 1, gov=gov, now=NOW)
    assert gv.release_hold(world.state, "src", owner="records", now=NOW)
    assert not gv.release_hold(world.state, "src", owner="records", now=NOW)
    rep = gv.purge(world.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.OPERATOR, gov=gov)
    assert rep.verified
    actions = [a["action"] for a in gv.read_audit(world.state)]
    assert actions[:3] == ["hold-set", "hold-set", "hold-released"]
    with pytest.raises(gv.GovernanceError):
        gv.set_hold(world.state, "Not A Source!", reason="x", owner="y")


def test_purge_queue_runs_and_keeps_held_requests(world: World) -> None:
    # S1 is live: an operator purge, which is never judged (a label escalation of it is kept, G30)
    s1 = gv.PurgeSelector(stable_id="S1", source_id="src")
    assert gv.enqueue_purge(world.state, s1, gv.PurgeReason.OPERATOR, now=NOW)
    assert not gv.enqueue_purge(world.state, s1, gv.PurgeReason.OPERATOR, now=NOW)
    assert [q.selector for q in gv.pending_purges(world.state)] == [s1]
    gv.set_hold(world.state, "src", reason="hold", owner="legal", now=NOW)
    (held,) = gv.run_purge_queue(world.config, now=NOW)
    # R96: a held entry is reported, as a kept one is, and the report names the hold, not the item
    assert not held.verified and held.not_run and held.items == () and held.remote == "not run"
    assert held.notes == (f"stays queued: {held.not_run[len('stays queued: ') :]}",)
    assert held.not_run.startswith("stays queued: purge suspended by legal/records hold: hold src")
    assert "S1" not in held.not_run
    assert len(gv.pending_purges(world.state)) == 1
    gv.release_hold(world.state, "src", owner="legal", now=NOW)
    reports = gv.run_purge_queue(world.config, now=NOW)
    assert len(reports) == 1 and reports[0].verified
    assert gv.pending_purges(world.state) == []


def test_purge_queue_dry_run_writes_nothing_and_keeps_the_queue(world: World) -> None:
    """Field report 2026-10-05: `purge --queue --dry-run` ran both queued purges and rewrote history."""
    s1 = gv.PurgeSelector(stable_id="S1", source_id="src")
    assert gv.enqueue_purge(world.state, s1, gv.PurgeReason.OPERATOR, now=NOW)
    head = git(world.repo, "rev-parse", "HEAD").strip()
    reports = gv.run_purge_queue(world.config, dry_run=True, now=NOW)
    assert len(reports) == 1 and reports[0].dry_run and reports[0].commits_rewritten == 0
    assert git(world.repo, "rev-parse", "HEAD").strip() == head
    assert [q.selector for q in gv.pending_purges(world.state)] == [s1]


def test_purge_queue_that_stops_part_way_keeps_no_entry_it_already_purged(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The queue file was written once, after the last entry: a stop left every purged entry queued."""
    held = gv.PurgeSelector(stable_id="HELD-X", source_id="src")
    g1 = gv.PurgeSelector(stable_id="G1", source_id="src")
    k1 = gv.PurgeSelector(stable_id="K1", source_id="src")
    for selector in (held, g1):
        assert gv.enqueue_purge(world.state, selector, gv.PurgeReason.UPSTREAM_DELETED, now=NOW)
    # K1 is live: an upstream deletion or a label escalation of it is kept before purge() is called (C8,
    # G30), so it stops as an operator purge
    assert gv.enqueue_purge(world.state, k1, gv.PurgeReason.OPERATOR, now=NOW)
    real = gv.purge

    def stops_on_the_last(config: Config, selector: gv.PurgeSelector, **kwargs: Any) -> gv.PurgeReport:
        if selector == held:
            raise gv.GovernanceError("held")
        if selector == k1:
            raise RuntimeError("stopped part-way")
        return real(config, selector, **kwargs)

    monkeypatch.setattr(gv, "purge", stops_on_the_last)
    with pytest.raises(RuntimeError, match="stopped part-way"):
        gv.run_purge_queue(world.config, now=NOW)
    assert not (world.repo / "mirror/src/gone.md").exists()
    assert [q.selector for q in gv.pending_purges(world.state)] == [held, k1]


def test_purge_queue_runs_an_entry_an_earlier_purge_of_the_run_covered_only_once(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R95: an operator purge then an erasure request for one item.  The second entry was a full no-op purge
    with its own audit summary; now it leaves the queue with a ``purge-covered`` audit line and a note on the
    first report, and a stop after the first purge does not put it back.  (R95 queued a label escalation
    first; since G30 one of a live file is kept, so an operator purge, never judged, stands in.)"""
    s1 = gv.PurgeSelector(stable_id="S1", source_id="src")
    k1 = gv.PurgeSelector(stable_id="K1", source_id="src")
    # the covered entry sits after the entry the run stops on (review of R95: it left the file unaudited)
    assert gv.enqueue_purge(world.state, s1, gv.PurgeReason.OPERATOR, now=NOW)
    assert gv.enqueue_purge(world.state, k1, gv.PurgeReason.OPERATOR, now=NOW)
    assert gv.enqueue_purge(world.state, s1, gv.PurgeReason.ERASURE_REQUEST, now=NOW)
    calls: list[gv.PurgeReason] = []
    real = gv.purge

    def counted(config: Config, selector: gv.PurgeSelector, **kwargs: Any) -> gv.PurgeReport:
        if selector == k1:
            raise RuntimeError("stopped part-way")
        calls.append(kwargs["reason"])
        return real(config, selector, **kwargs)

    monkeypatch.setattr(gv, "purge", counted)
    with pytest.raises(RuntimeError, match="stopped part-way"):
        gv.run_purge_queue(world.config, now=NOW)
    assert calls == [gv.PurgeReason.OPERATOR]
    assert [q.selector for q in gv.pending_purges(world.state)] == [k1]  # the covered entry is not put back
    assert [a["reason"] for a in gv.read_audit(world.state) if a["action"] == "purge-covered"] == [
        "erasure-request"
    ]
    monkeypatch.setattr(gv, "purge", real)
    assert gv.enqueue_purge(world.state, k1, gv.PurgeReason.ERASURE_REQUEST, now=NOW)
    rep, *rest = gv.run_purge_queue(world.config, now=NOW)
    assert rest == [] and rep.verified and rep.items == (("src", "K1"),)
    assert rep.notes[-1] == "also did the queued erasure-request purge of the same item: taken off the queue"
    assert gv.pending_purges(world.state) == []
    audit = [json.loads(x) for x in (world.state / "governance" / "audit.jsonl").read_text().splitlines()]
    assert [(c["reason"], c["covered_by_sha256"]) for c in audit if c["action"] == "purge-covered"] == [
        ("erasure-request", s1.fingerprint()),
        ("erasure-request", k1.fingerprint()),
    ]
    assert sum(1 for c in audit if c["action"] == "purge-summary") == 2


def test_purge_queue_keeps_an_upstream_deletion_of_a_file_that_is_listed_again(world: World) -> None:
    """C8 (2026-10-07), CONTRACTS 16.30 'Not done here': `purge --queue` ran an upstream-deleted entry whose
    id names a listed file, by its own id or as an alias, and erased that live file's page and history. Such
    an entry stays queued, not run and not verified, with its reason; a quarantined or refused row still runs.
    A label escalation of a listed file is kept the same way (B13 round 6 G30); DLP, erasure and operator
    entries still run."""
    db = world.config.state_paths.db
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO item_aliases (source_id, alias_id, stable_id) VALUES ('src', 'K0', 'K1')")
    conn.execute("UPDATE items SET state = 'quarantined', state_reason = 'encrypted' WHERE stable_id = 'S1'")
    conn.commit()
    conn.close()
    by_alias = gv.PurgeSelector(source_id="src", stable_id="K0")
    by_id = gv.PurgeSelector(source_id="src", stable_id="K1")
    quarantined = gv.PurgeSelector(source_id="src", stable_id="S1")
    deleted = gv.PurgeSelector(source_id="src", stable_id="G1")
    # queued now, after the manifest's first run, as a cycle queues them (a fake NOW before it reads as a
    # purge queued under a manifest a new start replaced: B13 round 27 P112)
    for selector in (by_alias, by_id, quarantined, deleted):
        assert gv.enqueue_purge(world.state, selector, gv.PurgeReason.UPSTREAM_DELETED)
    head = git(world.repo, "rev-parse", "HEAD").strip()
    previews = gv.run_purge_queue(world.config, dry_run=True, now=NOW)
    assert [r.notes[0].startswith("stays queued:") for r in previews] == [True, True, False, False]
    assert git(world.repo, "rev-parse", "HEAD").strip() == head
    reports = gv.run_purge_queue(world.config, now=NOW)
    assert [r.verified for r in reports] == [False, False, True, True]
    assert reports[0].notes == (
        "stays queued: the manifest lists the file as live or online-only (1 row, 1 of them under a later "
        "id); an upstream deletion is not run against a listed file; a later `purge --queue` runs it once "
        "the file is gone",
    )
    assert reports[0].items == () and reports[0].docs_paths == () and reports[0].commits_rewritten == 0
    assert [bool(r.not_run) for r in reports] == [True, True, False, False]
    assert (world.repo / "mirror/src/keep.md").is_file() and "KEEP-BODY" in git(world.repo, "log", "-p")
    assert not any(exists(world.repo, b) for b in world.secret_blobs), "the quarantined file's earlier text"
    assert not (world.repo / "mirror/src/gone.md").exists()
    assert [q.selector for q in gv.pending_purges(world.state)] == [by_alias, by_id]
    # online-only is listed too
    conn = sqlite3.connect(db)
    conn.execute("UPDATE items SET state = 'dataless' WHERE stable_id = 'K1'")
    conn.commit()
    conn.close()
    assert [r.not_run != "" for r in gv.run_purge_queue(world.config, dry_run=True, now=NOW)] == [True, True]
    # a label escalation of a listed file is kept too (G30): the label policy publishes it again
    assert gv.enqueue_purge(world.state, by_id, gv.PurgeReason.LABEL_ESCALATION, now=NOW)
    *_, label = gv.run_purge_queue(world.config, dry_run=True, now=NOW)
    assert label.reason is gv.PurgeReason.LABEL_ESCALATION and label.not_run.startswith(
        "stays queued: the label policy no longer refuses the file"
    )
    # an operator purge erases live content on purpose: never judged
    assert gv.enqueue_purge(world.state, by_id, gv.PurgeReason.OPERATOR, now=NOW)
    *_, op = gv.run_purge_queue(world.config, now=NOW)
    assert op.reason is gv.PurgeReason.OPERATOR and op.verified and not op.not_run
    assert not (world.repo / "mirror/src/keep.md").exists()
    # the run reported the three entries as staying queued, so the operator purge's cover does not drop them
    assert [q.selector for q in gv.pending_purges(world.state)] == [by_alias, by_id, by_id]
    # the file is gone now: the next run runs the entries it kept and empties the queue
    assert all(r.verified for r in gv.run_purge_queue(world.config, now=NOW))
    assert gv.pending_purges(world.state) == []
    assert gv.pending_purges(world.state) == []


def test_kept_purges_note_names_the_run_that_clears_them() -> None:
    """Q45 (B13 round 4): the note after the kept count said the entries "run on their own once the file is
    gone"; nothing runs them, the rewrite runs only from `purge --queue`, and then the wait returns."""
    assert "on their own" not in gv.KEPT_PURGES
    assert gv.KEPT_PURGES.endswith(
        # B13 round 5 F66/F95: by the binary's full path, as the queued-purge line beside it (Q49)
        "once the file is gone, they wait for `~/.local/bin/agentsync purge --queue` like any queued purge"
    )


def _add_items(
    db: Path, rows: list[tuple[str, str, int, str]], aliases: list[tuple[str, str, str]] = ()
) -> None:
    """Add manifest rows (source, id, is_dir, state) and aliases (source, retired id, current id)."""
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT OR IGNORE INTO sources (source_id, kind, config_state, config_fingerprint) "
        "VALUES (?, 'local', 'live', 'x')",
        sorted({(r[0],) for r in rows}),
    )
    conn.executemany(
        "INSERT INTO items (source_id, stable_id, name, rel_path, is_dir, state, first_seen_run, "
        "last_seen_run) VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
        [(s, i, f"{i}.txt", f"d/{i}.txt", d, st) for s, i, d, st in rows],
    )
    conn.executemany("INSERT INTO item_aliases (source_id, alias_id, stable_id) VALUES (?,?,?)", aliases)
    conn.commit()
    conn.close()


def test_partition_purges_judges_a_cycle_entry_by_lookup_as_kept_why_does(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q43 (B13 round 4): partition_purges resolved every queued entry over every manifest row, so status,
    doctor, the loop and STATE.md each cost entries x rows. A cycle-queued entry (source + id) is now
    judged by lookup; its verdict is the one _kept_why gives, entry by entry, and only other selector
    shapes resolve."""
    db = world.config.state_paths.db
    _add_items(
        db,
        [
            ("src", "R1", 0, "live"),  # the current id of re-keyed R0
            ("src", "T0", 0, "tombstone"),  # a tombstoned original whose file came back as T1
            ("src", "T1", 0, "live"),
            ("src", "D1", 1, "live"),  # a directory row, and an alias that names it
            ("src2", "K1", 0, "live"),  # the same id as world's K1 under a second source
            ("src2", "G1", 0, "live"),  # ... and as world's tombstoned G1
            ("src", "DL", 0, "dataless"),
            ("src", "QU", 0, "quarantined"),
            ("src", "RF", 0, "refused"),
            ("src", "RT", 0, "live"),  # an alias pointing at a tombstone
            ("src", "RX", 0, "tombstone"),
        ],
        [
            ("src", "R0", "R1"),
            ("src", "T0", "T1"),
            ("src", "DA", "D1"),
            ("src", "XA", "RX"),
            ("src", "RT", "G1"),
        ],
    )
    ids = ["R0", "R1", "T0", "T1", "D1", "DA", "K1", "G1", "S1", "DL", "QU", "RF", "RT", "RX", "XA", "NONE"]
    up = gv.PurgeReason.UPSTREAM_DELETED
    queue = [
        gv.QueuedPurge(gv.PurgeSelector(source_id=s, stable_id=i), up, NOW.isoformat())
        for s in ("src", "src2")
        for i in ids
    ]
    # other shapes go the long way: no source, a path glob, a docs glob, another reason
    queue += [
        gv.QueuedPurge(gv.PurgeSelector(stable_id="K1"), up, NOW.isoformat()),
        gv.QueuedPurge(gv.PurgeSelector(source_id="src", path_glob="d/*"), up, NOW.isoformat()),
        gv.QueuedPurge(gv.PurgeSelector(source_id="src", docs_glob="mirror/*"), up, NOW.isoformat()),
        gv.QueuedPurge(
            gv.PurgeSelector(source_id="src", stable_id="T1"), gv.PurgeReason.ERASURE_REQUEST, NOW.isoformat()
        ),
    ]
    conn = gv._connect(db)
    try:
        m = gv._read_manifest(conn)
        want = [gv._kept_why(conn, m, q) for q in queue]
    finally:
        conn.close()
    assert sum(1 for w in want if w) >= 8 and any("under a later id" in w for w in want)
    resolved: list[gv.PurgeSelector] = []
    real = gv._resolve_read

    def counted(
        conn: sqlite3.Connection, m: Any, selector: gv.PurgeSelector, ids: set[tuple[str, str]]
    ) -> Any:
        resolved.append(selector)
        return real(conn, m, selector, ids)

    monkeypatch.setattr(gv, "_resolve_read", counted)
    runnable, kept = gv.partition_purges(world.config.state_paths, queue)
    assert [q for q, _w in kept] == [q for q, w in zip(queue, want, strict=True) if w]
    assert [w for _q, w in kept] == [w for w in want if w]
    assert runnable == [q for q, w in zip(queue, want, strict=True) if not w]
    assert resolved == [q.selector for q in queue[-4:-1]]  # the erasure request is never judged


def test_partition_purges_costs_rows_plus_entries(world: World) -> None:
    """Q43: 1,000 queued upstream deletions over 10,000 rows took about 15 s per call, three calls per
    status; one read and a lookup per entry take well under a second."""
    _add_items(
        world.config.state_paths.db,
        [("big", f"F{n}", 0, "live" if n % 2 else "tombstone") for n in range(10_000)],
    )
    up = gv.PurgeReason.UPSTREAM_DELETED
    queue = [
        gv.QueuedPurge(gv.PurgeSelector(source_id="big", stable_id=f"F{n}"), up, "") for n in range(1000)
    ]
    start = time.monotonic()
    runnable, kept = gv.partition_purges(world.config.state_paths, queue)
    elapsed = time.monotonic() - start
    assert (len(runnable), len(kept)) == (500, 500)  # an odd id is live, an even one a tombstone
    assert elapsed < 3.0, f"{elapsed:.2f} s"


def _state_files(folder: Path) -> dict[str, tuple[str, int]]:
    """Every file in ``folder`` by name: its bytes' sha256 and its mode."""
    return {
        p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode)
        for p in sorted(folder.iterdir())
        if p.is_file()
    }


def test_partition_queue_ro_judges_as_partition_queue_does(world: World) -> None:
    """B13 round 30 ruling 36 (C394): the setup report's split is the writing split's one judge over a read
    that writes nothing, so on a current manifest the two give the same answer, every branch of it, and the
    read-only one leaves the at-rest WAL manifest with no -wal or -shm beside it."""
    sp = world.config.state_paths
    _add_items(
        sp.db,
        [("src", "R1", 0, "live"), ("src", "T0", 0, "tombstone"), ("src", "T1", 0, "live")],
        [("src", "R0", "R1"), ("src", "T0", "T1")],
    )
    conn = sqlite3.connect(sp.db)
    conn.execute("UPDATE runs SET started_at = '2026-09-01T00:00:00Z'")
    conn.execute(
        "INSERT INTO sources (source_id, kind, config_state, config_fingerprint, last_full_run_id) "
        "VALUES ('listed', 'local', 'live', 'x', 3)"
    )
    conn.commit()
    conn.close()
    up, le = gv.PurgeReason.UPSTREAM_DELETED, gv.PurgeReason.LABEL_ESCALATION
    old = "2026-08-01T00:00:00Z"  # before the manifest's first run: a new start
    queue = [
        gv.QueuedPurge(gv.PurgeSelector(source_id="src", stable_id=i), r, NOW.isoformat())
        for i in ("R0", "R1", "T0", "K1", "G1", "S1")
        for r in (up, le)
    ]
    queue += [
        gv.QueuedPurge(gv.PurgeSelector(source_id="src", stable_id="NONE"), up, old),  # its source unlisted
        gv.QueuedPurge(gv.PurgeSelector(source_id="listed", stable_id="NONE"), up, old),  # not looked up
        gv.QueuedPurge(gv.PurgeSelector(source_id="listed", stable_id="GONE"), up, NOW.isoformat(), (), True),
        gv.QueuedPurge(gv.PurgeSelector(stable_id="K1"), up, NOW.isoformat()),
        gv.QueuedPurge(
            gv.PurgeSelector(source_id="src", stable_id="K1"), gv.PurgeReason.ERASURE_REQUEST, old
        ),
    ]
    want = gv.partition_queue(sp, queue, config=world.config)
    assert want.runnable and want.not_looked_up
    notes = [w for _q, w in want.kept]
    assert any(w.startswith(gv.UNLISTED_HEAD) for w in notes)
    assert any(w.startswith(gv.UNRESOLVED_HEAD) for w in notes)
    assert any("under a later id" in w for w in notes)
    before = _state_files(sp.db.parent)
    assert not {sp.db.name + "-wal", sp.db.name + "-shm"} & before.keys()
    assert gv.partition_queue_ro(sp, queue, config=world.config) == want
    assert _state_files(sp.db.parent) == before


def _older_schema(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value = '0' WHERE key = 'manifest_schema_version'")
    conn.commit()
    conn.close()


def _newer_schema(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value = '99' WHERE key = 'manifest_schema_version'")
    conn.commit()
    conn.close()


def _lost_table(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE run_changes")
    conn.commit()
    conn.close()


def _sqlite_error(db: Path) -> None:
    conn = sqlite3.connect(db)  # every table there, a column the judge reads not: sqlite refuses its read
    conn.execute("ALTER TABLE runs RENAME COLUMN started_at TO began_at")
    conn.commit()
    conn.close()


def _unreadable(db: Path) -> None:
    db.chmod(0o000)


def _no_file(db: Path) -> None:
    db.unlink()


@pytest.mark.parametrize(
    ("damage", "note"),
    [
        (_older_schema, gv.SCHEMA_NOTE),
        (_lost_table, gv.SCHEMA_NOTE),
        (_newer_schema, gv.UNJUDGED_NOTE),
        (_sqlite_error, gv.UNJUDGED_NOTE),
        (_unreadable, gv.unjudged_note("state_dir.files")),
        (_no_file, gv.NEW_START_NOTE),
    ],
    ids=["older-schema", "missing-table", "newer-schema", "sqlite-error", "no-connection", "no-file"],
)
def test_partition_queue_ro_keeps_what_it_cannot_judge_unjudged(world: World, damage: Any, note: str) -> None:
    """Ruling 36 (C394): a manifest the read-only split cannot judge as a migrated one would (another schema
    version, a table missing, a read sqlite refuses, no connection, no file) gives the unjudged split, never
    an exception or a migration: the judged entries kept with why, a DLP, erasure or operator one runnable,
    and the state folder as it was. The why is true of the case: an older schema or a table missing (an
    upgrade before the next sync, whose open migrates it) is SCHEMA_NOTE, never "missing or unreadable",
    which no status line FAILs there; a newer one, which ``Manifest`` refuses, is unjudged_note."""
    if damage is _unreadable and os.geteuid() == 0:
        pytest.skip("root reads a 0000 file")
    sp = world.config.state_paths
    up = gv.PurgeReason.UPSTREAM_DELETED
    erase = gv.QueuedPurge(
        gv.PurgeSelector(source_id="src", stable_id="K1"), gv.PurgeReason.ERASURE_REQUEST, ""
    )
    judged = [gv.QueuedPurge(gv.PurgeSelector(source_id="src", stable_id=i), up, "") for i in ("K1", "G1")]
    damage(sp.db)
    before = sorted(p.name for p in sp.db.parent.iterdir())
    raw = sp.db.read_bytes() if damage is not _unreadable and sp.db.exists() else b""
    split = gv.partition_queue_ro(sp, [erase, *judged], config=world.config)
    assert (split.runnable, split.kept, split.not_looked_up) == ([erase], [(q, note) for q in judged], [])
    assert sorted(p.name for p in sp.db.parent.iterdir()) == before
    if raw:
        assert sp.db.read_bytes() == raw


def test_partition_queue_ro_leaves_an_older_builds_manifest_as_it_was(world: World) -> None:
    """Ruling 36 (C394): one setup report run over a manifest an older build left (a rollback journal, mode
    0644, an older converter key schema) chmodded it 0600, switched it to WAL, cleared its cache index and
    left a .pre-v1 copy. The read-only split and the read-only hold split judge it, as the key schema is no
    part of what they read, and leave the manifest byte for byte, with no -wal, -shm or .pre-v file; the
    writing split, over the same files, migrates it."""
    sp = world.config.state_paths
    conn = sqlite3.connect(sp.db)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("UPDATE meta SET value = '0' WHERE key = 'key_schema_version'")
    conn.commit()
    conn.close()
    sp.db.chmod(0o644)
    gv.set_hold(sp.root, "src", reason="case 7", owner="Contoso legal")
    up = gv.PurgeReason.UPSTREAM_DELETED
    listed = gv.QueuedPurge(gv.PurgeSelector(source_id="src", stable_id="K1"), up, "")
    gone = gv.QueuedPurge(gv.PurgeSelector(source_id="src", stable_id="G1"), up, "")
    sourceless = gv.QueuedPurge(gv.PurgeSelector(stable_id="K1"), gv.PurgeReason.OPERATOR, "")
    before = _state_files(sp.db.parent)
    split = gv.partition_queue_ro(sp, [listed, gone, sourceless], config=world.config)
    assert split.runnable == [gone, sourceless]
    assert [q for q, _w in split.kept] == [listed] and "lists the file as live" in split.kept[0][1]
    runnable, held = gv.held_purges(sp.root, gv.parse_governance({}), [sourceless], read_only=True)
    assert runnable == [] and [q for q, _h in held] == [sourceless]  # resolved to src, read only
    after = _state_files(sp.db.parent)
    assert after == before
    assert after[sp.db.name][1] & 0o777 == 0o644
    assert not any(n.endswith(("-wal", "-shm")) or ".pre-v" in n for n in after)
    gv.partition_queue(sp, [listed, gone], config=world.config)
    assert _state_files(sp.db.parent) != before  # the writing open migrates, as the CLI's must


def test_partition_queue_ro_reads_a_running_syncs_wal_and_changes_no_side_file(world: World) -> None:
    """Ruling 36 (C394): with a sync's -wal holding frames, any open that reads them writes its read mark into
    the -shm, and an ``immutable=1`` open misses them (measured). The read-only split reads a private copy:
    it sees the row only the -wal holds, and the manifest, its -wal and its -shm stay byte for byte."""
    sp = world.config.state_paths
    writer = sqlite3.connect(sp.db, isolation_level=None)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE items SET state = 'live' WHERE source_id = 'src' AND stable_id = 'G1'")
        before = _state_files(sp.db.parent)
        assert {sp.db.name + "-wal", sp.db.name + "-shm"} <= before.keys()
        q = gv.QueuedPurge(
            gv.PurgeSelector(source_id="src", stable_id="G1"), gv.PurgeReason.UPSTREAM_DELETED, ""
        )
        split = gv.partition_queue_ro(sp, [q], config=world.config)
        assert _state_files(sp.db.parent) == before
        assert [k for k, _w in split.kept] == [q]  # G1 is listed again only in the -wal
    finally:
        writer.close()


def test_partition_queue_ro_keeps_a_manifest_changing_under_every_read_busy(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C394: a manifest that changed under every read (a sync writing it) is kept with BUSY_NOTE, judged the
    next time, never unjudged_note's "missing or unreadable"; kept_lines counts it in those words."""
    sp = world.config.state_paths
    up = gv.PurgeReason.UPSTREAM_DELETED
    q = gv.QueuedPurge(gv.PurgeSelector(source_id="src", stable_id="K1"), up, "")
    real = manifest_mod._stamps
    seen = iter(range(100))

    def moving(db: Path) -> Any:
        size_mtime, wal, journal = real(db)
        return (size_mtime[0], next(seen)), wal, journal  # a new mtime at every stat: a writer at work

    monkeypatch.setattr(manifest_mod, "_stamps", moving)
    split = gv.partition_queue_ro(sp, [q], config=world.config)
    assert split.kept == [(q, gv.BUSY_NOTE)]
    assert gv.kept_lines(split.kept) == [gv.busy_purges(1)]
    assert gv.busy_purges(1) == (
        "1 queued purge was not judged this time: agentsync's state file changed while it was read (a sync "
        "is writing it); it is judged the next time"
    )
    assert gv.kept_lines([(q, gv.SCHEMA_NOTE)] * 2) == [gv.schema_purges(2)]
    assert gv.schema_purges(2) == (
        "2 queued purges wait for the next sync to bring agentsync's state file to this build's schema; they "
        "are judged after it: run `~/.local/bin/agentsync sync`"
    )


def test_partition_queue_ro_reads_again_when_the_wal_goes_before_its_copy(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C394: a -wal with frames that a checkpoint removes between the stat and its copy is read again, now
    ``immutable=1`` over the checkpointed file, and judged: never kept as "missing or unreadable"."""
    sp = world.config.state_paths
    writer = _wal_with_frames(sp.db)
    real = manifest_mod._copy_read
    calls: list[int] = []

    def checkpointed_first(db: Path, timeout: float, wal: bool, journal: bool) -> sqlite3.Connection:
        calls.append(1)
        if len(calls) == 1:
            writer.close()  # the last connection's close checkpoints the -wal into the file and removes it
            assert not db.with_name(db.name + "-wal").exists()
            raise FileNotFoundError(db.with_name(db.name + "-wal"))
        return real(db, timeout, wal, journal)

    monkeypatch.setattr(manifest_mod, "_copy_read", checkpointed_first)
    q = gv.QueuedPurge(gv.PurgeSelector(source_id="src", stable_id="G1"), gv.PurgeReason.UPSTREAM_DELETED, "")
    split = gv.partition_queue_ro(sp, [q], config=world.config)
    assert calls == [1]  # the second read went immutable, not through a copy
    assert [k for k, _w in split.kept] == [q] and "lists the file as live" in split.kept[0][1]


def test_read_only_snapshot_reads_again_when_the_file_changes_under_an_immutable_read(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C394: with no -wal or an empty one (a writer just opened it) the read is ``immutable=1``, which a
    checkpoint mid-read tears; the file's size and mtime are read again after the read, and a change reads
    again, so the answer is the file as it is after the change, never the torn or the older one."""
    sp = world.config.state_paths
    sp.db.with_name(sp.db.name + "-wal").write_bytes(b"")
    real = manifest_mod._immutable_read
    calls: list[int] = []

    def changed_after_first(db: Path, timeout: float) -> sqlite3.Connection:
        mem = real(db, timeout)
        calls.append(1)
        if len(calls) == 1:  # a writer's checkpoint lands in the file mid-read
            conn = sqlite3.connect(db)
            conn.execute("UPDATE items SET state = 'live' WHERE source_id = 'src' AND stable_id = 'G1'")
            conn.commit()
            conn.close()
            st = db.stat()
            os.utime(db, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        return mem

    monkeypatch.setattr(manifest_mod, "_immutable_read", changed_after_first)
    conn = manifest_mod.read_only_snapshot(sp.db, 0.5)
    assert conn is not None
    try:
        got = conn.execute("SELECT state FROM items WHERE source_id = 'src' AND stable_id = 'G1'").fetchone()
    finally:
        conn.close()
    assert len(calls) == 2 and got == ("live",)


def test_read_only_snapshot_rolls_back_an_older_builds_hot_journal(world: World) -> None:
    """C394: ``immutable=1`` ignores a hot rollback -journal an older build left mid-write, and reads pages a
    transaction never committed (measured: 1867 of 2000 rows). The read takes the copy path, where sqlite
    rolls the -journal back, and the manifest and its -journal stay byte for byte."""
    sp = world.config.state_paths
    writer = sqlite3.connect(sp.db, isolation_level=None)
    try:
        writer.execute("PRAGMA journal_mode=DELETE")
        writer.execute("BEGIN")
        writer.executemany(
            "INSERT INTO meta (key, value) VALUES (?, 'old')", ((f"filler-{n}",) for n in range(2000))
        )
        writer.execute("COMMIT")
        writer.execute("PRAGMA cache_size=1")  # every dirty page spills into the file before the commit
        writer.execute("BEGIN")
        writer.execute("UPDATE meta SET value = 'new' WHERE key LIKE 'filler-%'")
        assert sp.db.with_name(sp.db.name + "-journal").exists()
        imm = sqlite3.connect(f"file:{sp.db}?mode=ro&immutable=1", uri=True)
        try:
            torn = imm.execute("SELECT count(*) FROM meta WHERE value = 'new'").fetchone()[0]
        finally:
            imm.close()
        assert torn  # what immutable=1 reads: rows no commit made
        before = _state_files(sp.db.parent)
        conn = manifest_mod.read_only_snapshot(sp.db, 0.5)
        assert conn is not None
        try:
            assert conn.execute("SELECT count(*) FROM meta WHERE value = 'new'").fetchone() == (0,)
            assert conn.execute("SELECT count(*) FROM meta WHERE value = 'old'").fetchone() == (2000,)
        finally:
            conn.close()
        assert _state_files(sp.db.parent) == before
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def _wal_with_frames(db: Path) -> sqlite3.Connection:
    writer = sqlite3.connect(db, isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("UPDATE items SET state = 'live' WHERE source_id = 'src' AND stable_id = 'G1'")
    return writer


def test_read_only_snapshot_copies_into_the_state_folder_and_leaves_no_copy(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C394: the private copy of a manifest with -wal frames is made in a ``.ro-copy-`` folder beside it,
    never the system's temporary folder (where a killed run leaves a whole manifest), and is gone after the
    read, also when the backup into memory raises, which closes the in-memory database it opened; a copy
    folder a killed read left over an hour ago is removed by the next read, a newer one is not."""
    sp = world.config.state_paths
    system_tmp = tmp_path / "system-tmp"
    system_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_tmp))
    stale = sp.db.parent / ".ro-copy-killed"
    (stale / "inner").mkdir(parents=True)
    old = time.time() - 7200
    os.utime(stale, (old, old))
    fresh = sp.db.parent / ".ro-copy-running"
    fresh.mkdir()
    writer = _wal_with_frames(sp.db)
    try:
        during: list[list[str]] = []
        real = manifest_mod._into_memory

        def watched(src: sqlite3.Connection) -> sqlite3.Connection:
            during.append(sorted(p.name for p in sp.db.parent.glob(".ro-copy-*") if p != fresh))
            return real(src)

        monkeypatch.setattr(manifest_mod, "_into_memory", watched)
        conn = manifest_mod.read_only_snapshot(sp.db, 0.5)
        assert conn is not None
        conn.close()
        assert len(during) == 1 and len(during[0]) == 1  # one copy folder, in the state folder
        assert not stale.exists() and fresh.is_dir()
        assert sorted(p.name for p in sp.db.parent.glob(".ro-copy-*")) == [fresh.name]
        assert list(system_tmp.iterdir()) == []

        monkeypatch.setattr(manifest_mod, "_into_memory", real)
        opened: list[sqlite3.Connection] = []

        class Refusing:
            def __init__(self, conn: sqlite3.Connection) -> None:
                self.conn = conn

            def backup(self, target: sqlite3.Connection) -> None:
                raise sqlite3.OperationalError("disk I/O error")

            def close(self) -> None:
                self.conn.close()

        real_connect = sqlite3.connect

        def connect(target: Any, *args: Any, **kwargs: Any) -> Any:
            conn = real_connect(target, *args, **kwargs)
            if target == ":memory:":
                opened.append(conn)
                return conn
            return Refusing(conn)

        monkeypatch.setattr(manifest_mod.sqlite3, "connect", connect)
        with pytest.raises(sqlite3.OperationalError):
            manifest_mod.read_only_snapshot(sp.db, 0.5)
        monkeypatch.setattr(manifest_mod.sqlite3, "connect", real_connect)
        assert sorted(p.name for p in sp.db.parent.glob(".ro-copy-*")) == [fresh.name]
        assert opened
        for mem in opened:
            with pytest.raises(sqlite3.ProgrammingError):  # closed
                mem.execute("SELECT 1")
    finally:
        writer.close()


def test_held_purges_read_only_opens_the_manifest_once(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """C394: held_purges(read_only=True) resolves every sourceless entry over one read-only read of the
    manifest, never one open (and, with -wal frames, one whole copy) per entry."""
    sp = world.config.state_paths
    gv.set_hold(sp.root, "src", reason="case 7", owner="Contoso legal")
    real = gv.open_read_only
    opens: list[Path] = []

    def counted(db: Path, timeout: float) -> sqlite3.Connection | None:
        opens.append(db)
        return real(db, timeout)

    monkeypatch.setattr(gv, "open_read_only", counted)
    queued = [
        gv.QueuedPurge(gv.PurgeSelector(stable_id=i), gv.PurgeReason.OPERATOR, "")
        for i in ("K1", "S1", "NONE")
    ]
    runnable, held = gv.held_purges(sp.root, gv.parse_governance({}), queued, read_only=True)
    assert len(opens) == 1
    assert [q for q, _h in held] == queued[:2] and runnable == queued[2:]


def test_purge_works_on_a_sha256_repo(tmp_path: Path, tmp_state_dir: Path) -> None:
    repo = tmp_path / "docs256"
    subprocess.run(["git", "init", "-q", "-b", "main", "--object-format=sha256", str(repo)], check=True)
    write(repo / "mirror/s/a.md", page("s", "A", "a.txt", "SHA256-SECRET"))
    write(repo / "mirror/s/b.md", page("s", "B", "b.txt", "other"))
    commit_all(repo, "one", "2026-06-01T00:00:00Z")
    blobs = {s for s, p in all_blobs(repo).items() if p == "mirror/s/a.md"}
    cfg = make_config(tmp_path, repo, tmp_state_dir)
    rep = gv.purge(cfg, gv.PurgeSelector(stable_id="A"), reason=gv.PurgeReason.OPERATOR)
    assert rep.verified and blobs and not any(exists(repo, b) for b in blobs)
    assert (repo / "mirror/s/b.md").exists()


# ---------------------------------------------------------------------------------------------------------
# compaction (C15 req 39)
# ---------------------------------------------------------------------------------------------------------


def _dated_repo(repo: Path) -> dict[int, str]:
    shas: dict[int, str] = {}
    for age in (100, 60, 40, 10, 5):
        write(repo / "mirror/s/doc.md", f"version from {age} days ago\n")
        if age == 100:
            write(repo / "mirror/s/early-only.md", "ONLY-IN-THE-OLDEST-COMMIT\n")
        elif (repo / "mirror/s/early-only.md").exists():
            (repo / "mirror/s/early-only.md").unlink()
        when = (NOW - timedelta(days=age)).strftime("%Y-%m-%dT%H:%M:%SZ")
        shas[age] = commit_all(repo, f"sync: {age}", when)
    return shas


def test_compact_history_squashes_old_commits_and_prunes(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    repo = tmp_docs_repo
    shas = _dated_repo(repo)
    git(repo, "tag", "published", shas[60])
    old_blob = git(repo, "rev-parse", f"{shas[100]}:mirror/s/early-only.md").strip()
    trees = {a: git(repo, "rev-parse", f"{s}^{{tree}}").strip() for a, s in shas.items()}
    cfg = make_config(tmp_path, repo, tmp_state_dir)
    assert gv.compaction_due(repo, gv.GovernanceConfig(), now=NOW)

    rep = gv.compact_history(cfg, now=NOW)  # default history_days = 30

    assert rep.verified and rep.squashed == 3 and rep.kept == 2
    revs = git(repo, "rev-list", "--reverse", "HEAD").split()
    assert len(revs) == 3 and revs[0] == rep.new_root
    assert git(repo, "rev-list", "--parents", "-n1", revs[0]).split() == [revs[0]]
    assert git(repo, "rev-parse", f"{revs[0]}^{{tree}}").strip() == trees[40]
    assert [git(repo, "rev-parse", f"{r}^{{tree}}").strip() for r in revs[1:]] == [trees[10], trees[5]]
    assert "history before" in git(repo, "log", "-1", "--format=%s", revs[0])
    # B13 round 17 U71: the commit's own count line, no "(s)"
    assert "\n3 commits older than 30 days were squashed into this snapshot " in git(
        repo, "log", "-1", "--format=%B", revs[0]
    )
    assert git(repo, "rev-parse", "published").strip() == rep.new_root
    assert not exists(repo, old_blob) and not exists(repo, shas[100])
    assert git(repo, "reflog", "--all").strip() == ""
    assert not gv.compaction_due(repo, gv.GovernanceConfig(), now=NOW)
    assert gv.read_audit(tmp_state_dir)[-1]["action"] == "compact"


def test_compaction_keeps_curated_on_the_equivalent_rewritten_commit(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    """KISS K06: a sync's RECONCILE may compact right after the automatic checkpoint moved ``curated``; the
    annotated tag must follow its commit through the rewrite."""
    repo = tmp_docs_repo
    shas = _dated_repo(repo)
    gitops.tag_curated(repo, shas[10])
    tree = git(repo, "rev-parse", f"{shas[10]}^{{tree}}").strip()
    rep = gv.compact_history(make_config(tmp_path, repo, tmp_state_dir), now=NOW)
    assert rep.verified and rep.squashed == 3
    curated = gitops.curated_checkpoint(repo)
    assert curated is not None and curated[0] != shas[10]
    assert git(repo, "rev-parse", f"{curated[0]}^{{tree}}").strip() == tree
    assert git(repo, "cat-file", "-t", gitops.CURATED_TAG).strip() == "tag"
    git(repo, "merge-base", "--is-ancestor", curated[0], "HEAD")


def test_compaction_is_a_noop_when_history_is_recent(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    shas = _dated_repo(tmp_docs_repo)
    cfg = make_config(tmp_path, tmp_docs_repo, tmp_state_dir)
    rep = gv.compact_history(cfg, 365, now=NOW)
    assert rep.squashed == 0 and rep.new_root is None
    assert git(tmp_docs_repo, "rev-parse", "HEAD").strip() == shas[5]
    dry = gv.compact_history(cfg, 30, dry_run=True, now=NOW)
    assert dry.dry_run and dry.squashed == 3
    assert git(tmp_docs_repo, "rev-parse", "HEAD").strip() == shas[5]


def test_compaction_nulls_dangling_tombstone_hints(world: World) -> None:
    rep = gv.compact_history(world.config, 1, now=datetime(2026, 6, 18, tzinfo=UTC))
    assert rep.verified and rep.squashed == 3 and rep.dangling_recovery_hints == 1
    conn = sqlite3.connect(world.config.state_paths.db)
    try:
        assert conn.execute("SELECT last_commit FROM tombstones").fetchone() == (None,)
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------
# remote policy (C15 req 36; ACL flattening)
# ---------------------------------------------------------------------------------------------------------


def test_remote_policy_and_push(world: World, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(world.repo, "remote", "add", "origin", str(remote))
    git(world.repo, "push", "-q", "origin", "main")
    git(world.repo, "fetch", "-q", "origin")
    default = gv.GovernanceConfig()
    findings = gv.remote_policy_findings(world.repo, default)
    assert len(findings) == 1 and "allow_remote = false" in findings[0]
    other = gv.GovernanceConfig(allow_remote=True, remote_url_prefixes=("https://dev.azure.com/contoso/",))
    assert "not under a tenant-owned prefix" in gv.remote_policy_findings(world.repo, other)[0]
    # B13 round 17 U2, U16: each finding says the removal or the allowing is the person's, never "run ..."
    for finding in (*findings, *gv.remote_policy_findings(world.repo, other)):
        assert "the person removes it" in finding and finding.endswith("not the agent"), finding
        assert "run `git" not in finding and finding.startswith("remote origin ("), finding
    allowed = gv.GovernanceConfig(allow_remote=True, remote_url_prefixes=(str(tmp_path),))
    assert gv.remote_policy_findings(world.repo, allowed) == []
    assert gv.push_rewritten(world.repo, default, {}) == "disabled"
    assert gv.push_rewritten(world.repo, other, {}).startswith("refused")

    rep = gv.purge(
        world.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.OPERATOR, gov=allowed, push=True
    )
    assert rep.verified and rep.remote.startswith("pushed")
    assert git(remote, "rev-parse", "main") == git(world.repo, "rev-parse", "main")
    assert any("remote-tracking" in n for n in rep.notes)
    # B13 round 18 V90: counted nouns, never "ref(s)"
    assert rep.remote == "pushed 1 ref to origin", rep.remote
    assert "deleted 1 remote-tracking ref; remotes are NOT purged" in rep.notes, rep.notes
    git(world.repo, "branch", "extra")
    assert gv.push_rewritten(world.repo, allowed, {}) == "pushed 2 refs to origin"


def test_purge_without_push_names_the_remote(world: World, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(world.repo, "remote", "add", "origin", str(remote))
    git(world.repo, "push", "-q", "origin", "main")
    before = git(remote, "rev-parse", "main")
    rep = gv.purge(world.config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.OPERATOR)
    assert rep.remote.startswith("not pushed: remote(s) origin")
    assert git(remote, "rev-parse", "main") == before  # nothing left the machine


# ---------------------------------------------------------------------------------------------------------
# Time Machine (C15 req 42)
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.macos
@pytest.mark.skipif(sys.platform != "darwin", reason="tmutil is macOS-only")
def test_time_machine_exclusions_are_applied(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    # real tmutil costs ~11 s per path on this Mac (measured), so the command runs for real on mirror/ only
    cfg = make_config(tmp_path, tmp_docs_repo, tmp_state_dir)
    seen: list[list[str]] = []

    def real_on_first_path(argv: object) -> subprocess.CompletedProcess[str]:
        args = list(argv)  # type: ignore[call-overload]
        seen.append(args)
        return subprocess.run(args[:3], capture_output=True, text=True, check=False)

    lines = gv.apply_time_machine_exclusions(cfg, runner=real_on_first_path)
    assert f"pending {cfg.state_paths.db}" in lines
    dirs = [
        tmp_docs_repo / "mirror",
        tmp_docs_repo / "archive",  # [governance] archive: what a source deleted, until a purge erases it
        tmp_docs_repo / ".git",  # every version of every page: a purge cannot reach a backup of it
        cfg.cache_dir,
        cfg.state_paths.teams_store,
        cfg.state_paths.staging,
    ]
    assert seen == [["/usr/bin/tmutil", "addexclusion", *map(str, dirs)]]
    assert all(f"excluded {p}" in lines and p.is_dir() for p in dirs)
    assert f"excluded {tmp_docs_repo / '.git'}" in lines
    assert f"pending {cfg.state_paths.db}-wal" in lines and f"pending {cfg.state_paths.db}-shm" in lines
    argv = ["/usr/bin/tmutil", "isexcluded", str(dirs[0])]
    out = subprocess.run(argv, capture_output=True, text=True, check=False).stdout
    assert "[Excluded]" in out
    assert tmp_docs_repo / "topics" not in gv.time_machine_exclusions(cfg)


# ---------------------------------------------------------------------------------------------------------
# offboarding (C15 req 42 uninstall; audit corporate-macos-uninstall-remanence)
# ---------------------------------------------------------------------------------------------------------

_DUMP = """keychain: "/k.keychain-db"
version: 512
class: "genp"
attributes:
    "acct"<blob>="msal_token_cache:t:c"
    "svce"<blob>="agentsync"
keychain: "/k.keychain-db"
class: "genp"
attributes:
    "acct"<blob>="x"
    "svce"<blob>="agentsync-other"
keychain: "/k.keychain-db"
class: "inet"
attributes:
    "acct"<blob>="y"
    "svce"<blob>="agentsync"
"""


def _fake_security(deleted: list[str]) -> gv.Runner:
    def run(argv: object) -> subprocess.CompletedProcess[str]:
        args = list(argv)  # type: ignore[call-overload]
        if args[1] == "dump-keychain":
            return subprocess.CompletedProcess(args, 0, _DUMP, "")
        if args[1] == "delete-generic-password":
            acct = args[args.index("-a") + 1]
            if acct in deleted:
                return subprocess.CompletedProcess(args, 44, "", "not found")
            deleted.append(acct)
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    return run


def _install_fake_agents(cfg: Config) -> list[Path]:
    paths = [plist_path(f"{cfg.launchd_label_prefix}.{s}") for s in ("poll", "reconcile")]
    for p in paths:
        write(p, "<plist/>")
    return paths


def _populate(world: World) -> None:
    for d in (world.config.cache_dir, world.config.log_dir):
        d.mkdir(parents=True, exist_ok=True)


def test_offboard_dry_run_lists_exact_locations(world: World) -> None:
    _populate(world)
    plists = _install_fake_agents(world.config)
    rep = gv.offboard(world.config, runner=_fake_security([]))
    assert rep.dry_run and rep.removed == ()
    kinds = [(loc.kind, loc.location) for loc in rep.locations]
    assert kinds == [
        ("launch-agent", str(plists[0])),
        ("launch-agent", str(plists[1])),
        ("keychain-item", "keychain:agentsync/msal_token_cache:t:c"),
        ("cache-dir", str(world.config.cache_dir)),
        ("log-dir", str(world.config.log_dir)),
        ("docs-repo", str(world.repo)),
        ("config-file", str(world.config.config_path)),
        ("state-dir", str(world.state)),
    ]
    docs = next(loc for loc in rep.locations if loc.kind == "docs-repo")
    assert docs.action.startswith("keep")
    assert all(p.exists() for p in plists) and world.repo.exists() and world.state.exists()
    assert any("retention owner" in s for s in rep.manual_steps)


def test_offboard_needs_the_confirm_token(world: World) -> None:
    with pytest.raises(gv.GovernanceError, match="confirm="):
        gv.offboard(world.config, dry_run=False, confirm="yes", runner=_fake_security([]))
    assert world.state.exists()


def test_offboard_refused_under_hold(world: World) -> None:
    gv.set_hold(world.state, gv.HOLD_ALL, reason="litigation", owner="legal")
    dry = gv.offboard(world.config, runner=_fake_security([]))
    assert dry.manual_steps[0].startswith("BLOCKED")
    with pytest.raises(gv.HoldActiveError, match="litigation"):
        gv.offboard(world.config, dry_run=False, confirm=str(world.repo), runner=_fake_security([]))


def test_offboard_removes_everything_but_keeps_docs_unless_asked(world: World) -> None:
    _populate(world)
    plists = _install_fake_agents(world.config)
    booted: list[str] = []

    def fake_uninstall(label: str) -> bool:
        booted.append(label)
        plist_path(label).unlink()
        return True

    deleted: list[str] = []
    rep = gv.offboard(
        world.config,
        dry_run=False,
        confirm=str(world.repo),
        runner=_fake_security(deleted),
        launchd_uninstall=fake_uninstall,
    )
    assert rep.errors == ()
    assert booted == ["com.agentsync-test.poll", "com.agentsync-test.reconcile"]
    assert not any(p.exists() for p in plists)
    assert deleted == ["msal_token_cache:t:c"]
    assert (
        not world.config.cache_dir.exists() and not world.config.log_dir.exists() and not world.state.exists()
    )
    assert world.repo.exists() and world.config.config_path.exists()
    # idempotent, and purge_data removes the docs repo and sources.toml
    rep2 = gv.offboard(
        world.config,
        purge_data=True,
        dry_run=False,
        confirm=str(world.repo),
        runner=_fake_security(deleted),
        launchd_uninstall=lambda label: False,
    )
    assert set(rep2.removed) == {str(world.repo), str(world.config.config_path)}
    assert not world.repo.exists() and not world.config.config_path.exists()


def _login_keychain() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library" / "Keychains" / "login.keychain-db"


@pytest.mark.macos
@pytest.mark.skipif(
    sys.platform != "darwin" or not _login_keychain().exists(), reason="needs the macOS login Keychain"
)
def test_offboard_deletes_real_keychain_items_of_its_service(world: World) -> None:
    """Q83 (B13 round 4): the login keychain is one per user, outside the isolated HOME, so gates running at
    once shared this test's fixed service and deleted each other's items. Each run uses its own service, and
    holds a lock across processes so no sibling writes the keychain while this run's dump reads it."""
    kc = _login_keychain()
    service = f"agentsync-test-{secrets.token_hex(4)}"
    accounts = ["governance-test-a", "governance-test-b"]
    with (Path("/tmp") / f"agentsync-keychain-test-{os.getuid()}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            for a in accounts:
                subprocess.run(
                    [
                        "/usr/bin/security",
                        "add-generic-password",
                        "-U",
                        "-s",
                        service,
                        "-a",
                        a,
                        "-w",
                        "not-a-secret",
                        str(kc),
                    ],
                    check=True,
                    capture_output=True,
                )
            plan = gv.offboard_plan(world.config, keychain_service=service, keychain=kc)
            items = [loc.location for loc in plan if loc.kind == "keychain-item"]
            assert items == [f"keychain:{service}/{a}" for a in accounts]
            rep = gv.offboard(
                world.config,
                dry_run=False,
                confirm=str(world.repo),
                keychain_service=service,
                keychain=kc,
                launchd_uninstall=lambda label: False,
            )
            assert set(items) <= set(rep.removed)
            for a in accounts:
                rc = subprocess.run(
                    ["/usr/bin/security", "find-generic-password", "-s", service, "-a", a, str(kc)],
                    capture_output=True,
                    check=False,
                ).returncode
                assert rc == 44
        finally:
            for a in accounts:
                subprocess.run(
                    ["/usr/bin/security", "delete-generic-password", "-s", service, "-a", a, str(kc)],
                    capture_output=True,
                    check=False,
                )


def test_time_machine_exclusions_batch_and_fallback(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    cfg = make_config(tmp_path, tmp_docs_repo, tmp_state_dir)
    calls: list[list[str]] = []

    def fake(argv: object) -> subprocess.CompletedProcess[str]:
        args = list(argv)  # type: ignore[call-overload]
        calls.append(args)
        bad = len(args) > 3 or args[2].endswith("staging")
        return subprocess.CompletedProcess(args, 1 if bad else 0, "", "denied" if bad else "")

    lines = gv.apply_time_machine_exclusions(cfg, runner=fake)
    assert calls[0][:2] == ["/usr/bin/tmutil", "addexclusion"] and len(calls[0]) == 8
    assert len(calls) == 7  # one batch + six per-path retries
    assert lines[-1] == f"failed {cfg.state_paths.staging}: denied"
    assert lines[4] == f"pending {cfg.state_paths.db}"


def test_purge_keeps_the_rows_and_index_lines_of_another_item_now_at_the_purged_path(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    """An upstream-deleted item's page path is later taken by a different live file (the tombstone was reaped,
    the queued purge was not yet run).  The purge drops the old item's blobs and its manifest line, and keeps
    the live file's page, outputs row, DEPENDS row, manifest line and DEPENDS.tsv line (field triage
    2026-10-08: they were erased while the run said VERIFIED)."""
    repo, at = tmp_docs_repo, "mirror/src/report.md"

    def shard(stable_id: str, rel: str) -> str:
        line = {
            "outputs": [{"path": at, "unit_id": "whole"}],
            "rel_path": rel,
            "source_id": "src",
            "stable_id": stable_id,
            "state": "live",
        }
        return json.dumps(line, sort_keys=True) + "\n"

    write(repo / at, page("src", "A1", "old/report.txt", "ALPHA-GONE"))
    write(repo / "_manifest/src.jsonl", shard("A1", "old/report.txt"))
    write(repo / "DEPENDS.tsv", f"page\tsource\ntopics/x.md\t{at}\n")
    commit_all(repo, "c1", "2026-06-01T10:00:00Z")
    (repo / at).unlink()
    write(repo / "_manifest/src.jsonl", "")
    write(repo / "DEPENDS.tsv", "page\tsource\n")
    commit_all(repo, "c2", "2026-06-02T10:00:00Z")
    write(repo / at, page("src", "B1", "new/report.txt", "BRAVO-LIVE"))
    write(repo / "_manifest/src.jsonl", shard("B1", "new/report.txt"))
    write(repo / "DEPENDS.tsv", f"page\tsource\ntopics/x.md\t{at}\n")
    commit_all(repo, "c3", "2026-06-03T10:00:00Z")
    config = make_config(tmp_path, repo, tmp_state_dir)
    db = config.state_paths.db
    m = Manifest(db)
    m.set_tree_sha(git(repo, "rev-parse", "HEAD^{tree}").strip())
    m.close()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sources (source_id, kind, config_state, config_fingerprint) "
        "VALUES ('src','local','live','x')"
    )
    conn.execute(
        "INSERT INTO items (source_id, stable_id, name, rel_path, state, first_seen_run, last_seen_run) "
        "VALUES ('src', 'B1', 'report.txt', 'new/report.txt', 'live', 1, 1)"
    )
    conn.execute(
        "INSERT INTO outputs (output_path, source_id, stable_id, unit_id, rendered_sha256, status, "
        "built_run) VALUES (?, 'src', 'B1', 'whole', ?, 'ok', 1)",
        (at, sha("BRAVO-LIVE")),
    )
    conn.execute("INSERT INTO depends VALUES ('topics/x.md', ?, ?, 'primary')", (at, "0" * 64))
    conn.commit()
    conn.close()
    selector = gv.PurgeSelector(source_id="src", stable_id="A1")

    preview = gv.purge(config, selector, reason=gv.PurgeReason.UPSTREAM_DELETED, dry_run=True)
    assert preview.docs_paths == (at,) and preview.taken_paths == (at,)
    rep = gv.purge(config, selector, reason=gv.PurgeReason.UPSTREAM_DELETED)

    assert rep.verified and rep.taken_paths == (at,)
    assert rep.manifest_rows_removed["outputs"] == 0 and rep.manifest_rows_removed["depends"] == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT stable_id FROM outputs WHERE output_path = ?", (at,)).fetchall() == [("B1",)]
    assert conn.execute("SELECT page FROM depends WHERE source = ?", (at,)).fetchall() == [("topics/x.md",)]
    conn.close()
    assert "BRAVO-LIVE" in git(repo, "show", f"HEAD:{at}")
    assert git(repo, "show", "HEAD:_manifest/src.jsonl") == shard("B1", "new/report.txt")
    assert f"topics/x.md\t{at}" in git(repo, "show", "HEAD:DEPENDS.tsv")
    history = "".join(git(repo, "cat-file", "-p", s) for s in all_blobs(repo))
    assert "ALPHA-GONE" not in history and '"stable_id": "A1"' not in history


def _seed_live(repo: Path, config: Config, rows: list[tuple[str, str, str, str]]) -> Path:
    """A manifest whose tree_sha is HEAD's, with source ``src`` and one live item and outputs row per
    (stable_id, rel_path, output_path, body)."""
    db = config.state_paths.db
    m = Manifest(db)
    m.set_tree_sha(git(repo, "rev-parse", "HEAD^{tree}").strip())
    m.close()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sources (source_id, kind, config_state, config_fingerprint) "
        "VALUES ('src', 'local', 'live', 'x')"
    )
    for stable_id, rel, out, body in rows:
        conn.execute(
            "INSERT INTO items (source_id, stable_id, name, rel_path, state, first_seen_run, last_seen_run) "
            "VALUES ('src', ?, 'report.txt', ?, 'live', 1, 1)",
            (stable_id, rel),
        )
        conn.execute(
            "INSERT INTO outputs (output_path, source_id, stable_id, unit_id, rendered_sha256, status, "
            "built_run) VALUES (?, 'src', ?, 'whole', ?, 'ok', 1)",
            (out, stable_id, sha(body)),
        )
    conn.commit()
    conn.close()
    return db


def _shard_line(stable_id: str, rel: str, at: str) -> str:
    """One ``_manifest/src.jsonl`` line in the export's own form (``Manifest.export_shard``)."""
    line = {
        "outputs": [{"path": at, "rendered_sha256": None, "unit_id": "whole"}],
        "rel_path": rel,
        "source_id": "src",
        "stable_id": stable_id,
        "state": "live",
        "state_reason": None,
    }
    return json.dumps(line, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"


def test_purge_keeps_the_manifest_line_of_a_new_file_with_the_same_name(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    """The field shape of a taken path: the deleted file and the new one have the same name in the same
    folder, so the same source path.  The source-path rule would hit the new file's manifest line; it is kept
    by its id, while the purged item's line and page leave every commit."""
    repo, at, rel = tmp_docs_repo, "mirror/src/dir/report.md", "dir/report.txt"
    write(repo / at, page("src", "A1", rel, "ALPHA-GONE"))
    write(repo / "_manifest/src.jsonl", _shard_line("A1", rel, at))
    commit_all(repo, "c1", "2026-06-01T10:00:00Z")
    (repo / at).unlink()
    write(repo / "_manifest/src.jsonl", "")
    commit_all(repo, "c2", "2026-06-02T10:00:00Z")
    write(repo / at, page("src", "B1", rel, "BRAVO-LIVE"))
    write(repo / "_manifest/src.jsonl", _shard_line("B1", rel, at))
    commit_all(repo, "c3", "2026-06-03T10:00:00Z")
    config = make_config(tmp_path, repo, tmp_state_dir)
    db = _seed_live(repo, config, [("B1", rel, at, "BRAVO-LIVE")])
    selector = gv.PurgeSelector(source_id="src", stable_id="A1")

    rep = gv.purge(config, selector, reason=gv.PurgeReason.UPSTREAM_DELETED)

    assert rep.verified and rep.taken_paths == (at,)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT stable_id FROM outputs WHERE output_path = ?", (at,)).fetchall() == [("B1",)]
    conn.close()
    assert git(repo, "show", "HEAD:_manifest/src.jsonl") == _shard_line("B1", rel, at)
    history = "".join(git(repo, "cat-file", "-p", s) for s in all_blobs(repo))
    assert "ALPHA-GONE" not in history and '"stable_id":"A1"' not in history


def test_purge_keeps_the_manifest_line_of_each_worktree_heads_own_file_at_a_taken_path(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    """Two worktree HEADs each hold a different live file at the purged item's page path, with its source
    path.  The taken map kept only the first HEAD's id, so the other HEAD's manifest line was erased under a
    VERIFIED report (B13 round 2, R91)."""
    repo, at, rel = tmp_docs_repo, "mirror/src/dir/report.md", "dir/report.txt"
    write(repo / at, page("src", "A1", rel, "ALPHA-GONE"))
    write(repo / "_manifest/src.jsonl", _shard_line("A1", rel, at))
    commit_all(repo, "c1", "2026-06-01T10:00:00Z")
    (repo / at).unlink()
    write(repo / "_manifest/src.jsonl", "")
    c2 = commit_all(repo, "c2", "2026-06-02T10:00:00Z")
    side = tmp_path / "side"
    git(repo, "worktree", "add", "-q", "-b", "side", str(side), c2)
    write(side / at, page("src", "C1", rel, "CHARLIE-LIVE"))
    write(side / "_manifest/src.jsonl", _shard_line("C1", rel, at))
    commit_all(side, "c3 side", "2026-06-03T09:00:00Z")
    write(repo / at, page("src", "B1", rel, "BRAVO-LIVE"))
    write(repo / "_manifest/src.jsonl", _shard_line("B1", rel, at))
    commit_all(repo, "c3", "2026-06-03T10:00:00Z")
    config = make_config(tmp_path, repo, tmp_state_dir)
    _seed_live(repo, config, [("B1", rel, at, "BRAVO-LIVE")])

    rep = gv.purge(config, gv.PurgeSelector(source_id="src", stable_id="A1"), reason=gv.PurgeReason.OPERATOR)

    assert rep.verified and rep.taken_paths == (at,)
    assert git(repo, "show", "main:_manifest/src.jsonl") == _shard_line("B1", rel, at)
    assert git(repo, "show", "side:_manifest/src.jsonl") == _shard_line("C1", rel, at)
    assert "CHARLIE-LIVE" in git(repo, "show", f"side:{at}")
    history = "".join(git(repo, "cat-file", "-p", s) for s in all_blobs(repo))
    assert "ALPHA-GONE" not in history and '"stable_id":"A1"' not in history


def test_purge_keeps_a_listed_files_outputs_row_at_a_purged_path_with_no_page_in_head(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    """A live file's outputs row sits at the purged item's earlier page path, but HEAD has no page there (a
    crash between the manifest write and the commit, the tree then cleaned by hand).  The path is not taken,
    so the history's index lines naming it still go; the live file's row stays for the next cycle's repair
    (B13 round 2, R93: it was deleted by path whoever owned it)."""
    repo, at = tmp_docs_repo, "mirror/src/dir/report.md"
    write(repo / at, page("src", "A1", "dir/report.txt", "ALPHA-GONE"))
    write(repo / "CHANGELOG/2026-06.md", f"- A {at}\n")
    commit_all(repo, "c1", "2026-06-01T10:00:00Z")
    (repo / at).unlink()
    write(repo / "CHANGELOG/2026-06.md", f"- A {at}\n- D {at}\n")
    commit_all(repo, "c2", "2026-06-02T10:00:00Z")
    config = make_config(tmp_path, repo, tmp_state_dir)
    db = _seed_live(repo, config, [("B1", "new/report.txt", at, "BRAVO-LIVE")])

    rep = gv.purge(config, gv.PurgeSelector(source_id="src", stable_id="A1"), reason=gv.PurgeReason.OPERATOR)

    assert rep.verified and rep.taken_paths == () and rep.docs_paths == (at,)
    assert rep.manifest_rows_removed["outputs"] == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT stable_id FROM outputs WHERE output_path = ?", (at,)).fetchall() == [("B1",)]
    conn.close()
    assert at not in git(repo, "show", "HEAD:CHANGELOG/2026-06.md")
    history = "".join(git(repo, "cat-file", "-p", s) for s in all_blobs(repo))
    assert "ALPHA-GONE" not in history and at not in history


def test_purge_marks_no_path_taken_that_the_rewrite_drops(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    """A docs-glob purge finds item Y only in history; the manifest read that follows makes Y's current page
    path explicit, where HEAD holds a page under an id the manifest does not know for the same source path (a
    rekey from before item_aliases), which the rewrite drops.  That path is not taken: its index lines go, and
    the preview counts what the run drops."""
    repo, old, cur, rel = (
        tmp_docs_repo,
        "mirror/src/tmp/report.md",
        "mirror/src/dir/report.md",
        "dir/report.txt",
    )
    write(repo / old, page("src", "Y", rel, "YANKEE-OLD"))
    commit_all(repo, "c1", "2026-06-01T10:00:00Z")
    (repo / old).unlink()
    write(repo / cur, page("src", "Z", rel, "ZULU-LEGACY"))
    write(repo / "CHANGELOG/2026-06.md", f"- D {old}\n- A {cur}\n")
    write(repo / "DEPENDS.tsv", f"page\tsource\ntopics/x.md\t{cur}\n")
    commit_all(repo, "c2", "2026-06-02T10:00:00Z")
    config = make_config(tmp_path, repo, tmp_state_dir)
    _seed_live(repo, config, [("Y", rel, cur, "ZULU-LEGACY")])
    selector = gv.PurgeSelector(docs_glob="mirror/src/tmp/*")

    preview = gv.purge(config, selector, reason=gv.PurgeReason.OPERATOR, dry_run=True)
    rep = gv.purge(config, selector, reason=gv.PurgeReason.OPERATOR)

    assert preview.taken_paths == () and rep.taken_paths == ()
    assert preview.blobs_targeted == rep.blobs_targeted == 2
    assert preview.items == rep.items == (("src", "Y"), ("src", "Z"))
    assert rep.verified and not exists(repo, f"HEAD:{cur}")
    assert cur not in git(repo, "show", "HEAD:CHANGELOG/2026-06.md")
    assert cur not in git(repo, "show", "HEAD:DEPENDS.tsv")


def test_purge_queue_runs_an_upstream_deletion_of_a_file_that_came_back_refused(world: World) -> None:
    """C8: a refused row does not keep an upstream deletion queued; the queued purge is the only erasure of
    the file's earlier text."""
    conn = sqlite3.connect(world.config.state_paths.db)
    conn.execute("UPDATE items SET state = 'refused', state_reason = 'label' WHERE stable_id = 'K1'")
    conn.commit()
    conn.close()
    k1 = gv.PurgeSelector(source_id="src", stable_id="K1")
    assert gv.enqueue_purge(world.state, k1, gv.PurgeReason.UPSTREAM_DELETED, now=NOW)
    (rep,) = gv.run_purge_queue(world.config, now=NOW)
    assert rep.verified and not rep.not_run and rep.items == (("src", "K1"),)
    assert "KEEP-BODY" not in git(world.repo, "log", "-p", "--all")
    assert gv.pending_purges(world.state) == []


def test_purge_spares_a_live_file_whose_earlier_page_path_a_deleted_file_took(world: World) -> None:
    """S1 was hr/secret.docx at mirror/src/secret.docx.md and was renamed (c3). A new file N9 named
    hr/secret.docx took the freed page path and was deleted upstream. The same-path rule for a re-key the
    manifest did not record took S1's earlier pages there as N9's, and the purge of N9 erased the live S1:
    its row, its page in HEAD and its history, VERIFIED. S1 is a file the manifest lists: never N9's."""
    repo = world.repo
    write(repo / "mirror/src/secret.docx.md", page("src", "N9", "hr/secret.docx", "NEW-FILE-BODY"))
    c6 = commit_all(repo, "sync: 1a src", "2026-06-25T10:00:00Z")
    tomb = TombstoneRow(
        output_path="mirror/src/secret.docx.md",
        source_id="src",
        stable_id="N9",
        unit_id="whole",
        deleted_at="2026-06-26",
        deleted_run=6,
        last_rendered_sha256=sha("NEW-FILE-BODY"),
        last_commit=c6,
        reap_after="2026-12-17",
        reason="deleted-upstream",
    )
    write(
        repo / "mirror/src/secret.docx.md",
        render_tombstone(tomb, title="secret", source_kind="local", source_path="hr/secret.docx"),
    )
    commit_all(repo, "sync: 1d src", "2026-06-26T10:00:00Z")
    conn = sqlite3.connect(world.config.state_paths.db)
    conn.execute(
        "INSERT INTO items (source_id, stable_id, name, rel_path, state, first_seen_run, last_seen_run) "
        "VALUES ('src', 'N9', 'secret.docx', 'hr/secret.docx', 'tombstone', 6, 6)"
    )
    conn.execute(
        "INSERT INTO outputs (output_path, source_id, stable_id, unit_id, action_key, rendered_sha256, "
        "status, built_run) VALUES ('mirror/src/secret.docx.md', 'src', 'N9', 'whole', NULL, ?, "
        "'tombstone', 6)",
        (sha("N9"),),
    )
    conn.execute(
        "INSERT INTO tombstones VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            tomb.output_path,
            tomb.source_id,
            tomb.stable_id,
            tomb.unit_id,
            tomb.deleted_at,
            tomb.deleted_run,
            tomb.last_rendered_sha256,
            tomb.last_commit,
            tomb.reap_after,
            tomb.reason,
        ),
    )
    conn.commit()
    conn.close()
    n9 = gv.PurgeSelector(source_id="src", stable_id="N9")
    assert gv.enqueue_purge(world.state, n9, gv.PurgeReason.UPSTREAM_DELETED, now=NOW)
    (preview,) = gv.run_purge_queue(world.config, dry_run=True, now=NOW)
    assert preview.items == (("src", "N9"),) and preview.docs_paths == ("mirror/src/secret.docx.md",)
    (rep,) = gv.run_purge_queue(world.config, now=NOW)
    assert rep.verified and rep.items == (("src", "N9"),)
    assert (repo / "mirror/src/renamed-secret.docx.md").is_file()
    conn = sqlite3.connect(world.config.state_paths.db)
    assert conn.execute("SELECT state FROM items WHERE stable_id = 'S1'").fetchall() == [("live",)]
    conn.close()
    history = git(repo, "log", "-p", "--all")
    assert "SECRET-ALPHA-1" in history and "SECRET-ALPHA-3" in history and "NEW-FILE-BODY" not in history
    assert any(p.endswith(".files/img.png") for p in all_blobs(repo).values()), "S1's earlier sidecar"
    assert gv.pending_purges(world.state) == []


@pytest.mark.parametrize("reason", [gv.PurgeReason.ERASURE_REQUEST, gv.PurgeReason.UPSTREAM_DELETED])
def test_a_purge_by_selector_takes_the_queued_purges_it_did_off_the_queue(
    world: World, reason: gv.PurgeReason
) -> None:
    """v10 bring-back (E5.c): `agentsync purge id=...` erased an item and left its queued purge waiting."""
    up, label = gv.PurgeReason.UPSTREAM_DELETED, gv.PurgeReason.LABEL_ESCALATION
    s1 = gv.PurgeSelector(stable_id="S1", source_id="src")
    k1 = gv.PurgeSelector(stable_id="K1", source_id="src")
    any_source = gv.PurgeSelector(stable_id="S1")  # may name S1 in another source too: kept
    for selector, why in ((s1, up), (s1, label), (k1, up), (any_source, up)):
        assert gv.enqueue_purge(world.state, selector, why, now=NOW)
    gv.purge(world.config, s1, reason=reason, dry_run=True, now=NOW)
    assert len(gv.pending_purges(world.state)) == 4  # a dry run takes nothing off
    rep = gv.purge(world.config, s1, reason=reason, now=NOW)
    assert rep.verified and rep.items == (("src", "S1"),)
    # an upstream-deleted purge suppresses nothing: the label-escalation entry stays, its run still suppresses
    kept = [] if reason is not up else [(s1, label)]
    left = [(q.selector, q.reason) for q in gv.pending_purges(world.state)]
    assert left == [*kept, (k1, up), (any_source, up)]


def test_a_purge_leaves_a_queued_purge_it_cannot_stand_in_for(world: World) -> None:
    """A docs glob narrowed to another source erases the matched page only, so the item's queued purge stays;
    a queue file this build cannot read is left as it is, and the purge's report stands."""
    s1 = gv.PurgeSelector(stable_id="S1", source_id="src")
    assert gv.enqueue_purge(world.state, s1, gv.PurgeReason.LABEL_ESCALATION, now=NOW)
    page = gv.PurgeSelector(source_id="other", docs_glob="mirror/src/renamed-secret.docx.md")
    rep = gv.purge(world.config, page, reason=gv.PurgeReason.OPERATOR, now=NOW)
    assert rep.verified and rep.items == (("src", "S1"),)
    assert "SECRET-ALPHA-1" in git(world.repo, "log", "--all", "-p")  # the earlier page is still in history
    assert [q.selector for q in gv.pending_purges(world.state)] == [s1]
    queue = world.state / "governance" / "purge-queue.json"
    data = json.loads(queue.read_text(encoding="utf-8"))
    data["queue"].append(
        {"selector": {"stable_id": "X9", "source_id": "src"}, "reason": "later", "enqueued_at": ""}
    )
    queue.write_text(json.dumps(data), encoding="utf-8")
    before = queue.read_bytes()
    assert gv.purge(world.config, s1, reason=gv.PurgeReason.OPERATOR, now=NOW).verified
    assert queue.read_bytes() == before


def test_the_compaction_line_takes_its_noun_from_the_day_count(tmp_docs_repo: Path) -> None:
    """B13 round 15 S26: doctor's governance line, which the setup report shows, read "no commit older than 37
    day(s)" though the count is known."""
    for days, said in ((1, "older than 1 day "), (37, "older than 37 days ")):
        gov = gv.GovernanceConfig(history_days=days, compaction_slack_days=0)
        state, line = gv.compaction_state(tmp_docs_repo, gov)
        assert state == "ok" and said in line and "(s)" not in line, line


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_an_audit_trail_this_account_cannot_open_changes_no_hold_and_queues_no_purge(tmp_path: Path) -> None:
    """B13 round 23 A79: set_hold, release_hold and enqueue_purge wrote holds.json or the queue and then
    raised on the audit line, so the change took effect with no record. The audit trail is opened first."""
    state = tmp_path / "state"
    gv.set_hold(state, "all", reason="matter 7", owner="Contoso Legal")
    gv.enqueue_purge(
        state, gv.PurgeSelector(source_id="contoso-docs", stable_id="A"), gv.PurgeReason.DLP_REMEDIATION
    )
    audit = gv.audit_path(state)
    files = [gv._holds_file(state), gv._queue_file(state), audit]
    before = [f.read_bytes() for f in files]
    audit.chmod(0)
    try:
        for change in (
            lambda: gv.set_hold(state, "contoso-docs", reason="matter 8", owner="Contoso Legal"),
            lambda: gv.release_hold(state, "all", owner="Contoso Legal"),
            lambda: gv.enqueue_purge(
                state,
                gv.PurgeSelector(source_id="contoso-docs", stable_id="B"),
                gv.PurgeReason.UPSTREAM_DELETED,
            ),
        ):
            with pytest.raises(PermissionError):
                change()
    finally:
        audit.chmod(0o600)
    assert [f.read_bytes() for f in files] == before
    assert [h.scope for h in gv.state_holds(state)] == ["all"]


@pytest.mark.parametrize("name", ["holds.json", "purge-queue.json", "suppressed.json"])
def test_a_write_through_a_working_link_lands_in_its_target_and_keeps_the_link(
    tmp_path: Path, name: str
) -> None:
    """B13 round 28 I94: a working link at holds.json or purge-queue.json (ruling 15: it stays ok) was
    replaced by a local file on the first write, and the target kept the old holds (a release left the
    hold on all there). Every governance write goes into the target, through its folder, and the link
    stays a link."""
    state = tmp_path / "state"
    gov = gv.governance_dir(state)
    gov.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = elsewhere / f"contoso-{name}"
    target.write_text(
        {"holds.json": '{"holds": []}\n', "purge-queue.json": '{"queue": []}\n'}.get(name, "{}\n")
    )
    link = gov / name
    link.symlink_to(target)
    if name == "holds.json":
        gv.set_hold(state, "all", reason="matter 8", owner="Contoso Legal")
        assert link.is_symlink() and [h["scope"] for h in json.loads(target.read_text())["holds"]] == ["all"]
        assert gv.release_hold(state, "all", owner="Contoso Legal")
        assert json.loads(target.read_text()) == {"holds": []}
    elif name == "purge-queue.json":
        sel = gv.PurgeSelector.parse("docs=mirror/src/a.md", source_id=None)
        assert gv.enqueue_purge(state, sel, gv.PurgeReason.ERASURE_REQUEST)
        assert len(json.loads(target.read_text())["queue"]) == 1
    else:
        gv.suppress_items(state, [("src", "id-1")])
        assert json.loads(target.read_text())["items"] == [["src", "id-1"]]
    assert link.is_symlink() and link.resolve() == target.resolve()
    assert gv.governance_not_a_file(state) is None
    assert not [p for p in gov.iterdir() if p.name.endswith(".tmp")], "no temporary file left"
    assert not [p for p in elsewhere.iterdir() if p.name.endswith(".tmp")]


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a 0500 folder")
def test_a_write_through_a_link_into_a_folder_it_cannot_write_is_a_handled_refusal(tmp_path: Path) -> None:
    """I94's verifier: a link's target folder this account cannot write fails as a GovernanceError naming
    the target, never a traceback, and never a silent local file beside the link."""
    state = tmp_path / "state"
    gov = gv.governance_dir(state)
    gov.mkdir(parents=True)
    shut = tmp_path / "shut"
    shut.mkdir()
    target = shut / "holds.json"
    target.write_text('{"holds": []}\n')
    (gov / "holds.json").symlink_to(target)
    shut.chmod(0o500)
    try:
        with pytest.raises(gv.GovernanceError, match=str(target.resolve().parent)):
            gv.set_hold(state, "all", reason="matter 8", owner="Contoso Legal")
    finally:
        shut.chmod(0o700)
    assert (gov / "holds.json").is_symlink() and json.loads(target.read_text()) == {"holds": []}


def test_a_hold_list_created_while_it_is_read_is_read_never_a_link_to_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 28 I107: a hold list a concurrent hold created between the reader's failed open and its
    look at what is there was called "a symlink to nothing", whose fix moves a valid hold list aside. It is
    read once more; a dangling link still raises."""
    state = tmp_path / "state"
    gv.governance_dir(state).mkdir(parents=True)
    holds = gv._holds_file(state)
    real = gv.read_regular_text
    body = {"holds": [{"scope": "all", "reason": "matter 8", "owner": "Contoso Legal", "set_at": "x"}]}
    calls = []

    def racing(path: Path) -> str | None:
        got = real(path)
        if path == holds and not calls:
            calls.append(path)
            gv._atomic_write_json(holds, body)  # the concurrent hold, written as set_hold writes it
        return got

    monkeypatch.setattr(gv, "read_regular_text", racing)
    assert [h.scope for h in gv._state_holds(state)] == ["all"]
    monkeypatch.setattr(gv, "read_regular_text", real)
    holds.unlink()
    holds.symlink_to(tmp_path / "gone" / "holds.json")
    with pytest.raises(gv.GovernanceNotAFileError, match="a symlink to nothing"):
        gv._state_holds(state)


def test_an_empty_or_unverified_run_of_a_lost_purge_clears_nothing_and_a_verified_one_clears_it(
    tmp_path: Path,
) -> None:
    """B13 round 30 C401 (ruling 33): any later purge-summary of the same selector cleared an earlier lost
    record, though an empty run (nothing left to find) or an unverified one put nothing back on the list.
    Only a verified run that found content again clears it; it is then judged by what it put back."""
    state = tmp_path / "state"
    gv.governance_dir(state).mkdir(parents=True)
    sel = gv.PurgeSelector(source_id="contoso", stable_id="contoso-volume:0042")

    def run(at: str, *, verified: bool, found: bool) -> None:
        if found:
            item = {"action": "purge", "at": at, "reason": "erasure-request", "source_id": "contoso"}
            gv.append_audit(
                state, {**item, "path_sha256": None, "stable_id_sha256": gv._sha(sel.stable_id or "")}
            )
        summary = {"action": "purge-summary", "at": at, "reason": "erasure-request", "verified": verified}
        gv.append_audit(state, {**summary, "selector_kind": sel.kind(), "selector_sha256": sel.fingerprint()})

    run("2026-10-01T00:00:00Z", verified=True, found=True)
    assert [r["at"] for r in gv.lost_suppressions(state)] == ["2026-10-01T00:00:00Z"]  # no list: lost
    run("2026-10-02T00:00:00Z", verified=True, found=False)
    run("2026-10-03T00:00:00Z", verified=False, found=True)
    lost = [r["at"] for r in gv.lost_suppressions(state)]
    assert lost == ["2026-10-01T00:00:00Z", "2026-10-03T00:00:00Z"], lost
    gv.suppress_items(state, [("contoso", "contoso-volume:0042")])
    run("2026-10-04T00:00:00Z", verified=True, found=True)
    assert gv.lost_suppressions(state) == []


def test_a_rerun_clears_an_older_lost_record_only_when_it_found_that_records_own_items(
    tmp_path: Path,
) -> None:
    """C344's review of C401: a record an older build wrote (no ``canonical_sha256s``) was cleared by a
    verified rerun that found any item; it is cleared only when the rerun found its own items again."""
    state = tmp_path / "state"
    gv.governance_dir(state).mkdir(parents=True)
    sel = gv.PurgeSelector(source_id="contoso", path_glob="legal/*")

    def run(at: str, *ids: str) -> None:
        for i in ids:
            item = {"action": "purge", "at": at, "reason": "erasure-request", "source_id": "contoso"}
            gv.append_audit(state, {**item, "path_sha256": None, "stable_id_sha256": gv._sha(i)})
        summary = {"action": "purge-summary", "at": at, "reason": "erasure-request", "verified": True}
        gv.append_audit(state, {**summary, "selector_kind": sel.kind(), "selector_sha256": sel.fingerprint()})

    run("2026-10-01T00:00:00Z", "contoso-0001")
    gv._add_suppressions(state, [("contoso", "contoso-0002")], [("contoso", "legal/*")])
    run("2026-10-02T00:00:00Z", "contoso-0002")  # found another item: the older record stays lost
    assert [r["at"] for r in gv.lost_suppressions(state)] == ["2026-10-01T00:00:00Z"]
    run("2026-10-03T00:00:00Z", "contoso-0001")  # found the older record's own item: that record clears
    assert [r["at"] for r in gv.lost_suppressions(state)] == ["2026-10-03T00:00:00Z"]
    gv.suppress_items(state, [("contoso", "contoso-0001")])
    assert gv.lost_suppressions(state) == []


def test_a_lost_queued_purge_is_named_by_kind_and_source_and_its_id_rebuilt_by_fingerprint(
    tmp_path: Path,
) -> None:
    """B13 round 30 C370, C312 (ruling 35): each lost queued purge is named by reason, time, selector kind
    and source; a stable id the manifest names (an alias too) is rebuilt by fingerprint into the exact
    command; with no row, the fix says where the id is and that a path= or docs= purge clears it only by
    erasing that id; a glob's text is the person's, the same kind and source. No selector text enters the
    audit trail."""
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    sels = [
        (gv.PurgeSelector(stable_id="contoso-0001", source_id="contoso"), gv.PurgeReason.ERASURE_REQUEST),
        (gv.PurgeSelector(stable_id="contoso-old", source_id="contoso"), gv.PurgeReason.DLP_REMEDIATION),
        (gv.PurgeSelector(stable_id="contoso-0009", source_id="contoso"), gv.PurgeReason.UPSTREAM_DELETED),
        (gv.PurgeSelector(path_glob="legal/*", source_id="contoso"), gv.PurgeReason.LABEL_ESCALATION),
    ]
    for sel, reason in sels:
        assert gv.enqueue_purge(state, sel, reason)
    conn = sqlite3.connect(state / "manifest.sqlite")
    conn.execute("CREATE TABLE items (source_id TEXT, stable_id TEXT)")
    conn.execute("CREATE TABLE item_aliases (source_id TEXT, alias_id TEXT, stable_id TEXT)")
    conn.execute("INSERT INTO items VALUES ('contoso', 'contoso-0001'), ('fabrikam', 'contoso-0009')")
    conn.execute("INSERT INTO item_aliases VALUES ('contoso', 'contoso-old', 'contoso-0002')")
    conn.commit()
    conn.close()
    gv._queue_file(state).rename(state / "queue.away")
    lost = gv.lost_queued(state)
    at = [r["at"] for r in lost]
    bin_ = "~/.local/bin/agentsync purge"
    err = gv.queue_lost(state)
    assert err is not None
    assert err.detail.endswith(
        f"as queued and not done (erasure-request at {at[0]}, a stable-id selector of source contoso; "
        f"dlp-remediation at {at[1]}, a stable-id selector of source contoso; upstream-deleted at {at[2]}, "
        f"a stable-id selector of source contoso; label-escalation at {at[3]}, a path-glob selector of "
        "source contoso)"
    ), err.detail
    # each record's redo, in order, in the fix's own form (C542: lost_queue_lines, only tests read, is gone)
    ways = [
        f"for erasure-request at {at[0]}: `{bin_} id=contoso-0001 --source contoso --reason erasure-request`",
        f"for dlp-remediation at {at[1]}: `{bin_} id=contoso-old --source contoso --reason dlp-remediation`",
        f"for upstream-deleted at {at[2]}: no manifest row names its id, "
        "which is the `stable_id:` line of the page's front matter in the docs repo's history under "
        f"mirror/contoso/ while no purge has erased the page; `{bin_} id=<that id> --source contoso --reason "
        "upstream-deleted`: a `path=` or `docs=` purge clears it only when it erases that id",
        f"for label-escalation at {at[3]}: agentsync keeps only its "
        f"selector's hash, so the glob comes from the person's own records: `{bin_} path=<glob> --source "
        "contoso --reason label-escalation`, as only a purge of the same kind and source clears it",
    ]
    assert err.fix.endswith(
        f"runs each purge by hand before the next sync, which refuses until then ({'; '.join(ways)})"
    ), err.fix
    trail = gv.audit_path(state).read_text(encoding="utf-8")
    assert "contoso-0" not in trail and "contoso-old" not in trail and "legal/" not in trail


def test_a_lost_queued_ids_rebuild_fingerprints_each_manifest_id_once_per_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 31 C538: each lost stable-id record walked every manifest id of its source, one fingerprint
    each, so 300 lost records over 20,000 ids took about 30 s per status check. Each id is fingerprinted once
    per record source; a source-narrowed and an unnarrowed record are both rebuilt, the first in sorted
    order, and nothing outlives the call, so a manifest that changed is read again."""
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    db = state / "manifest.sqlite"
    ids = [f"contoso-{n:04d}" for n in range(40)]
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE items (source_id TEXT, stable_id TEXT)")
    conn.executemany("INSERT INTO items VALUES (?, ?)", [("contoso", i) for i in ids])
    conn.execute("INSERT INTO items VALUES ('fabrikam', 'fabrikam-0001')")
    conn.commit()
    conn.close()
    sels = [gv.PurgeSelector(stable_id=f"contoso-gone-{n}", source_id="contoso") for n in range(8)]
    sels += [gv.PurgeSelector(stable_id="contoso-0007", source_id="contoso")]
    sels += [gv.PurgeSelector(stable_id="fabrikam-0001")]  # every source
    for sel in sels:
        assert gv.enqueue_purge(state, sel, gv.PurgeReason.ERASURE_REQUEST)
    gv._queue_file(state).rename(state / "queue.away")
    lost = gv.lost_queued(state)
    assert [r.get("source_id") for r in lost] == ["contoso"] * 9 + [None]
    calls: list[str | None] = []
    fingerprint = gv.PurgeSelector.fingerprint

    def counted(self: gv.PurgeSelector) -> str:
        calls.append(self.source_id)
        return fingerprint(self)

    monkeypatch.setattr(gv.PurgeSelector, "fingerprint", counted)
    assert gv._rebuilt_ids(db, lost) == {8: "contoso-0007", 9: "fabrikam-0001"}
    assert calls.count("contoso") <= len(ids) and calls.count(None) <= len(ids) + 1, len(calls)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM items WHERE stable_id = 'contoso-0007'")
    conn.commit()
    conn.close()
    assert gv._rebuilt_ids(db, lost) == {9: "fabrikam-0001"}


def test_the_purge_record_keeps_a_queued_purge_past_the_folders_loss_and_never_shrinks(
    tmp_path: Path,
) -> None:
    """B13 round 30 C381 (ruling 34): with governance/ moved aside, the queue and the audit trail went with
    it, and a queued erasure read as none. purge-record.json, written after the queue's audit line, keeps it:
    the queue reads as lost and the sync refuses. A new trail's lines are added after the kept ones, never in
    their place."""
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    first = gv.PurgeSelector(stable_id="contoso-0001", source_id="contoso")
    assert gv.enqueue_purge(state, first, gv.PurgeReason.ERASURE_REQUEST)
    assert [r["action"] for r in gv.recorded_purges(state)] == ["purge-enqueued"]
    gv.governance_dir(state).rename(state / "governance.away")
    (lost,) = gv.lost_queued(state)
    assert lost["selector_sha256"] == first.fingerprint()
    assert gv.queue_lost(state) is not None
    second = gv.PurgeSelector(stable_id="contoso-0002", source_id="contoso")
    assert gv.enqueue_purge(state, second, gv.PurgeReason.DLP_REMEDIATION)  # a new governance/
    kept = [r["selector_sha256"] for r in gv.recorded_purges(state)]
    assert kept == [first.fingerprint(), second.fingerprint()]
    gv.record_purges(state)
    assert [r["selector_sha256"] for r in gv.recorded_purges(state)] == kept
    assert [r["selector_sha256"] for r in gv.lost_queued(state)] == [first.fingerprint()]


def test_a_hold_only_the_record_keeps_has_the_origin_record_and_a_gone_folder_is_named(
    tmp_path: Path,
) -> None:
    """B13 round 30 C313 (ruling 42): with governance/ moved aside under a hold, describe() said "(audit)"
    with no audit trail on disk and the FAIL named only holds.json. A hold only the record keeps is
    ``record``; with the folder gone the FAIL names it and its fix puts it back whole; with the folder there
    and the audit trail replaying the hold, the words are as before."""
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    gv.set_hold(state, "all", reason="Contoso matter", owner="Dana Example", now=NOW)
    gov = gv.governance_dir(state)
    gov.rename(state / "governance.kept")
    (hold,) = gv.lost_holds(state)
    assert hold.origin == "record" and hold.kept_by == ("hold-record.json",), hold
    assert "hold all (record): lost from the hold list" in hold.describe()
    detail, fix = gv.lost_hold_words(state, hold)
    assert detail.startswith(
        f"{gov}/ is missing, with the hold list, agentsync's audit trail, the purge queue"
    ), detail
    assert "no longer lists the hold on all set by the records owner (kept by hold-record.json)" in detail
    assert fix.startswith("the person puts agentsync's governance folder (") and " back whole " in fix, fix
    (state / "governance.kept").rename(gov)
    gv._holds_file(state).write_text('{"holds": []}\n', encoding="utf-8")
    (hold,) = gv.lost_holds(state)
    assert hold.origin == "audit", hold
    detail, fix = gv.lost_hold_words(state, hold)
    assert detail.startswith(f"{gv._holds_file(state)} no longer lists"), detail
    assert fix.startswith("the person puts the hold list back from the copy kept"), fix


@pytest.mark.parametrize("record", ['{"holds": ["contoso-alpha"]}', '{"holds": []}', None, "{"])
def test_the_governance_folders_fix_asks_a_kept_folder_back_first_and_keeps_its_move_aside(
    tmp_path: Path, record: str | None
) -> None:
    """B13 round 30 C324 (ruling 42): with a hold record that names a scope, the governance folder's fix
    still said "only where none was ever kept", a condition the record answers. It says a folder was kept,
    by no scope, and keeps the move aside as the way out; an empty, missing or unreadable record keeps
    today's words and the records owner's clause."""
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    if record is not None:
        gv.hold_record_file(state).write_text(record, encoding="utf-8")
    gov = gv.governance_dir(state)
    gov.write_text("not a folder\n", encoding="utf-8")
    err = gv.governance_not_a_file(state)
    assert err is not None
    shown = f"moves {doctor._shell_path(gov)} aside, keeping a copy ("
    assert shown in err.fix, err.fix
    # the record's write is best effort, so it can miss a hold on all: the records owner is asked always
    assert "the records owner" in err.fix and "before any purge" in err.fix, err.fix
    if record == '{"holds": ["contoso-alpha"]}':
        kept = "a copy (a governance folder was kept: hold-record.json names a hold); only if no copy is left"
        assert kept in err.fix and "contoso-alpha" not in err.fix, err.fix
        assert "only where none was ever kept" not in err.fix, err.fix
        owner = (
            "hold-record.json's record; if a hold was ever set here that it does not name, the person checks "
            "with the records owner and sets it again, before any purge"
        )
        assert owner in err.fix, err.fix
    else:
        assert "from a copy; only where none was ever kept, moves " in err.fix, err.fix
        assert "the person checks with the records owner, and sets each hold again" in err.fix, err.fix
