#!/usr/bin/env python3
"""Run the three blind readers of spec 9.1 over a fixture, then brief the judge whose marks ``score.py`` reads.

    uv run python scripts/meeting-eval/run.py prepare FIXTURE OUT [--evidence DIR]
    uv run python scripts/meeting-eval/run.py ask OUT [--readers A,B,C] [--command "claude -p"] [--timeout 3600]
    uv run python scripts/meeting-eval/run.py judge-brief FIXTURE OUT

FIXTURE is a folder outside the repository with ``questions-gold.json`` (and ``questions-blind.json``, else the
blind copy is made by dropping every gold field), the speech-only source ``transcript-only.md`` (else the
``SAID`` lines of the package are taken) and the evidence package (``evidence/``, or ``--evidence DIR``, such as a
converter's ``<name>.mp4.d/`` folder).

``prepare`` gives each reader a folder of its own holding only its inputs: A the speech lines; B the package's
text, no picture; C the text and the keyframes, of which it may open at most 25.  Each gets the same brief and the
blind questions.  ``ask`` runs ``--command`` once per reader, in that reader's folder, with the brief on stdin, and
copies the ``answers.json`` the reader wrote to ``OUT/answers-<reader>.json``.  A fresh process per reader is
the "fresh session"; that it reads only its folder rests on the brief, not on a sandbox.  ``judge-brief`` writes
``OUT/judge/BRIEF.md`` with the 9.1 scoring rule, the gold and the three answer files; the judge writes
``marks.json``, and ``score.py FIXTURE --answers-dir OUT --marks OUT/judge/marks.json`` scores the run.

Nothing here names a fixture; every path is an argument.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

READERS = ("A", "B", "C")
MAX_IMAGES = 25
GOLD_ONLY = ("gold_answer", "time", "times", "verification", "expects_not_shown_or_not_discussed")
SAID = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] SAID\b.*$", re.MULTILINE)

SOURCES = {
    "A": "transcript.md, a transcript of the meeting's speech with times",
    "B": "evidence/, the text of an evidence package: speech and on-screen text with times",
    "C": f"evidence/, the text of an evidence package plus its keyframe pictures; open at most {MAX_IMAGES} pictures",
}

BRIEF = """# Blind reader {reader}

You answer questions about one recorded meeting from the files in this folder only: {source}. Do not open any
file outside this folder, search the web, or use what you may know about the meeting.

The questions are in questions.json. For each one, write an entry to answers.json, a JSON list:

    {{"id": "<question id>", "answer": "<your answer>", "time": "HH:MM:SS",
     "evidence": "<the exact line or lines you relied on, copied from the files, separated by ' / '>",
     "confidence": "high" | "medium" | "low"}}

- "time" is the moment in the recording that supports the answer.
- Copy evidence exactly as it stands in the files; never reword it.
- When the files do not hold the answer, say "cannot tell" and name what is missing. A wrong answer costs more
  than "cannot tell".
- End the list with {{"id": "_meta", "images_opened": <number of pictures you opened>, "files_read": [...]}}.

Write answers.json and stop.
"""

JUDGE = """# Judge

Score three blind readers' answers to the same questions against gold (spec 9.1). Gold is gold.json; the answers
are answers-A.json (speech only), answers-B.json (evidence text) and answers-C.json (text and pictures).

Correctness per answer: 1 when right; 0.5 when one asked-for part is right and the rest declined, with nothing
asserted wrongly; 0 otherwise. "Cannot tell" scores 0 unless gold is "not said" or "not shown", where it
scores 1. Mark a 0 that says "cannot tell" as cannot_tell, and a 0 that asserts something false as wrong.

Write marks.json: {{"<question id>": {{"A": {{"score": 1, "cannot_tell": false, "wrong": false}}, "B": ..., "C": ...}}}}
for every question, then stop. Citation, quotes and confidence are scored by score.py, not by you.
"""


def load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def blind_questions(fixture: Path) -> list[dict]:
    blind = fixture / "questions-blind.json"
    if blind.is_file():
        doc = load(blind)
        return doc["questions"] if isinstance(doc, dict) else doc
    gold = load(fixture / "questions-gold.json")
    gold = gold["questions"] if isinstance(gold, dict) else gold
    return [{k: v for k, v in q.items() if k not in GOLD_ONLY} for q in gold]


def prepare(fixture: Path, out: Path, evidence: Path) -> None:
    texts = sorted(evidence.rglob("*.md"))
    if not texts:
        raise SystemExit(f"run.py: no *.md under {evidence}")
    questions = blind_questions(fixture)
    if any("gold_answer" in q or "verification" in q for q in questions):
        raise SystemExit("run.py: the blind questions hold gold fields")
    transcript = fixture / "transcript-only.md"
    speech = (
        transcript.read_text(encoding="utf-8")
        if transcript.is_file()
        else "\n".join(m for p in texts for m in SAID.findall(p.read_text(encoding="utf-8"))) + "\n"
    )
    for reader in READERS:
        folder = out / reader
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        (folder / "questions.json").write_text(json.dumps(questions, indent=1), encoding="utf-8")
        (folder / "BRIEF.md").write_text(
            BRIEF.format(reader=reader, source=SOURCES[reader]), encoding="utf-8"
        )
        if reader == "A":
            (folder / "transcript.md").write_text(speech, encoding="utf-8")
            continue
        for path in sorted(evidence.rglob("*")):
            keep = path.suffix == ".md" or (
                reader == "C" and path.suffix.lower() in (".jpg", ".jpeg", ".png")
            )
            if path.is_file() and keep:
                target = folder / "evidence" / path.relative_to(evidence)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
    print(f"prepared {', '.join(READERS)} under {out}")


def ask(out: Path, readers: list[str], command: str, timeout: float) -> int:
    failed = 0
    for reader in readers:
        folder = out / reader
        brief = (folder / "BRIEF.md").read_text(encoding="utf-8")
        done = subprocess.run(
            shlex.split(command),
            input=brief,
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        answers = folder / "answers.json"
        try:
            json.loads(answers.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(
                f"reader {reader}: no readable answers.json (exit {done.returncode}): {exc}", file=sys.stderr
            )
            failed += 1
            continue
        shutil.copyfile(answers, out / f"answers-{reader}.json")
        print(f"reader {reader}: answers-{reader}.json")
    return 1 if failed else 0


def judge_brief(fixture: Path, out: Path) -> None:
    folder = out / "judge"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture / "questions-gold.json", folder / "gold.json")
    for reader in READERS:
        shutil.copyfile(out / f"answers-{reader}.json", folder / f"answers-{reader}.json")
    (folder / "BRIEF.md").write_text(JUDGE.format(), encoding="utf-8")
    print(f"judge brief at {folder / 'BRIEF.md'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("fixture", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--evidence", type=Path)
    a = sub.add_parser("ask")
    a.add_argument("out", type=Path)
    a.add_argument("--readers", default="A,B,C")
    a.add_argument("--command", default="claude -p")
    a.add_argument("--timeout", type=float, default=3600)
    j = sub.add_parser("judge-brief")
    j.add_argument("fixture", type=Path)
    j.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    if args.verb == "prepare":
        prepare(args.fixture, args.out, args.evidence or args.fixture / "evidence")
        return 0
    if args.verb == "ask":
        readers = [r.strip() for r in args.readers.split(",") if r.strip()]
        if set(readers) - set(READERS):
            parser.error("--readers takes A, B and C")
        return ask(args.out, readers, args.command, args.timeout)
    judge_brief(args.fixture, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
