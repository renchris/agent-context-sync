"""scripts/meeting-eval/: the 9.1 and 9.2 scorer, the C19 layout scorer and the blind-reader runner.

The real fixtures are public recordings of named people and stay outside the repository (spec 9.1); these
tests build a made-up Contoso fixture of the same shape in ``tmp_path``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"meeting_eval_{name}", ROOT / "scripts" / "meeting-eval" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


score = _script("score")
layout = _script("layout")
runner = _script("run")

CATEGORIES = ["SPEECH"] * 6 + ["SCREEN"] * 6 + ["CROSS"] * 6
SPEECH_LINE = "[00:{m:02d}:10] SAID: Dana said the west budget is {n} thousand"
SCREEN_LINE = "> Contoso budget row {n} | {n}00"


def clock(total: int) -> str:
    return f"{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}"


def gold() -> list[dict[str, Any]]:
    return [
        {
            "id": f"Q{i + 1:02d}",
            "category": cat,
            "question": f"What did the Contoso review say about item {i + 1}?",
            "gold_answer": f"item {i + 1}",
            "time": clock(60 * i + 10),
            "verification": f"cues {clock(60 * i + 70)}-{clock(60 * i + 90)}; frame {60 * i + 200:05d}.jpg",
        }
        for i, cat in enumerate(CATEGORIES)
    ]


def answers(reader: str, *, offset: int = 0, confidence: str = "high") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"id": "_meta", "images_opened": 7 if reader == "C" else 0}]
    for i in range(18):
        quote = (
            SPEECH_LINE.format(m=i, n=i)
            if reader == "A"
            else f"{SPEECH_LINE.format(m=i, n=i)} / " + (SCREEN_LINE.format(n=i))
        )
        out.append(
            {
                "id": f"Q{i + 1:02d}",
                "answer": f"item {i + 1}",
                "time": clock(60 * i + 10 + offset),
                "evidence": quote,
                "confidence": confidence,
            }
        )
    return out


def marks(a: list[float], b: list[float], c: list[float]) -> dict[str, Any]:
    return {
        f"Q{i + 1:02d}": {
            r: {"score": s[i], "cannot_tell": s[i] == 0, "wrong": False}
            for r, s in zip("ABC", (a, b, c), strict=True)
        }
        for i in range(18)
    }


A_MARKS = [1.0] * 6 + [0.0] * 12
B_MARKS = [1.0] * 6 + [1.0] * 10 + [0.5, 0.0]
C_MARKS = [1.0] * 18


def fixture(tmp_path: Path, **kw: Any) -> Path:
    root = tmp_path / "fixture"
    (root / "evidence").mkdir(parents=True)
    (root / "questions-gold.json").write_text(json.dumps({"questions": gold()}))
    speech = "\n".join(SPEECH_LINE.format(m=i, n=i) for i in range(18))
    (root / "transcript-only.md").write_text(speech.replace("SAID: ", "") + "\n")
    (root / "evidence" / "w-0000.md").write_text(
        speech + "\n" + "\n".join(SCREEN_LINE.format(n=i) for i in range(18))
    )
    for reader in "ABC":
        name = {"A": "answers-A-transcript.json", "B": "answers-B-text.json", "C": "answers-C-frames.json"}[
            reader
        ]
        (root / name).write_text(json.dumps(kw.get(reader, answers(reader))))
    (root / "marks.json").write_text(json.dumps(kw.get("marks", marks(A_MARKS, B_MARKS, C_MARKS))))
    return root


def checks(result: dict[str, Any]) -> dict[str, tuple[Any, bool]]:
    return {c["check"]: (c["measured"], c["pass"]) for c in result["checks"]}


def test_a_run_at_the_v2_figures_meets_every_mark(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = fixture(tmp_path)
    assert score.main([str(root)]) == 0
    out = capsys.readouterr().out
    assert "| B correctness, all | 16.5 | 15.5 or more | pass |" in out
    assert "| B minus A | +10.5 |" in out and "| C minus B | +1.5 |" in out
    result = score.score(root, score.load_marks(root / "marks.json"))
    b = result["readers"]["B"]
    assert (b["SPEECH"], b["SCREEN"] + b["CROSS"], b["quotes_found"], b["quotes"]) == (6, 10.5, 36, 36)
    assert result["readers"]["A"]["quotes_found"] == 18, "A's source is the transcript, not the package"


def test_a_regressed_run_fails_its_marks(tmp_path: Path) -> None:
    worse = [1.0] * 5 + [0.5] + [1.0] * 9 + [0.0] * 3
    root = fixture(tmp_path, marks=marks(A_MARKS, worse, C_MARKS))
    result = checks(score.score(root, score.load_marks(root / "marks.json")))
    assert result["B correctness, all"] == (14.5, False)
    assert result["B, SPEECH"] == (5.5, False)
    assert result["C minus B"] == (3.5, True)
    assert score.main([str(root)]) == 1


def test_wrong_and_confident_counts_only_a_wrong_mark_given_with_high_confidence(tmp_path: Path) -> None:
    graded = marks(A_MARKS, B_MARKS, C_MARKS)
    graded["Q18"]["B"] = {"score": 0, "cannot_tell": False, "wrong": False}
    root = fixture(tmp_path, marks=graded)
    result = score.score(root, score.load_marks(root / "marks.json"))
    assert result["readers"]["B"]["wrong_and_confident"] == ["Q18"]
    assert checks(result)["Wrong and confident, any reader"] == (1, False)
    unsure = answers("B", confidence="low")
    root = fixture(tmp_path / "low", B=unsure, marks=graded)
    assert checks(score.score(root, score.load_marks(root / "marks.json")))[
        "Wrong and confident, any reader"
    ][1]


def test_citation_is_strict_on_the_gold_time_and_lenient_on_every_time_gold_names(tmp_path: Path) -> None:
    root = fixture(tmp_path, B=answers("B", offset=75))
    b = score.score(root, score.load_marks(root / "marks.json"))["readers"]["B"]
    assert (b["cite_strict"], b["cite_lenient"]) == (0, 18), "inside the verification range"
    root = fixture(tmp_path / "frame", B=answers("B", offset=185))
    b = score.score(root, score.load_marks(root / "marks.json"))["readers"]["B"]
    assert b["cite_lenient"] == 18, "within 30 s of the frame gold names, at one frame a second"
    b = score.score(root, score.load_marks(root / "marks.json"), frame_fps=0)["readers"]["B"]
    assert b["cite_lenient"] == 0
    root = fixture(tmp_path / "far", B=answers("B", offset=300))
    assert (
        checks(score.score(root, score.load_marks(root / "marks.json")))[
            "B citation within 30 s of a gold time"
        ][1]
        is False
    )


def test_a_quote_that_is_not_in_the_readers_sources_is_counted(tmp_path: Path) -> None:
    forged = answers("B")
    forged[3]["evidence"] = "[00:02:10] SAID: Dana approved the whole budget"
    a_quotes_the_package = answers("A")
    a_quotes_the_package[1]["evidence"] = SCREEN_LINE.format(n=0)
    root = fixture(tmp_path, B=forged, A=a_quotes_the_package)
    result = score.score(root, score.load_marks(root / "marks.json"))
    assert result["readers"]["B"]["missing_quotes"] == ["Q03: Dana approved the whole budget"]
    assert result["readers"]["A"]["quotes_found"] == 17
    assert checks(result)["Quoted pieces found in the sources, B"] == (34, False)


VERDICT_SPLIT = """## 8. Per-question scores

| Q | Cat | A corr | A cit | B corr | B cit | C corr | C cit | Note |
|---|---|---|---|---|---|---|---|---|
""" + "".join(
    f"| Q{i + 1:02d} | {c} | {'1' if c == 'SPEECH' else '0 CT'} | 1 | "
    f"{'0.5' if i == 16 else '0 CT' if i == 17 else '1'} | 0 (1) | 1 | 1 | |\n"
    for i, c in enumerate(CATEGORIES)
)

VERDICT_JOINED = """## Appendix: per-question scores

| Q | Cat | A | B | C | Gold time |
|---|---|---|---|---|---|
""" + "".join(
    f"| Q{i + 1:02d} | {c} | {'1 / 1' if c == 'SPEECH' else '0 ct / 1'} | "
    f"{'0.5 / 1' if i == 16 else '0 ct / 1' if i == 17 else '1 / 1'} | 1 ct / 1 | 00:00:00 |\n"
    for i, c in enumerate(CATEGORIES)
)


@pytest.mark.parametrize(
    "verdict", [VERDICT_SPLIT, VERDICT_JOINED], ids=["corr-and-cit-columns", "score-slash-cit"]
)
def test_marks_are_read_from_either_verdict_table_layout(tmp_path: Path, verdict: str) -> None:
    path = tmp_path / "VERDICT.md"
    path.write_text("# VERDICT\n\n| Condition | All |\n|---|---|\n| A | 0.5 |\n\n" + verdict)
    read = score.load_marks(path)
    assert [read[f"Q{i + 1:02d}"]["B"]["score"] for i in range(18)] == B_MARKS
    assert read["Q18"]["B"]["cannot_tell"] and not read["Q18"]["B"]["wrong"]
    root = fixture(tmp_path)
    assert checks(score.score(root, read))["B correctness, all"] == (16.5, True)


def test_unreadable_input_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = fixture(tmp_path)
    (root / "answers-C-frames.json").unlink()
    assert score.main([str(root)]) == 2
    assert "answers-C" in capsys.readouterr().err
    assert score.main([str(fixture(tmp_path / "x")), "--marks", str(tmp_path / "missing.json")]) == 2


# ---------------------------------------------------------------------------------------------------------
# layout (C19)
# ---------------------------------------------------------------------------------------------------------

LABELS = '''"""Hand labels, one code per 10 s."""
GT = {
    "teams-share": "S" * 3 + "G" * 2,
    "zoom-gallery": "G" * 2 + "S" + "F",
}
'''


def kinds(codes: str, *, wrong: int = 0) -> list[str]:
    out = ["share" if codes[2 * k // 10] in "SC" else "camera" for k in range(5 * len(codes))]
    for k in range(wrong):
        out[k] = "camera" if out[k] == "share" else "share"
    return out


def test_the_layout_scorer_scores_stable_ticks_per_profile(tmp_path: Path) -> None:
    labels = tmp_path / "gt.py"
    labels.write_text(LABELS)
    predictions = tmp_path / "pred.json"
    predictions.write_text(
        json.dumps(
            {
                "teams-share": {"profile": "teams", "kinds": kinds("SSSGG", wrong=1)},
                "zoom-gallery": {"profile": "generic", "kinds": kinds("GGSF")},
            }
        )
    )
    result = layout.score(layout.load_labels(labels), layout.load_predictions(predictions))
    teams = result["profiles"]["teams"]
    assert (teams["ticks"], teams["agree"], teams["pass"]) == (23, 22, True), (
        "2 ticks before the change unscored"
    )
    zoom = result["excerpts"]["zoom-gallery"]
    assert (zoom["ticks"], zoom["apart"], zoom["unscored"]) == (11, 5, 4)
    assert layout.main([str(labels), str(predictions)]) == 0
    predictions.write_text(
        json.dumps({"teams-share": {"profile": "teams", "kinds": kinds("SSSGG", wrong=2)}})
    )
    assert layout.main([str(labels), str(predictions)]) == 1


def test_the_layout_labels_are_read_never_run(tmp_path: Path) -> None:
    labels = tmp_path / "gt.py"
    labels.write_text('GT = {"x": __import__("os").getcwd()}\n')
    with pytest.raises(layout.InputError, match="only string literals"):
        layout.load_labels(labels)
    labels.write_text('GT = {"x": "SQ"}\n')
    with pytest.raises(layout.InputError, match="code outside"):
        layout.load_labels(labels)
    predictions = tmp_path / "pred.json"
    predictions.write_text(json.dumps({"y": {"profile": "teams", "kinds": ["share"]}}))
    labels.write_text('GT = {"x": "S"}\n')
    assert layout.main([str(labels), str(predictions)]) == 2


# ---------------------------------------------------------------------------------------------------------
# the blind-reader runner
# ---------------------------------------------------------------------------------------------------------


def test_each_reader_gets_only_its_own_inputs_and_no_gold(tmp_path: Path) -> None:
    root = fixture(tmp_path)
    (root / "evidence" / "frames").mkdir()
    (root / "evidence" / "frames" / "t000010.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    out = tmp_path / "run"
    runner.prepare(root, out, root / "evidence")
    files = {
        r: sorted(str(p.relative_to(out / r)) for p in (out / r).rglob("*") if p.is_file()) for r in "ABC"
    }
    assert files["A"] == ["BRIEF.md", "questions.json", "transcript.md"]
    assert files["B"] == ["BRIEF.md", "evidence/w-0000.md", "questions.json"]
    assert files["C"] == ["BRIEF.md", "evidence/frames/t000010.jpg", "evidence/w-0000.md", "questions.json"]
    blind = json.loads((out / "B" / "questions.json").read_text())
    assert len(blind) == 18 and not any({"gold_answer", "time", "verification"} & set(q) for q in blind)
    assert "at most 25 pictures" in (out / "C" / "BRIEF.md").read_text()


def test_a_package_without_a_transcript_gives_reader_a_its_speech_lines(tmp_path: Path) -> None:
    root = fixture(tmp_path)
    (root / "transcript-only.md").unlink()
    runner.prepare(root, tmp_path / "run", root / "evidence")
    speech = (tmp_path / "run" / "A" / "transcript.md").read_text().splitlines()
    assert len(speech) == 18 and all("SAID:" in line for line in speech)


def test_ask_runs_the_command_per_reader_and_collects_its_answers(tmp_path: Path) -> None:
    root = fixture(tmp_path)
    out = tmp_path / "run"
    runner.prepare(root, out, root / "evidence")
    reader = tmp_path / "reader.py"
    reader.write_text(
        "import json, sys\nbrief = sys.stdin.read()\n"
        "json.dump([{'id': 'Q01', 'answer': brief.splitlines()[0]}], open('answers.json', 'w'))\n"
    )
    assert runner.ask(out, ["A", "C"], f"{sys.executable} {reader}", 60) == 0
    assert json.loads((out / "answers-C.json").read_text())[0]["answer"] == "# Blind reader C"
    assert not (out / "answers-B.json").exists()
    assert runner.ask(out, ["B"], f"{sys.executable} -c pass", 60) == 1
    (out / "answers-B.json").write_text("[]")
    runner.judge_brief(root, out)
    assert sorted(p.name for p in (out / "judge").iterdir()) == [
        "BRIEF.md",
        "answers-A.json",
        "answers-B.json",
        "answers-C.json",
        "gold.json",
    ]
