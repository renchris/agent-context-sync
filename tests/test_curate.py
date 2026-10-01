"""Curation (design 4.5, 10 week 0 / week 3): topics/ parsing, DEPENDS.tsv, refresh queue, banners, adopt."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import agentsync.curate as curate
from agentsync.curate import (
    BY_ENTITY_HEADER,
    DEPENDS_HEADER,
    HAND_WRITTEN,
    REFRESH_QUEUE_SH,
    RETIRED_BANNER,
    ROLES,
    STALE_BANNER,
    VERDICTS,
    RefreshVerdict,
    TopicSource,
    adopt_pages,
    apply_stale_banners,
    generate_depends,
    iter_topic_pages,
    lint_unlisted_pages,
    normalise_source_path,
    parse_topic_page,
    refresh_queue,
    write_by_entity,
    write_depends,
)
from agentsync.errors import CurateError
from agentsync.frontmatter import MirrorFrontmatter, render_mirror_page
from agentsync.manifest import DependsRow
from agentsync.model import PageStatus
from agentsync.paths import DocsLayout

H = "a" * 64
TODAY = "2026-09-29"


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def layout(tmp_path: Path) -> DocsLayout:
    root = tmp_path / "docs"
    (root / "topics").mkdir(parents=True)
    (root / "mirror").mkdir()
    return DocsLayout(root=root)


def mirror_page(
    layout: DocsLayout, rel: str, body: str = "# body\n", status: PageStatus = PageStatus.CURRENT
) -> str:
    """Write a real rendered mirror page; return its H2 (the pin a curated page would carry)."""
    kw: dict[str, object] = {}
    if status in (PageStatus.CURRENT, PageStatus.SUPERSEDED):
        kw = {
            "content_sha256": H,
            "canonical_sha256": "b" * 64,
            "rendered_sha256": sha(body),
            "converter": "text-plain@1",
            "options_hash": "sha256:" + "c" * 64,
            "summary": "s",
            "tokens_estimate": 3,
        }
    elif status is PageStatus.DELETED:
        kw = {"deleted_at": "2026-09-01", "last_rendered_sha256": sha(body)}
    else:
        kw = {"reason": "encrypted"}
    fm = MirrorFrontmatter(
        source_kind="local",
        source_id="s",
        stable_id="v:1",
        source_path=rel,
        status=status,
        **kw,  # type: ignore[arg-type]
    )
    path = layout.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_mirror_page(fm, body), encoding="utf-8")
    return sha(body)


def topic_page(layout: DocsLayout, rel: str, frontmatter: str, body: str = "# Page\n\nclaim.\n") -> Path:
    path = layout.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")
    return path


def sources_yaml(*entries: tuple[str, str, str]) -> str:
    lines = ["sources:\n"]
    lines += [f"  - {{path: {p}, at_rendered_sha256: {pin}, role: {role}}}\n" for p, pin, role in entries]
    return "".join(lines)


def run_script(root: Path, tmp_path: Path, tsv: str | None = None) -> tuple[int, list[str], str]:
    """Run the real design-4.5 shell script from the docs repo root (LC_ALL=C via conftest)."""
    script = tmp_path / "refresh-queue.sh"
    script.write_text(REFRESH_QUEUE_SH, encoding="utf-8")
    args = ["/bin/sh", str(script)] + ([tsv] if tsv else [])
    proc = subprocess.run(args, cwd=root, capture_output=True, check=False, stdin=subprocess.DEVNULL)
    lines = proc.stdout.decode("utf-8", "surrogateescape").splitlines()
    return proc.returncode, lines, proc.stderr.decode("utf-8", "replace")


def twin(layout: DocsLayout) -> tuple[int, list[str]]:
    rc, verdicts = refresh_queue(layout)
    return rc, [v.line() for v in verdicts]


# ---------------------------------------------------------------------------------------------------------
# value types and page discovery
# ---------------------------------------------------------------------------------------------------------


def test_constants_match_the_design() -> None:
    assert DEPENDS_HEADER.split("\t") == ["page", "source", "pinned_sha", "role"]
    assert BY_ENTITY_HEADER == "entity\tpage"
    assert ROLES == ("primary", "corroborating") and HAND_WRITTEN == "hand-written"
    assert set(VERDICTS) == {
        "STALE",
        "SOURCE-DELETED",
        "SOURCE-UNREADABLE",
        "MISSING-OR-UNPARSEABLE",
        "UNPINNED",
        "BAD-PIN",
        "MALFORMED",
    }
    assert "TSV=${1:-DEPENDS.tsv}" in REFRESH_QUEUE_SH


def test_refresh_verdict_line_matches_script_printf() -> None:
    assert RefreshVerdict("STALE", "topics/p.md", "mirror/a.md").line() == "STALE\ttopics/p.md\tmirror/a.md"
    assert (
        RefreshVerdict("BAD-PIN", "topics/p.md", "mirror/a.md", "(len=6)").line()
        == "BAD-PIN\ttopics/p.md\tmirror/a.md\t(len=6)"
    )
    assert (
        RefreshVerdict("MALFORMED", "topics/p.md", "", "(fields=2)").line()
        == "MALFORMED\ttopics/p.md\t(fields=2)"
    )


def test_iter_topic_pages_excludes_claude_and_index_at_any_depth(layout: DocsLayout) -> None:
    for rel in [
        "topics/CLAUDE.md",
        "topics/INDEX.md",
        "topics/b.md",
        "topics/a/z.md",
        "topics/a/CLAUDE.md",
        "topics/a/INDEX.md",
        "topics/a/notes.txt",
        "topics/a/b/c.md",
    ]:
        (layout.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (layout.root / rel).write_text("x", encoding="utf-8")
    assert iter_topic_pages(layout) == ["topics/a/b/c.md", "topics/a/z.md", "topics/b.md"]


def test_iter_topic_pages_without_topics_dir(tmp_path: Path) -> None:
    assert iter_topic_pages(DocsLayout(root=tmp_path / "nowhere")) == []


# ---------------------------------------------------------------------------------------------------------
# parse_topic_page / normalise_source_path
# ---------------------------------------------------------------------------------------------------------

DESIGN_PAGE = """kind: topic
entity: acme
purpose: >-                           # DeepWiki's required field: what this page is, and is NOT
  What we have agreed with Acme commercially: pricing, discounts, renewal.
sources:
  - {path: ../../../mirror/sharepoint/acme/finance/fy26-budget.xlsx.d/01-q3-budget.md,
     at_rendered_sha256: %s, role: primary}   # three levels up
  - {path: ../../../mirror/email/2026/09/2026-09-14-acme-kickoff-recap.md,
     at_rendered_sha256: 71c0aa…, role: corroborating}
depends_on_pages: [acme-integration-scope.md]
reviewed_at: 2026-09-21
modified: 2026-09-21T14:40:00Z
aliases: [Acme pricing, Acme MSA terms]
"""


def test_parse_design_example(layout: DocsLayout) -> None:
    topic_page(layout, "topics/clients/acme/acme-commercial.md", DESIGN_PAGE % H)
    page = parse_topic_page(layout, "topics/clients/acme/acme-commercial.md")
    assert page.path == "topics/clients/acme/acme-commercial.md"
    assert (page.kind, page.entity, page.provenance, page.adopted_at) == ("topic", "acme", None, None)
    assert page.sources == (
        TopicSource(
            "../../../mirror/sharepoint/acme/finance/fy26-budget.xlsx.d/01-q3-budget.md", H, "primary"
        ),
        TopicSource(
            "../../../mirror/email/2026/09/2026-09-14-acme-kickoff-recap.md", "71c0aa…", "corroborating"
        ),
    )
    assert page.depends_on_pages == ("acme-integration-scope.md",)
    assert page.aliases == ("Acme pricing", "Acme MSA terms")


def test_parse_adopted_page_and_scalar_lists(layout: DocsLayout) -> None:
    topic_page(
        layout,
        "topics/old.md",
        "provenance: hand-written\nadopted_at: 2026-09-29\nsources: []\naliases: Old\n",
    )
    page = parse_topic_page(layout, "topics/old.md")
    assert page.provenance == HAND_WRITTEN and page.adopted_at == "2026-09-29"
    assert page.sources == () and page.aliases == ("Old",) and page.entity is None


def test_parse_missing_pin_and_role_become_empty_and_int_pin_is_text(layout: DocsLayout) -> None:
    topic_page(
        layout,
        "topics/p.md",
        "entity: e\nsources:\n  - path: ../mirror/a.md\n"
        "  - {path: ../mirror/b.md, at_rendered_sha256: 1234}\n",
    )
    page = parse_topic_page(layout, "topics/p.md")
    assert page.sources == (TopicSource("../mirror/a.md", "", ""), TopicSource("../mirror/b.md", "1234", ""))


def test_parse_tolerates_bom(layout: DocsLayout) -> None:
    p = layout.root / "topics/bom.md"
    p.write_bytes(b"\xef\xbb\xbf---\nentity: e\n---\nx\n")
    assert parse_topic_page(layout, "topics/bom.md").entity == "e"


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("# no frontmatter\n", "no frontmatter"),
        ("---\nentity: [unclosed\n---\n", "invalid YAML"),
        ("---\n- a\n- b\n---\n", "not a mapping"),
        ("---\nsources: {path: x}\n---\n", "must be a list"),
        ("---\nsources:\n  - just-a-string\n---\n", "must be a mapping"),
        ("---\nsources:\n  - {role: primary}\n---\n", "'path'"),
        ("---\nsources:\n  - {path: ../m.md, at_rendered_sha256: [a]}\n---\n", "at_rendered_sha256"),
        ("---\nsources:\n  - {path: ../m.md, role: [primary]}\n---\n", "'role'"),
        ('---\nsources:\n  - {path: "../m\\tx.md"}\n---\n', "tab"),
        ('---\nentity: "a\\tb"\n---\n', "tab"),
        ("---\nentity: {a: b}\n---\n", "must be a string"),
        ("---\naliases: [[a]]\n---\n", "must be a string"),
        ("---\nentity: e\nno close\n", "unterminated"),
    ],
)
def test_parse_errors_raise_curate_error(layout: DocsLayout, text: str, needle: str) -> None:
    (layout.root / "topics/bad.md").write_text(text, encoding="utf-8")
    with pytest.raises(CurateError, match=needle):
        parse_topic_page(layout, "topics/bad.md")


def test_parse_non_utf8_and_missing(layout: DocsLayout) -> None:
    (layout.root / "topics/latin1.md").write_bytes(b"---\nentity: caf\xe9\n---\n")
    with pytest.raises(CurateError, match="not UTF-8"):
        parse_topic_page(layout, "topics/latin1.md")
    with pytest.raises(CurateError, match="unreadable"):
        parse_topic_page(layout, "topics/nope.md")


@pytest.mark.parametrize(
    ("page", "src", "expected"),
    [
        (
            "topics/clients/acme/acme-commercial.md",
            "../../../mirror/sharepoint/acme/finance/fy26-budget.xlsx.d/01-q3-budget.md",
            "mirror/sharepoint/acme/finance/fy26-budget.xlsx.d/01-q3-budget.md",
        ),
        ("topics/p.md", "../mirror/s/a.md", "mirror/s/a.md"),
        ("topics/p.md", "./../mirror//s/./a.md", "mirror/s/a.md"),
        ("topics/a/p.md", "sibling.md", "topics/a/sibling.md"),
        ("topics/p.md", "../mirror/s/cafe\u0301.md", "mirror/s/caf\u00e9.md"),  # NFD in, NFC out
    ],
)
def test_normalise_source_path(page: str, src: str, expected: str) -> None:
    assert normalise_source_path(page, src) == expected


@pytest.mark.parametrize(
    "src", ["../../outside.md", "../../../etc/passwd", "/abs/mirror/a.md", "", "..", "../.."]
)
def test_normalise_source_path_rejects_escapes(src: str) -> None:
    with pytest.raises(CurateError):
        normalise_source_path("topics/p.md", src)


# ---------------------------------------------------------------------------------------------------------
# generate_depends / write_depends / write_by_entity
# ---------------------------------------------------------------------------------------------------------


def build_corpus(layout: DocsLayout) -> dict[str, str]:
    pins = {
        "fresh": mirror_page(layout, "mirror/s/fresh.md", "# fresh\n"),
        "stale": mirror_page(layout, "mirror/s/stale.md", "# now\n"),
        "gone": mirror_page(layout, "mirror/s/gone.md", "# gone\n", PageStatus.DELETED),
        "bad": mirror_page(layout, "mirror/s/bad.md", "", PageStatus.UNREADABLE),
    }
    topic_page(
        layout,
        "topics/clients/acme/commercial.md",
        "kind: topic\nentity: acme\n"
        + sources_yaml(
            ("../../../mirror/s/fresh.md", pins["fresh"], "primary"),
            ("../../../mirror/s/stale.md", sha("# before\n"), "primary"),
            ("../../../mirror/s/gone.md", pins["gone"], "corroborating"),
        )
        + "depends_on_pages: [scope.md]\n",
    )
    topic_page(
        layout,
        "topics/clients/acme/scope.md",
        "entity: acme\n" + sources_yaml(("../../../mirror/s/bad.md", H, "primary")),
    )
    topic_page(
        layout,
        "topics/decisions/d1.md",
        "entity: platform\n" + sources_yaml(("../../mirror/s/fresh.md", pins["fresh"], "primary")),
    )
    topic_page(layout, "topics/legacy.md", "provenance: hand-written\nadopted_at: 2026-09-29\nsources: []\n")
    return pins


def test_generate_depends_rows_entities_and_clean_findings(layout: DocsLayout) -> None:
    pins = build_corpus(layout)
    rows, entities, findings = generate_depends(layout)
    assert rows == [
        DependsRow("topics/clients/acme/commercial.md", "mirror/s/fresh.md", pins["fresh"], "primary"),
        DependsRow("topics/clients/acme/commercial.md", "mirror/s/gone.md", pins["gone"], "corroborating"),
        DependsRow("topics/clients/acme/commercial.md", "mirror/s/stale.md", sha("# before\n"), "primary"),
        DependsRow("topics/clients/acme/scope.md", "mirror/s/bad.md", H, "primary"),
        DependsRow("topics/decisions/d1.md", "mirror/s/fresh.md", pins["fresh"], "primary"),
    ]
    assert entities == [
        ("acme", "topics/clients/acme/commercial.md"),
        ("acme", "topics/clients/acme/scope.md"),
        ("platform", "topics/decisions/d1.md"),
    ]
    assert findings == []
    assert generate_depends(layout) == (rows, entities, findings)  # deterministic


def test_generate_depends_findings(layout: DocsLayout) -> None:
    pin = mirror_page(layout, "mirror/s/a.md")
    topic_page(
        layout,
        "topics/p.md",
        "entity: e\n"
        + sources_yaml(
            ("../mirror/s/a.md", pin, "primary"),
            ("../mirror/s/a.md", pin, "corroborating"),  # duplicate
            ("../mirror/s/b.md", "", "primary"),  # unpinned
            ("../mirror/s/c.md", "3ab77e", "primary"),  # short pin
            ("../mirror/s/d.md", H.upper(), "primary"),  # uppercase is not a pin
            ("../mirror/s/e.md", H, "main"),  # bad role
            ("../../escape.md", H, "primary"),  # escapes docs/
            ("other.md", H, "primary"),  # not a mirror page
        )
        + "depends_on_pages: [missing.md, ../../x.md]\n",
    )
    topic_page(layout, "topics/noentity.md", sources_yaml(("../mirror/s/a.md", pin, "primary")))
    (layout.root / "topics/broken.md").write_text("no frontmatter\n", encoding="utf-8")
    topic_page(
        layout,
        "topics/adopted.md",
        "provenance: hand-written\nadopted_at: 2026-09-29\n"
        + sources_yaml(("../mirror/s/a.md", pin, "primary")),
    )
    rows, entities, findings = generate_depends(layout)
    assert [(r.page, r.source, r.pinned_sha, r.role) for r in rows] == [
        ("topics/noentity.md", "mirror/s/a.md", pin, "primary"),
        ("topics/p.md", "mirror/s/a.md", pin, "primary"),
        ("topics/p.md", "mirror/s/b.md", "", "primary"),
        ("topics/p.md", "mirror/s/c.md", "3ab77e", "primary"),
        ("topics/p.md", "mirror/s/d.md", H.upper(), "primary"),
        ("topics/p.md", "mirror/s/e.md", H, "main"),
        ("topics/p.md", "topics/other.md", H, "primary"),
    ]
    assert entities == [("e", "topics/p.md")]
    codes = [(f.path, f.code) for f in findings]
    assert codes == [
        ("topics/adopted.md", "HAND-WRITTEN-WITH-SOURCES"),
        ("topics/broken.md", "CURATE-PARSE"),
        ("topics/noentity.md", "MISSING-ENTITY"),
        ("topics/p.md", "BAD-PIN"),
        ("topics/p.md", "BAD-PIN"),
        ("topics/p.md", "BAD-ROLE"),
        ("topics/p.md", "BROKEN-PAGE-EDGE"),
        ("topics/p.md", "BROKEN-PAGE-EDGE"),
        ("topics/p.md", "CURATE-PARSE"),
        ("topics/p.md", "DUPLICATE-SOURCE"),
        ("topics/p.md", "SOURCE-NOT-MIRROR"),
        ("topics/p.md", "UNPINNED"),
    ]
    assert not any(f.blocking for f in findings)
    assert any("len=6" in f.message for f in findings if f.code == "BAD-PIN")


def test_write_depends_exact_bytes_and_idempotent(layout: DocsLayout) -> None:
    rows = [
        DependsRow("topics/b.md", "mirror/s/z.md", H, "primary"),
        DependsRow("topics/a.md", "mirror/s/y.md", "", "corroborating"),
    ]
    assert write_depends(layout, rows) is True
    data = layout.depends_tsv.read_bytes()
    assert (
        data
        == (
            f"{DEPENDS_HEADER}\ntopics/a.md\tmirror/s/y.md\t\tcorroborating\ntopics/b.md\tmirror/s/z.md\t{H}\tprimary\n"
        ).encode()
    )
    mtime = layout.depends_tsv.stat().st_mtime_ns
    assert write_depends(layout, list(reversed(rows))) is False
    assert layout.depends_tsv.stat().st_mtime_ns == mtime
    assert write_depends(layout, []) is True
    assert layout.depends_tsv.read_text(encoding="utf-8") == DEPENDS_HEADER + "\n"
    assert not [p for p in layout.root.iterdir() if p.name.endswith(".tmp")]


def test_write_depends_keeps_mode_and_rejects_unrepresentable(layout: DocsLayout) -> None:
    write_depends(layout, [])
    assert stat.S_IMODE(layout.depends_tsv.stat().st_mode) == 0o600  # new generated files: owner-only
    layout.depends_tsv.chmod(0o740)
    write_depends(layout, [DependsRow("topics/a.md", "mirror/a.md", H, "primary")])
    assert stat.S_IMODE(layout.depends_tsv.stat().st_mode) == 0o700  # owner bits kept, group/other dropped
    with pytest.raises(CurateError):
        write_depends(layout, [DependsRow("topics/a.md", "mirror/a\tb.md", H, "primary")])
    with pytest.raises(CurateError):
        write_depends(layout, [DependsRow("topics/a.md", "", H, "primary")])


def test_write_by_entity(layout: DocsLayout) -> None:
    rows = [
        ("zeta", "topics/z.md"),
        ("acme", "topics/b.md"),
        ("acme", "topics/a.md"),
        ("acme", "topics/a.md"),
    ]
    assert write_by_entity(layout, rows) is True
    assert layout.by_entity_tsv.read_text(encoding="utf-8") == (
        "entity\tpage\nacme\ttopics/a.md\nacme\ttopics/b.md\nzeta\ttopics/z.md\n"
    )
    assert write_by_entity(layout, rows) is False


# ---------------------------------------------------------------------------------------------------------
# refresh queue: the Python twin against the real script
# ---------------------------------------------------------------------------------------------------------


def test_design_fixture_exactly_one_stale_and_one_deleted(layout: DocsLayout, tmp_path: Path) -> None:
    """Design 4.5: one page, one present source, one tombstone -> exactly one STALE and one SOURCE-DELETED."""
    fresh = mirror_page(layout, "mirror/s/fresh.md", "# fresh\n")
    mirror_page(layout, "mirror/s/changed.md", "# new\n")
    gone = mirror_page(layout, "mirror/s/gone.md", "# gone\n", PageStatus.DELETED)
    topic_page(
        layout,
        "topics/p.md",
        "entity: e\n"
        + sources_yaml(
            ("../mirror/s/fresh.md", fresh, "primary"),
            ("../mirror/s/changed.md", sha("# old\n"), "primary"),
            ("../mirror/s/gone.md", gone, "corroborating"),
        ),
    )
    rows, _, _ = generate_depends(layout)
    write_depends(layout, rows)
    expected = ["SOURCE-DELETED\ttopics/p.md\tmirror/s/gone.md", "STALE\ttopics/p.md\tmirror/s/changed.md"]
    assert twin(layout) == (1, expected)
    rc, out, _ = run_script(layout.root, tmp_path)
    assert (rc, out) == (1, expected)


def test_full_corpus_twin_matches_script(layout: DocsLayout, tmp_path: Path) -> None:
    build_corpus(layout)
    rows, _, _ = generate_depends(layout)
    write_depends(layout, rows)
    rc, lines = twin(layout)
    assert rc == 1
    assert lines == [
        "SOURCE-DELETED\ttopics/clients/acme/commercial.md\tmirror/s/gone.md",
        "SOURCE-UNREADABLE\ttopics/clients/acme/scope.md\tmirror/s/bad.md",
        "STALE\ttopics/clients/acme/commercial.md\tmirror/s/stale.md",
    ]
    s_rc, s_lines, _ = run_script(layout.root, tmp_path)
    assert (s_rc, s_lines) == (rc, lines)


def test_all_fresh_is_rc_zero(layout: DocsLayout, tmp_path: Path) -> None:
    pin = mirror_page(layout, "mirror/s/a.md")
    topic_page(layout, "topics/p.md", "entity: e\n" + sources_yaml(("../mirror/s/a.md", pin, "primary")))
    write_depends(layout, generate_depends(layout)[0])
    assert twin(layout) == (0, [])
    assert run_script(layout.root, tmp_path)[:2] == (0, [])


RAW_MIRROR_FILES: dict[str, bytes] = {
    # no fences at all: the awk scans the whole file
    "mirror/raw/nofence.md": f"rendered_sha256: {H}\nstatus: current\n".encode(),
    # a body quoting the key after the closing fence must not supply the current sha
    "mirror/raw/quoted.md": f"---\nstatus: current\n---\nrendered_sha256: {H}\n".encode(),
    # last value wins; CRLF keeps a \r in the value (so it is STALE against a clean pin)
    "mirror/raw/crlf.md": f"---\r\nrendered_sha256: {H}\r\n---\r\n".encode(),
    "mirror/raw/twice.md": f"---\nrendered_sha256: {'b' * 64}\nrendered_sha256: {H}\n---\n".encode(),
    "mirror/raw/refused.md": b"---\nstatus: refused\nreason: x\n---\n",
    "mirror/raw/deleted-no-sha.md": b"---\nstatus: deleted\n---\n",
    "mirror/raw/extra-space.md": f"---\nrendered_sha256:  {H}\n---\n".encode(),
    "mirror/raw/no-trailing-nl.md": f"---\nrendered_sha256: {H}".encode(),
}


@pytest.mark.parametrize(
    "tsv",
    [
        pytest.param(None, id="missing"),
        pytest.param(b"", id="empty"),
        pytest.param(b"\n", id="blank-first-line"),
        pytest.param(b"pages\tsource\n", id="bad-header"),
        pytest.param(b"topics/p.md\tmirror/raw/nofence.md\t" + H.encode() + b"\tprimary\n", id="headerless"),
        pytest.param(DEPENDS_HEADER.encode(), id="header-only-no-newline"),
        pytest.param(DEPENDS_HEADER.encode() + b"\n", id="header-only"),
        pytest.param(
            b"page\tsource\n\nx\ty\none\n\t\t\n" + b"q\tmirror/raw/nofence.md\t" + H.encode(),
            id="malformed-mix",
        ),
        pytest.param(
            DEPENDS_HEADER.encode()
            + b"\n"
            + b"".join(
                b"topics/p.md\t" + name.encode() + b"\t" + H.encode() + b"\tprimary\n"
                for name in sorted([*RAW_MIRROR_FILES, "mirror/raw/absent.md"])
            ),
            id="raw-pages",
        ),
        pytest.param(
            DEPENDS_HEADER.encode()
            + b"\ntopics/b.md\tmirror/x.md\t\tprimary\n"
            + b"topics/a.md\tmirror/x.md\t\tprimary\n"
            + b"topics/a.md\tmirror/x.md\t\tprimary\n"  # duplicate: sort -u
            + b"topics/a.md\tmirror/x.md\t3ab77e\n"
            + b"topics/a.md\tmirror/x.md\t"
            + H.upper().encode()
            + b"\n"
            + b"topics/a.md\tmirror/x.md\t"
            + H.encode()
            + b"\r\n"  # CRLF pin: BAD-PIN len=65
            + b"topics/a.md\tmirror/x.md\t71c0aa\xe2\x80\xa6\tprimary\n"  # non-ASCII: byte length
            + b"Topics/Z.md\tmirror/x.md\t\n"
            + b"topics/caf\xc3\xa9.md\tmirror/x.md\t\n",
            id="pins-and-order",
        ),
    ],
)
def test_twin_matches_script(layout: DocsLayout, tmp_path: Path, tsv: bytes | None) -> None:
    for rel, data in RAW_MIRROR_FILES.items():
        (layout.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (layout.root / rel).write_bytes(data)
    if tsv is not None:
        layout.depends_tsv.write_bytes(tsv)
    s_rc, s_lines, _ = run_script(layout.root, tmp_path)
    p_rc, p_lines = twin(layout)
    assert (p_rc, p_lines) == (s_rc, s_lines)
    if tsv is None or tsv in (b"", b"\n", b"pages\tsource\n"):
        assert p_rc == 2
        assert not refresh_queue(layout)[1]


def test_raw_pages_verdicts_are_the_expected_ones(layout: DocsLayout) -> None:
    for rel, data in RAW_MIRROR_FILES.items():
        (layout.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (layout.root / rel).write_bytes(data)
    rows = [
        DependsRow("topics/p.md", rel, H, "primary") for rel in [*RAW_MIRROR_FILES, "mirror/raw/absent.md"]
    ]
    write_depends(layout, rows)
    rc, verdicts = refresh_queue(layout)
    got = {v.source: v.verdict for v in verdicts}
    assert rc == 1
    assert got == {
        "mirror/raw/quoted.md": "MISSING-OR-UNPARSEABLE",
        "mirror/raw/crlf.md": "STALE",
        "mirror/raw/refused.md": "SOURCE-UNREADABLE",
        "mirror/raw/deleted-no-sha.md": "SOURCE-DELETED",
        "mirror/raw/extra-space.md": "STALE",
        "mirror/raw/absent.md": "MISSING-OR-UNPARSEABLE",
    }  # nofence, twice and no-trailing-nl are fresh


def test_empty_source_column_is_reported_where_the_script_crashes(layout: DocsLayout, tmp_path: Path) -> None:
    """Documented divergence: macOS awk dies on ``getline < ""`` and the script silently drops later rows."""
    layout.depends_tsv.write_bytes(
        DEPENDS_HEADER.encode()
        + b"\ntopics/a.md\t\t"
        + H.encode()
        + b"\tprimary\ntopics/b.md\tmirror/x.md\t\t\n"
    )
    assert twin(layout) == (
        1,
        ["MISSING-OR-UNPARSEABLE\ttopics/a.md\t", "UNPINNED\ttopics/b.md\tmirror/x.md"],
    )
    _rc, lines, err = run_script(layout.root, tmp_path)
    if "null file name" in err:  # the macOS awk failure mode the twin deliberately does not copy
        assert "UNPINNED\ttopics/b.md\tmirror/x.md" not in lines


def test_directory_source_twin_continues_where_the_script_dies(layout: DocsLayout, tmp_path: Path) -> None:
    """Documented divergence: awk dies on a directory source; the queue must not read that as 'all fresh'."""
    (layout.root / "mirror/s/book.xlsx.d").mkdir(parents=True)
    layout.depends_tsv.write_text(
        f"{DEPENDS_HEADER}\ntopics/a.md\tmirror/s/book.xlsx.d\t{H}\tprimary\ntopics/b.md\tmirror/x.md\t{H}\tprimary\n",
        encoding="utf-8",
    )
    assert twin(layout) == (
        1,
        [
            "MISSING-OR-UNPARSEABLE\ttopics/a.md\tmirror/s/book.xlsx.d",
            "MISSING-OR-UNPARSEABLE\ttopics/b.md\tmirror/x.md",
        ],
    )
    rc, lines, err = run_script(layout.root, tmp_path)
    if "i/o error" in err:  # measured on macOS awk 20200816: rc 0, nothing printed
        assert (rc, lines) == (0, [])


def test_generator_never_emits_a_directory_source(layout: DocsLayout, tmp_path: Path) -> None:
    pin = mirror_page(layout, "mirror/s/book.xlsx.d/01-q3.md", "# q3\n")
    topic_page(
        layout,
        "topics/p.md",
        "entity: e\n"
        + sources_yaml(
            ("../mirror/s/book.xlsx.d", H, "primary"), ("../mirror/s/book.xlsx.d/01-q3.md", pin, "primary")
        ),
    )
    rows, _, findings = generate_depends(layout)
    assert [r.source for r in rows] == ["mirror/s/book.xlsx.d/01-q3.md"]
    assert [(f.code, f.blocking) for f in findings] == [("SOURCE-IS-DIRECTORY", False)]
    write_depends(layout, rows)
    assert twin(layout) == run_script(layout.root, tmp_path)[:2] == (0, [])


def test_absolute_source_is_read_like_awk_does(layout: DocsLayout, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text(f"---\nrendered_sha256: {H}\n---\n", encoding="utf-8")
    layout.depends_tsv.write_text(
        f"{DEPENDS_HEADER}\ntopics/a.md\t{outside}\t{H}\tprimary\n", encoding="utf-8"
    )
    assert twin(layout) == (0, [])
    assert run_script(layout.root, tmp_path)[:2] == (0, [])


# ---------------------------------------------------------------------------------------------------------
# STALE / RETIRED banners
# ---------------------------------------------------------------------------------------------------------


def stale_setup(layout: DocsLayout) -> tuple[Path, str]:
    mirror_page(layout, "mirror/s/a.md", "# v2\n")
    fm = "entity: e\n" + sources_yaml(("../mirror/s/a.md", sha("# v1\n"), "primary"))
    path = topic_page(layout, "topics/p.md", fm, "# Page\n\nclaim.\n")
    write_depends(layout, generate_depends(layout)[0])
    return path, fm


def test_stale_banner_added_kept_and_removed_byte_exact(layout: DocsLayout) -> None:
    path, fm = stale_setup(layout)
    original = path.read_bytes()
    rc, verdicts = refresh_queue(layout)
    assert rc == 1 and [v.verdict for v in verdicts] == ["STALE"]
    assert apply_stale_banners(layout, verdicts, TODAY) == ["topics/p.md"]
    banner = STALE_BANNER.format(date=TODAY)
    assert path.read_text(encoding="utf-8") == f"---\n{fm}---\n{banner}\n\n# Page\n\nclaim.\n"
    parse_topic_page(layout, "topics/p.md")  # frontmatter still parses

    # a later cycle keeps the first date: no churn, no write
    before = path.read_bytes()
    assert apply_stale_banners(layout, verdicts, "2026-10-05") == []
    assert path.read_bytes() == before

    # re-pinned -> fresh -> banner removed, page byte-identical to the original
    path.write_bytes(before.replace(sha("# v1\n").encode(), sha("# v2\n").encode()))
    write_depends(layout, generate_depends(layout)[0])
    rc, verdicts = refresh_queue(layout)
    assert (rc, verdicts) == (0, [])
    assert apply_stale_banners(layout, verdicts, TODAY) == ["topics/p.md"]
    assert path.read_bytes() == original.replace(sha("# v1\n").encode(), sha("# v2\n").encode())


def test_stale_banner_only_for_stale_verdicts(layout: DocsLayout) -> None:
    mirror_page(layout, "mirror/s/gone.md", "# g\n", PageStatus.DELETED)
    path = topic_page(
        layout, "topics/p.md", "entity: e\n" + sources_yaml(("../mirror/s/gone.md", H, "primary"))
    )
    write_depends(layout, generate_depends(layout)[0])
    _, verdicts = refresh_queue(layout)
    assert [v.verdict for v in verdicts] == ["SOURCE-DELETED"]
    before = path.read_bytes()
    assert apply_stale_banners(layout, verdicts, TODAY) == []
    assert path.read_bytes() == before


def test_hand_written_pages_are_never_touched(layout: DocsLayout) -> None:
    write_depends(layout, [])
    body = f"{STALE_BANNER.format(date='2026-01-01')}\n\n# kept\n"
    path = topic_page(layout, "topics/old.md", "provenance: hand-written\nsources: []\n", body)
    before = path.read_bytes()
    forged = [RefreshVerdict("STALE", "topics/old.md", "mirror/x.md")]
    assert apply_stale_banners(layout, forged, TODAY) == []
    assert apply_stale_banners(layout, [], TODAY) == []
    assert path.read_bytes() == before


def test_unusable_depends_never_strips_banners(layout: DocsLayout) -> None:
    body = f"{STALE_BANNER.format(date='2026-01-01')}\n\n# x\n"
    path = topic_page(layout, "topics/p.md", "entity: e\n", body)
    before = path.read_bytes()
    assert apply_stale_banners(layout, [], TODAY) == []  # DEPENDS.tsv missing: rc 2 is not "all fresh"
    layout.depends_tsv.write_text("garbage\n", encoding="utf-8")
    assert apply_stale_banners(layout, [], TODAY) == []
    assert path.read_bytes() == before


def test_banner_edge_cases(layout: DocsLayout) -> None:
    write_depends(layout, [])
    # closing fence with no newline and no body
    p1 = layout.root / "topics/nobody.md"
    p1.write_text("---\nentity: e\n---", encoding="utf-8")
    # BOM and an unparseable page are skipped / preserved
    p2 = layout.root / "topics/bom.md"
    p2.write_bytes(b"\xef\xbb\xbf---\nentity: e\n---\nbody\n")
    p3 = layout.root / "topics/broken.md"
    p3.write_text("no frontmatter\n", encoding="utf-8")
    verdicts = [
        RefreshVerdict("STALE", p, "mirror/x.md")
        for p in ("topics/nobody.md", "topics/bom.md", "topics/broken.md")
    ]
    changed = apply_stale_banners(layout, verdicts, TODAY)
    assert changed == ["topics/bom.md", "topics/nobody.md"]
    banner = STALE_BANNER.format(date=TODAY)
    assert p1.read_text(encoding="utf-8") == f"---\nentity: e\n---\n{banner}\n\n"
    assert p2.read_bytes() == b"\xef\xbb\xbf" + f"---\nentity: e\n---\n{banner}\n\nbody\n".encode()
    assert p3.read_text(encoding="utf-8") == "no frontmatter\n"
    assert apply_stale_banners(layout, [], TODAY) == ["topics/bom.md", "topics/nobody.md"]
    assert p1.read_text(encoding="utf-8") == "---\nentity: e\n---\n"
    assert p2.read_bytes() == b"\xef\xbb\xbf---\nentity: e\n---\nbody\n"


def test_today_must_be_a_date(layout: DocsLayout) -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        apply_stale_banners(layout, [], "29/09/2026")


def test_retired_banners_coexist_with_stale(layout: DocsLayout) -> None:
    path, fm = stale_setup(layout)
    rows = generate_depends(layout)[0]
    _, verdicts = refresh_queue(layout)
    apply_stale_banners(layout, verdicts, TODAY)
    assert curate._apply_retired_banners(layout, rows, {"s": "vendor contract ended\n2026"}) == [
        "topics/p.md"
    ]
    stale = STALE_BANNER.format(date=TODAY)
    retired = RETIRED_BANNER.format(reason="vendor contract ended 2026")
    assert path.read_text(encoding="utf-8") == f"---\n{fm}---\n{stale}\n{retired}\n\n# Page\n\nclaim.\n"
    assert curate._apply_retired_banners(layout, rows, {"s": "vendor contract ended 2026"}) == []
    assert curate._apply_retired_banners(layout, rows, {"other": "x"}) == ["topics/p.md"]
    assert path.read_text(encoding="utf-8") == f"---\n{fm}---\n{stale}\n\n# Page\n\nclaim.\n"


# ---------------------------------------------------------------------------------------------------------
# UNLISTED
# ---------------------------------------------------------------------------------------------------------


def test_lint_unlisted_pages(layout: DocsLayout) -> None:
    pin = mirror_page(layout, "mirror/s/a.md")
    topic_page(layout, "topics/listed.md", "entity: e\n" + sources_yaml(("../mirror/s/a.md", pin, "primary")))
    topic_page(layout, "topics/nosources.md", "entity: e\nsources: []\n")
    topic_page(layout, "topics/adopted.md", "provenance: hand-written\nadopted_at: 2026-09-29\nsources: []\n")
    (layout.root / "topics/broken-adopted.md").write_text(
        "---\nprovenance: hand-written\nentity: [\n---\n", encoding="utf-8"
    )
    (layout.root / "topics/broken.md").write_text("no frontmatter\n", encoding="utf-8")
    rows = generate_depends(layout)[0]
    findings = lint_unlisted_pages(layout, rows)
    assert [(f.code, f.path, f.blocking) for f in findings] == [
        ("UNLISTED", "topics/broken.md", False),
        ("UNLISTED", "topics/nosources.md", False),
    ]


def test_lint_unlisted_agrees_with_design_land_gate(layout: DocsLayout, tmp_path: Path) -> None:
    """Design 4.5 land gate (paths adapted to docs/ being the repo root) must print the same pages."""
    pin = mirror_page(layout, "mirror/s/a.md")
    topic_page(layout, "topics/listed.md", "entity: e\n" + sources_yaml(("../mirror/s/a.md", pin, "primary")))
    topic_page(layout, "topics/a/nosources.md", "entity: e\n")
    topic_page(layout, "topics/adopted.md", "provenance: hand-written\nsources: []\n")
    topic_page(layout, "topics/CLAUDE.md", "x: 1\n")
    rows = generate_depends(layout)[0]
    write_depends(layout, rows)
    gate = (
        "comm -23 <(grep -L '^provenance: hand-written' $(find topics -name '*.md' ! -name CLAUDE.md "
        "! -name INDEX.md) | sort) <(awk -F'\\t' 'NR>1{print $1}' DEPENDS.tsv | sort -u)"
    )
    proc = subprocess.run(
        ["/bin/bash", "-c", gate], cwd=layout.root, capture_output=True, text=True, check=False
    )
    assert (
        proc.stdout.split()
        == [f.path for f in lint_unlisted_pages(layout, rows)]
        == ["topics/a/nosources.md"]
    )


# ---------------------------------------------------------------------------------------------------------
# adopt
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def handmade(tmp_path: Path) -> Path:
    src = tmp_path / "old-docs"
    (src / "guides" / "img").mkdir(parents=True)
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    (src / "README.md").write_text("# Readme\n\nsee [guide](guides/setup.md)\n", encoding="utf-8")
    (src / "CLAUDE.md").write_text("# agent notes\n", encoding="utf-8")
    (src / "guides" / "setup.md").write_text(
        "---\ntitle: Setup   # keep this comment\naliases:\n  - install\n  - onboarding\n---\n# Setup\n",
        encoding="utf-8",
    )
    (src / "guides" / "crlf.md").write_bytes(b"\xef\xbb\xbf# Windows\r\nline\r\n")
    (src / "guides" / "hr.md").write_text("---\n\nA page starting with a rule.\n", encoding="utf-8")
    (src / "guides" / "prose-fences.md").write_text("---\nJust prose.\n---\nmore\n", encoding="utf-8")
    (src / "guides" / "readopt.md").write_text(
        "---\nentity: acme\nprovenance: hand-written\nadopted_at: 2020-01-01\nsources: []\n---\nbody\n",
        encoding="utf-8",
    )
    (src / "guides" / "img" / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (src / "guides" / "budget.xlsx").write_bytes(b"PK\x03\x04")
    (src / ".DS_Store").write_bytes(b"\x00")
    (src / "~$lock.md").write_text("lock", encoding="utf-8")
    return src


def test_adopt_stamps_pages_and_keeps_the_tree(layout: DocsLayout, handmade: Path) -> None:
    created = adopt_pages(handmade, layout, TODAY)
    assert created == [
        "topics/CLAUDE.adopted.md",
        "topics/README.md",
        "topics/guides/crlf.md",
        "topics/guides/hr.md",
        "topics/guides/img/diagram.png",
        "topics/guides/prose-fences.md",
        "topics/guides/readopt.md",
        "topics/guides/setup.md",
    ]
    stamp = f"provenance: hand-written\nadopted_at: {TODAY}\nsources: []\n"
    t = layout.root / "topics"
    assert (t / "README.md").read_text(encoding="utf-8") == (
        f"---\n{stamp}---\n# Readme\n\nsee [guide](guides/setup.md)\n"
    )
    assert (t / "guides/setup.md").read_text(encoding="utf-8") == (
        "---\ntitle: Setup   # keep this comment\naliases:\n  - install\n  - onboarding\n"
        f"{stamp}---\n# Setup\n"
    )
    assert (t / "guides/crlf.md").read_bytes() == f"---\n{stamp}---\n# Windows\nline\n".encode()
    assert (t / "guides/hr.md").read_text(
        encoding="utf-8"
    ) == f"---\n{stamp}---\n---\n\nA page starting with a rule.\n"
    assert (t / "guides/prose-fences.md").read_text(encoding="utf-8") == (
        f"---\n{stamp}---\n---\nJust prose.\n---\nmore\n"
    )
    assert (t / "guides/readopt.md").read_text(encoding="utf-8") == f"---\nentity: acme\n{stamp}---\nbody\n"
    assert (t / "guides/img/diagram.png").read_bytes() == b"\x89PNG\r\n\x1a\nfake"
    assert not (t / "guides/budget.xlsx").exists() and not (t / ".git").exists()

    for rel in created:
        if rel.endswith(".md"):
            page = parse_topic_page(layout, rel)
            assert page.provenance == HAND_WRITTEN and page.adopted_at == TODAY and page.sources == ()
    setup = parse_topic_page(layout, "topics/guides/setup.md")
    assert setup.aliases == ("install", "onboarding")

    # adopted pages: absent from DEPENDS, no MISSING-ENTITY, not UNLISTED; in by-entity if they name one
    rows, entities, findings = generate_depends(layout)
    assert rows == [] and findings == []
    assert entities == [("acme", "topics/guides/readopt.md")]
    assert lint_unlisted_pages(layout, rows) == []


def test_adopt_refuses_to_overwrite_and_writes_nothing(layout: DocsLayout, handmade: Path) -> None:
    existing = layout.root / "topics" / "guides" / "setup.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("mine\n", encoding="utf-8")
    with pytest.raises(CurateError, match="already exists"):
        adopt_pages(handmade, layout, TODAY)
    assert existing.read_text(encoding="utf-8") == "mine\n"
    assert iter_topic_pages(layout) == ["topics/guides/setup.md"]


def test_adopt_case_insensitive_overwrite_of_scaffold(layout: DocsLayout, tmp_path: Path) -> None:
    (layout.root / "topics" / "CLAUDE.md").write_text("scaffold\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "claude.md").write_text("# lower\n", encoding="utf-8")
    assert adopt_pages(src, layout, TODAY) == ["topics/claude.adopted.md"]
    assert (layout.root / "topics" / "CLAUDE.md").read_text(encoding="utf-8") == "scaffold\n"


@pytest.mark.parametrize(
    ("name", "content", "needle"),
    [
        (
            "curated.md",
            f"---\nsources:\n  - {{path: ../m.md, at_rendered_sha256: {H}}}\n---\n",
            "already cites",
        ),
        ("prov.md", "---\nprovenance: meeting notes\n---\n", "provenance"),
        ("latin1.md", None, "not UTF-8"),
        ("badalias.md", "---\naliases: {a: b}\n---\n", "must be a string"),
    ],
)
def test_adopt_refusals(
    layout: DocsLayout, tmp_path: Path, name: str, content: str | None, needle: str
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "fine.md").write_text("# fine\n", encoding="utf-8")
    if content is None:
        (src / name).write_bytes(b"caf\xe9\n")
    else:
        (src / name).write_text(content, encoding="utf-8")
    with pytest.raises(CurateError, match=needle):
        adopt_pages(src, layout, TODAY)
    assert iter_topic_pages(layout) == []


def test_adopt_argument_checks(layout: DocsLayout, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="adopted_at"):
        adopt_pages(tmp_path, layout, "yesterday")
    with pytest.raises(CurateError, match="not a directory"):
        adopt_pages(tmp_path / "missing", layout, TODAY)
    with pytest.raises(CurateError, match="overlaps"):
        adopt_pages(layout.root, layout, TODAY)
    with pytest.raises(CurateError, match="overlaps"):
        adopt_pages(layout.root / "topics", layout, TODAY)
    with pytest.raises(CurateError, match="overlaps"):
        adopt_pages(tmp_path, layout, TODAY)  # the docs repo lives inside it
    src = tmp_path / "iso"
    src.mkdir()
    (src / "a.md").write_text("x\n", encoding="utf-8")
    assert adopt_pages(src, layout, "2026-09-29T10:00:00Z") == ["topics/a.md"]
    assert parse_topic_page(layout, "topics/a.md").adopted_at == "2026-09-29T10:00:00Z"


def test_adopt_skips_symlinks_and_nfc_names(layout: DocsLayout, tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "real").mkdir(parents=True)
    (src / "real" / "cafe\u0301.md").write_text("# nfd name\n", encoding="utf-8")
    (src / "linked-dir").symlink_to(src / "real")
    (src / "link.md").symlink_to(src / "real" / "cafe\u0301.md")
    created = adopt_pages(src, layout, TODAY)
    assert created == ["topics/real/caf\u00e9.md"]


def test_adopt_refuses_path_too_long(layout: DocsLayout, tmp_path: Path) -> None:
    src = tmp_path / "src"
    deep = src / ("d" * 100) / ("e" * 100)
    deep.mkdir(parents=True)
    (deep / "page.md").write_text("x\n", encoding="utf-8")
    with pytest.raises(CurateError, match="longer than 200"):
        adopt_pages(src, layout, TODAY)


def test_adopt_refuses_dataless_placeholders(
    layout: DocsLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SF_DATALESS cannot be set without a File Provider; UF_NODUMP stands in for the flag bit."""
    if not hasattr(os, "chflags"):
        pytest.skip("no chflags")
    src = tmp_path / "src"
    src.mkdir()
    page = src / "cloud.md"
    page.write_text("x\n", encoding="utf-8")
    os.chflags(page, stat.UF_NODUMP)
    monkeypatch.setattr(curate, "_SF_DATALESS", stat.UF_NODUMP)
    with pytest.raises(CurateError, match="dataless"):
        adopt_pages(src, layout, TODAY)
    assert iter_topic_pages(layout) == []


def test_adopt_rolls_back_on_write_failure(
    layout: DocsLayout, handmade: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copyfileobj", boom)
    with pytest.raises(CurateError, match="rolled back"):
        adopt_pages(handmade, layout, TODAY)
    left = [p for p in (layout.root / "topics").rglob("*") if p.is_file()]
    assert left == []


# ---------------------------------------------------------------------------------------------------------
# the whole week-3 loop
# ---------------------------------------------------------------------------------------------------------


def test_week3_loop_generate_write_queue_banner(layout: DocsLayout, tmp_path: Path) -> None:
    build_corpus(layout)
    rows, entities, findings = generate_depends(layout)
    assert write_depends(layout, rows) and write_by_entity(layout, entities)
    assert findings == [] and lint_unlisted_pages(layout, rows) == []
    rc, verdicts = refresh_queue(layout)
    assert (rc, [v.line() for v in verdicts]) == run_script(layout.root, tmp_path)[:2]
    assert apply_stale_banners(layout, verdicts, TODAY) == ["topics/clients/acme/commercial.md"]
    # a second identical cycle writes nothing
    rows2, entities2, _ = generate_depends(layout)
    assert rows2 == rows
    assert not write_depends(layout, rows2) and not write_by_entity(layout, entities2)
    assert apply_stale_banners(layout, refresh_queue(layout)[1], "2026-09-30") == []


def test_uncovered_mirror_pages_lists_current_pages_no_topic_cites(layout: DocsLayout) -> None:
    pin = mirror_page(layout, "mirror/s/cited.md")
    mirror_page(layout, "mirror/s/uncovered.md", body="# other\n")
    mirror_page(layout, "mirror/s/deep/also-uncovered.md", body="# deep\n")
    mirror_page(layout, "mirror/s/gone.md", status=PageStatus.DELETED)
    mirror_page(layout, "mirror/s/old.md", status=PageStatus.SUPERSEDED)
    mirror_page(layout, "mirror/s/locked.md", status=PageStatus.REFUSED)
    (layout.root / "mirror" / "CLAUDE.md").write_text("# guide\n", encoding="utf-8")
    sidecar = layout.root / "mirror" / "s" / "cited.md.d"
    sidecar.mkdir()
    (sidecar / "note.md").write_text("no frontmatter\n", encoding="utf-8")
    topic_page(layout, "topics/p.md", "entity: e\n" + sources_yaml(("../mirror/s/cited.md", pin, "primary")))
    rows, _entities, _findings = curate.generate_depends(layout)
    assert curate.uncovered_mirror_pages(layout, rows) == [
        "mirror/s/deep/also-uncovered.md",
        "mirror/s/uncovered.md",
    ]
    assert curate.uncovered_mirror_pages(layout, []) == [
        "mirror/s/cited.md",
        "mirror/s/deep/also-uncovered.md",
        "mirror/s/uncovered.md",
    ]


def test_uncovered_mirror_pages_without_mirror_dir(tmp_path: Path) -> None:
    assert curate.uncovered_mirror_pages(DocsLayout(root=tmp_path / "none"), []) == []
