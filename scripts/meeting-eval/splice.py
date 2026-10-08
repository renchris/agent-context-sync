#!/usr/bin/env python3
"""Splice a transcript's speech into a converter's evidence package, for the 9.2 run (spec section 10).

    uv run python scripts/meeting-eval/splice.py PACKAGE TRANSCRIPT OUT

PACKAGE is a converter output folder (``<name>.mp4.d/``: ``00-index.md`` and the ``NN-tHHMMSS.md`` window units,
with their ``.files/`` keyframes); TRANSCRIPT is a speech-only source, one ``[HH:MM:SS] text`` line per utterance
with no speaker (``transcript-only.md``).  OUT, which must not exist yet or be empty, gets a copy of the whole
package in which every transcript line is a ``[HH:MM:SS] SAID v1: text`` line of the window whose span holds its
time, inside the state on screen at that time (S9 rule 2), at its place in S9 rule 1's order: by time, and at one
time after KEYFRAME, TILE, SCREEN, SCREEN-, SCREEN+, SPEAKING and the SAID lines already there, before NOTE.

Speech text is cleaned as picture text is (S9 rule 5): no control character, lone surrogate or Unicode line break,
``<!--`` neutralised; a line left empty is dropped.  Headings, footer and sidecars are copied unchanged and the
index gains no Voices block.  It refuses, writing nothing: a transcript line it cannot parse, a time no window or
no state holds, and a cut window (its whole text is in ``full-text.txt``, which the footer's digest pins).

Nothing here names a fixture; every path is an argument.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

HMS = r"\d{2}:[0-5]\d:[0-5]\d"
WINDOW_NAME = re.compile(r"\d{2,}-t\d{6}\.md")
TITLE = re.compile(rf"# Recording ({HMS})-({HMS}) · window \d+ of \d+")
HEADING = re.compile(rf"## ({HMS})-({HMS}) · s\d{{3}} · .+")
TAGGED = re.compile(rf"\[({HMS})\] (SAID v\d{{1,2}}|[A-Z]+[+-]?): .*")
TRANSCRIPT_LINE = re.compile(rf"\[({HMS})\] (.*)")
TRUNCATED = "[truncated: "
SPEAKER = "SAID v1"
SAID_RANK = 5
RANK = {
    "KEYFRAME": 0,
    "TILE": 1,
    "SCREEN": 2,
    "SCREEN-": 3,
    "SCREEN+": 3,
    "SPEAKING": 4,
    "SAID": 5,
    "NOTE": 6,
}
UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff  ]")


class SpliceError(Exception):
    """The package or the transcript cannot be spliced; nothing was written."""


def seconds(hms: str) -> int:
    h, m, s = (int(part) for part in hms.split(":"))
    return h * 3600 + m * 60 + s


def clean(text: str) -> str:
    """Speech text as a page may print it (S9 rule 5)."""
    return UNPRINTABLE.sub("", text).replace("<!--", "&lt;!--").strip()


def read_transcript(text: str) -> list[tuple[int, str, str]]:
    """``(seconds, HH:MM:SS, cleaned text)`` of every utterance, in the transcript's order; blank lines and
    markdown headings are skipped, a line left empty by cleaning is dropped."""
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        match = TRANSCRIPT_LINE.fullmatch(line.strip())
        if match is None:
            raise SpliceError(f"transcript line {number}: not '[HH:MM:SS] text'")
        said = clean(match[2])
        if said:
            out.append((seconds(match[1]), match[1], said))
    return out


def _rank(line: str) -> tuple[int, int]:
    match = TAGGED.fullmatch(line)
    if match is None:
        raise SpliceError(f"not a tagged line inside a state: {line!r}")
    tag = "SAID" if match[2].startswith("SAID") else match[2]
    return seconds(match[1]), RANK.get(tag, RANK["NOTE"])


class Window:
    """One window unit: the lines before its first state, its states (heading and lines) and what follows."""

    def __init__(self, name: str, page: str) -> None:
        self.name = name
        lines = page.split("\n")
        title = next((i for i, line in enumerate(lines) if TITLE.fullmatch(line)), None)
        if title is None:
            raise SpliceError(f"{name}: no window title")
        match = TITLE.fullmatch(lines[title])
        assert match is not None
        self.start, self.end = seconds(match[1]), seconds(match[2])
        if any(line.startswith(TRUNCATED) for line in lines):
            raise SpliceError(f"{name}: a cut window (its whole text is in full-text.txt) cannot be spliced")
        self.head = lines[: title + 1]
        self.states: list[tuple[int, int, list[str]]] = []
        self.tail: list[str] = []
        current: list[str] | None = None
        for line in lines[title + 1 :]:
            heading = HEADING.fullmatch(line)
            if heading is not None and not self.tail:
                current = [line]
                self.states.append((seconds(heading[1]), seconds(heading[2]), current))
            elif not line.strip():
                if current is not None:
                    current = None
                (self.tail if self.tail else self._gap()).append(line)
            elif current is not None:
                current.append(line)
            else:
                self.tail.append(line)

    def _gap(self) -> list[str]:
        """Blank lines between states belong to the state above them; before the first, to the head."""
        return self.states[-1][2] if self.states else self.head

    def holds(self, t: int) -> bool:
        return self.start <= t < self.end

    def insert(self, t: int, hms: str, text: str) -> None:
        state = next((s for s in self.states if s[0] <= t < s[1]), None)
        if state is None:
            raise SpliceError(f"{self.name}: no state on screen at {hms}")
        body = state[2]
        end = len(body)
        while end > 1 and not body[end - 1].strip():
            end -= 1
        at = end
        while at > 1 and _rank(body[at - 1]) > (t, SAID_RANK):
            at -= 1
        body.insert(at, f"[{hms}] {SPEAKER}: {text}")

    def text(self) -> str:
        return "\n".join([*self.head, *(line for s in self.states for line in s[2]), *self.tail])


def splice(package: Path, transcript: Path, out: Path) -> tuple[int, int]:
    """Write the spliced copy of ``package`` to ``out``; return (speech lines, windows changed)."""
    if not (package / "00-index.md").is_file():
        raise SpliceError("the package has no 00-index.md")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise SpliceError("OUT exists and is not an empty folder")
    windows = [
        Window(p.name, p.read_text(encoding="utf-8"))
        for p in sorted(package.iterdir())
        if p.is_file() and WINDOW_NAME.fullmatch(p.name)
    ]
    if not windows:
        raise SpliceError("the package has no window units")
    lines = read_transcript(transcript.read_text(encoding="utf-8"))
    changed = set()
    for t, hms, text in lines:
        window = next((w for w in windows if w.holds(t)), None)
        if window is None:
            raise SpliceError(f"no window holds {hms}")
        window.insert(t, hms, text)
        changed.add(window.name)
    shutil.copytree(package, out, dirs_exist_ok=True)
    for window in windows:
        if window.name in changed:
            (out / window.name).write_text(window.text(), encoding="utf-8")
    return len(lines), len(changed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("package", type=Path)
    parser.add_argument("transcript", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    try:
        said, changed = splice(args.package, args.transcript, args.out)
    except SpliceError as exc:
        print(f"splice.py: {exc}", file=sys.stderr)
        return 1
    print(f"spliced {said} speech line(s) into {changed} window(s) under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
