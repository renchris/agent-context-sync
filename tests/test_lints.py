"""lints: the land gate, over real files in a tmp docs repo."""

from __future__ import annotations

import hashlib
import subprocess
import unicodedata
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentsync import gitops, lints, slug
from agentsync.frontmatter import MirrorFrontmatter, render_mirror_page
from agentsync.model import LintFinding, PageStatus, RenderedUnit, UnitKind

# ---------------------------------------------------------------------------------------------------------
# A reference slugifier written from the CONTRACTS.md slug section.  slug.py (a separate task) is still a
# stub while this module is built; ``use_reference_slug`` swaps these in ONLY if the real functions raise
# NotImplementedError, so the suite runs against the real slug as soon as it lands.
# ---------------------------------------------------------------------------------------------------------

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
_STRIP = set('[](){}#<>:"/\\|?*')


def ref_slugify(text: str) -> str:
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFC", text))
    s = "".join(c for c in s if not unicodedata.combining(c)).translate(_DASHES).lower()
    s = "".join(c for c in s if c not in _STRIP and ord(c) >= 32 and ord(c) != 127)
    s = "-".join(s.split())
    s = s.rstrip(". ")
    stem, dot, ext = s.partition(".")
    if stem in slug.WINDOWS_RESERVED_STEMS:
        s = f"{stem}-doc{dot}{ext}"
    return unicodedata.normalize("NFC", s) or "untitled"


def ref_mirror_name(source_name: str) -> str:
    low = source_name.lower()
    for suffix in slug.STRIP_SUFFIXES:
        if low.endswith(suffix) and len(low) > len(suffix):
            source_name = source_name[: -len(suffix)]
            break
    return ref_slugify(source_name) + ".md"


def ref_mirror_dir_name(source_name: str) -> str:
    return ref_slugify(source_name) + ".d"


def ref_mirror_rel_path(source_id: str, rel_path: str, *, file_stem: str = "") -> str:
    parts = [p for p in rel_path.split("/") if p]
    dirs = [ref_slugify(p) for p in parts[:-1]]
    if file_stem:
        leaf = [ref_mirror_dir_name(parts[-1]), ref_slugify(file_stem) + ".md"]
    else:
        leaf = [ref_mirror_name(parts[-1])]
    path = "/".join(["mirror", source_id, *dirs, *leaf])
    if len(path) > slug.MAX_PATH_CHARS:
        h = hashlib.sha256(path.encode()).hexdigest()[:8]
        path = path[: slug.MAX_PATH_CHARS - 12] + f"-{h}.md"
    return path


def ref_disambiguate(path: str, stable_id: str) -> str:
    h = hashlib.sha256(stable_id.encode("utf-8")).hexdigest()[:8]
    return f"{path[:-3]}-{h}.md" if path.endswith(".md") else f"{path}-{h}"


def ref_collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", path).casefold())


def ref_is_portable_path(path: str) -> bool:
    return len(path) <= slug.MAX_PATH_CHARS and all(ref_slugify(s) == s for s in path.split("/"))


REFERENCE_SLUG: dict[str, Callable[..., object]] = {
    "slugify": ref_slugify,
    "mirror_name": ref_mirror_name,
    "mirror_dir_name": ref_mirror_dir_name,
    "mirror_rel_path": ref_mirror_rel_path,
    "disambiguate": ref_disambiguate,
    "collision_key": ref_collision_key,
    "is_portable_path": ref_is_portable_path,
}


_PROBES: dict[str, tuple[str, ...]] = {
    "slugify": ("a",),
    "mirror_name": ("a",),
    "mirror_dir_name": ("a",),
    "mirror_rel_path": ("src", "a.md"),
    "disambiguate": ("mirror/x/a.md", "id"),
    "collision_key": ("a",),
    "is_portable_path": ("a",),
}


def install_reference_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch slug.* with the reference only where the real function is still a stub."""
    for name, fn in REFERENCE_SLUG.items():
        try:
            getattr(slug, name)(*_PROBES[name])
        except NotImplementedError:
            monkeypatch.setattr(slug, name, fn)


@pytest.fixture(autouse=True)
def use_reference_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    install_reference_slug(monkeypatch)


# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def page(
    body: str = "# Title\n\nhello\n", *, sid: str = "src", status: PageStatus = PageStatus.CURRENT
) -> str:
    fm = MirrorFrontmatter(
        source_kind="local",
        source_id=sid,
        stable_id="vol:1",
        source_path="a.docx",
        status=status,
        content_sha256="a" * 64,
        canonical_sha256="b" * 64,
        rendered_sha256=sha(body),
        converter="pandoc-gfm@1.0.0+pandoc-3.9",
        options_hash="sha256:" + "c" * 64,
        summary="Title",
        tokens_estimate=5,
        source_title="Title",
    )
    return render_mirror_page(fm, body)


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def codes(findings: list[LintFinding]) -> list[tuple[str, str, bool]]:
    return [(f.code, f.path, f.blocking) for f in findings]


@pytest.fixture
def repo(tmp_docs_repo: Path) -> Path:
    gitops.ensure_repo(tmp_docs_repo)
    write(tmp_docs_repo, ".gitignore", "_sync/STATE.md\n_manifest/cache/\n.sync.lock\n")
    return tmp_docs_repo


# ---- symlinks ----------------------------------------------------------------------------------------------


def test_no_symlinks_clean(repo: Path) -> None:
    write(repo, "mirror/src/a.docx.md", page())
    assert lints.lint_no_symlinks(repo) == []


def test_symlinks_anywhere_are_blocking(repo: Path, tmp_path: Path) -> None:
    write(repo, "mirror/src/a.docx.md", page())
    (repo / "mirror/src/link.md").symlink_to(repo / "mirror/src/a.docx.md")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "topics").mkdir()
    (repo / "topics/shared").symlink_to(outside, target_is_directory=True)
    (repo / ".git" / "ignored-link").symlink_to(outside)
    found = lints.lint_no_symlinks(repo)
    assert codes(found) == [("SYMLINK", "mirror/src/link.md", True), ("SYMLINK", "topics/shared", True)]


# ---- mirror frontmatter ------------------------------------------------------------------------------------


def test_valid_page_passes(repo: Path) -> None:
    write(repo, "mirror/src/a.docx.md", page())
    write(repo, "mirror/CLAUDE.md", "# guide\n")
    write(repo, "mirror/src/big.xlsx.d/01-q3.files/q3.csv", "a,b\n")
    assert lints.lint_mirror_frontmatter(repo) == []


def test_page_without_frontmatter_or_with_unknown_keys(repo: Path) -> None:
    write(repo, "mirror/src/plain.md", "# no frontmatter\n")
    write(
        repo,
        "mirror/src/extra.md",
        page().replace("status: current\n", "status: current\nconverted_at: now\n"),
    )
    found = lints.lint_mirror_frontmatter(repo)
    assert [f.path for f in found] == ["mirror/src/extra.md", "mirror/src/plain.md"]
    assert all(f.blocking for f in found)
    assert "converted_at" in found[0].message


def test_hand_edited_body_is_blocking(repo: Path) -> None:
    write(repo, "mirror/src/a.docx.md", page().replace("hello", "hello, edited by an agent"))
    [finding] = lints.lint_mirror_frontmatter(repo)
    assert finding.blocking and "rendered_sha256" in finding.message
    assert "git checkout -- mirror/src/a.docx.md" in finding.message


def test_body_without_trailing_newline_hash_is_accepted(repo: Path) -> None:
    body = "no newline at end"
    text = page(body)  # renderer appends the LF, H2 was over the raw body
    assert text.endswith("no newline at end\n")
    write(repo, "mirror/src/a.md", text)
    assert lints.lint_mirror_frontmatter(repo) == []


def test_source_id_must_match_mirror_dir(repo: Path) -> None:
    write(repo, "mirror/other/a.md", page(sid="src"))
    [finding] = lints.lint_mirror_frontmatter(repo)
    assert "does not match" in finding.message


def test_non_canonical_frontmatter_is_reported_non_blocking(repo: Path) -> None:
    write(repo, "mirror/src/a.md", page().replace("status: current", "status:   current"))
    [finding] = lints.lint_mirror_frontmatter(repo)
    assert not finding.blocking and "canonical" in finding.message


def test_deleted_status_needs_a_tombstone_body(repo: Path) -> None:
    fm = MirrorFrontmatter(
        source_kind="local",
        source_id="src",
        stable_id="vol:1",
        source_path="a.docx",
        status=PageStatus.DELETED,
        deleted_at="2026-09-29",
        last_rendered_sha256="d" * 64,
    )
    write(repo, "mirror/src/a.md", render_mirror_page(fm, "still the old body\n"))
    write(repo, "mirror/src/b.md", render_mirror_page(fm, "# [DELETED UPSTREAM] b\n"))
    found = lints.lint_mirror_frontmatter(repo)
    assert [f.path for f in found] == ["mirror/src/a.md"]


def test_foreign_files_in_mirror_are_blocking(repo: Path) -> None:
    write(repo, "mirror/src/notes.txt", "hand-made\n")
    write(repo, "mirror/src/.agentsync-abc.tmp", "crash leftover\n")
    found = lints.lint_mirror_frontmatter(repo)
    assert {f.path for f in found} == {"mirror/src/notes.txt", "mirror/src/.agentsync-abc.tmp"}
    assert all(f.blocking for f in found)


def test_frontmatter_lint_on_given_paths_only(repo: Path) -> None:
    write(repo, "mirror/src/bad.md", "no fm\n")
    write(repo, "mirror/src/good.md", page())
    assert lints.lint_mirror_frontmatter(repo, ["mirror/src/good.md", "mirror/src/gone.md", "INDEX.md"]) == []
    assert len(lints.lint_mirror_frontmatter(repo, ["mirror/src/bad.md"])) == 1


# ---- paths -------------------------------------------------------------------------------------------------


def test_paths_clean_repo(repo: Path) -> None:
    write(repo, "mirror/src/fy26-budget.xlsx.d/00-index.md", page())
    write(repo, "INDEX.md", "# i\n")
    write(repo, "topics/clients/acme/acme-commercial.md", "x\n")
    write(repo, "topics/CLAUDE.md", "x\n")
    assert lints.lint_paths(repo) == []


def test_non_slug_mirror_path_is_blocking_topics_warn(repo: Path) -> None:
    write(repo, "mirror/src/Bad Name.md", page())
    write(repo, "topics/Acme Notes.md", "x\n")
    found = lints.lint_paths(repo)
    assert codes(found) == [("PATH", "mirror/src/Bad Name.md", True), ("PATH", "topics/Acme Notes.md", False)]


def test_long_paths_are_blocking(repo: Path) -> None:
    rel = "mirror/src/" + "/".join(["d" * 60] * 4) + "/x.md"
    write(repo, rel, page())
    found = lints.lint_paths(repo, [rel])
    assert any("chars >" in f.message for f in found)


def _case_insensitive(path: Path) -> bool:
    probe = path / "CaseProbe"
    probe.write_text("x")
    try:
        return (path / "caseprobe").exists()
    finally:
        probe.unlink()


def test_case_twins_from_the_index_are_blocking(repo: Path) -> None:
    if not _case_insensitive(repo):
        pytest.skip("filesystem is case-sensitive")
    write(repo, "mirror/src/a.md", page())
    subprocess.run(["git", "-C", str(repo), "add", "mirror/src/a.md"], check=True)
    blob = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ":mirror/src/a.md"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", f"100644,{blob},mirror/src/A.md"],
        check=True,
    )
    found = [f for f in lints.lint_paths(repo) if "twin" in f.message]
    assert {f.path for f in found} == {"mirror/src/a.md", "mirror/src/A.md"}
    assert all(f.blocking for f in found)


def test_paths_given_that_do_not_exist_are_skipped(repo: Path) -> None:
    assert lints.lint_paths(repo, ["mirror/src/Deleted Page.md"]) == []


# ---- cache -------------------------------------------------------------------------------------------------


def test_cache_in_git_is_blocking(repo: Path) -> None:
    write(repo, "_manifest/cache/ab/key/result.json", "{}\n")
    assert lints.lint_no_cache_in_git(repo) == []  # ignored and untracked
    subprocess.run(["git", "-C", str(repo), "add", "-f", "_manifest/cache/ab/key/result.json"], check=True)
    [finding] = lints.lint_no_cache_in_git(repo)
    assert finding.path == "_manifest/cache/ab/key/result.json" and finding.blocking


def test_cache_not_ignored_is_blocking(repo: Path) -> None:
    (repo / ".gitignore").write_text("")
    write(repo, "_manifest/cache/k/unit-0.md", "x\n")
    [finding] = lints.lint_no_cache_in_git(repo)
    assert "not gitignored" in finding.message


# ---- tokens ------------------------------------------------------------------------------------------------


def test_token_in_pipeline_file_is_blocking_and_not_quoted(repo: Path) -> None:
    secret = "https://graph.microsoft.com/v1.0/drives/x/root/delta?token=SUPERSECRETCURSOR"
    write(repo, "_manifest/src.jsonl", '{"note": "' + secret + '"}\n')
    write(repo, "mirror/src/api.md", page(f"# API\n\nAuthorization: Bearer {OPAQUE_BEARER}\n"))
    write(repo, "INDEX.md", "# fine\n")
    found = lints.lint_no_tokens(repo)
    assert codes(found) == [("TOKEN", "_manifest/src.jsonl", True), ("TOKEN", "mirror/src/api.md", False)]
    assert all("SUPERSECRETCURSOR" not in f.message and OPAQUE_BEARER not in f.message for f in found)
    assert "line 1" in found[0].message


OPAQUE_BEARER = "8f2kQz71mVb0aLx3TnWp9cRd"
JWT_BEARER = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl"
PADDED_BEARER = "dGhpc2lzYXNlY3JldHRva2VuMTIzNA=="
MID_EQUALS_BEARER = "ya29.a0Xq7Lm2Rz=Q9zKp4Wn8Tb3Vc"
QUERY_TOKEN = "Zm9vYmFyYmF6cXV4MTIzNDU2"


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Our desk covers Bearer Securities and other instruments.", None),
        ("Authorization: Bearer <your token>", None),
        ("See the reset-token=howto page.", None),
        (f"Authorization: Bearer {OPAQUE_BEARER}", OPAQUE_BEARER),
        (f"Authorization: Bearer {JWT_BEARER}", JWT_BEARER),
        (f"Authorization: Bearer {PADDED_BEARER}", PADDED_BEARER),
        (f"Authorization: Bearer {MID_EQUALS_BEARER}", MID_EQUALS_BEARER),
        (f"https://app.example.com/cb#access_token={QUERY_TOKEN}&state=x", QUERY_TOKEN),
        (f"https://graph.microsoft.com/v1.0/me/delta?$deltatoken={QUERY_TOKEN}", QUERY_TOKEN),
    ],
)
def test_page_token_check_needs_a_token_shaped_value(repo: Path, text: str, value: str | None) -> None:
    """field N7: prose like "Bearer Securities" is no finding; a real-looking token is a non-blocking one
    whose advice names an action that exists (no SECRET quarantine is fed by it) and quotes no part of the
    value, even one holding "=" (base64 padding)."""
    write(repo, "mirror/src/notes.md", page(f"# Notes\n\n{text}\n"))
    found = lints.lint_no_tokens(repo)
    assert codes(found) == ([("TOKEN", "mirror/src/notes.md", False)] if value else [])
    for f in found:
        assert "rotate it" in f.message and "quarantine" not in f.message
        assert value is not None
        assert not any(value[i : i + 8] in f.message for i in range(len(value) - 7))


def test_token_lint_on_given_paths_and_skips_cache(repo: Path) -> None:
    write(repo, "_sync/STATE.md", "cursor: deltatoken=abc\n")
    write(repo, "_manifest/cache/k/result.json", "token=abc\n")
    assert lints.lint_no_tokens(repo, ["INDEX.md"]) == []
    found = lints.lint_no_tokens(repo, ["_sync/STATE.md", "_manifest/cache/k/result.json"])
    assert codes(found) == [("TOKEN", "_sync/STATE.md", True)]


def test_third_party_names_never_block_but_cursor_shapes_and_live_cursors_do(repo: Path) -> None:
    """security-governance-07: file names / mail subjects reach pipeline files verbatim."""
    write(repo, "_manifest/src.jsonl", '{"rel_path":"Bearer bonds - Q3 memo.txt"}\n')
    write(repo, "_sync/QUARANTINE.tsv", "src\treset-token=howto.txt\tno converter\n")
    write(repo, "CHANGELOG/2026-09.md", "- A `mirror/src/your-password-reset-token=8h2k.md`\n")
    assert [f for f in lints.lint_no_tokens(repo) if f.blocking] == []
    write(repo, "_sync/QUARANTINE.tsv", "src\tx?$skiptoken=abc\tno converter\n")
    [hit] = [f for f in lints.lint_no_tokens(repo) if f.blocking]
    assert hit.path == "_sync/QUARANTINE.tsv"
    write(repo, "_sync/QUARANTINE.tsv", "src\tfine\tno converter\n")
    live = "Zm9vYmFyYmF6cXV4cXV1eGNvcmdl"
    write(repo, "INDEX.md", f"# index {live}\n")
    [hit] = [f for f in lints.lint_no_tokens(repo, known_secrets=[live]) if f.blocking]
    assert hit.path == "INDEX.md" and live not in hit.message


# ---- secrets -----------------------------------------------------------------------------------------------

AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
GH_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def test_builtin_secret_scan(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lints, "_gitleaks", lambda: None)
    write(repo, "mirror/src/leak.md", page(f"# creds\n\nkey = {AWS_KEY}\n"))
    write(repo, "mirror/src/clean.md", page())
    found = lints.lint_secrets(repo, ["mirror/src/leak.md", "mirror/src/clean.md", "mirror/src/missing.md"])
    assert codes(found) == [("SECRET", "mirror/src/leak.md", False)]
    assert AWS_KEY not in found[0].message
    assert "aws-access-key" in found[0].message


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        (
            "Join: https://teams.microsoft.com/l/meetup-join/19%3ameeting_Zx9%40thread.v2/0?pwd=Qm9vbGVhbjEy",
            False,
        ),
        ("<https://teams.microsoft.com/meet/2468013579?p=Ab12Cd&pwd=Qm9vbGVhbjEy>", False),
        ("[Join](https://gov.teams.microsoft.us/l/meetup-join/x?context=y&PWD=Qm9vbGVhbjEy)", False),
        ("https://teams.live.com/meet/9912345678?pwd=Qm9vbGVhbjEy", False),
        ("https://teams.cloud.microsoft/meet/123?pwd=Qm9vbGVhbjEy", False),
        ("https://teams.microsoft.com.example.net/l/meetup-join/x?pwd=Qm9vbGVhbjEy", True),
        ("https://evilteams.microsoft.com/l/meetup-join/x?pwd=Qm9vbGVhbjEy", True),
        ("https://example.net/go?to=https://teams.microsoft.com/l/x&pwd=Qm9vbGVhbjEy", True),
        ("https://us02web.zoom.us/j/81234567890?pwd=Qm9vbGVhbjEy", True),
        ("https://teams.microsoft.com/l/meetup-join/x?password=Qm9vbGVhbjEy", True),
        ("https://teams.microsoft.com/l/meetup-join/x?pwd=Qm9vbGVhbjEy then pwd: hunter2hunter2", True),
    ],
)
def test_builtin_secret_scan_ignores_teams_join_pwd(
    repo: Path, monkeypatch: pytest.MonkeyPatch, line: str, flagged: bool
) -> None:
    """field N6: a Teams invite's join-link passcode is not a credential; anything else named pwd still is."""
    monkeypatch.setattr(lints, "_gitleaks", lambda: None)
    write(repo, "mirror/src/invite.md", page(f"# Weekly sync\n\n{line}\n"))
    found = lints.lint_secrets(repo, ["mirror/src/invite.md"])
    assert [("generic-password" in f.message) for f in found] == ([True] if flagged else [])


def test_secret_scan_of_nothing(repo: Path) -> None:
    assert lints.lint_secrets(repo, []) == []


@pytest.mark.skipif(lints._gitleaks() is None, reason="gitleaks not installed")
def test_gitleaks_secret_scan(repo: Path) -> None:
    write(repo, "mirror/src/leak.md", page(f"# creds\n\ngithub_token = {GH_TOKEN}\n"))
    write(repo, "mirror/src/sub dir/clean.md", page())
    found = lints.lint_secrets(repo, ["mirror/src/leak.md", "mirror/src/sub dir/clean.md"])
    assert [f.path for f in found] == ["mirror/src/leak.md"]
    assert "(gitleaks)" in found[0].message and GH_TOKEN not in found[0].message
    assert not list((repo / ".git").glob("agentsync-secret-*"))


def test_gitleaks_failure_falls_back_to_builtin(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "gitleaks"
    broken.write_text("#!/bin/sh\necho boom >&2\nexit 2\n")
    broken.chmod(0o755)
    monkeypatch.setattr(lints, "_gitleaks", lambda: broken)
    write(repo, "mirror/src/leak.md", page(f"# creds\n\n{AWS_KEY}\n"))
    [finding] = lints.lint_secrets(repo, ["mirror/src/leak.md"])
    assert "builtin" in finding.message


# ---- index budget ------------------------------------------------------------------------------------------


def test_index_budget(repo: Path) -> None:
    [missing] = lints.lint_index_budget(repo)
    assert not missing.blocking
    write(repo, "INDEX.md", "# i\n" + "- line\n" * 150)
    assert lints.lint_index_budget(repo) == []
    write(repo, "INDEX.md", "- line\n" * (lints.INDEX_MAX_LINES + 1))
    assert [f.message.split()[1] for f in lints.lint_index_budget(repo)] == ["lines"]
    write(repo, "INDEX.md", "x" * (lints.INDEX_MAX_BYTES + 1))
    assert "bytes" in lints.lint_index_budget(repo)[0].message


# ---- double conversion -------------------------------------------------------------------------------------


def unit(body: str) -> RenderedUnit:
    return RenderedUnit(
        unit_id="whole",
        kind=UnitKind.WHOLE,
        index=0,
        of=1,
        name="",
        file_stem="",
        title="t",
        summary="s",
        tokens_estimate=1,
        body=body,
        rendered_sha256=sha(body),
    )


class FakeRegistry:
    def __init__(self, bodies: list[str] | None, *, raises: Exception | None = None) -> None:
        self.bodies = bodies
        self.raises = raises
        self.calls = 0

    def for_name(self, name: str) -> object | None:
        if name.endswith(".xyz"):
            return None
        return SimpleNamespace(converter_id="fake", convert=self.convert)

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        if self.raises is not None:
            raise self.raises
        assert self.bodies is not None
        body = self.bodies[self.calls % len(self.bodies)]
        self.calls += 1
        return (unit(body),)


def test_double_conversion(tmp_path: Path) -> None:
    src = tmp_path / "a.docx"
    src.write_bytes(b"x")
    det = FakeRegistry(["same\n"])
    assert lints.lint_double_conversion([(src, "a.docx"), (src, "b.xyz")], det) == []  # type: ignore[arg-type]
    nondet = FakeRegistry(["one\n", "two\n"])
    [finding] = lints.lint_double_conversion([(src, "a.docx")], nondet)  # type: ignore[arg-type]
    assert finding.code == "NONDETERMINISTIC" and finding.blocking and "whole" in finding.message
    failing = FakeRegistry(None, raises=RuntimeError("boom"))
    assert lints.lint_double_conversion([(src, "a.docx")], failing) == []  # type: ignore[arg-type]


# ---- the land gate -----------------------------------------------------------------------------------------


def test_land_gate_clean_then_catches_unreported_hand_edit(repo: Path) -> None:
    write(repo, "mirror/src/a.docx.md", page())
    write(repo, "INDEX.md", "# index\n")
    write(repo, "_manifest/src.jsonl", '{"stable_id":"vol:1"}\n')
    assert [f for f in lints.run_land_gate(repo, ["mirror/src/a.docx.md"]) if f.blocking] == []
    gitops.commit_cycle(repo, "sync: seed")
    (repo / "mirror/src/a.docx.md").write_text(page().replace("hello", "edited"), encoding="utf-8")
    blocking = [f for f in lints.run_land_gate(repo, []) if f.blocking]
    assert codes(blocking) == [("FRONTMATTER", "mirror/src/a.docx.md", True)]


def test_land_gate_blocks_symlinks_and_tokens(repo: Path) -> None:
    write(repo, "INDEX.md", "# index\n")
    write(repo, "CHANGELOG.md", "leak: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2ln\n")
    (repo / "topics").mkdir()
    (repo / "topics/link.md").symlink_to(repo / "INDEX.md")
    blocking = {(f.code, f.path) for f in lints.run_land_gate(repo, []) if f.blocking}
    assert blocking == {("SYMLINK", "topics/link.md"), ("TOKEN", "CHANGELOG.md")}
