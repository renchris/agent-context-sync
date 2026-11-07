"""fsutil.atomic_write: no fixed temp name, the old file safe on failure, the mode rule (§16.300.3)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agentsync import fsutil, governance, skill


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_symlink_at_the_old_fixed_temp_name_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("not yours\n", encoding="utf-8")
    target = tmp_path / "skills" / "SKILL.md"
    target.parent.mkdir()
    (target.parent / ".SKILL.md.agentsync-tmp").symlink_to(outside)

    assert skill._write_if_changed(target, "the skill\n") is True

    assert outside.read_text(encoding="utf-8") == "not yours\n"
    assert target.read_text(encoding="utf-8") == "the skill\n"
    assert not target.is_symlink()


def test_an_interrupted_replace_keeps_the_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "page.md"
    p.write_bytes(b"old bytes\n")

    def refuse(src: object, dst: object) -> None:
        raise OSError("interrupted")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="interrupted"):
        fsutil.atomic_write(p, b"new bytes\n")

    assert p.read_bytes() == b"old bytes\n"
    assert not list(p.parent.glob(f".{p.name}.*.tmp"))


def test_mode_none_keeps_the_existing_bits(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.write_text("old\n", encoding="utf-8")
    existing.chmod(0o640)
    fsutil.atomic_write(existing, "new\n")
    assert existing.read_text(encoding="utf-8") == "new\n"
    assert _mode(existing) == 0o640

    new = tmp_path / "new"
    fsutil.atomic_write(new, b"bytes\n")
    assert new.read_bytes() == b"bytes\n"
    assert _mode(new) == 0o600


def test_scrub_keeps_the_page_mode(tmp_path: Path) -> None:
    page = tmp_path / "page.log"
    page.write_text("keep\nsecret\n", encoding="utf-8")
    page.chmod(0o644)

    assert governance._scrub_file(page, lambda text: (text.replace("secret\n", ""), 1)) is True

    assert page.read_text(encoding="utf-8") == "keep\n"
    assert _mode(page) == 0o644
