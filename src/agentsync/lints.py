"""Land-gate lints: a blocking finding stops the cycle's commit (owner: publish).

Every lint returns findings sorted by path and never raises for a content problem; a finding's message never
quotes the matched secret or token (it names the rule and the line).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from agentsync import gitops, slug
from agentsync.convert.registry import Registry
from agentsync.errors import AgentSyncError, UnreadableSourceError
from agentsync.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
    parse_mirror_page,
    render_mirror_page,
    validate_mirror_frontmatter,
)
from agentsync.model import LintFinding, PageStatus

log = logging.getLogger(__name__)

TOKEN_PATTERN = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"
    r"|(?:\$?(?:delta|skip)|access_|refresh_|id_)?token=[^\s&\"'<>]{16,}"
)
"""Mirror/archive/topics pages matching this are reported (non-blocking) as a review hint.  Only a
token-shaped value counts: ``Bearer Securities``, ``Bearer <your token>`` and a short ``reset-token=``
example do not, an opaque bearer token and an ``access_token=`` parameter do (PIPELINE_TOKEN_PATTERN would
miss opaque bearer tokens, and gitleaks is optional)."""

PIPELINE_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])\$?(?:delta|skip)token=[^\s&\"'<>]+"
    r"|https://(?:graph\.microsoft\.(?:com|us)|dod-graph\.microsoft\.us|microsoftgraph\.chinacloudapi\.cn)"
    r"/[^\s\"'<>]*[?&]\$?(?:delta|skip)?token=[^\s&\"'<>]+"
    r"|\bBearer\s+eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)
"""No pipeline-written file may match this (cursor / bearer token custody, design 4.7): real cursor and token
shapes only -- a ``$deltatoken=``/``$skiptoken=`` parameter, a Graph URL carrying a token, a JWT bearer.
Third-party strings (file names, mail subjects: ``Bearer bonds``, ``reset-token=``) reach ``_manifest``,
``CHANGELOG`` and ``QUARANTINE.tsv`` verbatim, so the broad TOKEN_PATTERN there let any sender halt every
sync;
the cycle also passes the actual cursor values (``known_secrets``), which are matched literally."""

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("azure-conn-string", re.compile(r"AccountKey=[A-Za-z0-9+/=]{40,}")),
    ("generic-password", re.compile(r"(?i)\b(?:password|pwd)\s*[=:]\s*\S{6,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b")),
)
"""Built-in content secret scan; gitleaks is used instead when on PATH (``lint_secrets`` prefers it)."""

_TEAMS_JOIN_URL = re.compile(
    r"(?i)(?<![^\s\"'<>(\[])https?://"
    r"(?:(?:gov\.|dod\.)?teams\.microsoft\.(?:com|us)|teams\.live\.com|teams\.cloud\.microsoft)"
    r"(?::\d+)?[/?#][^\s\"'<>]*"
)
"""A Teams meeting link that starts a token (so not one nested in another URL's parameter), host-anchored:
``teams.microsoft.com.example.net`` or ``evilteams.microsoft.com`` is not one."""
_TEAMS_JOIN_PWD = re.compile(r"(?i)[?&]pwd=[^&#\s\"'<>()]*")
"""The ``pwd=`` passcode of a Teams join link: an invite, not a credential (gitleaks ignores it too)."""

INDEX_MAX_BYTES = 25_000
INDEX_MAX_LINES = 200

_MIRROR = "mirror"
_ARCHIVE = "archive"  # [governance] archive: the last full page of each deleted item, agentsync-written
_PAGE_TOPS = (_MIRROR, _ARCHIVE)
_SIDECAR_DIR_SUFFIX = ".files"
_DIR_GUIDES = frozenset({"CLAUDE.md", "INDEX.md"})
_PIPELINE_DIRS = ("_manifest", "_sync", "_index", "CHANGELOG")
_PIPELINE_FILES = ("INDEX.md", "CHANGELOG.md", "DEPENDS.tsv")
_TEXT_SUFFIXES = (".md", ".txt", ".csv")
"""The page and sidecar files the token lint and the secret scan read; a keyframe's JPEG bytes can spell
``PWd=`` (spec S10), and its scan would stub the whole recording for good."""
_GITLEAKS_CANDIDATES = (Path("/opt/homebrew/bin/gitleaks"), Path("/usr/local/bin/gitleaks"))
_GITLEAKS_TIMEOUT_S = 300.0


# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------


def _sorted(findings: Iterable[LintFinding]) -> list[LintFinding]:
    return sorted(findings, key=lambda f: (f.path, f.code, f.message))


def _rel(repo: Path, path: Path) -> str:
    return path.relative_to(repo).as_posix()


def _walk_files(repo: Path, top: str) -> Iterator[str]:
    """Yield repo-relative paths of regular files under ``top`` (never following symlinks), sorted."""
    root = repo / top
    if not root.is_dir() or root.is_symlink():
        return
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        base = Path(dirpath)
        out.extend(_rel(repo, base / f) for f in filenames if not (base / f).is_symlink())
    yield from sorted(out)


def _is_sidecar(path: str) -> bool:
    return any(part.endswith(_SIDECAR_DIR_SUFFIX) for part in path.split("/")[:-1])


def _is_mirror_page(path: str) -> bool:
    return (
        path.startswith(tuple(f"{top}/" for top in _PAGE_TOPS))
        and path.endswith(".md")
        and path != f"{_MIRROR}/CLAUDE.md"
        and not _is_sidecar(path)
    )


def _is_pipeline_file(path: str) -> bool:
    if path in _PIPELINE_FILES:
        return True
    head = path.split("/", 1)[0]
    return head in _PIPELINE_DIRS and not path.startswith("_manifest/cache/")


def _body_matches(body: str, rendered: str) -> bool:
    """H2 is sha256(unit body); the page writer adds one final LF when the body lacked it."""
    if hashlib.sha256(body.encode("utf-8")).hexdigest() == rendered:
        return True
    return body.endswith("\n") and hashlib.sha256(body[:-1].encode("utf-8")).hexdigest() == rendered


def _read_text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError as exc:
        log.warning("cannot read %s: %s", path, exc.strerror)
        return None


# ---------------------------------------------------------------------------------------------------------
# lints
# ---------------------------------------------------------------------------------------------------------


def lint_no_symlinks(repo: Path) -> list[LintFinding]:
    """SYMLINK: any symlink anywhere under the docs repo (excluding .git) — ``find docs -type l`` must be
    empty."""
    findings: list[LintFinding] = []
    if repo.is_symlink():
        findings.append(LintFinding("SYMLINK", ".", "the docs repo root itself is a symlink"))
    for dirpath, dirnames, filenames in os.walk(repo, followlinks=False):
        base = Path(dirpath)
        if base == repo and ".git" in dirnames:
            dirnames.remove(".git")
        for name in [*dirnames, *filenames]:
            p = base / name
            if p.is_symlink():
                findings.append(
                    LintFinding(
                        "SYMLINK",
                        _rel(repo, p),
                        "symlink in docs/: Glob/Grep/find/git do not see through it; replace it with a real "
                        "file or directory",
                    )
                )
    return _sorted(findings)


def _lint_one_page(repo: Path, path: str) -> list[LintFinding]:
    full = repo / path
    if full.is_symlink() or not full.is_file():
        return []
    text = _read_text(full)
    if text is None:
        return [LintFinding("FRONTMATTER", path, "unreadable file")]
    try:
        data, body = parse_frontmatter(text)
    except FrontmatterError as exc:
        return [LintFinding("FRONTMATTER", path, str(exc))]
    problems = validate_mirror_frontmatter(data)
    if problems:
        return [LintFinding("FRONTMATTER", path, "; ".join(problems))]
    findings: list[LintFinding] = []
    parts = path.split("/")
    top = parts[0]
    if len(parts) < 3 or str(data.get("source_id")) != parts[1]:
        findings.append(
            LintFinding(
                "FRONTMATTER",
                path,
                f"source_id {data.get('source_id')!r} does not match the {top}/<id>/ dir",
            )
        )
    status = PageStatus(str(data["status"]))
    if (top == _ARCHIVE) != (status is PageStatus.ARCHIVED):
        where = "archive/ pages carry" if top == _ARCHIVE else "only archive/ pages carry"
        findings.append(
            LintFinding(
                "FRONTMATTER",
                path,
                f"status {status.value}: {where} status archived (archive/ is written only by agentsync)",
            )
        )
    rendered = data.get("rendered_sha256")
    if status is not PageStatus.DELETED and rendered is not None and not _body_matches(body, str(rendered)):
        findings.append(
            LintFinding(
                "FRONTMATTER",
                path,
                f"body does not match rendered_sha256: {top}/ is written only by agentsync (hand edit?); "
                f"restore it with `git checkout -- {path}`",
            )
        )
    if status is PageStatus.DELETED and not body.lstrip("\n").startswith(
        ("# [DELETED UPSTREAM]", "# [MOVED OUT OF SCOPE]", "# [RETIRED]")
    ):
        findings.append(
            LintFinding("FRONTMATTER", path, "status deleted but the body is not a tombstone stub")
        )
    try:
        fm, parsed_body = parse_mirror_page(text)
        if render_mirror_page(fm, parsed_body) != text:
            findings.append(
                LintFinding(
                    "FRONTMATTER",
                    path,
                    "frontmatter is not in the canonical rendering (edited by hand, or a value that does not "
                    "round-trip)",
                    blocking=False,
                )
            )
    except (FrontmatterError, TypeError, ValueError) as exc:
        findings.append(LintFinding("FRONTMATTER", path, f"frontmatter does not round-trip: {exc}"))
    return findings


def lint_mirror_frontmatter(repo: Path, paths: Sequence[str] | None = None) -> list[LintFinding]:
    """FRONTMATTER: every mirror and archive page (or just ``paths``) parses and satisfies the mirror
    contract; an archive page also carries ``status: archived`` and a body matching ``rendered_sha256``."""
    if paths is None:
        candidates = [p for top in _PAGE_TOPS for p in _walk_files(repo, top)]
    else:
        candidates = sorted(set(paths))
    findings: list[LintFinding] = []
    for path in candidates:
        top = path.split("/", 1)[0]
        if top not in _PAGE_TOPS or "/" not in path or path == f"{_MIRROR}/CLAUDE.md":
            continue
        full = repo / path
        if not full.exists() or full.is_symlink() or not full.is_file():
            continue  # deleted this cycle; symlinks are lint_no_symlinks' finding
        if _is_mirror_page(path):
            findings.extend(_lint_one_page(repo, path))
        elif not _is_sidecar(path):
            findings.append(
                LintFinding(
                    "FRONTMATTER",
                    path,
                    f"not a mirror page: only agentsync writes {top}/ (pages are *.md; sidecars live in "
                    f"*{_SIDECAR_DIR_SUFFIX}/ dirs)",
                )
            )
    return _sorted(findings)


def _candidate_paths(repo: Path) -> list[str]:
    """Tracked and would-be-tracked (untracked, not ignored) files that exist in the working tree."""
    out = gitops.run_git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    return sorted({p for p in out.split("\0") if p and os.path.lexists(repo / p)})


def _prefixes(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def lint_paths(repo: Path, paths: Sequence[str] | None = None) -> list[LintFinding]:
    """PATH: tracked/generated paths are slug fixed points, <= 200 chars, with no NFC/case-insensitive
    twins."""
    universe = _candidate_paths(repo)
    targets = universe if paths is None else sorted({p for p in paths if os.path.lexists(repo / p)})
    findings: list[LintFinding] = []
    for path in targets:
        if len(path) > slug.MAX_PATH_CHARS:
            findings.append(
                LintFinding("PATH", path, f"{len(path)} chars > {slug.MAX_PATH_CHARS} (Windows MAX_PATH)")
            )
        name = path.rsplit("/", 1)[-1]
        if path.split("/", 1)[0] in _PAGE_TOPS and path != f"{_MIRROR}/CLAUDE.md":
            if not slug.is_portable_path(path):
                findings.append(LintFinding("PATH", path, "mirror path is not a slug fixed point"))
        elif path.startswith("topics/") and name not in _DIR_GUIDES and not slug.is_portable_path(path):
            findings.append(
                LintFinding(
                    "PATH",
                    path,
                    "curated path is not portable (lowercase kebab-case, no NTFS-illegal characters)",
                    blocking=False,
                )
            )
    groups: dict[str, set[str]] = {}
    for path in {*universe, *targets}:
        for prefix in _prefixes(path):
            groups.setdefault(slug.collision_key(prefix), set()).add(prefix)
    target_set = set(targets)
    for members in groups.values():
        if len(members) < 2:
            continue
        clash = sorted(members)
        for path in targets:
            if any(p == path or path.startswith(p + "/") for p in clash) and path in target_set:
                others = [p for p in clash if not (p == path or path.startswith(p + "/"))]
                findings.append(
                    LintFinding(
                        "PATH",
                        path,
                        f"case/NFC twin of {', '.join(others)} (collides on APFS and NTFS)",
                    )
                )
    return _sorted(set(findings))


def lint_no_cache_in_git(repo: Path) -> list[LintFinding]:
    """CACHE: nothing under ``_manifest/cache/`` is tracked."""
    spec = ":(literal)_manifest/cache"
    tracked = gitops.run_git(repo, "ls-files", "-z", "--", spec).stdout
    untracked = gitops.run_git(repo, "ls-files", "-z", "--others", "--exclude-standard", "--", spec).stdout
    findings = [
        LintFinding("CACHE", p, "converter cache content is tracked by git (it must stay machine-local)")
        for p in tracked.split("\0")
        if p
    ]
    findings += [
        LintFinding("CACHE", p, "converter cache content is not gitignored and would be committed")
        for p in untracked.split("\0")
        if p
    ]
    return _sorted(findings)


def _pipeline_files(repo: Path) -> list[str]:
    out: list[str] = [f for f in _PIPELINE_FILES if (repo / f).is_file() and not (repo / f).is_symlink()]
    for top in _PIPELINE_DIRS:
        out.extend(p for p in _walk_files(repo, top) if _is_pipeline_file(p))
    return sorted(out)


def _first_token_hit(
    path: Path, pattern: re.Pattern[str], known: Sequence[str] = ()
) -> tuple[int, str] | None:
    try:
        with path.open("rb") as fh:
            for n, raw in enumerate(fh, start=1):
                line = raw.decode("utf-8", errors="replace")
                if any(k in line for k in known):
                    return n, "a live cursor value"
                m = pattern.search(line)
                if m:
                    what = m.group(0).strip()
                    # A bearer value may hold "=" (base64 padding), so its label is the scheme alone.
                    return n, "Bearer" if what.startswith("Bearer") else what.split("=", 1)[0] + "="
    except OSError as exc:
        log.warning("cannot read %s: %s", path, exc.strerror)
    return None


def _is_text_file(path: str) -> bool:
    """A page or a text sidecar (``.md``, ``.txt``, ``.csv``): what the token lint and secret scan read."""
    return path.lower().endswith(_TEXT_SUFFIXES)


def lint_no_tokens(
    repo: Path, paths: Sequence[str] | None = None, *, known_secrets: Sequence[str] = ()
) -> list[LintFinding]:
    """TOKEN: no pipeline-written file (``_manifest``, ``_sync``, INDEX, CHANGELOG, DEPENDS) matches
    PIPELINE_TOKEN_PATTERN or holds one of ``known_secrets`` (the live cursor values; blocking). Pages
    matching TOKEN_PATTERN are reported non-blocking with advice the reader can act on, because corporate API
    docs legitimately show sample tokens; a page holding a known secret blocks."""
    known = [k for k in known_secrets if len(k) >= 16]
    if paths is None:
        candidates = [
            *_pipeline_files(repo),
            *(p for top in (*_PAGE_TOPS, "topics") for p in _walk_files(repo, top)),
        ]
    else:
        candidates = list(paths)
    findings: list[LintFinding] = []
    for path in sorted(set(candidates)):
        full = repo / path
        if full.is_symlink() or not full.is_file():
            continue
        pipeline = _is_pipeline_file(path)
        if not (pipeline or path.startswith((_MIRROR + "/", _ARCHIVE + "/", "topics/"))):
            continue
        if not (pipeline or _is_text_file(path)):  # a keyframe or other binary sidecar: never read
            continue
        hit = _first_token_hit(full, PIPELINE_TOKEN_PATTERN if pipeline else TOKEN_PATTERN, known)
        if hit is None:
            continue
        line, what = hit
        leaked = what == "a live cursor value"
        if pipeline or leaked:
            msg = f"file holds {what!r} at line {line}: a cursor or bearer token leaked"
        else:
            msg = (
                f"page holds a token-shaped {what!r} value at line {line} (not blocking); if it is a live "
                "credential, rotate it and remove it from the source file or topic page"
            )
        findings.append(LintFinding("TOKEN", path, msg, blocking=pipeline or leaked))
    return _sorted(findings)


def _gitleaks() -> Path | None:
    found = shutil.which("gitleaks")
    if found:
        return Path(found)
    return next((p for p in _GITLEAKS_CANDIDATES if p.is_file() and os.access(p, os.X_OK)), None)


def _strip_teams_join_pwd(line: str) -> str:
    """``line`` with only the ``pwd=`` parameter of each Teams join link removed (a meeting invite would
    otherwise be quarantined as ``generic-password``); a lookalike host, Zoom's ``pwd=`` and ``password=``
    are left for the scan."""
    return _TEAMS_JOIN_URL.sub(lambda m: _TEAMS_JOIN_PWD.sub("", m.group(0)), line)


def _builtin_secret_scan(repo: Path, paths: Sequence[str]) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for path in paths:
        text = _read_text(repo / path)
        if text is None:
            continue
        seen: set[str] = set()
        for n, line in enumerate(text.splitlines(), start=1):
            for rule, pattern in SECRET_PATTERNS:
                if rule in seen:
                    continue
                if pattern.search(_strip_teams_join_pwd(line) if rule == "generic-password" else line):
                    seen.add(rule)
                    findings.append(
                        LintFinding("SECRET", path, f"{rule} at line {n} (builtin scan)", blocking=False)
                    )
    return findings


def _gitleaks_scan(exe: Path, repo: Path, paths: Sequence[str]) -> list[LintFinding]:
    """Scan ``paths`` in one gitleaks run over a hard-linked shadow tree; raises on any gitleaks problem."""
    git_dir = repo / ".git"
    base = git_dir if git_dir.is_dir() and not git_dir.is_symlink() else Path(tempfile.gettempdir())
    token = secrets.token_hex(6)
    scan_dir = base / f"agentsync-secret-scan-{token}"
    report = base / f"agentsync-secret-report-{token}.json"
    try:
        scan_dir.mkdir(mode=0o700)
        scan_dir = scan_dir.resolve()
        for path in paths:
            dest = scan_dir / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(repo / path, dest)
            except OSError:
                shutil.copyfile(repo / path, dest)
        argv = [
            str(exe),
            "dir",
            str(scan_dir),
            "--report-format",
            "json",
            "--report-path",
            str(report),
            "--no-banner",
            "--no-color",
            "--redact",
            "--exit-code",
            "0",
            "--log-level",
            "error",
            "--gitleaks-ignore-path",
            str(scan_dir),
        ]
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_GITLEAKS_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
            cwd=scan_dir,
        )
        if proc.returncode != 0:
            raise AgentSyncError(f"gitleaks exited {proc.returncode}: {proc.stderr.strip()[:300]}")
        raw = json.loads(report.read_text(encoding="utf-8") or "[]")
        if not isinstance(raw, list):
            raise AgentSyncError("gitleaks report is not a JSON list")
        findings: list[LintFinding] = []
        seen: set[tuple[str, str]] = set()
        for hit in raw:
            file = Path(str(hit.get("File", "")))
            rel = (file if file.is_absolute() else scan_dir / file).resolve().relative_to(scan_dir)
            rel_s = rel.as_posix()
            rule = str(hit.get("RuleID", "gitleaks"))
            if (rel_s, rule) in seen:
                continue
            seen.add((rel_s, rule))
            findings.append(
                LintFinding(
                    "SECRET", rel_s, f"{rule} at line {hit.get('StartLine', '?')} (gitleaks)", blocking=False
                )
            )
        return findings
    finally:
        shutil.rmtree(scan_dir, ignore_errors=True)
        report.unlink(missing_ok=True)


def lint_secrets(repo: Path, paths: Sequence[str]) -> list[LintFinding]:
    """SECRET: content secret scan over the given mirror pages; each hit names the page (caller quarantines it
    to an ``UNREADABLE: contains a credential`` stub instead of blocking the whole cycle: blocking=False).
    Only text files are read (``.md``, ``.txt``, ``.csv``); a binary sidecar such as a keyframe is skipped."""
    existing = sorted(
        {
            p
            for p in paths
            if _is_text_file(p)
            and (repo / p).is_file()
            and not (repo / p).is_symlink()
            and not p.startswith(".git/")
        }
    )
    if not existing:
        return []
    exe = _gitleaks()
    if exe is not None:
        try:
            return _sorted(_gitleaks_scan(exe, repo.resolve(), existing))
        except (OSError, ValueError, subprocess.SubprocessError, AgentSyncError) as exc:
            log.warning("gitleaks unusable (%s); falling back to the builtin secret patterns", exc)
    return _sorted(_builtin_secret_scan(repo, existing))


def lint_index_budget(repo: Path) -> list[LintFinding]:
    """INDEX-BUDGET: INDEX.md <= 25 KB and <= 200 lines."""
    index = repo / "INDEX.md"
    if not index.is_file():
        return [LintFinding("INDEX-BUDGET", "INDEX.md", "INDEX.md is missing", blocking=False)]
    data = index.read_bytes()
    findings: list[LintFinding] = []
    if len(data) > INDEX_MAX_BYTES:
        findings.append(LintFinding("INDEX-BUDGET", "INDEX.md", f"{len(data)} bytes > {INDEX_MAX_BYTES}"))
    lines = data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
    if lines > INDEX_MAX_LINES:
        findings.append(LintFinding("INDEX-BUDGET", "INDEX.md", f"{lines} lines > {INDEX_MAX_LINES}"))
    return findings


def lint_double_conversion(samples: Sequence[tuple[Path, str]], registry: Registry) -> list[LintFinding]:
    """NONDETERMINISTIC: each (staged file, name) converted twice must yield identical rendered_sha256s."""
    findings: list[LintFinding] = []
    for src, name in samples:
        conv = registry.for_name(name)
        if conv is None:
            continue
        try:
            first = conv.convert(src, name=name)
            second = conv.convert(src, name=name)
        except UnreadableSourceError:
            continue
        except Exception as exc:  # a converter failure is the FAILED stub's business, not nondeterminism
            log.warning("double conversion of %s failed: %s", name, exc)
            continue
        a = [(u.unit_id, u.rendered_sha256) for u in first]
        b = [(u.unit_id, u.rendered_sha256) for u in second]
        if a != b:
            differing = sorted({x[0] for x in set(a) ^ set(b)})
            findings.append(
                LintFinding(
                    "NONDETERMINISTIC",
                    name,
                    f"{conv.converter_id}: two conversions differ in unit(s) {', '.join(differing)}",
                )
            )
    return _sorted(findings)


def _dirty_mirror_paths(repo: Path) -> list[str]:
    """Mirror and archive paths git would commit (modified, added, untracked) — catches edits nobody
    reported."""
    out = gitops.run_git(
        repo,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--no-renames",
        "--",
        ":(literal)mirror",
        ":(literal)archive",
    ).stdout
    paths: list[str] = []
    for entry in out.split("\0"):
        if len(entry) > 3:
            paths.append(entry[3:])
    return paths


def run_land_gate(
    repo: Path, changed_paths: Sequence[str], *, known_secrets: Sequence[str] = ()
) -> list[LintFinding]:
    """Run every repo lint (symlinks, frontmatter on changed pages, paths, cache, tokens, index budget);
    ``known_secrets`` (extension) are live cursor values no committed file may contain."""
    changed = sorted({*changed_paths, *_dirty_mirror_paths(repo)})
    findings: list[LintFinding] = []
    findings += lint_no_symlinks(repo)
    findings += lint_mirror_frontmatter(repo, [p for p in changed if p.split("/", 1)[0] in _PAGE_TOPS])
    findings += lint_paths(repo, changed)
    findings += lint_no_cache_in_git(repo)
    findings += lint_no_tokens(repo, sorted({*_pipeline_files(repo), *changed}), known_secrets=known_secrets)
    findings += lint_index_budget(repo)
    blocking = [f for f in findings if f.blocking]
    if blocking:
        log.warning("land gate: %d blocking finding(s)", len(blocking))
    return findings
