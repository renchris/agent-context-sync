from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agentsync import lints
from agentsync.config import Config
from agentsync.curate import refresh_queue
from agentsync.frontmatter import (
    MIRROR_KEY_ORDER,
    Bare,
    FrontmatterError,
    MirrorFrontmatter,
    parse_frontmatter,
    parse_mirror_page,
    render_frontmatter,
    render_mirror_page,
    split_frontmatter,
    validate_mirror_frontmatter,
)
from agentsync.model import PageStatus
from agentsync.paths import DocsLayout
from agentsync.policy import CONTENT_TRUST_KEY, CONTENT_TRUST_VALUE
from test_e2e import porcelain, run

H = "a" * 64


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def current(body: str = "# Title\n\nbody\n", **kw: object) -> MirrorFrontmatter:
    base: dict[str, object] = {
        "source_kind": "local",
        "source_id": "local-fixture",
        "stable_id": "UUID-1:42",
        "source_path": "projects/FY26 Budget.xlsx",
        "status": PageStatus.CURRENT,
        "content_sha256": H,
        "canonical_sha256": "b" * 64,
        "rendered_sha256": sha(body),
        "part_kind": "sheet",
        "part_name": "Q3 Budget",
        "unit_index": 1,
        "unit_of": 2,
        "converter": "xlsx-openpyxl@1.0.0+openpyxl-3.1.5",
        "options_hash": "sha256:" + "c" * 64,
        "content_trust": CONTENT_TRUST_VALUE,
        "source_title": "FY26 Budget — Q3",
        "summary": 'Q3 budget: "spend", forecast',
        "tokens_estimate": 42,
    }
    base.update(kw)
    return MirrorFrontmatter(**base)  # type: ignore[arg-type]


def test_render_is_deterministic_and_awk_readable() -> None:
    body = "# Title\n\nbody\n"
    a = render_mirror_page(current(body), body)
    b = render_mirror_page(current(body), body)
    assert a == b
    lines = a.splitlines()
    assert lines[0] == "---"
    assert f"rendered_sha256: {sha(body)}" in lines
    assert "status: current" in lines
    assert 'part: {kind: sheet, name: "Q3 Budget", index: 1, of: 2}' in lines
    assert 'source_title: "FY26 Budget — Q3"' in lines  # unicode kept, JSON-quoted
    assert f"content_trust: {CONTENT_TRUST_VALUE}" in lines  # bare, one spelling
    assert a.endswith(body)


def test_content_trust_is_in_the_contract_and_optional() -> None:
    assert CONTENT_TRUST_KEY == "content_trust"
    order = MIRROR_KEY_ORDER
    assert order.index("sensitivity_label") < order.index("content_trust") < order.index("status")
    body = "# T\n"
    with_trust = render_mirror_page(current(body), body)
    without = render_mirror_page(current(body, content_trust=None), body)
    assert "content_trust" not in without  # pages written before the field existed stay valid
    assert parse_mirror_page(without)[0].content_trust is None
    assert with_trust.split("---\n", 2)[2] == without.split("---\n", 2)[2]  # the body (H2 input) is unchanged
    assert validate_mirror_frontmatter(parse_frontmatter(with_trust)[0]) == []
    bad = with_trust.replace(f"content_trust: {CONTENT_TRUST_VALUE}", "content_trust: 3")
    assert "content_trust is not a non-empty string" in validate_mirror_frontmatter(parse_frontmatter(bad)[0])


def test_round_trip() -> None:
    body = "# T\n"
    fm = current(body)
    parsed, parsed_body = parse_mirror_page(render_mirror_page(fm, body))
    assert parsed == fm
    assert parsed_body == body


def test_tombstone_contract() -> None:
    fm = MirrorFrontmatter(
        source_kind="graph_drive",
        source_id="lib",
        stable_id="01ABC",
        source_path="a.docx",
        status=PageStatus.DELETED,
        deleted_at="2026-09-29",
        last_rendered_sha256=H,
        last_commit="0123456789abcdef0123456789abcdef01234567",
    )
    text = render_mirror_page(fm, "# [DELETED UPSTREAM] a.docx\n")
    parsed, _ = parse_mirror_page(text)
    assert parsed.deleted_at == "2026-09-29"


def test_validation_reports_every_problem() -> None:
    problems = validate_mirror_frontmatter(
        {"source_kind": "local", "status": "current", "rendered_sha256": "abc", "converted_at": "now"}
    )
    joined = "; ".join(problems)
    assert "unknown key(s): converted_at" in joined
    assert "missing required key 'stable_id'" in joined
    assert "rendered_sha256 is not 64 lowercase hex" in joined
    with pytest.raises(FrontmatterError):
        parse_mirror_page("---\nstatus: nope\n---\nbody\n")


def test_split_and_parse_edge_cases() -> None:
    assert split_frontmatter("no frontmatter") == (None, "no frontmatter")
    with pytest.raises(FrontmatterError):
        split_frontmatter("---\nkey: 1\nno close\n")
    data, body = parse_frontmatter("---\nreviewed_at: 2026-09-21\n---\nx\n")
    assert data == {"reviewed_at": "2026-09-21"} and body == "x\n"
    assert (
        render_frontmatter({"a": Bare("true"), "b": None, "c": Bare("abc")})
        == '---\na: "true"\nc: abc\n---\n'
    )


def test_refresh_queue_reads_rendered_pages(tmp_path: Path) -> None:
    """Design 4.5 fixture: one page, one fresh source, one stale source, one tombstone."""
    repo = tmp_path / "docs"
    (repo / "mirror" / "s").mkdir(parents=True)
    fresh_body, stale_body = "# fresh\n", "# changed\n"
    (repo / "mirror/s/fresh.md").write_text(render_mirror_page(current(fresh_body), fresh_body))
    (repo / "mirror/s/stale.md").write_text(render_mirror_page(current(stale_body), stale_body))
    tomb = MirrorFrontmatter(
        source_kind="local",
        source_id="s",
        stable_id="v:9",
        source_path="gone.docx",
        status=PageStatus.DELETED,
        deleted_at="2026-09-29",
        last_rendered_sha256=H,
        content_trust=CONTENT_TRUST_VALUE,
    )
    (repo / "mirror/s/gone.md").write_text(render_mirror_page(tomb, "# [DELETED UPSTREAM] gone\n"))
    old_pin = sha("# old\n")
    rows = [
        "page\tsource\tpinned_sha\trole",
        f"topics/p.md\tmirror/s/fresh.md\t{sha(fresh_body)}\tprimary",
        f"topics/p.md\tmirror/s/stale.md\t{old_pin}\tprimary",
        f"topics/p.md\tmirror/s/gone.md\t{H}\tcorroborating",
    ]
    (repo / "DEPENDS.tsv").write_text("\n".join(rows) + "\n")
    rc, verdicts = refresh_queue(DocsLayout(root=repo))
    assert rc == 1
    assert [v.line() for v in verdicts] == [
        "SOURCE-DELETED\ttopics/p.md\tmirror/s/gone.md",
        "STALE\ttopics/p.md\tmirror/s/stale.md",
    ]


_UNSAFE = ["Contoso\x92s", "note\x7fone", "a\x85b", "a\u2028b", "a\u2029b", "x\ufffey", "lone \udc80 half"]


@pytest.mark.parametrize("text", _UNSAFE)
def test_a_character_yaml_refuses_or_folds_reads_back_as_written(text: str) -> None:
    """B13 round 9 K74: ``json.dumps`` writes DEL, a C1 control, U+FFFE/FFFF and a lone surrogate raw, which
    YAML refuses, and U+0085, U+2028 and U+2029, which YAML folds to a space. Each is written ``\\uXXXX`` now,
    and both the YAML reader and the ``reason:`` line's JSON read give the string back."""
    rendered = render_frontmatter({"source_title": text, "part": {"name": text}})
    fm, _body = parse_frontmatter(rendered + "body\n")
    assert (fm["source_title"], fm["part"]) == (text, {"name": text})
    line = rendered.splitlines()[1]
    assert json.loads(line.removeprefix("source_title: ")) == text
    assert line.isascii()


def test_a_plain_title_renders_as_it_did() -> None:
    """K74 changes the bytes of a page only where it holds such a character."""
    fields = {"source_title": 'Q3 "plan" \\ 日本 😀', "source_path": "projects/Q3 plan.docx"}
    assert render_frontmatter(fields) == (
        '---\nsource_title: "Q3 \\"plan\\" \\\\ 日本 😀"\nsource_path: "projects/Q3 plan.docx"\n---\n'
    )


_EML = (
    "From: Alex Example <alex@contoso.example>\r\n"
    "To: Sam Example <sam@contoso.example>\r\n"
    "Subject: =?iso-8859-1?Q?Contoso=92s_Q3_plan?=\r\n"
    "Date: Mon, 12 Oct 2026 10:00:00 +0000\r\n"
    "Message-ID: <q3-plan-1@contoso.example>\r\n"
    "MIME-Version: 1.0\r\n"
    "Content-Type: text/plain; charset=iso-8859-1\r\n"
    "Content-Transfer-Encoding: quoted-printable\r\n"
    "\r\n"
    "Hello, here is the plan=92s summary.\r\n"
)


@pytest.mark.parametrize(
    ("name", "data"),
    [("q3-plan.eml", _EML.encode("ascii")), ("note\x7fone.md", b"# Hello\n\nbody text\n")],
    ids=["a C1 control in a mail subject", "DEL in a file name"],
)
def test_a_control_character_in_a_title_or_name_holds_no_sync(
    sample_config: Config, local_source_dir: Path, name: str, data: bytes
) -> None:
    """B13 round 9 K74: one page whose title or file name held such a character failed the land gate's
    FRONTMATTER check, which is repo-wide, so no source's work was committed, on any run, until the item
    was renamed. It syncs now, and a second run changes nothing."""
    (local_source_dir / "projects" / name).write_bytes(data)
    first = run(sample_config)
    assert (first.exit_code, first.commit_sha is not None) == (0, True)
    assert [f for f in lints.run_land_gate(sample_config.docs_repo, []) if f.blocking] == []
    second = run(sample_config)
    assert (second.exit_code, second.commit_sha, porcelain(sample_config.docs_repo)) == (0, None, "")
