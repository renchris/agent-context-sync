"""Converter: Outlook ``.msg`` (MS-OXMSG, a compound file) via olefile (owner: convert).

The page has the shape of an ``.eml`` page (``eml.py``): the header table, the plain-text body (else the HTML
body through pandoc -> gfm), then the attachments listed by name, type, size and sha256, never converted.
From is the sent-representing party (what an ``.eml``'s From header holds), else the sender.
A rich-text-only body is not read (no RTF decompressor is shipped): the page says so.  An S/MIME-encrypted
or IRM-protected message is UNREADABLE.  A file that starts with an RFC 822 header block (mail saved as
MIME text under a ``.msg`` name) is read by the ``.eml`` converter; any other file that is not an Outlook
message is UNREADABLE (``not-msg``), and a compound file that cannot be read as one is UNREADABLE
(``damaged-msg``). A MemoryError fails the pass.

Never registered under a ``[policy]`` label rule (plan D8, as for images): a ``.msg`` can carry a sensitivity
label in a named MAPI property that the policy screen does not read, so a rule fails closed.
"""

from __future__ import annotations

import codecs
import datetime as dt
import hashlib
import logging
import re
import struct
import unicodedata
from collections.abc import Mapping
from email.utils import format_datetime
from pathlib import Path

import olefile

from agentsync.config import ConvertConfig
from agentsync.convert import eml
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    _cap_body,
    _cell,
    _decode_text,
    _emitter,
    _escape_line,
    _escape_plain,
    _gfm_table,
    _python_version,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.convert.eml import EmlConverter
from agentsync.convert.pandoc import _pandoc_options, _PandocRunner
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.model import RenderedUnit, UnitKind

log = logging.getLogger(__name__)

_EMITTER_VERSION = "1.0.1"
_REREAD_BELOW = "1.0.1"
"""A ``not-msg`` stub an emitter below this wrote is read again once (``MsgConverter.outdated``): 1.0.0 gave
it to a file that holds MIME text, which 1.0.1 reads as an ``.eml``.  Never above ``_EMITTER_VERSION``."""
_HEAD_BYTES = 64 * 1024
"""How much of a file that is not a compound file is looked at for an RFC 822 header block.  A message's
headers end well inside it; one routed through many servers can pass 8 KiB."""
_HEADER_LINE = re.compile(rb"[!-9;-~]+:(?:[ \t]|$)")
_MAIL_HEADERS = frozenset((b"from", b"subject", b"date", b"message-id", b"mime-version"))
NOT_MSG_REASON = "not-msg (not an Outlook message)"
DAMAGED_MSG_REASON = "damaged-msg (a compound file that cannot be read as an Outlook message)"
_PROPS = "__properties_version1.0"
_SUBSTG = "__substg1.0_"
_ATTACH = "__attach_version1.0_#"
_RECIP = "__recip_version1.0_#"
# MAPI property ids (MS-OXPROPS).
_SUBJECT, _MESSAGE_CLASS, _CLIENT_SUBMIT_TIME, _DELIVERY_TIME = 0x0037, 0x001A, 0x0039, 0x0E06
_SENDER_NAME, _SENDER_EMAIL, _SENDER_SMTP = 0x0C1A, 0x0C1F, 0x5D01
_REPRESENTING_NAME, _REPRESENTING_EMAIL, _REPRESENTING_SMTP = 0x0042, 0x0065, 0x5D02
_DISPLAY_TO, _DISPLAY_CC = 0x0E04, 0x0E03
_MESSAGE_ID, _IN_REPLY_TO, _REFERENCES = 0x1035, 0x1042, 0x1039
_BODY, _BODY_HTML, _RTF_COMPRESSED = 0x1000, 0x1013, 0x1009
_INTERNET_CPID, _MESSAGE_CODEPAGE = 0x3FDE, 0x3FFD
_DISPLAY_NAME, _SMTP_ADDRESS, _EMAIL_ADDRESS, _RECIPIENT_TYPE = 0x3001, 0x39FE, 0x3003, 0x0C15
_ATTACH_DATA, _ATTACH_FILENAME, _ATTACH_LONG_FILENAME, _ATTACH_MIME = 0x3701, 0x3704, 0x3707, 0x370E
_ATTACH_METHOD, _ATTACH_EMBEDDED = 0x3705, 5
_SMIME_ENCRYPTED = ("application/pkcs7-mime", "application/x-pkcs7-mime")
_RPMSG_TYPE = "application/x-microsoft-rpmsg-message"
_SIGNED_CLASS = "ipm.note.smime.multipartsigned"
_PT_UNICODE, _PT_STRING8, _PT_BINARY, _PT_OBJECT = 0x001F, 0x001E, 0x0102, 0x000D
_PT_LONG, _PT_SYSTIME = 0x0003, 0x0040
_HEADER_SIZES = {"": 32, "attach": 8, "recip": 8, "embedded": 24}
_FILETIME_EPOCH = dt.datetime(1601, 1, 1, tzinfo=dt.UTC)
# Windows code page ids Python does not know as ``cpN``.
_CODEPAGES = {
    20127: "ascii",
    20866: "koi8_r",
    21866: "koi8_u",
    **{28590 + n: f"iso8859_{n}" for n in (*range(1, 12), 13, 14, 15, 16)},
    50220: "iso2022_jp",
    50221: "iso2022_jp",
    50222: "iso2022_jp",
    51932: "euc_jp",
    51949: "euc_kr",
    54936: "gb18030",
    65000: "utf_7",
}


def _codec(cpid: object) -> str:
    """The Python codec of a Windows code page id, or "" (unknown, or one ``_decode_text`` already tries)."""
    if not isinstance(cpid, int) or cpid in (0, 1200, 65001):
        return ""
    try:
        return codecs.lookup(_CODEPAGES.get(cpid, f"cp{cpid}")).name
    except LookupError:
        return ""


class _Msg:
    """One message storage of an open compound file: the root, or an embedded message's ``3701000D``."""

    def __init__(
        self, ole: olefile.OleFileIO, prefix: tuple[str, ...], header: int, parent: _Msg | None = None
    ) -> None:
        self._ole, self._prefix = ole, prefix
        self.props = self._fixed(header)
        # PT_STRING8 text is in the message code page; the HTML body in the internet one. A recipient or an
        # attachment storage holds neither property: it takes its message's.
        string_cp = _codec(self.props.get(_MESSAGE_CODEPAGE)) or _codec(self.props.get(_INTERNET_CPID))
        html_cp = _codec(self.props.get(_INTERNET_CPID)) or _codec(self.props.get(_MESSAGE_CODEPAGE))
        self._codepage: str = string_cp or (parent._codepage if parent is not None else "")
        self._html_codepage: str = html_cp or (parent._html_codepage if parent is not None else "")

    def _path(self, *parts: str) -> list[str]:
        return [*self._prefix, *parts]

    def _fixed(self, header: int) -> dict[int, int]:
        """PT_LONG and PT_SYSTIME values of the properties stream (16-byte entries after its header)."""
        if not self._ole.exists("/".join(self._path(_PROPS))):
            return {}
        raw = self._ole.openstream(self._path(_PROPS)).read()
        out: dict[int, int] = {}
        for off in range(header, len(raw) - 15, 16):
            tag, _flags, value = struct.unpack_from("<IIQ", raw, off)
            ptype, pid = tag & 0xFFFF, tag >> 16
            if ptype == _PT_LONG:
                out[pid] = value & 0xFFFFFFFF
            elif ptype == _PT_SYSTIME:
                out[pid] = value
        return out

    def stream(self, pid: int, ptype: int) -> bytes | None:
        path = self._path(f"{_SUBSTG}{pid:04X}{ptype:04X}")
        return self._ole.openstream(path).read() if self._ole.exists("/".join(path)) else None

    def text(self, pid: int) -> str:
        """A PT_UNICODE (else PT_STRING8, by the message's code page) property, whitespace-collapsed."""
        return " ".join(self.raw_text(pid).replace("\x00", "").split())

    def raw_text(self, pid: int) -> str:
        wide = self.stream(pid, _PT_UNICODE)
        if wide is not None:
            text = wide.decode("utf-16-le", errors="replace").rstrip("\x00")
            return unicodedata.normalize("NFC", text)  # as ``_decode_text`` leaves an .eml's text
        narrow = self.stream(pid, _PT_STRING8)
        return "" if narrow is None else self.decode(narrow.rstrip(b"\x00"))

    def decode(self, data: bytes, *, html: bool = False) -> str:
        codepage = self._html_codepage if html else self._codepage
        if codepage:
            try:
                return unicodedata.normalize("NFC", data.decode(codepage))
            except UnicodeError:
                pass
        try:
            return _decode_text(data)
        except ConversionError:
            return data.decode("utf-8", errors="replace")

    def children(self, marker: str) -> list[tuple[str, ...]]:
        """The sub-storages named ``marker`` + 8 hex digits directly under this message, in number order."""
        depth = len(self._prefix) + 1
        found = {
            tuple(entry[:depth])
            for entry in self._ole.listdir(streams=True, storages=True)
            if len(entry) >= depth
            and tuple(entry[: depth - 1]) == self._prefix
            and entry[depth - 1].startswith(marker)
        }
        return sorted(found)

    def date(self) -> str:
        filetime = self.props.get(_CLIENT_SUBMIT_TIME) or self.props.get(_DELIVERY_TIME)
        if not filetime:
            return ""
        try:
            return format_datetime(_FILETIME_EPOCH + dt.timedelta(microseconds=filetime // 10))
        except (OverflowError, ValueError):
            return ""


def _rfc822_head(head: bytes) -> bool:
    """True when ``head`` starts with an RFC 822 header block: from the first line, header lines (a name of
    printable ASCII without a colon, a colon, then a space, a tab or the end of the line) or folded
    continuations of them, ended by a blank line, with From, Subject, Date, Message-ID or MIME-Version among
    the names.  A zip or other binary file fails at its first line; the ``.eml`` converter would make a page
    of almost anything, so this is the only gate in front of it."""
    names: set[bytes] = set()
    lines = head.split(b"\n")
    for raw in lines[:-1]:  # the last piece has no line end: cut at _HEAD_BYTES, or no blank line came
        line = raw.removesuffix(b"\r")
        if not line:
            return not names.isdisjoint(_MAIL_HEADERS)
        if line[:1] in (b" ", b"\t"):
            if not names:
                return False
            continue
        if _HEADER_LINE.match(line) is None:
            return False
        names.add(line.split(b":", 1)[0].lower())
    return False


def _address(name: str, addr: str) -> str:
    addr = addr if "@" in addr else ""
    if name and addr and name != addr:
        return f"{name} <{addr}>"
    return name or addr


class MsgConverter:
    """Outlook ``.msg`` via olefile: header table, text body, attachments listed (see module doc)."""

    converter_id = "msg-olefile"
    extensions: tuple[str, ...] = (".msg",)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg
        self._pandoc = _PandocRunner(cfg.pandoc_path)
        self._eml = EmlConverter(cfg)  # a .msg that holds MIME text (``_rfc822_head``)

    def version(self) -> str:
        """Version as run (emitter + olefile + python + pandoc + the ``.eml`` emitter, which renders a
        ``.msg`` that holds MIME text: a new one is a new action key for that page)."""
        return (
            f"{_EMITTER_VERSION}+olefile-{olefile.__version__}+python-{_python_version()}"
            f"+pandoc-{self._pandoc.version()}+eml-{eml._EMITTER_VERSION}"
        )

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        return {
            **_pandoc_options(self._pandoc),
            "body_preference": "plain,html",
            "max_page_bytes": self._cfg.max_page_bytes,
        }

    @property
    def outdated_key(self) -> str:
        """What ``outdated`` goes by: the running emitter and the floor.  The cycle keeps it in what it
        remembers having looked for, so a new floor makes it look again."""
        return f"{_EMITTER_VERSION}<{_REREAD_BELOW}"

    def outdated(self, produced: str, reason: str | None = None) -> bool:
        """True for a stub emitter 1.0.0 settled as ``not-msg (…): <exception type>`` (a damaged message,
        or a MemoryError), and for a ``not-msg`` stub from an emitter below ``_REREAD_BELOW`` (a file that
        holds MIME text, read as an ``.eml`` since 1.0.1).  An unchanged file is never converted again, so
        only this reads it again.  No reason this emitter writes has the suffix, and what a re-read writes is
        under the running emitter, so the rule ends after one re-read.  Never true for a page."""
        if reason is None or not reason.startswith(NOT_MSG_REASON):
            return False
        if reason.startswith(NOT_MSG_REASON + ": "):
            return True
        emitter, running, floor = _emitter(produced), _emitter(_EMITTER_VERSION), _emitter(_REREAD_BELOW)
        if emitter is None or running is None or floor is None:
            return False
        return reason == NOT_MSG_REASON and emitter < min(floor, running)

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        try:
            with src.open("rb") as fh:
                head = fh.read(_HEAD_BYTES)
        except OSError as exc:
            raise ConversionError(f"cannot read: {exc}") from exc
        if head[: len(olefile.MAGIC)] != olefile.MAGIC:
            if _rfc822_head(head):  # mail saved as MIME text under a .msg name: an .eml by another name
                return self._eml.convert(src, name=name)
            raise UnreadableSourceError(NOT_MSG_REASON)
        try:
            ole = olefile.OleFileIO(str(src))
        except MemoryError:  # not a fact about the file: the pass fails and the file is read again
            raise
        except Exception as exc:  # olefile raises OSError, OleFileError, struct.error, IndexError ...
            log.info("%s: the Outlook file cannot be read: %s", name, type(exc).__name__)
            raise UnreadableSourceError(DAMAGED_MSG_REASON) from exc
        try:
            return self._convert(ole)
        except (UnreadableSourceError, ConversionError, MemoryError):
            raise
        except Exception as exc:  # a broken chain or directory inside a valid header: settled, not retried
            log.info("%s: the Outlook file cannot be read: %s", name, type(exc).__name__)
            raise UnreadableSourceError(DAMAGED_MSG_REASON) from exc
        finally:
            ole.close()

    def _convert(self, ole: olefile.OleFileIO) -> tuple[RenderedUnit, ...]:
        if not ole.exists(_PROPS):
            raise UnreadableSourceError(NOT_MSG_REASON)
        msg = _Msg(ole, (), _HEADER_SIZES[""])
        attachments = self._attachments(ole, msg)
        self._refuse_protected(msg, attachments)
        subject = msg.text(_SUBJECT)
        headers = [
            ("From", self._sender(msg)),
            ("To", self._recipients(ole, msg, 1) or msg.text(_DISPLAY_TO)),
            ("Cc", self._recipients(ole, msg, 2) or msg.text(_DISPLAY_CC)),
            ("Date", msg.date()),
            ("Subject", subject),
            ("Message-ID", msg.text(_MESSAGE_ID)),
            ("In-Reply-To", msg.text(_IN_REPLY_TO)),
            ("References", msg.text(_REFERENCES)),
        ]
        present = [(h, v) for h, v in headers if v]
        out = [f"# {_escape_line(subject) if subject else '(no subject)'}", ""]
        if present:
            out += _gfm_table(["Header", "Value"], ([h, _cell(v)] for h, v in present))
            out.append("")
        out += ["## Body", "", self._body(msg), ""]
        rows = [[str(i), *row[:4]] for i, row in enumerate(attachments, 1)]
        if rows:
            out += ["## Attachments", ""]
            out += _gfm_table(["#", "Name", "Type", "Size (bytes)", "sha256"], rows)
            out += ["", "Attachments are listed, not converted; save one into the inbox to convert it.", ""]
        body = "\n".join(out)
        sender = dict(present).get("From") or "unknown sender"
        date = dict(present).get("Date", "")
        summary = f"Email from {sender}" + (f", {date}" if date else "") + f": {subject or '(no subject)'}"
        if rows:
            summary += f"; {len(rows)} attachment(s)"
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
    def _sender(msg: _Msg) -> str:
        """The party the message is from: sent-representing (a shared mailbox, a delegate's principal), as
        an ``.eml``'s From header; else the sender."""
        representing = _address(
            msg.text(_REPRESENTING_NAME), msg.text(_REPRESENTING_SMTP) or msg.text(_REPRESENTING_EMAIL)
        )
        return representing or _address(
            msg.text(_SENDER_NAME), msg.text(_SENDER_SMTP) or msg.text(_SENDER_EMAIL)
        )

    def _body(self, msg: _Msg) -> str:
        plain = msg.raw_text(_BODY)
        if plain.strip():
            return _escape_plain(plain)
        html = msg.stream(_BODY_HTML, _PT_BINARY)
        html_text = msg.decode(html, html=True) if html is not None else msg.raw_text(_BODY_HTML)
        if html_text.strip():
            rendered = self._pandoc.html_to_gfm(html_text).strip("\n")
            return rendered if rendered.strip() else "[empty body]"
        if msg.stream(_RTF_COMPRESSED, _PT_BINARY) is not None:
            return "[no text body: the message has only a rich-text body, which is not read]"
        return "[no text body]"

    @staticmethod
    def _recipients(ole: olefile.OleFileIO, msg: _Msg, kind: int) -> str:
        names: list[str] = []
        for path in msg.children(_RECIP):
            recip = _Msg(ole, path, _HEADER_SIZES["recip"], msg)
            if recip.props.get(_RECIPIENT_TYPE, 1) & 0xF != kind:
                continue
            addr = recip.text(_SMTP_ADDRESS) or recip.text(_EMAIL_ADDRESS)
            entry = _address(recip.text(_DISPLAY_NAME), addr)
            if entry:
                names.append(entry)
        return ", ".join(names)

    @staticmethod
    def _attachments(ole: olefile.OleFileIO, msg: _Msg) -> list[tuple[str, str, str, str, str]]:
        """(name, type, size, sha256, tag) per attachment storage, in number order.

        An attached message is named and never opened further; an embedded OLE object (a chart pasted from a
        workbook) sits in the same ``3701000D`` storage and is listed under its own type. Neither has bytes
        of its own to measure: size and sha256 are ``-``. ``tag`` is the storage's own type as
        ``Message.get_content_type`` gives a MIME part's (no parameters, lower case), whatever type the row
        lists: it is what ``_refuse_protected`` reads."""
        rows: list[tuple[str, str, str, str, str]] = []
        for index, path in enumerate(msg.children(_ATTACH), 1):
            att = _Msg(ole, path, _HEADER_SIZES["attach"], msg)
            name = att.text(_ATTACH_LONG_FILENAME) or att.text(_ATTACH_FILENAME) or att.text(_DISPLAY_NAME)
            mime = att.text(_ATTACH_MIME) or "application/octet-stream"
            tag = mime.split(";", 1)[0].strip().lower()
            embedded = (*path, f"{_SUBSTG}{_ATTACH_DATA:04X}{_PT_OBJECT:04X}")
            storage = ole.exists("/".join(embedded))
            inner = storage and ole.exists("/".join((*embedded, _PROPS)))
            if att.props.get(_ATTACH_METHOD) == _ATTACH_EMBEDDED or inner:
                subject = _Msg(ole, embedded, _HEADER_SIZES["embedded"], msg).text(_SUBJECT) if inner else ""
                name = name or (f"{subject}.msg" if subject else f"attached-message-{index}.msg")
                rows.append((_cell(name), "application/vnd.ms-outlook", "-", "-", tag))
            elif storage:
                rows.append((_cell(name or f"unnamed-{index} ({mime})"), _cell(mime), "-", "-", tag))
            else:
                data = att.stream(_ATTACH_DATA, _PT_BINARY) or b""
                rows.append(
                    (
                        _cell(name or f"unnamed-{index} ({mime})"),
                        _cell(mime),
                        str(len(data)),
                        hashlib.sha256(data).hexdigest(),
                        tag,
                    )
                )
        return rows

    @staticmethod
    def _refuse_protected(msg: _Msg, attachments: list[tuple[str, str, str, str, str]]) -> None:
        """S/MIME-encrypted or IRM (rpmsg) messages are UNREADABLE, never converted to a cover page.

        By message class, and by attachment name and type as ``EmlConverter._refuse_protected`` reads a MIME
        part: the type is the storage's own tag without its parameters, also for a storage listed as an
        attached message. No ``smime-type`` parameter is read to tell a signed PKCS #7 part from an enveloped
        one, so outside the signed class such a part refuses the message."""
        cls = msg.text(_MESSAGE_CLASS).lower()
        signed = cls.startswith(_SIGNED_CLASS)
        types = [row[4] for row in attachments]
        if (cls.startswith("ipm.note.smime") or any(t in _SMIME_ENCRYPTED for t in types)) and not signed:
            raise UnreadableSourceError("encrypted")
        if (
            "rpmsg" in cls
            or _RPMSG_TYPE in types
            or any(r[0].lower().endswith(".rpmsg") for r in attachments)
        ):
            raise UnreadableSourceError("IRM-protected")
