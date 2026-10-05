"""Converter: RFC 822 message via the stdlib email package (owner: convert)."""

from __future__ import annotations

import codecs
import email
import email.policy
import hashlib
from collections.abc import Iterator, Mapping
from email.message import EmailMessage, Message
from pathlib import Path

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    _cap_body,
    _cell,
    _decode_text,
    _escape_line,
    _escape_plain,
    _gfm_table,
    _python_version,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.convert.pandoc import _pandoc_options, _PandocRunner
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.model import RenderedUnit, UnitKind

_EMITTER_VERSION = "1.1.0"
_HEADERS: tuple[str, ...] = ("From", "To", "Cc", "Date", "Subject", "Message-ID", "In-Reply-To", "References")
_SMIME_ENCRYPTED = frozenset({"application/pkcs7-mime", "application/x-pkcs7-mime"})
# codecs.lookup() names of the labels Windows mail clients put on cp1252 bytes.
_WINDOWS_PRONE_CODECS = frozenset({"utf-8", "ascii", "iso8859-1", "cp1252"})


def _header(msg: Message, key: str) -> str:
    """Decoded (RFC 2047) header value, whitespace-collapsed; "" when absent or undecodable."""
    try:
        raw = msg.get(key)
    except (ValueError, TypeError, IndexError, AttributeError):
        raw = None
    if raw is None:
        return ""
    return " ".join(str(raw).split())


def _decode_part(part: Message) -> str:
    """Text of a leaf part, tolerating unknown, missing or lying charsets (never raises).

    A part labelled UTF-8, ASCII, Latin-1 or cp1252, or not labelled at all, goes through ``_decode_text``
    (strict UTF-8, then cp1252): Windows mail clients send cp1252 under those labels or none, and a lenient
    decode turns its smart quotes and accents into replacement characters.  Any other declared charset is
    decoded strictly, falling back to the same order when the charset is unknown or the bytes do not fit it.
    """
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset()
    if charset:
        try:
            codec = codecs.lookup(charset).name
        except (LookupError, ValueError):
            codec = ""
        if codec and codec not in _WINDOWS_PRONE_CODECS:
            try:
                return payload.decode(codec)
            except (LookupError, UnicodeError):  # a bytes-only codec (base64), or bytes that do not fit
                pass
    try:
        return _decode_text(payload)
    except ConversionError:
        return payload.decode("utf-8", errors="replace")


def _payload_bytes(part: Message) -> bytes:
    """Decoded bytes of an attachment (an attached message is serialised), never base64 text."""
    if part.get_content_type() == "message/rfc822":
        inner = part.get_payload()
        if isinstance(inner, list) and inner and isinstance(inner[0], Message):
            return inner[0].as_bytes(policy=email.policy.default)
        return b""
    payload = part.get_payload(decode=True)
    return payload if isinstance(payload, bytes) else b""


def _leaves(msg: Message) -> Iterator[Message]:
    """Every leaf part in MIME order; an attached message (message/rfc822) is a leaf, not descended into."""
    if msg.is_multipart() and msg.get_content_type() != "message/rfc822":
        payload = msg.get_payload()
        if isinstance(payload, list):
            for sub in payload:
                if isinstance(sub, Message):
                    yield from _leaves(sub)
        return
    yield msg


def _is_attachment(part: Message) -> bool:
    """True for parts carrying a file (a filename, disposition attachment, or a non-text payload)."""
    if part.get_content_type() == "message/rfc822":
        return True
    if part.get_filename():
        return True
    disposition = (part.get_content_disposition() or "").lower()
    if disposition == "attachment":
        return True
    return part.get_content_maintype() not in ("text", "multipart")


def _attachment_name(part: Message, index: int) -> str:
    """The attachment's decoded file name, or a placeholder derived from its type and position."""
    name = part.get_filename()
    if name:
        return " ".join(str(name).split())
    if part.get_content_type() == "message/rfc822":
        inner = part.get_payload()
        if isinstance(inner, list) and inner and isinstance(inner[0], Message):
            subject = _header(inner[0], "Subject")
            if subject:
                return f"{subject}.eml"
        return f"attached-message-{index}.eml"
    return f"unnamed-{index} ({part.get_content_type()})"


class EmlConverter:
    """RFC 822 message via the stdlib email package.

    Header table (From, To, Cc, Date, Subject, Message-ID, In-Reply-To, References) then the text/plain part
    (or text/html through pandoc -> gfm); attachments are listed by name, size and sha256, not converted (save
    one into the inbox to convert it). Never emits raw MIME or base64.
    """

    converter_id = "eml-stdlib"
    extensions: tuple[str, ...] = (".eml",)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg
        self._pandoc = _PandocRunner(cfg.pandoc_path)

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""
        return f"{_EMITTER_VERSION}+python-{_python_version()}+pandoc-{self._pandoc.version()}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        return {
            **_pandoc_options(self._pandoc),
            "body_preference": "plain,html",
            "max_page_bytes": self._cfg.max_page_bytes,
        }

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        try:
            data = src.read_bytes()
        except OSError as exc:
            raise ConversionError(f"cannot read: {exc}") from exc
        if not data.strip():
            raise ConversionError("empty message file")
        msg = email.message_from_bytes(data, policy=email.policy.default)
        self._refuse_protected(msg)
        subject = _header(msg, "Subject")
        headers = [(h, _header(msg, h)) for h in _HEADERS]
        present = [(h, v) for h, v in headers if v]
        if not present and not msg.get_payload():
            raise ConversionError("not an RFC 822 message (no headers, no body)")
        out = [f"# {_escape_line(subject) if subject else '(no subject)'}", ""]
        if present:
            out += _gfm_table(["Header", "Value"], ([h, _cell(v)] for h, v in present))
            out.append("")
        body_part = self._body_part(msg)
        out += ["## Body", ""]
        if body_part is None:
            out += ["[no text body]", ""]
        else:
            text = _decode_part(body_part)
            if body_part.get_content_subtype() == "html":
                rendered = self._pandoc.html_to_gfm(text).strip("\n")
            else:
                rendered = _escape_plain(text)
            out += [rendered if rendered.strip() else "[empty body]", ""]
        attachments = self._attachments(msg, body_part)
        if attachments:
            out += ["## Attachments", ""]
            out += _gfm_table(["#", "Name", "Type", "Size (bytes)", "sha256"], attachments)
            out += [
                "",
                "Attachments are listed, not converted; save one into the inbox to convert it.",
                "",
            ]
        body = "\n".join(out)
        sender = _header(msg, "From") or "unknown sender"
        date = _header(msg, "Date")
        summary = f"Email from {sender}" + (f", {date}" if date else "") + f": {subject or '(no subject)'}"
        if attachments:
            summary += f"; {len(attachments)} attachment(s)"
        body, sidecars = _cap_body(body, self._cfg.max_page_bytes, sidecar_name=_FULL_TEXT_SIDECAR)
        return (
            make_unit(
                unit_id="whole",
                kind=UnitKind.WHOLE,
                index=0,
                of=1,
                name="",
                file_stem="",
                title=subject or "(no subject)",
                summary=summary,
                body=body,
                sidecars=sidecars,
            ),
        )

    @staticmethod
    def _refuse_protected(msg: Message) -> None:
        """S/MIME-encrypted or IRM (rpmsg) messages are UNREADABLE, never converted to an empty page."""
        for part in _leaves(msg):
            ctype = part.get_content_type()
            if ctype in _SMIME_ENCRYPTED:
                smime = str(part.get_param("smime-type") or "enveloped-data").lower()
                if smime != "signed-data":
                    raise UnreadableSourceError("encrypted")
            filename = str(part.get_filename() or "").lower()
            if filename.endswith(".rpmsg") or ctype == "application/x-microsoft-rpmsg-message":
                raise UnreadableSourceError("IRM-protected")

    @staticmethod
    def _body_part(msg: Message) -> Message | None:
        """The text/plain body, else the text/html one (never an attachment)."""
        if isinstance(msg, EmailMessage):
            try:
                found = msg.get_body(preferencelist=("plain", "html"))
            except (KeyError, ValueError):
                found = None
            if found is not None and not found.get_filename():
                return found
        for want in ("plain", "html"):
            for part in _leaves(msg):
                if (
                    part.get_content_maintype() == "text"
                    and part.get_content_subtype() == want
                    and not _is_attachment(part)
                ):
                    return part
        return None

    @staticmethod
    def _attachments(msg: Message, body_part: Message | None) -> list[list[str]]:
        """Rows (index, name, type, size, sha256) for every attachment in MIME order."""
        rows: list[list[str]] = []
        for part in _leaves(msg):
            if part is body_part:
                continue
            if not _is_attachment(part):
                continue
            payload = _payload_bytes(part)
            index = len(rows) + 1
            rows.append(
                [
                    str(index),
                    _cell(_attachment_name(part, index)),
                    _cell(part.get_content_type()),
                    str(len(payload)),
                    hashlib.sha256(payload).hexdigest(),
                ]
            )
        return rows
