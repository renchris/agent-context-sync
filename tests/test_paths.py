from __future__ import annotations

from pathlib import Path

import pytest

from agentsync.paths import DocsLayout, glob_match, is_cloud_path, is_included, is_under


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("a/b/~$doc.docx", "~$*", True),
        ("~$doc.docx", "~$*", True),
        ("a/b/report.TMP", "*.tmp", True),
        ("a/b/report.docx", "*.tmp", False),
        ("a/b/c.pdf", "**/*.pdf", True),
        ("c.pdf", "**/*.pdf", True),
        ("a/b/c.pdf", "a/*.pdf", False),
        ("a/c.pdf", "a/*.pdf", True),
        ("archive/x/y.docx", "archive/", True),
        ("x/archive/y.docx", "archive/", True),  # gitignore: trailing-only slash matches at any depth
        ("x/archive/y.docx", "archive/**", False),
        ("x/archive/y.docx", "**/archive/**", True),
        ("q1.xlsx", "q[0-9].xlsx", True),
        ("qa.xlsx", "q[!0-9].xlsx", True),
        ("sub/Icon\r", "Icon\r", True),  # the macOS custom-icon file: the \r is part of the name
        ("Icon", "Icon\r", False),
    ],
)
def test_glob_match(path: str, pattern: str, expected: bool) -> None:
    assert glob_match(path, pattern) is expected


def test_is_included() -> None:
    assert is_included("a/b.docx", [], ["~$*"])
    assert not is_included("a/~$b.docx", [], ["~$*"])
    assert is_included("a/b.docx", ["*.docx"], [])
    assert not is_included("a/b.pdf", ["*.docx"], [])


def test_cloud_path_detection() -> None:
    assert is_cloud_path(Path("~/Library/CloudStorage/OneDrive-X/a"))
    assert not is_cloud_path(Path("~/agent-context/docs"))
    assert is_under(Path("/a/b/c"), Path("/a/b"))
    assert not is_under(Path("/a/bc"), Path("/a/b"))


def test_docs_layout_rel(tmp_path: Path) -> None:
    layout = DocsLayout(tmp_path / "docs")
    assert layout.rel(layout.mirror / "x" / "y.md") == "mirror/x/y.md"
    assert layout.state_md.relative_to(layout.root).as_posix() == "_sync/STATE.md"
    with pytest.raises(ValueError):
        layout.rel(tmp_path / "elsewhere")
