"""git via subprocess for the docs repo: init, status, one commit per cycle, recovery (owner: publish).

git is resolved once to an absolute path (launchd has a minimal PATH) and run with ``LC_ALL=C``,
``GIT_TERMINAL_PROMPT=0``, ``GIT_OPTIONAL_LOCKS=0``.  Nothing here fetches, and nothing pushes except
:func:`push_if_allowed`, which does nothing unless the caller passes ``allow=True`` AND a remote exists
(the week-0 default is no remote and no push: corporate content never leaves the machine).

The operator's own git configuration can never drop, rewrite or block a mirror page (audit
critic-git-global-excludes-drop-mirror): every invocation carries ``-c`` overrides (``_SAFE_CONFIG``) for
global excludes, hooks, global attributes (clean/smudge filters), fsmonitor, signing, CRLF policy, comment
character and encodings; inherited ``GIT_CONFIG_*`` injection variables are scrubbed; ``git init`` uses an
empty template (no hooks copied from ``init.templateDir``); the repo-local ``info/exclude`` is rewritten to
agentsync's own content on every ``ensure_repo``; and ``commit_cycle`` refuses to commit while any generated
page is ignored.
"""

from __future__ import annotations

import fnmatch
import functools
import logging
import os
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from agentsync.errors import GitError, PublishError
from agentsync.model import ChangeOp, MirrorChange
from agentsync.paths import expand, is_cloud_path

log = logging.getLogger(__name__)

GENERATED_PATHSPECS: tuple[str, ...] = (
    "mirror",
    "archive",
    "_manifest",
    "_index",
    "CHANGELOG",
    "CHANGELOG.md",
    "INDEX.md",
    "DEPENDS.tsv",
    "_sync/QUARANTINE.tsv",
    "_sync/STATE.snapshot.md",
)
"""Paths the pipeline owns and may reset on recovery."""

COMMIT_PATHSPECS: tuple[str, ...] = (
    *GENERATED_PATHSPECS,
    "topics",
    "_eval",
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/settings.json",
    "SYNONYMS.tsv",
    ".gitignore",
    ".gitattributes",
)
"""Paths a cycle commit stages (``git add -A -- <these>``); ``_sync/STATE.md`` is gitignored."""

PUBLISHED_TAG = "published"
CURATED_TAG = "curated"
SNAPSHOT_TAG_PREFIX = "snapshot/"
"""``[governance] archive``: ``agentsync checkpoint`` adds a permanent tag ``snapshot/<UTC %Y-%m-%dT%H%M%SZ>``
at the same commit; agentsync never moves or deletes one."""

_GIT_TIMEOUT_S = 600.0
_PUSH_TIMEOUT_S = 300.0
_LOCAL_IDENTITY = (("user.name", "agentsync"), ("user.email", "agentsync@localhost"))
# Command-line config (highest precedence) on EVERY git call: the operator's global/system settings must not
# drop pages (excludesFile), block commits (hooksPath, gpgSign), rewrite blobs (attributesFile -> filters),
# run programs (fsmonitor), refuse adds (safecrlf) or eat message lines (commentChar with --cleanup=strip).
_SAFE_CONFIG: tuple[tuple[str, str], ...] = (
    ("core.excludesFile", "/dev/null"),
    ("core.hooksPath", "/dev/null"),
    ("core.attributesFile", "/dev/null"),
    ("core.fsmonitor", "false"),
    ("core.autocrlf", "false"),
    ("core.safecrlf", "false"),
    ("core.commentChar", "#"),
    ("commit.gpgSign", "false"),
    ("tag.gpgSign", "false"),
    ("log.showSignature", "false"),
    ("color.ui", "false"),
    ("i18n.commitEncoding", "UTF-8"),
    ("i18n.logOutputEncoding", "UTF-8"),
)
_SAFE_ARGS: tuple[str, ...] = tuple(arg for key, value in _SAFE_CONFIG for arg in ("-c", f"{key}={value}"))
_INFO_EXCLUDE_PATTERNS: tuple[str, ...] = (".agentsync-*.tmp", ".DS_Store")
_INFO_EXCLUDE = (
    "# Managed by agentsync: rewritten on every run.  Ignore rules for this repo live in the tracked\n"
    "# .gitignore; global excludes (core.excludesFile) are disabled for every agentsync git call.\n"
    + "".join(f"{p}\n" for p in _INFO_EXCLUDE_PATTERNS)
)
_MUST_TRACK: tuple[str, ...] = ("mirror", "_manifest")
"""Generated trees in which an ignored file is a lost page (``_manifest/cache/`` excepted)."""
_CORE_SETTINGS = (
    ("core.precomposeunicode", "true"),
    ("core.quotepath", "false"),
    ("core.sharedRepository", "0600"),  # objects, packs and refs owner-only, whatever the umask
)
# Inherited variables that would redirect git away from ``-C repo`` (e.g. when run from a git hook).
_SCRUBBED_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_COMMON_DIR",
    # config injection: a -c we pass must be the last word
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_TEMPLATE_DIR",
)
_XCODE_GIT_CANDIDATES = (
    Path("/Library/Developer/CommandLineTools/usr/bin/git"),
    Path("/Applications/Xcode.app/Contents/Developer/usr/bin/git"),
)


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _usr_bin_git_usable() -> bool:
    """``/usr/bin/git`` on macOS is an xcrun shim: usable only when a developer dir provides git."""
    if not _is_executable(Path("/usr/bin/git")):
        return False
    if sys.platform != "darwin":
        return True
    if os.environ.get("DEVELOPER_DIR"):
        return True
    return any(_is_executable(p) for p in _XCODE_GIT_CANDIDATES)


@functools.cache
def git_executable() -> Path:
    """Return the absolute path of git (``/usr/bin/git`` preferred); raises GitError if absent."""
    if _usr_bin_git_usable():
        return Path("/usr/bin/git")
    candidates: list[Path] = []
    found = shutil.which("git")
    if found:
        candidates.append(Path(found))
    candidates += [Path("/opt/homebrew/bin/git"), Path("/usr/local/bin/git"), *_XCODE_GIT_CANDIDATES]
    for c in candidates:
        if c.is_absolute() and _is_executable(c):
            return c
    raise GitError(["--version"], 127, "git not found (install the Xcode Command Line Tools)")


def _env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _SCRUBBED_ENV and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    env.update(
        {
            "LC_ALL": "C",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_ADVICE": "0",
            "GIT_EDITOR": ":",
            "GIT_PAGER": "cat",
        }
    )
    return env


def _run(
    repo: Path,
    args: Sequence[str],
    *,
    check: bool = True,
    input_text: str | None = None,
    timeout: float = _GIT_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    argv = [str(git_executable()), *_SAFE_ARGS, "-C", str(repo), *args]
    try:
        proc = subprocess.run(
            argv,
            input=input_text,
            stdin=None if input_text is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            env=_env(),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise GitError(list(args), 124, f"timed out after {timeout:.0f}s") from None
    except OSError as exc:
        raise GitError(list(args), 127, str(exc)) from None
    if check and proc.returncode != 0:
        raise GitError(list(args), proc.returncode, proc.stderr)
    return proc


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``git <safe -c overrides> -C repo *args`` with the fixed env; raises GitError on non-zero exit when
    ``check``."""
    return _run(repo, args, check=check)


def _config_get(repo: Path, key: str, *, local: bool) -> str | None:
    args = ["config", "--local", "--get", key] if local else ["config", "--get", key]
    proc = run_git(repo, *args, check=False)
    if proc.returncode == 0:
        return proc.stdout.rstrip("\n")
    if proc.returncode == 1:  # key unset
        return None
    raise GitError(args, proc.returncode, proc.stderr)


def _info_exclude_path(repo: Path) -> Path:
    out = run_git(repo, "rev-parse", "--git-path", "info/exclude").stdout.strip()
    path = Path(out)
    return path if path.is_absolute() else repo / path


def _write_info_exclude(repo: Path) -> bool:
    """Make ``<git-dir>/info/exclude`` hold exactly agentsync's content; True when it was rewritten."""
    path = _info_exclude_path(repo)
    try:
        if path.is_file() and not path.is_symlink() and path.read_text(encoding="utf-8") == _INFO_EXCLUDE:
            return False
    except (OSError, UnicodeDecodeError):
        pass
    if path.is_symlink():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.agentsync-tmp")
    tmp.write_text(_INFO_EXCLUDE, encoding="utf-8")
    tmp.replace(path)
    log.info("wrote agentsync's info/exclude in %s", repo)
    return True


def ensure_repo(repo: Path) -> bool:
    """``git init -b main`` (empty template: no hooks) if needed; set core.precomposeunicode=true,
    core.quotepath=false and a local user.name/user.email (``agentsync``/``agentsync@localhost``) only if
    unset; rewrite ``info/exclude`` to agentsync's content.  Returns True if created.
    Refuses (PublishError) a repo under ~/Library/CloudStorage."""
    repo = expand(repo)
    resolved = repo.resolve() if repo.exists() else repo
    if is_cloud_path(repo) or is_cloud_path(resolved):
        raise PublishError(
            f"{repo}: the docs repo must live outside ~/Library/CloudStorage (design 4.7: a git dir inside "
            "a File Provider tree inherits every hydration/eviction failure)"
        )
    if repo.exists() and not repo.is_dir():
        raise PublishError(f"{repo}: exists and is not a directory")
    if not repo.exists():
        # The docs repo is a plaintext copy of tenant data: owner-only (0700) whatever the umask.
        for parent in reversed(repo.parents):
            if not parent.exists():
                parent.mkdir(mode=0o700)
        repo.mkdir(mode=0o700)
    created = not (repo / ".git").exists()
    if created:
        run_git(repo, "init", "-q", "--template=", "-b", "main")
        log.info("initialised docs repo %s", repo)
    for key, value in _CORE_SETTINGS:
        if _config_get(repo, key, local=True) != value:
            run_git(repo, "config", "--local", key, value)
    for key, value in _LOCAL_IDENTITY:
        if _config_get(repo, key, local=True) is None:
            run_git(repo, "config", "--local", key, value)
    _write_info_exclude(repo)
    return created


def _rev_parse(repo: Path, rev: str) -> str | None:
    proc = run_git(repo, "rev-parse", "--verify", "-q", rev, check=False)
    if proc.returncode == 0:
        return proc.stdout.strip() or None
    if proc.returncode == 1:
        return None
    raise GitError(["rev-parse", "--verify", "-q", rev], proc.returncode, proc.stderr)


def head_sha(repo: Path) -> str | None:
    """Return HEAD's commit sha, or None for an unborn branch."""
    return _rev_parse(repo, "HEAD^{commit}")


def head_tree_sha(repo: Path) -> str | None:
    """Return ``git rev-parse HEAD^{tree}``, or None for an unborn branch."""
    return _rev_parse(repo, "HEAD^{tree}")


def has_changes(repo: Path, pathspecs: Sequence[str] = COMMIT_PATHSPECS) -> bool:
    """True when the working tree or index differs from HEAD under ``pathspecs`` (untracked included)."""
    proc = run_git(
        repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *_literal(pathspecs)
    )
    return bool(proc.stdout.strip("\0"))


def commit_subject(changes: Sequence[MirrorChange], source_ids: Sequence[str]) -> str:
    """Return ``"sync: <A>a <M>m <R>r <D>d <comma-joined sorted source ids>"``."""
    counts = Counter(c.op for c in changes)
    ids = ",".join(sorted({s for s in source_ids if s}))
    subject = (
        f"sync: {counts[ChangeOp.ADDED]}a {counts[ChangeOp.MODIFIED]}m "
        f"{counts[ChangeOp.RENAMED]}r {counts[ChangeOp.DELETED]}d"
    )
    return f"{subject} {ids}" if ids else subject


def commit_body(
    changes: Sequence[MirrorChange],
    *,
    run_id: int,
    mode: str,
    notes: Sequence[str] = (),
) -> str:
    """Return the structured commit body: per-source A/M/R/D counts, notes, then ``Agentsync-*`` trailers."""
    per_source: dict[str, Counter[ChangeOp]] = {}
    for c in changes:
        per_source.setdefault(c.source_id, Counter())[c.op] += 1
    lines = [
        f"{sid}: {n[ChangeOp.ADDED]}a {n[ChangeOp.MODIFIED]}m {n[ChangeOp.RENAMED]}r {n[ChangeOp.DELETED]}d"
        for sid, n in sorted(per_source.items())
    ]
    lines += [" ".join(note.split()) for note in notes if note.strip()]
    trailers = [f"Agentsync-Run: {run_id}", f"Agentsync-Mode: {mode}"]
    return "\n".join([*lines, "", *trailers]) if lines else "\n".join(trailers)


def _literal(pathspecs: Sequence[str]) -> list[str]:
    """Pathspecs as literal paths (no glob magic: a mirror name may legally contain ``*`` or ``[``)."""
    return [f":(literal){p}" for p in pathspecs]


def _matching_specs(present: Sequence[str], pathspecs: Sequence[str]) -> list[str]:
    """Pathspecs that name one of ``present`` exactly or as a directory prefix."""
    out: list[str] = []
    for spec in pathspecs:
        s = spec.rstrip("/")
        if any(p == s or p.startswith(s + "/") for p in present):
            out.append(spec)
    return out


def _stageable_specs(repo: Path, pathspecs: Sequence[str]) -> list[str]:
    """Pathspecs that exist on disk or are tracked in the index (``git add`` rejects the rest)."""
    tracked = tracked_files(repo, pathspecs)
    known = set(_matching_specs(tracked, pathspecs))
    return [p for p in pathspecs if p in known or os.path.lexists(repo / p)]


def _ignored_generated(repo: Path) -> list[str]:
    """Generated files (under ``mirror/`` and ``_manifest/``, cache excepted) that git would NOT stage because
    an ignore rule matches them, excluding agentsync's own temp/Finder patterns; sorted."""
    present = [p for p in _MUST_TRACK if os.path.lexists(repo / p)]
    if not present:
        return []
    out = run_git(
        repo, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", *_literal(present)
    ).stdout
    lost: list[str] = []
    for path in (p for p in out.split("\0") if p):
        if path.startswith("_manifest/cache/"):
            continue
        name = path.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatchcase(name, pat) for pat in _INFO_EXCLUDE_PATTERNS):
            continue
        lost.append(path)
    return sorted(lost)


def commit_cycle(
    repo: Path, subject: str, body: str = "", pathspecs: Sequence[str] = COMMIT_PATHSPECS
) -> str | None:
    """Stage ``pathspecs`` and commit once; return the new sha, or None (and no commit) if nothing staged."""
    specs = _stageable_specs(repo, pathspecs)
    if not specs:
        return None
    lost = _ignored_generated(repo)
    if lost:
        raise PublishError(
            f"{len(lost)} generated file(s) are ignored by a .gitignore rule and would silently not be "
            f"committed (first: {lost[0]}); remove the rule from docs/.gitignore"
        )
    literal = _literal(specs)
    run_git(repo, "add", "-A", "--", *literal)
    staged = run_git(repo, "diff", "--cached", "--quiet", "--", *literal, check=False)
    if staged.returncode == 0:
        return None
    if staged.returncode != 1:
        raise GitError(["diff", "--cached", "--quiet"], staged.returncode, staged.stderr)
    one_line = " ".join(subject.split())
    if not one_line:
        raise PublishError("empty commit subject")
    message = one_line + ("\n\n" + body.strip("\n") if body.strip() else "") + "\n"
    _run(
        repo,
        [
            "commit",
            "-q",
            "--no-verify",
            "--cleanup=strip",
            "-F",
            "-",
            "--only",
            "--",
            *literal,
        ],
        input_text=message,
    )
    sha = head_sha(repo)
    if sha is None:
        raise PublishError("commit reported success but HEAD is unborn")
    log.info("committed %s %s", sha[:12], one_line)
    return sha


def restore_generated(repo: Path) -> None:
    """Recovery: ``git checkout HEAD -- <generated>`` + ``git clean -fd -- <generated>`` (never topics/)."""
    literal = _literal(GENERATED_PATHSPECS)
    if head_sha(repo) is None:
        run_git(repo, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", *literal)
    else:
        run_git(repo, "reset", "-q", "HEAD", "--", *literal)
        in_head = run_git(repo, "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *literal).stdout
        present = [p for p in in_head.split("\0") if p]
        matched = _matching_specs(present, GENERATED_PATHSPECS)
        if matched:
            run_git(repo, "checkout", "-q", "HEAD", "--", *_literal(matched))
    run_git(repo, "clean", "-f", "-d", "-q", "--", *literal)
    log.warning("restored generated paths of %s to HEAD", repo)


def tag_published(repo: Path, sha: str) -> None:
    """Force-move the lightweight ``published`` tag to ``sha`` (the tree readers should consume)."""
    run_git(repo, "tag", "-f", PUBLISHED_TAG, f"{sha}^{{commit}}")


def tag_curated(repo: Path, sha: str) -> None:
    """Force-move the annotated ``curated`` tag to ``sha``: the build-session checkpoint (its tagger date is
    when the session ended, not when the commit was made)."""
    run_git(
        repo, "tag", "-f", "-a", CURATED_TAG, "-m", "agentsync build-session checkpoint", f"{sha}^{{commit}}"
    )


def tag_snapshot(repo: Path, sha: str, when: datetime) -> str:
    """Create the permanent annotated tag ``snapshot/<UTC %Y-%m-%dT%H%M%SZ>`` at ``sha`` and return its name.
    Never moved: an existing tag of that name (a second checkpoint in the same second) raises GitError."""
    name = SNAPSHOT_TAG_PREFIX + when.astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    run_git(repo, "tag", "-a", name, "-m", "agentsync point-in-time snapshot", f"{sha}^{{commit}}")
    return name


def curated_checkpoint(repo: Path) -> tuple[str, str] | None:
    """``(commit sha, checkpoint date ISO 8601)`` of the ``curated`` tag; None before the first one."""
    sha = _rev_parse(repo, f"refs/tags/{CURATED_TAG}^{{commit}}")
    if sha is None:
        return None
    date = run_git(
        repo,
        "for-each-ref",
        "--format=%(taggerdate:iso-strict)%(committerdate:iso-strict)",
        f"refs/tags/{CURATED_TAG}",
    ).stdout.strip()
    return sha, date[:25]


def changes_since(repo: Path, rev: str, pathspecs: Sequence[str] = ("mirror",)) -> list[tuple[str, str]]:
    """``(A|M|D, path)`` for every file under ``pathspecs`` that differs between ``rev`` and HEAD, sorted by
    path; a rename reads as a delete plus an add."""
    out = run_git(
        repo,
        "diff",
        "--name-status",
        "-z",
        "--no-renames",
        f"{rev}^{{commit}}",
        "HEAD",
        "--",
        *_literal(pathspecs),
    ).stdout
    fields = [f for f in out.split("\0") if f]
    pairs = [(fields[i][0], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]
    return sorted(pairs, key=lambda p: p[1])


def paths_changed_since(repo: Path, rev: str, pathspecs: Sequence[str]) -> set[str]:
    """Paths under ``pathspecs`` that differ between ``rev`` and the WORKING TREE (staged or not), plus the
    untracked, non-ignored files there: what a session wrote since ``rev``, committed or not.

    Read-only on the index: a commit-to-worktree ``git diff <rev>`` refreshes and rewrites ``.git/index``
    (it takes ``index.lock``) even under GIT_OPTIONAL_LOCKS=0, which would race a concurrent sync's
    add/commit.  So the set is a tree-to-tree diff (``rev`` vs HEAD, what is committed) united with
    ``git status``, which honours GIT_OPTIONAL_LOCKS (staged, unstaged and untracked).  A file committed since
    ``rev`` and then reverted in the working tree still counts."""
    literal = _literal(pathspecs)
    diff = run_git(
        repo, "diff", "--name-only", "-z", "--no-renames", f"{rev}^{{commit}}", "HEAD", "--", *literal
    )
    status = run_git(
        repo, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all", "--", *literal
    )
    paths = {p for p in diff.stdout.split("\0") if p}
    return paths | {entry[3:] for entry in status.stdout.split("\0") if len(entry) > 3}  # "XY path"


def file_at(repo: Path, rev: str, path: str) -> bytes | None:
    """The bytes of ``path`` (repo-relative) in commit ``rev``; None when it is absent there or git cannot
    read it."""
    proc = run_git(repo, "cat-file", "blob", f"{rev}^{{commit}}:{path}", check=False)
    return proc.stdout.encode("utf-8", "surrogateescape") if proc.returncode == 0 else None


def is_ancestor(repo: Path, rev: str, of: str) -> bool:
    """True when commit ``rev`` exists and is reachable from ``of`` (``git merge-base --is-ancestor``)."""
    return run_git(repo, "merge-base", "--is-ancestor", rev, of, check=False).returncode == 0


def tracked_files(repo: Path, pathspecs: Sequence[str] = ()) -> list[str]:
    """Return ``git ls-files -z`` paths (repo-relative, sorted)."""
    args = ["ls-files", "-z"]
    if pathspecs:
        args += ["--", *_literal(pathspecs)]
    out = run_git(repo, *args).stdout
    return sorted(p for p in out.split("\0") if p)


def push_if_allowed(repo: Path, *, allow: bool, remote: str = "origin") -> str:
    """Push the current branch (fast-forward only) and the ``published`` tag, only when ``allow`` AND the
    remote exists; return what happened (``disabled`` | ``no-remote`` | ``refused: …`` | ``pushed …``)."""
    if not allow:
        return "disabled"
    remotes = run_git(repo, "remote").stdout.split()
    if remote not in remotes:
        return "no-remote"
    url = run_git(repo, "remote", "get-url", remote).stdout.strip()
    if url and "://" not in url and ":" not in url.split("/")[0] and is_cloud_path(Path(url)):
        return f"refused: remote {remote} is inside ~/Library/CloudStorage"
    branch = run_git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
    if not branch or head_sha(repo) is None:
        return "refused: nothing committed"
    refspecs = [f"refs/heads/{branch}:refs/heads/{branch}"]
    if _rev_parse(repo, f"refs/tags/{PUBLISHED_TAG}") is not None:
        refspecs.append(f"+refs/tags/{PUBLISHED_TAG}:refs/tags/{PUBLISHED_TAG}")
    _run(repo, ["push", "--porcelain", "--quiet", remote, *refspecs], timeout=_PUSH_TIMEOUT_S)
    log.info("pushed %s to %s", branch, remote)
    return f"pushed {branch} to {remote}"
