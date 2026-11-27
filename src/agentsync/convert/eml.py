"""Converter: RFC 822 message via the stdlib email package (owner: convert)."""

from __future__ import annotations

import codecs
import email
import email.policy
import hashlib
from collections.abc import Iterator, Mapping
from email.message import Message
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
    _replace_controls,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.convert.pandoc import _pandoc_options, _PandocRunner
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.model import RenderedUnit, UnitKind
from agentsync.policy import ZERO_BYTES_REASON
from agentsync.text import plural

_EMITTER_VERSION = "1.1.5"
"""B13 round 48 C3533 with C3532 (ruling 229): every inline text part of a message reaches its page, each part
after the first under its own heading, where only the first was rendered and the rest dropped unmentioned, and
no C0 control is left in a part's text, where a NUL made the page a binary file to git; the cached pages are
missed, msg's versions carrying this one.  1.1.4: round 47 C3321 (ruling 215): a UTF-32 part with its mark
decodes, where it took the lenient fallback's garbage, a cached OK page, so the same bytes miss it; msg's
versions carry this one, so .msg moves with it, and no page is read again.  1.1.3: round 43 C2450, C2451 (a
cut fence always closes)."""
_HEADERS: tuple[str, ...] = ("From", "To", "Cc", "Date", "Subject", "Message-ID", "In-Reply-To", "References")
_BODY_SUBTYPES = frozenset({"plain", "html"})  # the text leaves a page renders as its body
_SMIME_ENCRYPTED = frozenset({"application/pkcs7-mime", "application/x-pkcs7-mime"})
# codecs.lookup() names of the labels Windows mail clients put on cp1252 bytes.
_WINDOWS_PRONE_CODECS = frozenset({"utf-8", "ascii", "iso8859-1", "cp1252"})
# Legacy CJK labels mail clients put on bytes from the Windows superset codepage (GBK under gb2312, ...).
_CJK_SUPERSETS: Mapping[str, str] = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "shift_jis": "cp932",
    "euc_kr": "cp949",
    "big5": "cp950",
}


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
    decoded by its label (a legacy CJK label by its Windows superset): strictly, then as UTF-8, then leniently
    by the label, so one out-of-label character costs one replacement, not the whole body.  An unknown or
    bytes-only label falls back to the ``_decode_text`` order.  Whichever way it decoded, each C0 control but
    tab, newline, form feed and carriage return, and DEL, is U+FFFD in the text returned: the lenient fallback
    keeps a NUL byte, and a part labelled UTF-16 or UTF-32 decodes to U+0000, and a page holding one is a
    binary file to git (B13 round 48 C3532).
    """
    return _replace_controls(_decoded_payload(part))


def _decoded_payload(part: Message) -> str:
    """``_decode_part``'s decode, before its controls are replaced."""
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
            codec = _CJK_SUPERSETS.get(codec, codec)
            try:
                return payload.decode(codec)
            except LookupError:  # a bytes-only codec (base64): fall through to the cp1252 order
                pass
            except (
                UnicodeError
            ):  # bytes that do not fit the label: UTF-8 if they are, else the label leniently
                try:
                    return payload.decode("utf-8")
                except UnicodeError:
                    return payload.decode(codec, errors="replace")
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


def _body_parts(msg: Message) -> list[Message]:
    """The text leaves the page renders as its body, in MIME order, walked from the MIME tree (B13 round 48
    C3533): one child of a multipart/alternative (the first that renders as plain text, else the first that
    renders at all: its other children are the same message in another form), every child of any other
    multipart (mixed, related, signed, ...) that is not an attachment, and a text/plain or text/html leaf
    that is not one.  Another text leaf (text/calendar, say) is not rendered; ``_attachments`` lists it."""
    if _is_attachment(msg):
        return []
    if not msg.is_multipart():
        return [msg] if msg.get_content_subtype() in _BODY_SUBTYPES else []
    payload = msg.get_payload()
    children = [sub for sub in payload if isinstance(sub, Message)] if isinstance(payload, list) else []
    if msg.get_content_subtype() == "alternative":
        forms = [found for found in map(_body_parts, children) if found]
        plain = [found for found in forms if found[0].get_content_subtype() == "plain"]
        return (plain or forms or [[]])[0]
    return [part for child in children for part in _body_parts(child)]


class EmlConverter:
    """RFC 822 message via the stdlib email package.

    Header table (From, To, Cc, Date, Subject, Message-ID, In-Reply-To, References) then the text/plain part
    (or text/html through pandoc -> gfm), each further inline text/plain or text/html part after it under
    ``### Part <n> (<type>)``; attachments, and any other inline text part, are listed by name, size and
    sha256, not converted (save one into the inbox to convert it). Never emits raw MIME or base64.
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
        if not data:  # zero bytes: one `empty` stub (B13 round 36 C1050); blanks stay below
            raise UnreadableSourceError(ZERO_BYTES_REASON)
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
        body_parts = _body_parts(msg)
        out += ["## Body", ""]
        if not body_parts:
            out += ["[no text body]", ""]
        for number, part in enumerate(body_parts, start=1):
            if number > 1:  # every text part after the first under its own heading (C3533)
                out += [f"### Part {number} ({part.get_content_type()})", ""]
            text = _decode_part(part)
            if part.get_content_subtype() == "html":
                rendered = self._pandoc.html_to_gfm(text).strip("\n")
            else:
                rendered = _escape_plain(text)
            out += [rendered if rendered.strip() else ("[empty body]" if number == 1 else "[empty part]"), ""]
        attachments = self._attachments(msg, body_parts)
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
            summary += f"; {plural(len(attachments), 'attachment')}"
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
    def _attachments(msg: Message, body_parts: list[Message]) -> list[list[str]]:
        """Rows (index, name, type, size, sha256) for every attachment in MIME order, and for every text leaf
        the body does not render that is neither plain nor HTML (a text/calendar part, say), so the page names
        it (B13 round 48 C3533); a plain or HTML leaf left out is an alternative of what the body renders."""
        rendered = {id(part) for part in body_parts}
        rows: list[list[str]] = []
        for part in _leaves(msg):
            if id(part) in rendered:
                continue
            if not _is_attachment(part) and part.get_content_subtype() in _BODY_SUBTYPES:
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
