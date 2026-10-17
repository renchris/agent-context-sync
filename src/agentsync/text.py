"""Words that agree with the count printed beside them."""

from __future__ import annotations


def plural(n: int, word: str, many: str | None = None) -> str:
    """``"1 <word>"``, or ``"<n> <many>"`` for any other ``n``, 0 included; ``many`` is ``word`` plus "s"
    unless given. A known count needs no "(s)" (B13 round 14 Q39; setup_report's ``_plural`` before it)."""
    return f"{n} {word if n == 1 else (many or word + 's')}"
