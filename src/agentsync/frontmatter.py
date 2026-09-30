"""Deterministic YAML frontmatter: the mirror-page contract (design 4.4/4.6) and a tolerant reader.

Rendering is hand-rolled (fixed key order, no wall-clock, one spelling per value) so two renders of the same
inputs are byte-identical and the refresh-queue awk can read ``rendered_sha256:`` / ``status:`` lines raw.
Parsing uses ``yaml.safe_load`` because curated pages are written by an agent in free YAML.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from agentsync.errors import AgentSyncError
from agentsync.model import PageStatus

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_BARE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+/-]*$")
_YAML_SPECIAL = frozenset({"true", "false", "yes", "no", "on", "off", "null", "~", "y", "n"})

FENCE = "---"


class FrontmatterError(AgentSyncError):
    """A page has no frontmatter, unterminated frontmatter, invalid YAML, or violates the mirror contract."""


@dataclass(frozen=True, slots=True)
class Bare:
    """A scalar rendered without quotes (hashes, enum values, ids); validated to be YAML-safe."""

    value: str


Scalar = str | int | float | bool | Bare
FrontmatterValue = Scalar | Mapping[str, Scalar] | None


def _render_scalar(v: Scalar) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, Bare):
        if not _BARE_RE.match(v.value) or v.value.lower() in _YAML_SPECIAL:
            return json.dumps(v.value, ensure_ascii=False)
        return v.value
    return json.dumps(v, ensure_ascii=False)


def render_frontmatter(fields: Mapping[str, FrontmatterValue]) -> str:
    """Render ``fields`` in their given order as a ``---``-fenced block ending in a newline; None values are
    omitted.

    Nested mappings render as one flow mapping (``part: {kind: sheet, index: 1}``) in their given order.
    """
    lines = [FENCE]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, Mapping):
            inner = ", ".join(f"{k}: {_render_scalar(v)}" for k, v in value.items())
            lines.append(f"{key}: {{{inner}}}")
        else:
            lines.append(f"{key}: {_render_scalar(value)}")
    lines.append(FENCE)
    return "\n".join(lines) + "\n"


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split a page into (frontmatter YAML text without fences, body); (None, text) when there is none."""
    if not text.startswith(FENCE + "\n"):
        return None, text
    end = text.find("\n" + FENCE + "\n", len(FENCE))
    if end == -1:
        if text.endswith("\n" + FENCE):
            return text[len(FENCE) + 1 : len(text) - len(FENCE) - 1], ""
        raise FrontmatterError("unterminated frontmatter (no closing '---' line)")
    return text[len(FENCE) + 1 : end + 1], text[end + len(FENCE) + 2 :]


def _normalise(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse a page into (frontmatter mapping, body); dates become ISO strings; raises FrontmatterError."""
    fm, body = split_frontmatter(text)
    if fm is None:
        raise FrontmatterError("no frontmatter (page must start with a '---' line)")
    try:
        data = yaml.safe_load(fm) if fm.strip() else {}
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"invalid YAML frontmatter: {exc}") from None
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter is not a mapping")
    return _normalise(data), body


# ---------------------------------------------------------------------------------------------------------
# the mirror page contract
# ---------------------------------------------------------------------------------------------------------

MIRROR_KEY_ORDER: tuple[str, ...] = (
    "source_kind",
    "source_id",
    "stable_id",
    "source_path",
    "source_web_url",
    "source_etag",
    "source_version",
    "content_sha256",
    "canonical_sha256",
    "rendered_sha256",
    "part",
    "unit_index",
    "converter",
    "options_hash",
    "sensitivity_label",
    "content_trust",
    "status",
    "reason",
    "superseded_by",
    "deleted_at",
    "last_rendered_sha256",
    "last_commit",
    "source_title",
    "summary",
    "tokens_estimate",
)
"""Every key a mirror page may carry, in the one order they are written.  Nothing else is allowed."""

MIRROR_REQUIRED: tuple[str, ...] = ("source_kind", "source_id", "stable_id", "source_path", "status")
MIRROR_REQUIRED_CURRENT: tuple[str, ...] = (
    "content_sha256",
    "canonical_sha256",
    "rendered_sha256",
    "converter",
    "options_hash",
    "summary",
    "tokens_estimate",
)
MIRROR_REQUIRED_DELETED: tuple[str, ...] = ("deleted_at", "last_rendered_sha256")


@dataclass(frozen=True, slots=True)
class MirrorFrontmatter:
    """Content-derived frontmatter of one docs/mirror page.  No wall-clock field exists here by design.

    ``deleted_at`` is the UTC date (``YYYY-MM-DD``) of the run that tombstoned the page, recorded in the
    manifest's ``tombstones`` row, so re-rendering a tombstone from the manifest is byte-identical.
    """

    source_kind: str
    source_id: str
    stable_id: str
    source_path: str
    status: PageStatus
    source_web_url: str | None = None
    source_etag: str | None = None
    source_version: str | None = None
    content_sha256: str | None = None
    canonical_sha256: str | None = None
    rendered_sha256: str | None = None
    part_kind: str | None = None
    part_name: str | None = None
    unit_index: int | None = None
    unit_of: int | None = None
    converter: str | None = None  # "<converter_id>@<converter_version>"
    options_hash: str | None = None  # "sha256:<hex>"
    sensitivity_label: str | None = None
    content_trust: str | None = None  # constant policy.CONTENT_TRUST_VALUE on every page publish writes
    reason: str | None = None  # unreadable/refused/failed stubs
    superseded_by: str | None = None
    deleted_at: str | None = None
    last_rendered_sha256: str | None = None
    last_commit: str | None = None
    source_title: str | None = None
    summary: str | None = None
    tokens_estimate: int | None = None

    def to_fields(self) -> dict[str, FrontmatterValue]:
        """Return the ordered field mapping ``render_frontmatter`` writes."""

        def bare(v: str | None) -> Bare | None:
            return Bare(v) if v is not None else None

        part: dict[str, Scalar] | None = None
        if self.part_kind is not None:
            part = {"kind": Bare(self.part_kind)}
            if self.part_name:
                part["name"] = self.part_name
            if self.unit_index is not None:
                part["index"] = self.unit_index
            if self.unit_of is not None:
                part["of"] = self.unit_of
        values: dict[str, FrontmatterValue] = {
            "source_kind": Bare(self.source_kind),
            "source_id": Bare(self.source_id),
            "stable_id": self.stable_id,
            "source_path": self.source_path,
            "source_web_url": self.source_web_url,
            "source_etag": self.source_etag,
            "source_version": self.source_version,
            "content_sha256": bare(self.content_sha256),
            "canonical_sha256": bare(self.canonical_sha256),
            "rendered_sha256": bare(self.rendered_sha256),
            "part": part,
            "unit_index": self.unit_index,
            "converter": self.converter,
            "options_hash": bare(self.options_hash),
            "sensitivity_label": self.sensitivity_label,
            "content_trust": bare(self.content_trust),
            "status": Bare(self.status.value),
            "reason": self.reason,
            "superseded_by": self.superseded_by,
            "deleted_at": bare(self.deleted_at),
            "last_rendered_sha256": bare(self.last_rendered_sha256),
            "last_commit": bare(self.last_commit),
            "source_title": self.source_title,
            "summary": self.summary,
            "tokens_estimate": self.tokens_estimate,
        }
        return {k: values[k] for k in MIRROR_KEY_ORDER}


def render_mirror_page(fm: MirrorFrontmatter, body: str) -> str:
    """Render one complete mirror page: frontmatter, then ``body`` (which must end in exactly one newline)."""
    text = body if body.endswith("\n") else body + "\n"
    return render_frontmatter(fm.to_fields()) + text


def validate_mirror_frontmatter(data: Mapping[str, Any]) -> list[str]:
    """Return every contract violation in a parsed mirror frontmatter mapping ([] when it conforms)."""
    problems: list[str] = []
    unknown = sorted(set(data) - set(MIRROR_KEY_ORDER))
    if unknown:
        problems.append(f"unknown key(s): {', '.join(unknown)}")
    keys = [k for k in data if k in MIRROR_KEY_ORDER]
    if keys != sorted(keys, key=MIRROR_KEY_ORDER.index):
        problems.append("keys are not in contract order")
    problems.extend(f"missing required key {k!r}" for k in MIRROR_REQUIRED if k not in data)
    status = data.get("status")
    try:
        st = PageStatus(str(status))
    except ValueError:
        problems.append(f"status {status!r} is not one of {', '.join(s.value for s in PageStatus)}")
        return problems
    if st is PageStatus.CURRENT or st is PageStatus.SUPERSEDED:
        problems.extend(
            f"missing {k!r} for status {st.value}" for k in MIRROR_REQUIRED_CURRENT if k not in data
        )
    if st is PageStatus.DELETED:
        problems.extend(f"missing {k!r} for status deleted" for k in MIRROR_REQUIRED_DELETED if k not in data)
    if st in (PageStatus.UNREADABLE, PageStatus.REFUSED) and "reason" not in data:
        problems.append(f"missing 'reason' for status {st.value}")
    for k in ("content_sha256", "canonical_sha256", "rendered_sha256", "last_rendered_sha256"):
        if k in data and not HEX64_RE.match(str(data[k])):
            problems.append(f"{k} is not 64 lowercase hex")
    ct = data.get("content_trust")
    if ct is not None and not (isinstance(ct, str) and ct):
        problems.append("content_trust is not a non-empty string")
    oh = data.get("options_hash")
    if oh is not None and not (isinstance(oh, str) and oh.startswith("sha256:") and HEX64_RE.match(oh[7:])):
        problems.append("options_hash is not 'sha256:<64 hex>'")
    return problems


def parse_mirror_page(text: str) -> tuple[MirrorFrontmatter, str]:
    """Parse and validate a mirror page; raises FrontmatterError listing every violation."""
    data, body = parse_frontmatter(text)
    problems = validate_mirror_frontmatter(data)
    if problems:
        raise FrontmatterError("; ".join(problems))
    part = data.get("part") or {}

    def s(key: str) -> str | None:
        v = data.get(key)
        return None if v is None else str(v)

    return (
        MirrorFrontmatter(
            source_kind=str(data["source_kind"]),
            source_id=str(data["source_id"]),
            stable_id=str(data["stable_id"]),
            source_path=str(data["source_path"]),
            status=PageStatus(str(data["status"])),
            source_web_url=s("source_web_url"),
            source_etag=s("source_etag"),
            source_version=s("source_version"),
            content_sha256=s("content_sha256"),
            canonical_sha256=s("canonical_sha256"),
            rendered_sha256=s("rendered_sha256"),
            part_kind=None if not part else str(part.get("kind")),
            part_name=None if not part or part.get("name") is None else str(part.get("name")),
            unit_index=data.get("unit_index"),
            unit_of=None if not part or part.get("of") is None else int(part["of"]),
            converter=s("converter"),
            options_hash=s("options_hash"),
            sensitivity_label=s("sensitivity_label"),
            content_trust=s("content_trust"),
            reason=s("reason"),
            superseded_by=s("superseded_by"),
            deleted_at=s("deleted_at"),
            last_rendered_sha256=s("last_rendered_sha256"),
            last_commit=s("last_commit"),
            source_title=s("source_title"),
            summary=s("summary"),
            tokens_estimate=data.get("tokens_estimate"),
        ),
        body,
    )
