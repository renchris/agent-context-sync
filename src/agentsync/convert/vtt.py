"""Converter: a downloaded meeting transcript (WebVTT, as Teams, Zoom and Webex write it) as timed ``SAID``
turns (owner: convert; spec P3, ``vtt-turns``)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    _cap_body,
    _decode_text,
    _python_version,
    _unicode_version,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.model import RenderedUnit, UnitKind

_EMITTER_VERSION = "1.0.0"
_TURN_PAUSE_MS = 1000  # a pause of this much or more between two cues of one speaker starts a new turn
_NOT_VTT = "not a WebVTT transcript (no WEBVTT first line)"
_NO_CUE = "a WebVTT transcript with no cue"

_SIGNATURE_RE = re.compile(r"WEBVTT(?:[ \t].*)?")
_SKIPPED_BLOCK_RE = re.compile(r"(?:NOTE|STYLE|REGION)(?:[ \t].*)?")
_TIME = r"(?:(\d+):)?(\d\d):(\d\d)[.,](\d{3})"
_TIMING_RE = re.compile(rf"\s*{_TIME}[ \t]+-->[ \t]+{_TIME}(?:[ \t].*)?")
_TAG_RE = re.compile(r"<([^>]*)>")
_VOICE_RE = re.compile(r"v(?:\.[^\s>]*)?(?:[ \t\f]+(.*))?", re.DOTALL)
_ENTITY_RE = re.compile(r"&(amp|lt|gt|nbsp|lrm|rlm);")
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "nbsp": "\u00a0", "lrm": "\u200e", "rlm": "\u200f"}
_WS_RE = re.compile(r"\s+")
# S9 rule 5, as the recording page applies it to its own SAID text: C0, DEL, the C1 block (NEL among it), lone
# surrogates, the two Unicode line breaks, the tag characters and the blank fillers; format characters (Cf)
# go by category.  ``str.splitlines`` breaks at several of these, so one left in would start a new line.
_UNPRINTABLE_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\ud800-\udfff\u2028\u2029\U000e0000-\U000e007f\u115f\u1160\u3164\uffa0\u2800]"
)
_NAME_BREAK_RE = re.compile(r"[:\[\]\x00-\x1f\x7f-\x9f\u2028\u2029]")  # each becomes a space in a name
_FORGED_MARK = "[?]"  # text ending so would read as the low-confidence mark of a recording page


@dataclass(frozen=True, slots=True)
class _Said:
    """One voice's words in one cue: ``speaker`` "" for words under no voice tag."""

    start_ms: int
    end_ms: int
    speaker: str
    text: str


def _ms(hours: str | None, minutes: str, seconds: str, millis: str) -> int:
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def _clock(ms: int) -> str:
    """``HH:MM:SS`` of a cue time, floored to the second."""
    seconds = ms // 1000
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def _decode_entities(text: str) -> str:
    """The six character references a transcript writes, in one pass (``&amp;lt;`` stays ``&lt;``)."""
    return _ENTITY_RE.sub(lambda m: _ENTITIES[m[1]], text)


def _clean(text: str) -> str:
    """Cue text as a page may print it: no unprintable or format character, every ``<`` printed ``&lt;``
    (no HTML block or ``<!--`` anchor opens) and ``![`` printed ``!\\[``, a trailing ``[?]`` printed ``(?)``,
    whitespace runs one space."""
    kept = "".join(c for c in _UNPRINTABLE_RE.sub(" ", text) if unicodedata.category(c) != "Cf")
    out = unicodedata.normalize("NFC", kept).replace("<", "&lt;").replace("![", "!\\[")
    out = _WS_RE.sub(" ", out).strip()
    return out[: -len(_FORGED_MARK)] + "(?)" if out.endswith(_FORGED_MARK) else out


def _name(annotation: str) -> str:
    """A voice tag's name as a ``SAID`` line may hold it: never ``:``, ``[``, ``]`` or a control character
    (each a space), cleaned as text is, whitespace runs one space; "" when nothing is left."""
    return _clean(_NAME_BREAK_RE.sub(" ", _decode_entities(annotation)))


def _cue_said(start_ms: int, end_ms: int, payload: str) -> list[_Said]:
    """The voices' words in one cue's payload, in order.  A ``<v Name>`` (or ``<v.class Name>``) span runs
    to its ``</v>`` or the cue's end; every other tag (``<c>``, ``<b>``, ``<i>``, ``<u>``, ``<lang>``,
    ``<ruby>``, a timestamp) is dropped with its text kept."""
    runs: list[tuple[str, list[str]]] = [("", [])]
    pos = 0
    for tag in _TAG_RE.finditer(payload):
        runs[-1][1].append(payload[pos : tag.start()])
        pos = tag.end()
        inner = tag[1]
        if voice := _VOICE_RE.fullmatch(inner):
            runs.append((_name(voice[1] or ""), []))
        elif inner.strip() == "/v":
            runs.append(("", []))
    runs[-1][1].append(payload[pos:])
    out: list[_Said] = []
    for speaker, parts in runs:
        text = _clean(_decode_entities("".join(parts)))
        if not text:
            continue
        if out and out[-1].speaker == speaker:
            out[-1] = _Said(start_ms, end_ms, speaker, f"{out[-1].text} {text}")
        else:
            out.append(_Said(start_ms, end_ms, speaker, text))
    return out


def _cues(text: str) -> list[_Said]:
    """Every cue's words, in start order (a stable sort: cues that start together keep the file's order).
    The header block and ``NOTE``, ``STYLE`` and ``REGION`` blocks are skipped; a cue identifier is optional;
    cue settings after the timing are ignored; a block without a timing line is no cue."""
    said: list[_Said] = []
    blocks = re.split(r"\n[ \t]*\n", text)
    for block in blocks[1:]:  # the first block is the WEBVTT header and its lines
        lines = block.strip("\n").split("\n")
        if not lines or _SKIPPED_BLOCK_RE.fullmatch(lines[0]):
            continue
        at = 0 if "-->" in lines[0] else 1
        timing = _TIMING_RE.fullmatch(lines[at]) if at < len(lines) else None
        if timing is None:
            continue
        start, end = _ms(*timing.groups()[:4]), _ms(*timing.groups()[4:])
        said += _cue_said(start, end, "\n".join(lines[at + 1 :]))
    return sorted(said, key=lambda s: s.start_ms)


def _turns(said: list[_Said]) -> list[_Said]:
    """Consecutive cues of one named speaker as one turn, closed at a change of speaker or at a pause of
    ``_TURN_PAUSE_MS`` or more between one cue's end and the next one's start.  Words under no voice tag are
    never merged: nothing says they are one person's."""
    turns: list[_Said] = []
    for s in said:
        last = turns[-1] if turns else None
        if (
            last is not None
            and s.speaker
            and s.speaker == last.speaker
            and s.start_ms - last.end_ms < _TURN_PAUSE_MS
        ):
            turns[-1] = _Said(last.start_ms, max(last.end_ms, s.end_ms), s.speaker, f"{last.text} {s.text}")
        else:
            turns.append(s)
    return turns


class VttConverter:
    """A meeting transcript (``.vtt``) as one line per turn: ``[HH:MM:SS] SAID <name>: <text>``.

    A turn is consecutive cues of one speaker, closed at a change of speaker or at a pause of 1 s or more
    between cues (lead decision); its time is its first cue's start, floored to the second.  Words under no
    voice tag print ``SAID:`` and are never merged.  The name is the voice tag's, as the file says it: the
    curate rules check it before a page may use it (spec 5 rule 5), while the wording leads.  A name never
    holds ``:``, ``[`` or ``]`` and the text is cleaned as a recording page's (S9 rule 5), so every body
    line begins with ``[`` and none can open a comment, an HTML block or a forged tag.  The curate lint reads
    a page whose ``converter`` starts with ``vtt-turns@`` as a transcript; the id is part of that contract.

    No ``WEBVTT`` first line (after an optional BOM), or no cue: UnreadableSourceError, a fixed wording.
    Output depends on the bytes only.  ``replaces``: pages ``text-plain`` made of a ``.vtt`` before this
    converter existed are read again once (the cycle's re-read pass)."""

    converter_id = "vtt-turns"
    extensions: tuple[str, ...] = (".vtt",)
    replaces: tuple[str, ...] = ("text-plain",)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""
        return f"{_EMITTER_VERSION}+python-{_python_version()}+unicode-{_unicode_version()}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        return {
            "decode": "bom,utf-8,cp1252,latin-1",
            "max_page_bytes": self._cfg.max_page_bytes,
            "turn_pause_ms": _TURN_PAUSE_MS,
        }

    @property
    def outdated_key(self) -> str:
        """What the cycle's re-read record goes by besides: the converters this one replaces.  It moves the
        record's capability digest, so a source whose re-reads were done looks once more for their pages."""
        return "replaces " + ",".join(self.replaces)

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        try:
            data = src.read_bytes()
        except OSError as exc:
            raise ConversionError(f"cannot read: {exc}") from exc
        try:
            text = _decode_text(data).removeprefix("\ufeff")
        except ConversionError:
            raise UnreadableSourceError(_NOT_VTT) from None
        if not _SIGNATURE_RE.fullmatch(text.split("\n", 1)[0]):
            raise UnreadableSourceError(_NOT_VTT)
        turns = _turns(_cues(text))
        if not turns:
            raise UnreadableSourceError(_NO_CUE)
        lines = [
            f"[{_clock(t.start_ms)}] SAID{' ' + t.speaker if t.speaker else ''}: {t.text}" for t in turns
        ]
        body, sidecars = _cap_body(
            "\n".join(lines) + "\n", self._cfg.max_page_bytes, sidecar_name=_FULL_TEXT_SIDECAR
        )
        speakers = len({t.speaker for t in turns if t.speaker})
        return (
            make_unit(
                unit_id="whole",
                kind=UnitKind.WHOLE,
                index=0,
                of=1,
                name="",
                file_stem="",
                title="Meeting transcript",
                summary=(
                    f"Meeting transcript (WebVTT): {len(turns)} turn(s), {speakers} named speaker(s), "
                    f"{_clock(max(t.end_ms for t in turns))} long"
                ),
                body=body,
                sidecars=sidecars,
            ),
        )
