#!/usr/bin/env python3
"""Score one blind-reader run of a meeting-recording fixture against spec 9.1 and the pass marks of 9.2.

    uv run python scripts/meeting-eval/score.py FIXTURE --marks MARKS [--json]

FIXTURE is a folder outside the repository holding ``questions-gold.json``, the three readers' answers
(``answers-A*.json``, ``answers-B*.json``, ``answers-C*.json``), reader A's source (``transcript-only.md``, else
``transcript.md``) and the evidence package's text (``evidence/``, every ``*.md`` under it).  ``--answers-dir``
reads the answers from another folder.

Correctness is a judge's call (1, 0.5 or 0; "cannot tell" is 0 unless gold is "not said" or "not shown"), so it
comes in as MARKS: either a ``marks.json`` (``{"Q01": {"A": {"score": 1, "cannot_tell": false, "wrong":
false}, ...}}``, the form ``run.py judge-brief`` asks for) or the judge's ``VERDICT.md``, whose per-question table
is read in either of the two layouts the v2 verdicts used.  Everything else is computed here:

- citation: the cited time within 30 s of the gold time (strict), or of any valid gold time (lenient): the gold
  ``times`` list when there is one, else ``time`` plus every time, time range and frame named in
  ``verification`` (frame ``NNNNN.jpg`` is second NNNNN at ``--frame-fps``, 1 for the v2 fixtures);
- quoted pieces: every fragment of the ``evidence``, ``supporting_line`` and ``quote`` fields, split on `` / ``
  and `` ... ``, with its time and tag prefix taken off, must be a substring of that reader's sources
  (whitespace folded);
- wrong and confident: a mark that is wrong (0 and not "cannot tell") on an answer whose confidence is high.

Exit 0 when every 9.2 mark holds, 1 when one fails, 2 when the input cannot be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

READERS = ("A", "B", "C")
CATEGORIES = ("SPEECH", "SCREEN", "CROSS")
CITE_S = 30
MAX_IMAGES = 25
HMS = r"\d{1,2}:\d{2}:\d{2}(?:\.\d+)?"
QUOTE_FIELD = re.compile(r"(evidence|supporting_line|quote)(_?\d+)?")
PREFIX = re.compile(
    r"^(?:\[\d{1,2}:\d{2}(?::\d{2})?(?:[^\]]*)\]\s*)?"
    r"(?:(?:SAID(?: v\d+)?|SCREEN[+-]?|TILE|SPEAKING|NOTE|KEYFRAME|TERM|VOICE):\s*)?"
)


class InputError(Exception):
    """The fixture, the answers or the marks cannot be read."""


def seconds(text: str) -> float:
    parts = [float(p) for p in text.strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def fold(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"{path.name}: {exc}") from exc


def load_gold(fixture: Path) -> list[dict]:
    doc = load_json(fixture / "questions-gold.json")
    questions = doc["questions"] if isinstance(doc, dict) else doc
    if not isinstance(questions, list) or not questions:
        raise InputError("questions-gold.json holds no questions")
    return questions


def category(question: dict) -> str:
    word = str(question.get("category", "")).split(",")[0].split()[0].upper()
    if word not in CATEGORIES:
        raise InputError(f"{question.get('id')}: category {question.get('category')!r}")
    return word


def load_answers(folder: Path, reader: str) -> tuple[dict[str, dict], int | None]:
    """The reader's answers by question id, and the number of images it says it opened."""
    found = sorted(folder.glob(f"answers-{reader}*.json"))
    if len(found) != 1:
        raise InputError(f"expected one answers-{reader}*.json in {folder.name}, found {len(found)}")
    doc = load_json(found[0])
    items = doc.get("answers", []) if isinstance(doc, dict) else doc
    answers, images = {}, None
    for item in items:
        if str(item.get("id", "")).startswith("_"):
            images = item.get("images_opened", images)
            continue
        answers[str(item["id"])] = item
        if isinstance(item.get("images_opened"), int):
            images = max(images or 0, item["images_opened"])
    return answers, images


def sources(fixture: Path) -> dict[str, str]:
    transcript = next(
        (fixture / n for n in ("transcript-only.md", "transcript.md") if (fixture / n).is_file()), None
    )
    evidence = sorted((fixture / "evidence").rglob("*.md"))
    if transcript is None or not evidence:
        raise InputError("the fixture needs transcript-only.md (or transcript.md) and evidence/*.md")
    text = "\n".join(p.read_text(encoding="utf-8") for p in evidence)
    return {"A": fold(transcript.read_text(encoding="utf-8")), "B": fold(text), "C": fold(text)}


# ---------------------------------------------------------------------------------------------------------
# citation and quotes
# ---------------------------------------------------------------------------------------------------------


def valid_times(question: dict, frame_fps: float) -> tuple[list[float], list[tuple[float, float]]]:
    """Points and ranges of media time at which a citation of this question is right."""
    if isinstance(question.get("times"), list):
        points, spans = [], []
        for t in map(str, question["times"]):
            start, _, end = t.partition("-")
            if end:
                spans.append((seconds(start), seconds(end)))
            else:
                points.append(seconds(t))
        return points, spans
    points, spans = [seconds(question["time"])], []
    text = str(question.get("verification", ""))
    for a, b in re.findall(rf"({HMS})\s*-\s*({HMS})", text):
        spans.append((seconds(a), seconds(b)))
    points += [seconds(t) for t in re.findall(HMS, text)]
    if frame_fps > 0:
        points += [int(n) / frame_fps for n in re.findall(r"\b(\d{5})\.jpg\b", text)]
    return points, spans


def cited(answer: dict) -> float | None:
    match = re.search(r"\d{1,2}:\d{2}(?::\d{2})?", str(answer.get("time") or ""))
    return seconds(match[0]) if match else None


def fragments(answer: dict) -> list[str]:
    out = []
    for key, value in answer.items():
        if not QUOTE_FIELD.fullmatch(key) or not isinstance(value, str):
            continue
        for piece in re.split(r"\s+/\s+|\s+\.\.\.\s+|\s*…\s*", value):
            piece = PREFIX.sub("", piece.strip())
            piece = re.sub(r"^(>\s*)+", "", piece).strip().strip("'\"“”‘’").strip()
            if piece:
                out.append(piece)
    return out


# ---------------------------------------------------------------------------------------------------------
# marks
# ---------------------------------------------------------------------------------------------------------


def _mark(cell: str) -> dict:
    match = re.search(r"\d+(?:\.\d+)?", cell)
    if match is None:
        raise InputError(f"no score in the cell {cell!r}")
    low = cell.lower()
    return {
        "score": float(match[0]),
        "cannot_tell": bool(re.search(r"\bct\b|cannot tell", low)),
        "wrong": bool(re.search(r"\bwrong\b|\bw\b", low)),
    }


def marks_from_verdict(text: str) -> dict[str, dict[str, dict]]:
    """The per-question table of a judge's VERDICT.md: either ``| Q | Cat | A corr | A cit | B corr | ...`` or
    ``| Q | Cat | A | B | C | ...`` with ``score / citation`` cells.  The last such table wins."""
    found: dict[str, dict[str, dict]] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        head = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.startswith("|") or head[0] != "Q":
            continue
        cols = {}
        for r in READERS:
            for name in (f"{r} corr", r):
                if name in head:
                    cols[r] = head.index(name)
                    break
        if len(cols) != len(READERS):
            continue
        table = {}
        for row in lines[i + 2 :]:
            if not row.startswith("|"):
                break
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            table[cells[0]] = {r: _mark(cells[c].split("/")[0]) for r, c in cols.items()}
        found = table or found
    if not found:
        raise InputError("no per-question table with A, B and C columns in the verdict")
    return found


def load_marks(path: Path) -> dict[str, dict[str, dict]]:
    if path.suffix == ".md":
        return marks_from_verdict(path.read_text(encoding="utf-8"))
    doc = load_json(path)
    if not isinstance(doc, dict):
        raise InputError("marks.json is an object keyed by question id")
    return {
        qid: {
            r: _mark(str(m)) if not isinstance(m, dict) else {"cannot_tell": False, "wrong": False, **m}
            for r, m in by_reader.items()
        }
        for qid, by_reader in doc.items()
    }


# ---------------------------------------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------------------------------------


def score(fixture: Path, marks: dict, *, answers_dir: Path | None = None, frame_fps: float = 1.0) -> dict:
    gold = load_gold(fixture)
    texts = sources(fixture)
    readers = {}
    for r in READERS:
        answers, images = load_answers(answers_dir or fixture, r)
        row = {
            "correct": 0.0,
            **{c: 0.0 for c in CATEGORIES},
            "cite_strict": 0,
            "cite_lenient": 0,
            "quotes": 0,
            "quotes_found": 0,
            "missing_quotes": [],
            "wrong": 0,
            "wrong_and_confident": [],
            "images_opened": images,
        }
        for q in gold:
            qid = q["id"]
            if qid not in marks or r not in marks[qid]:
                raise InputError(f"no mark for {qid} reader {r}")
            mark, answer = marks[qid][r], answers.get(qid, {})
            row["correct"] += mark["score"]
            row[category(q)] += mark["score"]
            wrong = mark["wrong"] or (mark["score"] == 0 and not mark["cannot_tell"])
            row["wrong"] += wrong
            if wrong and str(answer.get("confidence", "")).lower() == "high":
                row["wrong_and_confident"].append(qid)
            at = cited(answer)
            if at is not None:
                points, spans = valid_times(q, frame_fps)
                row["cite_strict"] += abs(at - seconds(q["time"])) <= CITE_S
                row["cite_lenient"] += any(abs(at - p) <= CITE_S for p in points) or any(
                    a <= at <= b for a, b in spans
                )
            for piece in fragments(answer):
                row["quotes"] += 1
                if fold(piece) in texts[r]:
                    row["quotes_found"] += 1
                else:
                    row["missing_quotes"].append(f"{qid}: {piece[:80]}")
        readers[r] = row
    counts = {c: sum(category(q) == c for q in gold) for c in CATEGORIES}
    return {"questions": len(gold), "counts": counts, "readers": readers, "checks": checks(readers, counts)}


def checks(readers: dict, counts: dict) -> list[dict]:
    """The 9.2 rows that a run computes.  The fixed numbers are the measured results less one question, for
    fixtures of 18 questions, 6 per category (9.1)."""
    a, b, c = readers["A"], readers["B"], readers["C"]
    wac = sum(len(r["wrong_and_confident"]) for r in readers.values())
    rows = [
        ("B correctness, all", b["correct"], b["correct"] >= 15.5, "15.5 or more"),
        (
            "B, SPEECH",
            b["SPEECH"],
            b["SPEECH"] == counts["SPEECH"],
            f"{counts['SPEECH']} of {counts['SPEECH']}",
        ),
        ("B, SCREEN plus CROSS", b["SCREEN"] + b["CROSS"], b["SCREEN"] + b["CROSS"] >= 9.5, "9.5 or more"),
        ("B minus A", b["correct"] - a["correct"], b["correct"] - a["correct"] >= 6, "+6 or more"),
        ("C minus B", c["correct"] - b["correct"], c["correct"] - b["correct"] >= 0, "0 or more"),
        ("Wrong and confident, any reader", wac, wac == 0, "0"),
        ("Quoted pieces found in the sources, B", b["quotes_found"], b["quotes_found"] == b["quotes"], "all"),
        ("B citation within 30 s of a gold time", b["cite_lenient"], b["cite_lenient"] >= 17, "17 or more"),
        (
            "C images opened",
            c["images_opened"],
            c["images_opened"] is None or c["images_opened"] <= MAX_IMAGES,
            f"{MAX_IMAGES} or fewer",
        ),
    ]
    return [{"check": n, "measured": m, "pass": bool(p), "mark": k} for n, m, p, k in rows]


def number(value: object) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return "not given" if value is None else str(value)


def render(result: dict) -> str:
    out = [f"questions: {result['questions']} ({', '.join(f'{k} {v}' for k, v in result['counts'].items())})"]
    if result["questions"] != 18 or set(result["counts"].values()) != {6}:
        out.append("warning: the 9.2 marks are set for 18 questions, 6 per category")
    out += [
        "",
        "| Reader | All | SPEECH | SCREEN | CROSS | Cite strict | Cite lenient | Quotes found | Wrong | "
        "Wrong and confident | Images |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r, row in result["readers"].items():
        out.append(
            f"| {r} | {number(row['correct'])} | {number(row['SPEECH'])} | {number(row['SCREEN'])} | "
            f"{number(row['CROSS'])} | {row['cite_strict']} | {row['cite_lenient']} | "
            f"{row['quotes_found']} of {row['quotes']} | {row['wrong']} | {len(row['wrong_and_confident'])} | "
            f"{number(row['images_opened'])} |"
        )
    out += ["", "| 9.2 check | Measured | Pass mark | Result |", "|---|---|---|---|"]
    for c in result["checks"]:
        measured = number(c["measured"])
        if (
            c["check"] in ("B minus A", "C minus B")
            and isinstance(c["measured"], float)
            and c["measured"] >= 0
        ):
            measured = "+" + measured
        out.append(f"| {c['check']} | {measured} | {c['mark']} | {'pass' if c['pass'] else 'FAIL'} |")
    out.append("")
    out.append("Not computed: the three known B misses of 9.2 are a judge's reading of the B answers.")
    for r, row in result["readers"].items():
        out += [f"{r} quote not found: {m}" for m in row["missing_quotes"]]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("fixture", type=Path)
    parser.add_argument(
        "--marks", type=Path, help="marks.json or the judge's VERDICT.md (default: FIXTURE/marks.json)"
    )
    parser.add_argument("--answers-dir", type=Path)
    parser.add_argument(
        "--frame-fps", type=float, default=1.0, help="frame rate of NNNNN.jpg names in verification"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        marks = load_marks(args.marks or args.fixture / "marks.json")
        result = score(args.fixture, marks, answers_dir=args.answers_dir, frame_fps=args.frame_fps)
    except (InputError, KeyError, TypeError, OSError) as exc:
        print(f"score.py: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1) if args.json else render(result))
    return 0 if all(c["pass"] for c in result["checks"]) else 1


if __name__ == "__main__":
    sys.exit(main())
