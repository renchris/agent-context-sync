"""Curation (design 4.5, 10 week 0 / week 3): topics/ parsing, DEPENDS.tsv, refresh queue, banners, adopt."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

import agentsync.curate as curate
from agentsync import gitops
from agentsync.curate import (
    BY_ENTITY_HEADER,
    DEPENDS_HEADER,
    HAND_WRITTEN,
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
from test_recording_grammar import EXAMPLE

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
        "kind: topic\nentity: acme\npurpose: Acme terms.\n"
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
        "entity: acme\npurpose: Acme scope.\n" + sources_yaml(("../../../mirror/s/bad.md", H, "primary")),
    )
    topic_page(
        layout,
        "topics/decisions/d1.md",
        "entity: platform\npurpose: A decision.\n"
        + sources_yaml(("../../mirror/s/fresh.md", pins["fresh"], "primary")),
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
        ("topics/noentity.md", "MISSING-PURPOSE"),
        ("topics/p.md", "BAD-PIN"),
        ("topics/p.md", "BAD-PIN"),
        ("topics/p.md", "BAD-ROLE"),
        ("topics/p.md", "BROKEN-PAGE-EDGE"),
        ("topics/p.md", "BROKEN-PAGE-EDGE"),
        ("topics/p.md", "CURATE-PARSE"),
        ("topics/p.md", "DUPLICATE-SOURCE"),
        ("topics/p.md", "MISSING-PURPOSE"),
        *[("topics/p.md", "SOURCE-MISSING")] * 5,  # b, c, d, e and topics/other.md do not exist
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
# refresh queue (the design 4.5 awk script's semantics; the script itself was retired in KISS K09b)
# ---------------------------------------------------------------------------------------------------------


def test_design_fixture_exactly_one_stale_and_one_deleted(layout: DocsLayout) -> None:
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


def test_full_corpus_verdicts(layout: DocsLayout) -> None:
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


def test_all_fresh_is_rc_zero(layout: DocsLayout) -> None:
    pin = mirror_page(layout, "mirror/s/a.md")
    topic_page(layout, "topics/p.md", "entity: e\n" + sources_yaml(("../mirror/s/a.md", pin, "primary")))
    write_depends(layout, generate_depends(layout)[0])
    assert twin(layout) == (0, [])


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
    ("tsv", "expected"),
    [
        pytest.param(None, (2, []), id="missing"),
        pytest.param(b"", (2, []), id="empty"),
        pytest.param(b"\n", (2, []), id="blank-first-line"),
        pytest.param(b"pages\tsource\n", (2, []), id="bad-header"),
        pytest.param(
            b"topics/p.md\tmirror/raw/nofence.md\t" + H.encode() + b"\tprimary\n", (2, []), id="headerless"
        ),
        pytest.param(DEPENDS_HEADER.encode(), (0, []), id="header-only-no-newline"),
        pytest.param(DEPENDS_HEADER.encode() + b"\n", (0, []), id="header-only"),
        pytest.param(
            b"page\tsource\n\nx\ty\none\n\t\t\n" + b"q\tmirror/raw/nofence.md\t" + H.encode(),
            (
                1,
                [
                    "MALFORMED\t\t(fields=0)",
                    "MALFORMED\tone\t(fields=1)",
                    "MALFORMED\tx\t(fields=2)",
                    "UNPINNED\t\t",
                ],
            ),
            id="malformed-mix",
        ),
        pytest.param(
            DEPENDS_HEADER.encode()
            + b"\n"
            + b"".join(
                b"topics/p.md\t" + name.encode() + b"\t" + H.encode() + b"\tprimary\n"
                for name in sorted([*RAW_MIRROR_FILES, "mirror/raw/absent.md"])
            ),
            (
                1,
                [
                    "MISSING-OR-UNPARSEABLE\ttopics/p.md\tmirror/raw/absent.md",
                    "MISSING-OR-UNPARSEABLE\ttopics/p.md\tmirror/raw/quoted.md",
                    "SOURCE-DELETED\ttopics/p.md\tmirror/raw/deleted-no-sha.md",
                    "SOURCE-UNREADABLE\ttopics/p.md\tmirror/raw/refused.md",
                    "STALE\ttopics/p.md\tmirror/raw/crlf.md",
                    "STALE\ttopics/p.md\tmirror/raw/extra-space.md",
                ],
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
            (
                1,
                [  # sort -u in C-locale byte order
                    "BAD-PIN\ttopics/a.md\tmirror/x.md\t(len=6)",
                    "BAD-PIN\ttopics/a.md\tmirror/x.md\t(len=64)",
                    "BAD-PIN\ttopics/a.md\tmirror/x.md\t(len=65)",
                    "BAD-PIN\ttopics/a.md\tmirror/x.md\t(len=9)",
                    "UNPINNED\tTopics/Z.md\tmirror/x.md",
                    "UNPINNED\ttopics/a.md\tmirror/x.md",
                    "UNPINNED\ttopics/b.md\tmirror/x.md",
                    "UNPINNED\ttopics/caf\u00e9.md\tmirror/x.md",
                ],
            ),
            id="pins-and-order",
        ),
    ],
)
def test_queue_verdicts_and_rc(
    layout: DocsLayout, tsv: bytes | None, expected: tuple[int, list[str]]
) -> None:
    """Pinned from the design 4.5 awk script's output (LC_ALL=C) before it was retired in KISS K09b."""
    for rel, data in RAW_MIRROR_FILES.items():
        (layout.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (layout.root / rel).write_bytes(data)
    if tsv is not None:
        layout.depends_tsv.write_bytes(tsv)
    assert twin(layout) == expected


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


def test_empty_source_column_is_reported(layout: DocsLayout) -> None:
    """Where the awk script died on ``getline < ""`` and dropped every later row, each row is reported."""
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


def test_directory_source_does_not_stop_the_queue(layout: DocsLayout) -> None:
    """The awk script died on a directory source; the queue must not read that as 'all fresh'."""
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


def test_generator_never_emits_a_directory_source(layout: DocsLayout) -> None:
    pin = mirror_page(layout, "mirror/s/book.xlsx.d/01-q3.md", "# q3\n")
    topic_page(
        layout,
        "topics/p.md",
        "entity: e\npurpose: Q3.\n"
        + sources_yaml(
            ("../mirror/s/book.xlsx.d", H, "primary"), ("../mirror/s/book.xlsx.d/01-q3.md", pin, "primary")
        ),
    )
    rows, _, findings = generate_depends(layout)
    assert [r.source for r in rows] == ["mirror/s/book.xlsx.d/01-q3.md"]
    assert [(f.code, f.blocking) for f in findings] == [("SOURCE-IS-DIRECTORY", False)]
    write_depends(layout, rows)
    assert twin(layout) == (0, [])


def test_absolute_source_is_read_as_is(layout: DocsLayout, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text(f"---\nrendered_sha256: {H}\n---\n", encoding="utf-8")
    layout.depends_tsv.write_text(
        f"{DEPENDS_HEADER}\ntopics/a.md\t{outside}\t{H}\tprimary\n", encoding="utf-8"
    )
    assert twin(layout) == (0, [])


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


def test_week3_loop_generate_write_queue_banner(layout: DocsLayout) -> None:
    build_corpus(layout)
    rows, entities, findings = generate_depends(layout)
    assert write_depends(layout, rows) and write_by_entity(layout, entities)
    assert findings == [] and lint_unlisted_pages(layout, rows) == []
    rc, verdicts = refresh_queue(layout)
    assert rc == 1 and [v.verdict for v in verdicts] == ["SOURCE-DELETED", "SOURCE-UNREADABLE", "STALE"]
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


@pytest.mark.parametrize(
    "rel", ["mirror/s/plain-page.txt.md", "mirror/s/q1, q2 & more.md", "mirror/s/it's 100%: ok.md"]
)
def test_source_entry_pastes_into_a_lint_clean_page(layout: DocsLayout, rel: str) -> None:
    """KISS K09: the ready-made entry YAML reads back to the page's path and current pin, quoted only when a
    plain flow scalar would misread the path, and a page citing it is lint-clean and fresh."""
    pin = mirror_page(layout, rel)
    entry = curate.source_entry(layout, rel)
    assert entry is not None
    assert yaml.safe_load(entry) == {"path": rel, "at_rendered_sha256": pin, "role": "primary"}
    assert entry.startswith(f"{{path: {rel}, ") == (rel == "mirror/s/plain-page.txt.md")
    topic_page(layout, "topics/p.md", f"entity: e\npurpose: p\nsources:\n  - {entry}\n")
    rows, _entities, findings = curate.generate_depends(layout)
    assert findings == [] and [r.source for r in rows] == [rel]
    assert write_depends(layout, rows) and refresh_queue(layout) == (0, [])


def test_source_entry_only_for_curatable_mirror_pages(layout: DocsLayout) -> None:
    mirror_page(layout, "mirror/s/gone.md", status=PageStatus.DELETED)
    mirror_page(layout, "mirror/s/locked.md", status=PageStatus.REFUSED)
    (layout.root / "mirror" / "CLAUDE.md").write_text("# guide\n", encoding="utf-8")
    topic_page(layout, "topics/p.md", "entity: e\n")
    rels = ("mirror/s/gone.md", "mirror/s/locked.md", "mirror/CLAUDE.md", "mirror/s/none.md", "topics/p.md")
    for rel in rels:
        assert curate.source_entry(layout, rel) is None, rel


def test_uncovered_mirror_pages_without_mirror_dir(tmp_path: Path) -> None:
    assert curate.uncovered_mirror_pages(DocsLayout(root=tmp_path / "none"), []) == []


def test_missing_purpose_and_page_budget_are_warnings(layout: DocsLayout) -> None:
    topic_page(layout, "topics/a/ok.md", "entity: a\npurpose: Terms; not pricing.\n")
    topic_page(layout, "topics/a/nopurpose.md", "entity: a\n")
    topic_page(layout, "topics/a/huge.md", "entity: a\npurpose: Everything.\n", "line\n" * 401)
    topic_page(layout, "topics/a/adopted.md", "provenance: hand-written\nsources: []\n", "x" * 25_001)
    _rows, _entities, findings = generate_depends(layout)
    assert [(f.path, f.code) for f in findings] == [
        ("topics/a/adopted.md", "TOPIC-BUDGET"),
        ("topics/a/huge.md", "TOPIC-BUDGET"),
        ("topics/a/nopurpose.md", "MISSING-PURPOSE"),
    ]
    assert not any(f.blocking for f in findings)
    assert "split it" in findings[1].message


# ---------------------------------------------------------------------------------------------------------
# docs-root sources, SOURCE-MISSING, checkpoint blockers (KISS K10)
# ---------------------------------------------------------------------------------------------------------

GOOD = "entity: e\npurpose: Terms; not pricing.\n"


@pytest.mark.parametrize(
    ("page", "src", "expected"),
    [
        ("topics/p.md", "mirror/s/x.md", "mirror/s/x.md"),
        ("topics/a/b/c/p.md", "mirror/s/x.md", "mirror/s/x.md"),
        ("topics/a/p.md", "archive/s/x.md", "archive/s/x.md"),
        ("topics/a/p.md", "../../mirror/s/x.md", "mirror/s/x.md"),  # page-relative still resolves
        ("topics/a/p.md", "./mirror/s/x.md", "topics/a/mirror/s/x.md"),  # ./ keeps it page-relative
        ("topics/a/p.md", "sibling.md", "topics/a/sibling.md"),
    ],
)
def test_resolve_source_path(page: str, src: str, expected: str) -> None:
    assert curate.resolve_source_path(page, src) == expected


def test_resolve_source_path_still_rejects_escapes() -> None:
    with pytest.raises(CurateError, match="escapes"):
        curate.resolve_source_path("topics/p.md", "mirror/../../x.md")
    assert normalise_source_path("topics/a/p.md", "mirror/s/x.md") == "topics/a/mirror/s/x.md"


def test_docs_root_sources_and_archive_are_clean(layout: DocsLayout) -> None:
    pin = mirror_page(layout, "mirror/s/a.md")
    old = mirror_page(layout, "archive/s/old.md", "# old\n")
    topic_page(layout, "topics/a/b/sib.md", GOOD + sources_yaml(("mirror/s/a.md", pin, "primary")))
    topic_page(
        layout,
        "topics/a/b/p.md",
        GOOD
        + sources_yaml(("mirror/s/a.md", pin, "primary"), ("archive/s/old.md", old, "corroborating"))
        + "depends_on_pages: [sib.md]\n",  # page-relative, unlike sources:
    )
    rows, _entities, findings = generate_depends(layout)
    assert findings == []
    assert [(r.page, r.source) for r in rows] == [
        ("topics/a/b/p.md", "archive/s/old.md"),
        ("topics/a/b/p.md", "mirror/s/a.md"),
        ("topics/a/b/sib.md", "mirror/s/a.md"),
    ]
    assert curate.checkpoint_blockers(layout.root) == []


def test_unlisted_message_offers_no_hand_written_escape(layout: DocsLayout) -> None:
    topic_page(layout, "topics/nosources.md", GOOD)
    (finding,) = lint_unlisted_pages(layout, generate_depends(layout)[0])
    assert finding.code == "UNLISTED" and "hand-written" not in finding.message


def test_checkpoint_blockers_wrong_pin_holds_and_source_state_does_not(layout: DocsLayout) -> None:
    pin = mirror_page(layout, "mirror/s/a.md")
    mirror_page(layout, "mirror/s/unreadable.md", status=PageStatus.UNREADABLE)
    gone = mirror_page(layout, "mirror/s/gone.md", status=PageStatus.DELETED)
    topic_page(layout, "topics/wrong-pin.md", GOOD + sources_yaml(("mirror/s/a.md", H, "primary")))
    topic_page(layout, "topics/unreadable.md", GOOD + sources_yaml(("mirror/s/unreadable.md", H, "primary")))
    topic_page(layout, "topics/gone.md", GOOD + sources_yaml(("mirror/s/gone.md", gone, "primary")))
    topic_page(layout, "topics/typo.md", GOOD + sources_yaml(("mirror/s/typo.md", pin, "primary")))
    topic_page(layout, "topics/huge.md", GOOD + sources_yaml(("mirror/s/a.md", pin, "primary")), "x\n" * 401)
    blockers = curate.checkpoint_blockers(layout.root)
    assert [(f.path, f.code) for f in blockers] == [
        (
            "topics/typo.md",
            "SOURCE-MISSING",
        ),  # names the MISSING-OR-UNPARSEABLE verdict, which is not repeated
        ("topics/wrong-pin.md", "STALE"),
    ]
    assert all(f.blocking for f in blockers)
    assert "mirror/s/a.md" in blockers[1].message
    # the land gate's own findings stay non-blocking
    assert not any(f.blocking for f in generate_depends(layout)[2])


def test_checkpoint_blockers_scope_verdicts_to_pages_changed_since_curated(layout: DocsLayout) -> None:
    repo = layout.root
    gitops.ensure_repo(repo)
    mirror_page(layout, "mirror/s/a.md")
    topic_page(layout, "topics/old-stale.md", GOOD + sources_yaml(("mirror/s/a.md", H, "primary")))
    topic_page(layout, "topics/edited.md", GOOD + sources_yaml(("mirror/s/a.md", H, "primary")))
    topic_page(layout, "topics/nopurpose.md", "entity: e\n" + sources_yaml(("mirror/s/a.md", H, "primary")))
    gitops.run_git(repo, "add", "-A")
    gitops.run_git(repo, "commit", "-q", "-m", "pages")
    gitops.tag_curated(repo, "HEAD")
    topic_page(layout, "topics/edited.md", GOOD + sources_yaml(("mirror/s/a.md", H, "primary")), "# new\n")
    topic_page(layout, "topics/new.md", GOOD + sources_yaml(("mirror/s/a.md", H, "primary")))
    assert [(f.path, f.code) for f in curate.checkpoint_blockers(repo)] == [
        ("topics/edited.md", "STALE"),
        ("topics/new.md", "STALE"),
        ("topics/nopurpose.md", "MISSING-PURPOSE"),  # lint findings hold whatever page they are on
    ]


def _commit(repo: Path, message: str) -> None:
    gitops.run_git(repo, "add", "-A")
    gitops.run_git(repo, "commit", "-q", "-m", message)


def test_checkpoint_blockers_hold_a_page_committed_after_curated(layout: DocsLayout) -> None:
    repo = layout.root
    gitops.ensure_repo(repo)
    mirror_page(layout, "mirror/s/a.md")
    topic_page(layout, "topics/old-stale.md", GOOD + sources_yaml(("mirror/s/a.md", H, "primary")))
    _commit(repo, "pages")
    gitops.tag_curated(repo, "HEAD")
    topic_page(layout, "topics/later.md", GOOD + sources_yaml(("mirror/s/a.md", H, "primary")))
    _commit(repo, "later")  # clean working tree: only the commit since `curated` shows the page changed
    assert [(f.path, f.code) for f in curate.checkpoint_blockers(repo)] == [("topics/later.md", "STALE")]


def test_checkpoint_blockers_skip_a_vanished_source_on_an_unchanged_page(layout: DocsLayout) -> None:
    repo = layout.root
    gitops.ensure_repo(repo)
    pin = mirror_page(layout, "mirror/s/a.md")
    topic_page(layout, "topics/old.md", GOOD + sources_yaml(("mirror/s/a.md", pin, "primary")))
    _commit(repo, "pages")
    gitops.tag_curated(repo, "HEAD")
    (repo / "mirror/s/a.md").unlink()  # a OneDrive rename, a tombstone reap or a purge: not the page's fault
    _commit(repo, "source gone")
    assert curate.checkpoint_blockers(repo) == []
    assert [(f.path, f.code) for f in generate_depends(layout)[2]] == [("topics/old.md", "SOURCE-MISSING")]
    topic_page(layout, "topics/old.md", GOOD + sources_yaml(("mirror/s/a.md", pin, "primary")), "# edit\n")
    assert [(f.path, f.code) for f in curate.checkpoint_blockers(repo)] == [
        ("topics/old.md", "SOURCE-MISSING")
    ]


def test_source_missing_names_a_file_without_a_mirror_head(layout: DocsLayout) -> None:
    pin = mirror_page(layout, "mirror/s/a.md")
    (layout.mirror / "s" / "CLAUDE.md").write_text("# guide, no frontmatter\n", encoding="utf-8")
    topic_page(
        layout,
        "topics/p.md",
        GOOD + sources_yaml(("mirror/s/a.md", pin, "primary"), ("mirror/s/CLAUDE.md", pin, "corroborating")),
    )
    (finding,) = generate_depends(layout)[2]
    assert (finding.code, finding.path) == ("SOURCE-MISSING", "topics/p.md")
    assert "mirror/s/CLAUDE.md" in finding.message
    blockers = curate.checkpoint_blockers(layout.root)
    assert [(f.path, f.code) for f in blockers] == [("topics/p.md", "SOURCE-MISSING")]


# ---------------------------------------------------------------------------------------------------------
# meeting citation lint (meeting-video spec 7.4)
# ---------------------------------------------------------------------------------------------------------

REC = "mirror/onedrive/recordings/contoso-review.mp4.d"
MEETING = "topics/meetings/2026-10-02-contoso-review.md"
WINDOW_1 = """# Recording 00:00:00-00:05:00 · window 1 of 2

## 00:00:00-00:04:12 · s001 · camera
[00:00:00] KEYFRAME: t000000.jpg
[00:00:00] TILE: Dana Okafor
[00:01:10] SAID v2: my name is Dana Okafor, I look after capacity planning
[00:01:14] SAID v1: thanks Dana, let us start with the forecast

## 00:04:12-00:05:38 · s004 · share · "Demand forecast by region"
[00:04:12] KEYFRAME: t000412.jpg
[00:04:12] SCREEN: Demand forecast by region
[00:04:12] SCREEN: West | 31 %
"""
# The spec's window 2, with a SPEAKING line inside s004, the state whose keyframe is in window 1.
WINDOW_2 = EXAMPLE.replace("[00:05:06] SAID", "[00:05:02] SPEAKING: Dana Okafor\n[00:05:06] SAID")
D1 = (
    '| D1 | Q3 budget becomes 1,310,000 USD. | `seen+frame 00:08:46` "Q3 | B | 118 | 1,310,000" · '
    '`heard 00:08:58` "okay, one point three one, I can live with that" |'
)
PAGE = f"""# Contoso FY27 storage capacity review, 2026-10-02

Recording `r1` = `{REC}/`. Times are media time.

## Decisions
| # | Decision | Evidence |
|---|---|---|
{D1}

## Action items
| Owner | Action | Due | Evidence |
|---|---|---|---|
| Dana Okafor | Re-send the sheet | Friday | `heard 00:05:52` "this is the sheet ... mailed on   Tuesday" |
| Mei Tanaka | Book the cluster review | none | `chat ~00:41` "I'll book it" |

## Numbers shown
| Figure | As shown | Said as | Evidence |
|---|---|---|---|
| Q3 budget | 1,310,000 | one point three one | `seen+frame 00:08:46` "1,310,000" |
| Growth | 9 % | | `seen 00:05:38` "Assumes 9 % growth, West migration lands in Q3" · picture not kept |
| Presenter | Dana Okafor | | `seen+frame 00:05:02` "Dana Okafor" |

## Open questions
- Does tier B hold? `inferred` from `heard 00:08:58` "if tier B holds".

## People
| Person | Voice | Basis |
|---|---|---|
| Dana Okafor | v2 | `heard 00:01:10` "my name is Dana Okafor" |
| Luis Ferreira | v1 | voice 1, on shared audio of Room 4 |
| Mei Tanaka | v3 | voice 3, unidentified |
| Priya Raman | v4 | `VOICE line` |

## Verification log
- Keyframes opened: t000412, t000846.jpg.
"""


def unit_page(layout: DocsLayout, rel: str, body: str, kind: str, index: int, of: int) -> str:
    """Write one recording unit (``part: {kind, index, of}``); return its pin."""
    fm = MirrorFrontmatter(
        source_kind="local",
        source_id="s",
        stable_id="v:rec",
        source_path=rel,
        status=PageStatus.CURRENT,
        content_sha256=H,
        canonical_sha256="b" * 64,
        rendered_sha256=sha(body),
        part_kind=kind,
        unit_index=index,
        unit_of=of,
        converter="recording@1",
        options_hash="sha256:" + "c" * 64,
        summary="s",
        tokens_estimate=3,
    )
    path = layout.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_mirror_page(fm, body), encoding="utf-8")
    return sha(body)


def recording(
    layout: DocsLayout,
    folder: str = REC,
    windows: tuple[str, ...] = (WINDOW_1, WINDOW_2),
    stems: tuple[str, ...] = (),
) -> list[tuple[str, str, str]]:
    """An index unit and one window unit per body; ``stems`` overrides the window file names."""
    index = f"{folder}/00-index.md"
    entries = [(index, unit_page(layout, index, "# Recording index\n", "index", 0, len(windows)), "primary")]
    for n, body in enumerate(windows, 1):
        rel = f"{folder}/{stems[n - 1] if stems else f'{n:02d}-t00{(n - 1) * 5:02d}00'}.md"
        entries.append((rel, unit_page(layout, rel, body, "window", n, len(windows)), "primary"))
    return entries


def meeting(
    layout: DocsLayout, body: str, entries: list[tuple[str, str, str]], kind: str = "meeting"
) -> None:
    frontmatter = f"kind: {kind}\nentity: contoso-review\npurpose: what the review decided\n"
    topic_page(layout, MEETING, frontmatter + sources_yaml(*entries), body)


def cite_codes(layout: DocsLayout, body: str, entries: list[tuple[str, str, str]]) -> list[str]:
    meeting(layout, body, entries)
    findings = curate.lint_meeting_citations(layout)
    assert all(f.path == MEETING and not f.blocking for f in findings)
    return [f.code for f in findings]


def test_cite_codes_are_named_in_rule_order() -> None:
    assert curate.CITE_CODES == (
        "CITE-UNRESOLVED", "CITE-QUOTE", "CITE-MISSING", "CITE-FRAME", "CITE-INFERRED", "CITE-BASIS",
        "CITE-SHARED",
    )  # fmt: skip


def test_rule_1_a_tag_is_a_backticked_class_recording_and_time(layout: DocsLayout) -> None:
    """No ``rN`` means r1; a tag inside a quote is text."""
    entries = recording(layout)
    passing = PAGE.replace('`heard 00:08:58` "okay', '`heard r1 00:08:58` "okay') + (
        '\nA reader wrote "see `heard 00:00:59` there", which is quoted text.\n'
    )
    assert cite_codes(layout, passing, entries) == []
    failing = PAGE.replace('`heard 00:08:58` "okay', '`heard r2 00:08:58` "okay')
    assert cite_codes(layout, failing, entries) == ["CITE-UNRESOLVED"]


def test_rule_2_the_window_unit_must_be_in_sources(layout: DocsLayout) -> None:
    entries = recording(layout)
    assert cite_codes(layout, PAGE, entries) == []
    codes = cite_codes(layout, PAGE, [e for e in entries if not e[0].endswith("02-t000500.md")])
    assert codes and set(codes) == {"CITE-UNRESOLVED"}  # the unit is on disk, but not in sources:


def test_rule_3_the_unit_holds_a_line_of_the_tags_channel(layout: DocsLayout) -> None:
    """A heard tag resolves to a transcript page's SAID line too."""
    transcript = "mirror/onedrive/recordings/contoso-review.vtt.md"
    pin = mirror_page(layout, transcript, "[00:12:00] SAID Dana Okafor: let us wrap up here\n")
    entries = [*recording(layout), (transcript, pin, "corroborating")]
    passing = PAGE + '\n- Close: `heard 00:12:00` "let us wrap up".\n'
    assert cite_codes(layout, passing, entries) == []
    failing = PAGE.replace('`seen+frame 00:08:46` "Q3', '`seen+frame 00:08:58` "Q3')  # 08:58 is a SAID
    assert cite_codes(layout, failing, entries) == ["CITE-UNRESOLVED"]


def test_rule_4_the_quote_occurs_in_the_line(layout: DocsLayout) -> None:
    """Whitespace runs and ``...`` pieces match (the passing page has both); a changed word does not."""
    entries = recording(layout)
    assert cite_codes(layout, PAGE, entries) == []
    failing = PAGE.replace("one point three one, I can live with that", "one point three two")
    assert cite_codes(layout, failing, entries) == ["CITE-QUOTE"]


def test_the_ten_second_hint_is_still_a_finding(layout: DocsLayout) -> None:
    entries = recording(layout)
    page = PAGE + '\n- Opening: `heard 00:01:10` "let us start with the forecast".\n'
    meeting(layout, page, entries)
    (finding,) = curate.lint_meeting_citations(layout)
    assert finding.code == "CITE-QUOTE" and not finding.blocking
    assert "`heard 00:01:10`" in finding.message and "00:01:14, 4 s away" in finding.message


def test_rule_5_decision_action_and_number_rows_need_a_quoted_tag(layout: DocsLayout) -> None:
    """A chat tag with a quote counts (the passing page's second action item)."""
    entries = recording(layout)
    assert cite_codes(layout, PAGE, entries) == []
    failing = PAGE.replace('`heard 00:05:52` "this is the sheet ... mailed on   Tuesday"', "`heard 00:05:52`")
    assert cite_codes(layout, failing, entries) == ["CITE-MISSING"]


def test_rule_6_a_figure_needs_an_opened_keyframe(layout: DocsLayout) -> None:
    """The passing page's 00:05:02 state began in window 1: its keyframe t000412 is found there."""
    entries = recording(layout)
    assert cite_codes(layout, PAGE, entries) == []
    assert cite_codes(layout, PAGE.replace("t000412, ", ""), entries) == ["CITE-FRAME"]
    no_frame = PAGE.replace(" · picture not kept", "")
    assert cite_codes(layout, no_frame, entries) == ["CITE-FRAME"]


def test_rule_7_an_inference_names_the_tags_it_rests_on(layout: DocsLayout) -> None:
    entries = recording(layout)
    assert cite_codes(layout, PAGE, entries) == []
    failing = PAGE.replace('from `heard 00:08:58` "if tier B holds"', "from the mood in the room")
    assert cite_codes(layout, failing, entries) == ["CITE-INFERRED"]


def test_rule_8_a_people_basis_is_a_c11_form(layout: DocsLayout) -> None:
    entries = recording(layout)
    assert cite_codes(layout, PAGE, entries) == []
    failing = PAGE.replace("voice 3, unidentified", "sounds like Mei")
    assert cite_codes(layout, failing, entries) == ["CITE-BASIS"]
    shared = PAGE.replace("| Luis Ferreira | v1 |", "| Room  4 | v1 |")
    assert cite_codes(layout, shared, entries) == ["CITE-SHARED"]


def test_a_page_that_is_not_kind_meeting_is_not_linted(layout: DocsLayout) -> None:
    entries = recording(layout)
    failing = PAGE.replace("voice 3, unidentified", "sounds like Mei")
    meeting(layout, failing, entries, kind="client")
    assert curate.lint_meeting_citations(layout) == []


def test_a_window_is_found_by_its_unit_index_not_its_file_name(layout: DocsLayout) -> None:
    """Publish may suffix a stem; here the names even point at the wrong windows."""
    entries = recording(layout, stems=("02-t000500", "01-t000000-1"))
    assert cite_codes(layout, PAGE, entries) == []


def test_a_page_citing_r2_resolves_into_the_second_recording_folder(layout: DocsLayout) -> None:
    second = "# Recording 00:00:00-00:05:00 · window 1 of 1\n\n## 00:00:00-00:05:00 · s001 · camera\n"
    second += "[00:00:00] KEYFRAME: t000000.jpg\n[00:01:10] SAID v1: the second meeting opens here\n"
    entries = [
        *recording(layout),
        *recording(layout, "mirror/onedrive/recordings/contoso-2.mp4.d", (second,)),
    ]
    assert cite_codes(layout, PAGE + '\n- `heard r2 00:01:10` "second meeting opens".\n', entries) == []
    assert cite_codes(layout, PAGE + '\n- `heard 00:01:10` "second meeting opens".\n', entries) == [
        "CITE-QUOTE"
    ]


def test_the_citation_lint_is_never_part_of_generate_depends_or_the_checkpoint(layout: DocsLayout) -> None:
    meeting(layout, PAGE.replace("voice 3, unidentified", "sounds like Mei"), recording(layout))
    assert [f.code for f in curate.lint_meeting_citations(layout)] == ["CITE-BASIS"]
    assert not [f for f in generate_depends(layout)[2] if f.code.startswith("CITE-")]
    assert not [f for f in curate.checkpoint_blockers(layout.root) if f.code.startswith("CITE-")]
