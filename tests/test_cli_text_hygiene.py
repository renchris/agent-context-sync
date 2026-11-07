"""Remote text is sanitized at its source module, and two messages say one thing one way."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentsync import cli
from agentsync.graph import auth, discover

ROOT = Path(__file__).resolve().parents[1]


def test_discover_comment_strips_c1_bidi_and_zero_width() -> None:
    got = discover._comment("a\x85b\u202ec\u200bd")
    assert not {"\x85", "\u202e", "\u200b"} & set(got)
    assert got == "abcd"


def test_describe_error_strips_escape_sequences() -> None:
    result = {
        "error": "invalid_grant",
        "error_description": "AADSTS50105: \x1b[31mnot assigned\x1b[0m\nTrace ID: 1234",
    }
    got = auth._describe_error(result, during="sign-in")
    assert "\x1b" not in got
    assert got.startswith("sign-in failed: invalid_grant AADSTS50105")
    assert "not assigned" in got and "Trace ID" not in got


def test_one_unknown_source_is_singular(
    tmp_path: Path, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(tmp_path / "ctx" / "sources.toml")
    assert cli.main(["add-source", str(local_source_dir), "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["accept-deletions", "nope", "--config", cfg]) == cli.EXIT_CONFIG
    assert "unknown source id: nope" in capsys.readouterr().err


def test_tenant_probes_teach_graph_login() -> None:
    probes = (ROOT / "scripts" / "tenant-probes.sh").read_text(encoding="utf-8")
    assert "agentsync login --device-code" not in probes
