from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from agentsync.curate import REFRESH_QUEUE_SH
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
from agentsync.policy import CONTENT_TRUST_KEY, CONTENT_TRUST_VALUE

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


def test_refresh_queue_script_reads_rendered_pages(tmp_path: Path) -> None:
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
    script = tmp_path / "refresh-queue.sh"
    script.write_text(REFRESH_QUEUE_SH)
    proc = subprocess.run(["/bin/sh", str(script)], cwd=repo, capture_output=True, text=True, check=False)
    assert proc.returncode == 1, proc.stderr
    assert proc.stdout.splitlines() == [
        "SOURCE-DELETED\ttopics/p.md\tmirror/s/gone.md",
        "STALE\ttopics/p.md\tmirror/s/stale.md",
    ]
