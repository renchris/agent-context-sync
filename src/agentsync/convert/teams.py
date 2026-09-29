"""Converter: Teams channel month rollup (model.TEAMS_MONTH_SCHEMA JSON) -> one WHOLE page (owner:
convert)."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from agentsync.config import ConvertConfig
from agentsync.convert._common import _FULL_TEXT_SIDECAR, _cap_body, _decode_text, _python_version
from agentsync.convert.base import OptionValue, make_unit
from agentsync.convert.pandoc import _pandoc_options, _PandocRunner
from agentsync.errors import ConversionError
from agentsync.model import TEAMS_MONTH_SCHEMA, RenderedUnit, UnitKind

_EMITTER_VERSION = "1.0.0"
_MARK = "AGENTSYNCMSGSPLIT"
_SPLIT_RE = re.compile(rf"^{_MARK}(\d{{6}})[ \t]*$", re.MULTILINE)
_EMOJI_RE = re.compile(r"<emoji\b[^>]*?\balt=\"([^\"]*)\"[^>]*>(?:\s*</emoji>)?", re.IGNORECASE)
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class _Msg:
    """One validated message of the rollup."""

    id: str
    reply_to_id: str | None
    created: str
    last_modified: str
    sender: str
    subject: str
    body_html: str
    deleted: bool
    attachments: tuple[tuple[str, str], ...]


def _one_line(value: object) -> str:
    """Collapse whitespace; None -> ""."""
    return "" if value is None else _WS_RE.sub(" ", str(value)).strip()


def _req_str(obj: Mapping[str, Any], key: str, where: str) -> str:
    """A required string field or ConversionError."""
    v = obj.get(key)
    if not isinstance(v, str):
        raise ConversionError(f"teams rollup: {where}.{key} must be a string")
    return v


def _parse(doc: object) -> tuple[Mapping[str, Any], list[_Msg]]:
    """Validate the TEAMS_MONTH_SCHEMA shape; ConversionError on anything else."""
    if not isinstance(doc, dict):
        raise ConversionError("teams rollup: top level is not a JSON object")
    schema = doc.get("schema")
    if schema != TEAMS_MONTH_SCHEMA:
        raise ConversionError(f"teams rollup: unknown schema {schema!r} (expected {TEAMS_MONTH_SCHEMA})")
    for key in ("team_name", "channel_name", "month"):
        _req_str(doc, key, "rollup")
    if not _MONTH_RE.match(str(doc["month"])):
        raise ConversionError(f"teams rollup: month {doc['month']!r} is not YYYY-MM")
    raw = doc.get("messages")
    if not isinstance(raw, list):
        raise ConversionError("teams rollup: messages must be a list")
    msgs: list[_Msg] = []
    for i, m in enumerate(raw):
        where = f"messages[{i}]"
        if not isinstance(m, dict):
            raise ConversionError(f"teams rollup: {where} is not an object")
        reply = m.get("reply_to_id")
        atts: list[tuple[str, str]] = []
        for a in m.get("attachments") or []:
            if isinstance(a, dict):
                atts.append((_one_line(a.get("name")), _one_line(a.get("content_url"))))
        msgs.append(
            _Msg(
                id=_req_str(m, "id", where),
                reply_to_id=None if reply in (None, "") else str(reply),
                created=_req_str(m, "created", where),
                last_modified=_one_line(m.get("last_modified")),
                sender=_one_line(m.get("from")) or "unknown sender",
                subject=_one_line(m.get("subject")),
                body_html=str(m.get("body_html") or ""),
                deleted=bool(m.get("deleted", False)),
                attachments=tuple(atts),
            )
        )
    return doc, msgs


_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track"}
    | {"wbr"}
)


class _Balancer(HTMLParser):
    """Re-serialise an HTML fragment with every element closed and stray end tags dropped.

    Each message body must be self-contained before bodies are batched through one pandoc run; otherwise an
    unclosed ``<i>`` in one message would change how its neighbours render (measured).  Comments are dropped.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> str:
        return "".join(f' {k}="{html.escape(v or "", quote=True)}"' for k, v in attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.out.append(f"<{tag}{self._attrs(attrs)}>")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.out.append(f"<{tag}{self._attrs(attrs)}>")
        if tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data: str) -> None:
        self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        """The balanced fragment."""
        self.close()
        return "".join(self.out) + "".join(f"</{t}>" for t in reversed(self.stack))


def _prepare_html(body: str) -> str:
    """Teams-specific tags pandoc would drop (``<emoji alt="…">`` keeps its alt text), then balance tags."""
    balancer = _Balancer()
    balancer.feed(_EMOJI_RE.sub(lambda m: m.group(1), body))
    return balancer.result()


class TeamsMonthConverter:
    """Teams channel month rollup (model.TEAMS_MONTH_SCHEMA JSON) -> one WHOLE page.

    ``# <team> / <channel> — YYYY-MM`` then one ``### <created> — <from>`` section per top-level message in
    (created, id) order with replies indented as quotes; body_html through pandoc -> gfm; deleted messages
    render ``[deleted]``. Unknown schema -> ConversionError.
    """

    converter_id = "teams-month"
    extensions: tuple[str, ...] = (".teams.json",)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg
        self._pandoc = _PandocRunner(cfg.pandoc_path)

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""
        return f"{_EMITTER_VERSION}+python-{_python_version()}+pandoc-{self._pandoc.version()}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        return {**_pandoc_options(self._pandoc), "max_page_bytes": self._cfg.max_page_bytes}

    def _bodies(self, htmls: Sequence[str]) -> list[str]:
        """Convert every message body with ONE pandoc run (split markers); per-message fallback if unsafe."""
        if not htmls:
            return []
        doc = "".join(
            f"<p>{_MARK}{i:06d}</p>\n<div>\n{_prepare_html(h)}\n</div>\n" for i, h in enumerate(htmls)
        )
        out = self._pandoc.html_to_gfm(doc)
        pieces = _SPLIT_RE.split(out)
        expected = [f"{i:06d}" for i in range(len(htmls))]
        if len(pieces) == 1 + 2 * len(htmls) and pieces[1::2] == expected and not pieces[0].strip():
            return [p.strip("\n") for p in pieces[2::2]]
        return [self._pandoc.html_to_gfm(_prepare_html(h)).strip("\n") for h in htmls]

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        try:
            text = _decode_text(src.read_bytes())
            doc_raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConversionError(f"teams rollup: invalid JSON ({exc.msg} at line {exc.lineno})") from exc
        except OSError as exc:
            raise ConversionError(f"cannot read: {exc}") from exc
        doc, msgs = _parse(doc_raw)
        team, channel, month = _one_line(doc["team_name"]), _one_line(doc["channel_name"]), str(doc["month"])
        order = sorted(msgs, key=lambda m: (m.created, m.id))
        ids = {m.id for m in msgs}
        tops = [m for m in order if m.reply_to_id is None or m.reply_to_id not in ids]
        replies: dict[str, list[_Msg]] = {}
        for m in order:
            if m.reply_to_id is not None and m.reply_to_id in ids:
                replies.setdefault(m.reply_to_id, []).append(m)
        live = [m for m in order if not m.deleted]
        rendered = dict(zip((m.id for m in live), self._bodies([m.body_html for m in live]), strict=True))
        title = f"{team} / {channel} — {month}"
        out = [f"# {title}", ""]
        out += [f"{len(msgs)} message(s) in {len(tops)} thread(s) · channel `{channel}` in team `{team}`", ""]
        if not msgs:
            out += ["[no messages this month]", ""]
        for top in tops:
            out += self._message(top, rendered, quoted=False)
            for reply in replies.get(top.id, []):
                out += self._message(reply, rendered, quoted=True)
        body = "\n".join(out)
        summary = f"Teams {team} / {channel}, {month}: {len(msgs)} message(s) in {len(tops)} thread(s)"
        subjects = [m.subject for m in tops if m.subject]
        if subjects:
            summary += "; subjects: " + "; ".join(subjects)
        body, sidecars = _cap_body(body, self._cfg.max_page_bytes, sidecar_name=_FULL_TEXT_SIDECAR)
        return (
            make_unit(
                unit_id="whole",
                kind=UnitKind.WHOLE,
                index=0,
                of=1,
                name="",
                file_stem="",
                title=title,
                summary=summary,
                body=body,
                sidecars=sidecars,
            ),
        )

    @staticmethod
    def _message(m: _Msg, rendered: Mapping[str, str], *, quoted: bool) -> list[str]:
        """Lines for one message: a ``###`` section, or a ``>`` quote for a reply."""
        edited = f" (edited {m.last_modified})" if m.last_modified and m.last_modified != m.created else ""
        lines: list[str] = []
        if m.reply_to_id is not None and not quoted:
            lines += [f"(reply to message {m.reply_to_id}, which is not in this month)", ""]
        if m.subject:
            lines += [f"**{m.subject}**", ""]
        text = "[deleted]" if m.deleted else (rendered.get(m.id, "") or "[empty message]")
        lines += [text, ""]
        if m.attachments and not m.deleted:
            lines += ["Attachments:", ""]
            lines += [f"- {n or 'attachment'}" + (f" — <{u}>" if u else "") for n, u in m.attachments]
            lines.append("")
        if not quoted:
            return [f"### {m.created} — {m.sender}{edited}", "", *lines]
        head = [f"> **{m.sender}** — {m.created}{edited}", ">"]
        while lines and not lines[-1].strip():
            lines.pop()
        return [*head, *(f"> {ln}" if ln else ">" for ln in lines), ""]
