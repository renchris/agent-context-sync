"""governance: purge, history compaction, holds, remote policy, Time Machine exclusions, offboarding.

Every git test runs real git on repos under tmp_path.  The only Keychain writes are items with service
``agentsync-test`` that the test deletes; no launchd job is ever created or removed (a fake uninstaller).
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentsync import gitops
from agentsync import governance as gv
from agentsync.config import Config
from agentsync.errors import ConfigError
from agentsync.frontmatter import MirrorFrontmatter, render_mirror_page
from agentsync.graph import auth
from agentsync.manifest import Manifest, TombstoneRow
from agentsync.model import PageStatus
from agentsync.ops.launchd import plist_path
from agentsync.publish import render_tombstone

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
    out: dict[str, str] = {}
    for line in git(repo, "rev-list", "--objects", "--all").splitlines():
        s, _, p = line.partition(" ")
        if git(repo, "cat-file", "-t", s).strip() == "blob":
            out.setdefault(s, p)
    return out


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
            "at": "2026-09-29T12:00:00Z",
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
    s1 = gv.PurgeSelector(stable_id="S1", source_id="src")
    assert gv.enqueue_purge(world.state, s1, gv.PurgeReason.LABEL_ESCALATION, now=NOW)
    assert not gv.enqueue_purge(world.state, s1, gv.PurgeReason.LABEL_ESCALATION, now=NOW)
    assert [q.selector for q in gv.pending_purges(world.state)] == [s1]
    gv.set_hold(world.state, "src", reason="hold", owner="legal", now=NOW)
    assert gv.run_purge_queue(world.config, now=NOW) == []
    assert len(gv.pending_purges(world.state)) == 1
    gv.release_hold(world.state, "src", owner="legal", now=NOW)
    reports = gv.run_purge_queue(world.config, now=NOW)
    assert len(reports) == 1 and reports[0].verified
    assert gv.pending_purges(world.state) == []


def test_purge_queue_dry_run_writes_nothing_and_keeps_the_queue(world: World) -> None:
    """Field report 2026-10-05: `purge --queue --dry-run` ran both queued purges and rewrote history."""
    s1 = gv.PurgeSelector(stable_id="S1", source_id="src")
    assert gv.enqueue_purge(world.state, s1, gv.PurgeReason.LABEL_ESCALATION, now=NOW)
    head = git(world.repo, "rev-parse", "HEAD").strip()
    reports = gv.run_purge_queue(world.config, dry_run=True, now=NOW)
    assert len(reports) == 1 and reports[0].dry_run and reports[0].commits_rewritten == 0
    assert git(world.repo, "rev-parse", "HEAD").strip() == head
    assert [q.selector for q in gv.pending_purges(world.state)] == [s1]


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
    kc = _login_keychain()
    service = "agentsync-test"
    accounts = ["governance-test-a", "governance-test-b"]
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
