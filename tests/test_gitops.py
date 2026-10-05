"""gitops: the docs repo's git plumbing, exercised against real repos under tmp_path."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentsync import gitops
from agentsync.errors import GitError, PublishError
from agentsync.model import ChangeOp, MirrorChange


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def mc(op: ChangeOp, path: str, sid: str = "src") -> MirrorChange:
    return MirrorChange(op=op, path=path, source_id=sid, stable_id=path)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "agent-context" / "docs"
    assert gitops.ensure_repo(r) is True
    write(r, ".gitignore", "_sync/STATE.md\n_manifest/cache/\n")
    return r


# ---- executable and runner ---------------------------------------------------------------------------------


def test_git_executable_is_absolute_and_runs() -> None:
    exe = gitops.git_executable()
    assert exe.is_absolute()
    assert os.access(exe, os.X_OK)
    out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, check=True).stdout
    assert out.startswith("git version")


def test_run_git_raises_git_error_with_argv_and_stderr(repo: Path) -> None:
    with pytest.raises(GitError) as info:
        gitops.run_git(repo, "rev-parse", "--verify", "no-such-ref")
    assert info.value.argv == ["rev-parse", "--verify", "no-such-ref"]
    assert info.value.returncode != 0
    proc = gitops.run_git(repo, "rev-parse", "--verify", "no-such-ref", check=False)
    assert proc.returncode != 0


def test_run_git_ignores_inherited_git_dir(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("GIT_DIR", str(other / "nowhere.git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / "index"))
    top = gitops.run_git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    assert Path(top).resolve() == repo.resolve()


def test_run_git_sets_fixed_environment(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    env = gitops._env()
    assert env["LC_ALL"] == "C"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_OPTIONAL_LOCKS"] == "0"


# ---- ensure_repo -----------------------------------------------------------------------------------------


def test_ensure_repo_initialises_main_with_local_config(repo: Path) -> None:
    assert git(repo, "symbolic-ref", "--short", "HEAD").strip() == "main"
    assert git(repo, "config", "--local", "core.precomposeunicode").strip() == "true"
    assert git(repo, "config", "--local", "core.quotepath").strip() == "false"
    assert git(repo, "config", "--local", "user.name").strip() == "agentsync"
    assert git(repo, "config", "--local", "user.email").strip() == "agentsync@localhost"


def test_ensure_repo_is_idempotent_and_keeps_an_operator_identity(repo: Path) -> None:
    git(repo, "config", "--local", "user.name", "Operator")
    assert gitops.ensure_repo(repo) is False
    assert git(repo, "config", "--local", "user.name").strip() == "Operator"


def test_ensure_repo_adopts_an_existing_repo(tmp_docs_repo: Path) -> None:
    assert gitops.ensure_repo(tmp_docs_repo) is False
    assert git(tmp_docs_repo, "config", "--local", "user.email").strip() == "agentsync@localhost"


def test_ensure_repo_refuses_cloud_storage(tmp_path: Path) -> None:
    cloud = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "docs"
    with pytest.raises(PublishError, match="CloudStorage"):
        gitops.ensure_repo(cloud)
    assert not cloud.exists()


def test_ensure_repo_refuses_a_symlink_into_cloud_storage(tmp_path: Path) -> None:
    cloud = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "docs"
    cloud.mkdir(parents=True)
    link = tmp_path / "docs-link"
    link.symlink_to(cloud)
    with pytest.raises(PublishError, match="CloudStorage"):
        gitops.ensure_repo(link)


def test_ensure_repo_refuses_a_file(tmp_path: Path) -> None:
    f = tmp_path / "docs"
    f.write_text("x")
    with pytest.raises(PublishError, match="not a directory"):
        gitops.ensure_repo(f)


# ---- HEAD --------------------------------------------------------------------------------------------------


def test_head_is_none_on_an_unborn_branch_then_set(repo: Path) -> None:
    assert gitops.head_sha(repo) is None
    assert gitops.head_tree_sha(repo) is None
    write(repo, "INDEX.md", "# x\n")
    sha = gitops.commit_cycle(repo, "sync: first")
    assert sha is not None and len(sha) == 40
    assert gitops.head_sha(repo) == sha
    assert gitops.head_tree_sha(repo) == git(repo, "rev-parse", "HEAD^{tree}").strip()


# ---- status / subject / body -------------------------------------------------------------------------------


def test_has_changes_sees_untracked_modified_and_deleted(repo: Path) -> None:
    assert gitops.has_changes(repo) is True  # untracked .gitignore
    write(repo, "mirror/src/a.md", "a\n")
    gitops.commit_cycle(repo, "sync: seed")
    assert gitops.has_changes(repo) is False
    write(repo, "mirror/src/a.md", "b\n")
    assert gitops.has_changes(repo) is True
    git(repo, "checkout", "--", "mirror/src/a.md")
    (repo / "mirror/src/a.md").unlink()
    assert gitops.has_changes(repo) is True


def test_has_changes_ignores_gitignored_and_out_of_scope_files(repo: Path) -> None:
    gitops.commit_cycle(repo, "sync: seed")
    write(repo, "_sync/STATE.md", "volatile\n")
    write(repo, "_manifest/cache/k/result.json", "{}\n")
    write(repo, "scratch.txt", "not a pathspec\n")
    assert gitops.has_changes(repo) is False
    assert gitops.has_changes(repo, ["scratch.txt"]) is True


def test_commit_subject_counts_and_sorts_sources() -> None:
    changes = [
        mc(ChangeOp.ADDED, "mirror/b/x.md", "b"),
        mc(ChangeOp.ADDED, "mirror/a/y.md", "a"),
        mc(ChangeOp.MODIFIED, "mirror/a/z.md", "a"),
        mc(ChangeOp.RENAMED, "mirror/a/r.md", "a"),
        mc(ChangeOp.DELETED, "mirror/b/d.md", "b"),
        mc(ChangeOp.DELETED, "mirror/b/e.md", "b"),
    ]
    assert gitops.commit_subject(changes, ["b", "a", "b"]) == "sync: 2a 1m 1r 2d a,b"
    assert gitops.commit_subject([], []) == "sync: 0a 0m 0r 0d"


def test_commit_body_is_structured_and_deterministic() -> None:
    changes = [mc(ChangeOp.ADDED, "mirror/b/x.md", "b"), mc(ChangeOp.DELETED, "mirror/a/y.md", "a")]
    body = gitops.commit_body(changes, run_id=7, mode="poll", notes=["breaker  ok\n"])
    assert body == "a: 0a 0m 0r 1d\nb: 1a 0m 0r 0d\nbreaker ok\n\nAgentsync-Run: 7\nAgentsync-Mode: poll"
    assert gitops.commit_body(list(reversed(changes)), run_id=7, mode="poll", notes=["breaker ok"]) == body
    assert gitops.commit_body([], run_id=1, mode="reconcile") == "Agentsync-Run: 1\nAgentsync-Mode: reconcile"


# ---- commit_cycle ------------------------------------------------------------------------------------------


def test_commit_cycle_commits_once_with_subject_and_body(repo: Path) -> None:
    write(repo, "mirror/src/a.md", "a\n")
    write(repo, "_sync/STATE.md", "never committed\n")
    sha = gitops.commit_cycle(repo, "sync: 1a 0m 0r 0d src", "src: 1a 0m 0r 0d\n\nAgentsync-Run: 1")
    assert sha == gitops.head_sha(repo)
    msg = git(repo, "log", "-1", "--format=%B")
    assert msg == "sync: 1a 0m 0r 0d src\n\nsrc: 1a 0m 0r 0d\n\nAgentsync-Run: 1\n\n"
    assert git(repo, "log", "-1", "--format=%an <%ae>").strip() == "agentsync <agentsync@localhost>"
    files = gitops.tracked_files(repo)
    assert files == [".gitignore", "mirror/src/a.md"]
    assert git(repo, "rev-list", "--count", "HEAD").strip() == "1"


def test_commit_cycle_returns_none_when_nothing_changed(repo: Path) -> None:
    write(repo, "mirror/src/a.md", "a\n")
    first = gitops.commit_cycle(repo, "sync: seed")
    assert gitops.commit_cycle(repo, "sync: again") is None
    assert gitops.head_sha(repo) == first


def test_commit_cycle_on_empty_scope_returns_none(tmp_path: Path) -> None:
    r = tmp_path / "empty"
    gitops.ensure_repo(r)
    assert gitops.commit_cycle(r, "sync: nothing") is None
    assert gitops.head_sha(r) is None


def test_commit_cycle_stages_deletions_of_whole_directories(repo: Path) -> None:
    write(repo, "_index/by-entity.tsv", "entity\tpage\n")
    write(repo, "mirror/src/a.md", "a\n")
    gitops.commit_cycle(repo, "sync: seed")
    (repo / "_index/by-entity.tsv").unlink()
    (repo / "_index").rmdir()
    sha = gitops.commit_cycle(repo, "sync: 0a 0m 0r 1d src")
    assert sha is not None
    assert "_index/by-entity.tsv" not in gitops.tracked_files(repo)


def test_commit_cycle_never_commits_outside_its_pathspecs(repo: Path) -> None:
    write(repo, "mirror/src/a.md", "a\n")
    write(repo, "notes.txt", "operator scratch\n")
    write(repo, "staged-elsewhere.txt", "staged by hand\n")
    git(repo, "add", "staged-elsewhere.txt")
    gitops.commit_cycle(repo, "sync: seed")
    tracked = gitops.tracked_files(repo)
    assert "notes.txt" not in tracked
    assert "mirror/src/a.md" in tracked
    committed = git(repo, "ls-tree", "-r", "--name-only", "HEAD").split()
    assert "staged-elsewhere.txt" not in committed


def test_commit_cycle_handles_glob_characters_and_unicode(repo: Path) -> None:
    write(repo, "mirror/src/report[v2]*.md", "x\n")
    write(repo, "mirror/src/café.md", "y\n")
    gitops.commit_cycle(repo, "sync: odd names")
    tracked = gitops.tracked_files(repo)
    assert "mirror/src/report[v2]*.md" in tracked
    assert "mirror/src/café.md" in tracked
    assert gitops.has_changes(repo) is False


def test_commit_cycle_does_not_sign_or_run_hooks(repo: Path) -> None:
    git(repo, "config", "--local", "commit.gpgsign", "true")
    git(repo, "config", "--local", "gpg.program", "/usr/bin/false")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)  # ensure_repo inits with an empty template: no hooks dir
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    write(repo, "INDEX.md", "# i\n")
    assert gitops.commit_cycle(repo, "sync: unsigned") is not None


def test_commit_cycle_flattens_a_multiline_subject(repo: Path) -> None:
    write(repo, "INDEX.md", "# i\n")
    gitops.commit_cycle(repo, "sync: 1a\nsecond line")
    assert git(repo, "log", "-1", "--format=%s").strip() == "sync: 1a second line"


def test_commit_cycle_rejects_an_empty_subject(repo: Path) -> None:
    write(repo, "INDEX.md", "# i\n")
    with pytest.raises(PublishError):
        gitops.commit_cycle(repo, "  \n ")


# ---- recovery ----------------------------------------------------------------------------------------------


def test_restore_generated_resets_generated_paths_only(repo: Path) -> None:
    write(repo, "mirror/src/a.md", "committed\n")
    write(repo, "topics/acme.md", "curated\n")
    write(repo, "INDEX.md", "# committed\n")
    gitops.commit_cycle(repo, "sync: seed")
    write(repo, "mirror/src/a.md", "half-written\n")
    write(repo, "mirror/src/new.md", "untracked\n")
    write(repo, "_manifest/src.jsonl", "{}\n")
    git(repo, "add", "_manifest/src.jsonl")
    (repo / "INDEX.md").unlink()
    write(repo, "topics/acme.md", "agent edit in progress\n")
    write(repo, "_sync/STATE.md", "state\n")
    gitops.restore_generated(repo)
    assert (repo / "mirror/src/a.md").read_text() == "committed\n"
    assert not (repo / "mirror/src/new.md").exists()
    assert not (repo / "_manifest/src.jsonl").exists()
    assert (repo / "INDEX.md").read_text() == "# committed\n"
    assert (repo / "topics/acme.md").read_text() == "agent edit in progress\n"
    assert (repo / "_sync/STATE.md").exists()
    assert not gitops.has_changes(repo, gitops.GENERATED_PATHSPECS)


def test_paths_changed_since_unions_commits_and_worktree_without_writing_the_index(repo: Path) -> None:
    for name in ("old", "committed", "edited", "staged"):
        write(repo, f"topics/{name}.md", "a\n")
    base = gitops.commit_cycle(repo, "sync: seed")
    assert base is not None
    write(repo, "topics/committed.md", "b\n")
    gitops.commit_cycle(repo, "sync: later")
    write(repo, "topics/edited.md", "b\n")
    write(repo, "topics/staged.md", "b\n")
    git(repo, "add", "topics/staged.md")
    write(repo, "topics/new dir/untracked.md", "x\n")
    write(repo, "mirror/src/outside.md", "x\n")
    os.utime(repo / "topics/old.md", (0, 0))  # stat-dirty, same content: a diff against the tree refreshes it
    index = repo / ".git" / "index"
    before = index.stat()
    assert gitops.paths_changed_since(repo, base, ("topics",)) == {
        "topics/committed.md",
        "topics/edited.md",
        "topics/staged.md",
        "topics/new dir/untracked.md",
    }
    after = index.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_archive_is_pipeline_owned_and_snapshot_tags_never_move(repo: Path) -> None:
    assert "archive" in gitops.GENERATED_PATHSPECS and "archive" in gitops.COMMIT_PATHSPECS
    write(repo, "archive/src/a.md", "kept\n")
    first = gitops.commit_cycle(repo, "sync: archive")
    assert first is not None
    assert "archive/src/a.md" in gitops.tracked_files(repo)
    when = datetime(2026, 10, 2, 7, 8, 9, tzinfo=UTC)
    name = gitops.tag_snapshot(repo, first, when)
    assert name == "snapshot/2026-10-02T070809Z"
    assert git(repo, "cat-file", "-t", name).strip() == "tag"
    write(repo, "INDEX.md", "# later\n")
    later = gitops.commit_cycle(repo, "sync: later")
    assert later is not None
    with pytest.raises(GitError):  # an existing snapshot is never moved
        gitops.tag_snapshot(repo, later, when)
    assert git(repo, "rev-parse", f"{name}^{{commit}}").strip() == first


def test_restore_generated_on_an_unborn_branch_removes_generated(repo: Path) -> None:
    write(repo, "mirror/src/a.md", "x\n")
    write(repo, "CHANGELOG.md", "x\n")
    git(repo, "add", "CHANGELOG.md")
    write(repo, "topics/t.md", "keep\n")
    gitops.restore_generated(repo)
    assert not (repo / "mirror/src/a.md").exists()
    assert not (repo / "CHANGELOG.md").exists()
    assert (repo / "topics/t.md").exists()
    assert gitops.tracked_files(repo) == []


# ---- tags, listing -----------------------------------------------------------------------------------------


def test_tag_published_force_moves(repo: Path) -> None:
    write(repo, "INDEX.md", "1\n")
    first = gitops.commit_cycle(repo, "sync: 1")
    assert first is not None
    gitops.tag_published(repo, first)
    assert git(repo, "rev-parse", "published^{commit}").strip() == first
    write(repo, "INDEX.md", "2\n")
    second = gitops.commit_cycle(repo, "sync: 2")
    assert second is not None
    gitops.tag_published(repo, second)
    assert git(repo, "rev-parse", "published^{commit}").strip() == second
    assert git(repo, "cat-file", "-t", "published").strip() == "commit"  # lightweight


def test_tracked_files_sorted_and_filtered(repo: Path) -> None:
    for rel in ("mirror/b.md", "mirror/a.md", "topics/t.md", "INDEX.md"):
        write(repo, rel, "x\n")
    gitops.commit_cycle(repo, "sync: seed")
    assert gitops.tracked_files(repo, ["mirror"]) == ["mirror/a.md", "mirror/b.md"]
    assert gitops.tracked_files(repo)[0] == ".gitignore"


# ---- push --------------------------------------------------------------------------------------------------


def test_push_is_disabled_by_default_and_needs_a_remote(repo: Path) -> None:
    write(repo, "INDEX.md", "x\n")
    gitops.commit_cycle(repo, "sync: 1")
    assert gitops.push_if_allowed(repo, allow=False) == "disabled"
    assert gitops.push_if_allowed(repo, allow=True) == "no-remote"


def test_push_when_allowed_pushes_branch_and_published_tag(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    write(repo, "INDEX.md", "x\n")
    sha = gitops.commit_cycle(repo, "sync: 1")
    assert sha is not None
    gitops.tag_published(repo, sha)
    assert gitops.push_if_allowed(repo, allow=True) == "pushed main to origin"
    assert git(remote, "rev-parse", "refs/heads/main").strip() == sha
    assert git(remote, "rev-parse", "refs/tags/published").strip() == sha


def test_push_refuses_a_remote_inside_cloud_storage(repo: Path) -> None:
    remote = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "docs.git"
    remote.mkdir(parents=True)
    git(repo, "remote", "add", "origin", str(remote))
    write(repo, "INDEX.md", "x\n")
    gitops.commit_cycle(repo, "sync: 1")
    assert gitops.push_if_allowed(repo, allow=True).startswith("refused")


def test_push_refuses_an_unborn_branch(repo: Path, tmp_path: Path) -> None:
    git(repo, "remote", "add", "origin", str(tmp_path / "remote.git"))
    assert gitops.push_if_allowed(repo, allow=True) == "refused: nothing committed"


# ---- the operator's git configuration cannot drop, rewrite or block pages ----------------------------------


@pytest.fixture
def hostile_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A global git config (via GIT_CONFIG_GLOBAL) that ignores mirror pages, blocks commits and rewrites
    blobs: the measured ~/.gitignore_global failure (critic-git-global-excludes-drop-mirror) and worse."""
    h = tmp_path / "hostile"
    (h / "hooks").mkdir(parents=True)
    (h / "template" / "hooks").mkdir(parents=True)
    (h / "template" / "info").mkdir(parents=True)
    ran = h / "hook-ran"
    for hooks in (h / "hooks", h / "template" / "hooks"):
        for name in (
            "pre-commit",
            "commit-msg",
            "prepare-commit-msg",
            "post-commit",
            "reference-transaction",
        ):
            hook = hooks / name
            hook.write_text(f"#!/bin/sh\necho {name} >> '{ran}'\nexit 1\n")
            hook.chmod(0o755)
    (h / "template" / "info" / "exclude").write_text("*\n")
    excludes = h / "gitignore_global"
    excludes.write_text("*.md\nmirror/\n.claude/\nRESEARCH-*.md\n_manifest/\n*.jsonl\n")
    attributes = h / "gitattributes_global"
    attributes.write_text("* filter=evil\n")
    fsmonitor = h / "fsmonitor.sh"
    fsmonitor.write_text(f"#!/bin/sh\necho fsmonitor >> '{ran}'\nexit 1\n")
    fsmonitor.chmod(0o755)
    cfg = h / "gitconfig"
    cfg.write_text(
        f"""[core]
\texcludesFile = {excludes}
\thooksPath = {h / "hooks"}
\tattributesFile = {attributes}
\tfsmonitor = {fsmonitor}
\tautocrlf = true
\tsafecrlf = true
\tcommentChar = s
[init]
\ttemplateDir = {h / "template"}
[filter "evil"]
\tclean = sed s/secret/REDACTED/
\tsmudge = cat
\trequired = true
[commit]
\tgpgSign = true
[tag]
\tgpgSign = true
[gpg]
\tprogram = /usr/bin/false
[status]
\tshowUntrackedFiles = no
[i18n]
\tcommitEncoding = ISO-8859-1
[user]
\tuseConfigOnly = true
"""
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.excludesFile")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(excludes))
    return {"ran": ran, "excludes": excludes}


def test_hostile_global_config_cannot_drop_or_rewrite_mirror_pages(
    tmp_path: Path, hostile_global: dict[str, Path]
) -> None:
    repo = tmp_path / "docs"
    assert gitops.ensure_repo(repo) is True
    # the hostile config really is in force for a plain git call (so the test proves something)
    probe = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", "mirror/src/research-plan.md"], check=False
    )
    assert probe.returncode == 0
    hostile_global["ran"].unlink(missing_ok=True)  # the plain-git probe itself may run the fsmonitor program
    assert not (repo / ".git" / "hooks").exists()  # init.templateDir was not copied
    pages = {
        "mirror/src/research-plan.md": "secret plan\n",
        "mirror/src/dot-claude/settings.json.md": "secret\n",
        "mirror/src/notes.md": "line one\r\nline two\n",
        "_manifest/src.jsonl": '{"a":1}\n',
        "INDEX.md": "# index\n",
    }
    for rel, text in pages.items():
        write(repo, rel, text)
    assert gitops._ignored_generated(repo) == []
    assert gitops.has_changes(repo) is True
    sha = gitops.commit_cycle(repo, "sync: 3a 0m 0r 0d src", "src: 3a\nsecond line\n")
    assert sha is not None
    assert sorted(gitops.tracked_files(repo)) == sorted(pages)
    blob = gitops.run_git(repo, "cat-file", "blob", "HEAD:mirror/src/research-plan.md").stdout
    assert blob == "secret plan\n"  # no clean filter ran
    message = gitops.run_git(repo, "log", "-1", "--format=%B").stdout
    assert message.startswith("sync: 3a 0m 0r 0d src\n")  # commentChar 's' did not strip the subject
    assert "second line" in message
    gitops.tag_published(repo, sha)  # tag.gpgSign / gpg.program=false did not block the tag
    assert not hostile_global["ran"].exists()  # no hook and no fsmonitor program ran
    assert gitops.has_changes(repo) is False


def test_info_exclude_is_agentsyncs_own_and_rewritten(tmp_path: Path) -> None:
    repo = tmp_path / "docs"
    gitops.ensure_repo(repo)
    exclude = repo / ".git" / "info" / "exclude"
    original = exclude.read_text(encoding="utf-8")
    assert "Managed by agentsync" in original and ".agentsync-*.tmp" in original
    exclude.write_text("*.md\n", encoding="utf-8")  # an operator edit that would drop every page
    assert gitops.ensure_repo(repo) is False
    assert exclude.read_text(encoding="utf-8") == original
    write(repo, "mirror/src/a.md", "a\n")
    write(repo, "mirror/src/.agentsync-abc.tmp", "partial\n")  # a crashed write's temp: ignored, not lost
    write(repo, "mirror/.DS_Store", "finder\n")
    assert gitops.commit_cycle(repo, "sync: 1a") is not None
    assert gitops.tracked_files(repo) == ["mirror/src/a.md"]


def test_commit_cycle_refuses_while_a_repo_gitignore_hides_a_page(repo: Path) -> None:
    write(repo, ".gitignore", "_sync/STATE.md\n_manifest/cache/\nresearch-*\n")
    write(repo, "mirror/src/research-plan.md", "x\n")
    write(repo, "mirror/src/b.md", "y\n")
    write(repo, "_manifest/cache/k.json", "{}\n")  # the converter cache is meant to be ignored
    with pytest.raises(PublishError, match=r"1 generated file\(s\) are ignored .*research-plan\.md"):
        gitops.commit_cycle(repo, "sync: 2a")
    assert gitops.head_sha(repo) is None


def test_every_git_call_carries_the_safe_overrides(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    real = subprocess.run

    def spy(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(list(argv))
        return real(argv, **kw)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(gitops.subprocess, "run", spy)
    write(repo, "INDEX.md", "# i\n")
    sha = gitops.commit_cycle(repo, "sync: 1a")
    assert sha is not None
    gitops.tag_published(repo, sha)
    gitops.has_changes(repo)
    assert seen
    for argv in seen:
        head = argv[: argv.index("-C")]
        for key in ("core.excludesFile", "core.hooksPath", "commit.gpgSign", "core.attributesFile"):
            assert any(a.startswith(key + "=") for a in head), (key, argv)
    env = gitops._env()
    assert not any(k.startswith("GIT_CONFIG_KEY_") for k in env)
