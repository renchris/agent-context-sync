"""Words that agree with the count printed beside them, and text from elsewhere made safe to print."""

from __future__ import annotations

import re
import unicodedata

# S9 rule 5, as the recording page applies it to its own SAID text: C0, DEL, the C1 block (NEL among it), lone
# surrogates, the two Unicode line breaks, the tag characters and the blank fillers; format characters (Cf)
# go by category.  ``str.splitlines`` breaks at several of these, so one left in would start a new line.
UNPRINTABLE_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\ud800-\udfff\u2028\u2029\U000e0000-\U000e007f\u115f\u1160\u3164\uffa0\u2800]"
)
# CSI and terminated OSC sequences, whole, in the 7-bit and 8-bit forms. Without its terminator an OSC is only
# its introducer, which UNPRINTABLE_RE takes: the words after it stay visible. The body excludes both
# introducers so a long run of them cannot make this quadratic.
_ESCAPE_RE = re.compile(
    r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]|(?:\x1b\]|\x9d)[^\x07\x1b\x9c\x9d]*(?:\x07|\x1b\\|\x9c)"
)


def plural(n: int, word: str, many: str | None = None) -> str:
    """``"1 <word>"``, or ``"<n> <many>"`` for any other ``n``, 0 included; ``many`` is ``word`` plus "s"
    unless given. A known count needs no "(s)" (B13 round 14 Q39; setup_report's ``_plural`` before it)."""
    return f"{n} {word if n == 1 else (many or word + 's')}"


def safe_text(s: str, *, cell: bool = False) -> str:
    """Text from elsewhere (a file name, a Graph value) as one printable line: no escape sequence, control,
    bidi or zero-width character can reach the terminal. A newline shows as the two characters ``\\n``. With
    ``cell=True`` a tab shows as ``\\t``, for a table whose columns tabs separate; otherwise it goes with the
    other controls."""
    shown = _ESCAPE_RE.sub("", s).replace("\n", "\\n")
    if cell:
        shown = shown.replace("\t", "\\t")
    return "".join(c for c in UNPRINTABLE_RE.sub("", shown) if unicodedata.category(c) != "Cf")
