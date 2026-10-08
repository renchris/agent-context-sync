"""scripts/meeting-eval/: the 9.1 and 9.2 scorer, the C19 layout scorer and the blind-reader runner.

The real fixtures are public recordings of named people and stay outside the repository (spec 9.1); these
tests build a made-up Contoso fixture of the same shape in ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from agentsync.policy import UNTRUSTED_BANNER
from test_recording_grammar import window_errors

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
splicer = _script("splice")

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
    "contoso-room": "SSSSS",
}
PLATFORM = {"contoso-room": "teams"}
'''


def kinds(codes: str, *, wrong: int = 0) -> list[str]:
    out = ["share" if codes[2 * k // 10] in "SC" else "camera" for k in range(5 * len(codes))]
    for k in range(wrong):
        out[k] = "camera" if out[k] == "share" else "share"
    return out


def layout_run(tmp_path: Path, predictions: dict[str, Any]) -> tuple[dict[str, Any], int]:
    labels, pred = tmp_path / "gt.py", tmp_path / "pred.json"
    labels.write_text(LABELS)
    pred.write_text(json.dumps(predictions))
    gt, platforms = layout.load_labels(labels)
    return layout.score(gt, layout.load_predictions(pred), platforms), layout.main([str(labels), str(pred)])


ALL_RIGHT = {
    "teams-share": {"profile": "teams", "kinds": kinds("SSSGG", wrong=1)},
    "zoom-gallery": {"profile": "generic", "kinds": kinds("GGSF")},
    "contoso-room": {"profile": "teams", "kinds": kinds("SSSSS")},
}


def test_the_layout_scorer_scores_stable_ticks_per_platform(tmp_path: Path) -> None:
    result, exit_code = layout_run(tmp_path, ALL_RIGHT)
    teams = result["platforms"]["teams"]
    assert (teams["excerpts"], teams["ticks"], teams["agree"], teams["pass"]) == (2, 48, 47, True), (
        "2 ticks before the change are unscored"
    )
    zoom = result["excerpts"]["zoom-gallery"]
    assert (zoom["platform"], zoom["ticks"], zoom["apart"], zoom["unscored"]) == ("zoom", 11, 5, 4)
    assert result["platforms"]["zoom"]["detected_otherwise"] == 1
    assert exit_code == 0
    worse = {**ALL_RIGHT, "teams-share": {"profile": "teams", "kinds": kinds("SSSGG", wrong=4)}}
    assert layout_run(tmp_path, worse)[1] == 1


def test_a_teams_recording_detected_as_generic_is_still_held_to_the_teams_mark(tmp_path: Path) -> None:
    missed = {**ALL_RIGHT, "teams-share": {"profile": "generic", "kinds": kinds("SSSGG", wrong=3)}}
    result, exit_code = layout_run(tmp_path, missed)
    teams = result["platforms"]["teams"]
    assert (teams["agree"], teams["detected_otherwise"], teams["pass"]) == (45, 1, False), (
        "45 of 48 is 93.8 %"
    )
    assert "generic" not in result["platforms"] and exit_code == 1


def test_a_labelled_excerpt_without_a_prediction_fails_the_run(tmp_path: Path) -> None:
    result, exit_code = layout_run(tmp_path, {k: v for k, v in ALL_RIGHT.items() if k != "contoso-room"})
    assert result["unpredicted"] == ["contoso-room"] and exit_code == 1


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


def test_an_answer_credited_without_a_quote_or_with_a_trivial_one_fails_the_quote_check(
    tmp_path: Path,
) -> None:
    bare = answers("B")
    del bare[5]["evidence"]
    bare[6]["evidence"] = "10 / 11"
    root = fixture(tmp_path, B=bare)
    result = score.score(root, score.load_marks(root / "marks.json"))
    assert result["readers"]["B"]["unquoted"] == ["Q05", "Q06"]
    assert result["readers"]["B"]["quotes"] == 32, "pieces under 4 characters are not counted"
    assert checks(result)["Quoted pieces found in the sources, B"] == (32, False)


def test_a_run_folder_is_scored_against_the_package_its_readers_were_given(tmp_path: Path) -> None:
    root = fixture(tmp_path)
    (root / "transcript-only.md").unlink()
    package = tmp_path / "contoso-review.mp4.d"
    package.mkdir()
    lines = "".join(
        f"[00:{i:02d}:10] SAID v1: Dana said the west budget is {i} thousand\n" for i in range(18)
    )
    lines += "".join(f"[00:{i:02d}:10] SCREEN: {SCREEN_LINE.format(n=i)[2:]}\n" for i in range(18))
    (package / "01-t000000.md").write_text(lines)
    out = tmp_path / "run"
    runner.prepare(root, out, package)
    for reader in "ABC":
        (out / f"answers-{reader}.json").write_text(
            (root / next(root.glob(f"answers-{reader}*.json")).name).read_text()
        )
    result = score.score(root, score.load_marks(root / "marks.json"), answers_dir=out)
    assert result["readers"]["A"]["quotes_found"] == 18, "A's transcript is the package's SAID lines"
    assert (
        result["readers"]["B"]["quotes_found"] == 36
        and checks(result)["Quoted pieces found in the sources, B"][1]
    )
    assert result["times_from_verification"] == [q["id"] for q in gold()]


# ---- splice.py: a transcript's speech into the package (spec section 10) -----------------------------------

FRAME = b"\xff\xd8\xff\xe0 Contoso keyframe"
FRAME_SHA = hashlib.sha256(FRAME).hexdigest()

WINDOW_1 = f"""{UNTRUSTED_BANNER}

# Recording 00:00:00-00:05:00 · window 1 of 2

## 00:00:00-00:02:00 · s001 · share · "Contoso storage review"
[00:00:00] KEYFRAME: t000000.jpg
[00:00:00] SCREEN: Contoso storage review
[00:00:00] SCREEN: Q3 budget 1,240,000
[00:00:00] NOTE: layout not recognised; keyframes are full frames
[00:01:20] SCREEN+: Q3 budget 1,310,000

## 00:02:00-00:05:12 · s002 · camera
[00:02:00] KEYFRAME: t000200.jpg
[00:02:00] TILE: Dana Okafor

Sidecar file `t000000.jpg` sha256 {FRAME_SHA}
Sidecar file `t000200.jpg` sha256 {FRAME_SHA}
"""

WINDOW_2 = f"""{UNTRUSTED_BANNER}

# Recording 00:05:00-00:05:12 · window 2 of 2

## 00:02:00-00:05:12 · s002 · camera
[00:05:00] NOTE: s002 began at 00:02:00; its on-screen lines and keyframe are in window 1, 00:00:00
"""

TRANSCRIPT = """# Transcript

[00:00:00] good morning everyone
[00:01:20] so the west number moved
[00:01:10] we start with the budget

[00:02:00] over to me then
[00:05:00] that is all from me
[00:05:03] see\x07 you <!-- next --> week
[00:05:04] \x1b
"""


def _package(root: Path, *, window_2: str = WINDOW_2) -> Path:
    package = root / "contoso-review.mp4.d"
    (package / "01-t000000.files").mkdir(parents=True)
    (package / "00-index.md").write_text(
        f"{UNTRUSTED_BANNER}\n\n# Meeting recording · 00:05:12 · 2 windows\n"
    )
    (package / "01-t000000.md").write_text(WINDOW_1)
    (package / "02-t000500.md").write_text(window_2)
    for name in ("t000000.jpg", "t000200.jpg"):
        (package / "01-t000000.files" / name).write_bytes(FRAME)
    (root / "transcript-only.md").write_text(TRANSCRIPT)
    return package


def test_splice_puts_each_line_in_its_window_and_state_in_rule_1_order(tmp_path: Path) -> None:
    package = _package(tmp_path)
    assert window_errors(WINDOW_1) == [] and window_errors(WINDOW_2) == []
    out = tmp_path / "spliced"
    assert splicer.splice(package, tmp_path / "transcript-only.md", out) == (6, 2)
    first, second = (out / "01-t000000.md").read_text(), (out / "02-t000500.md").read_text()
    assert first == WINDOW_1.replace(
        "[00:00:00] NOTE:", "[00:00:00] SAID v1: good morning everyone\n[00:00:00] NOTE:"
    ).replace(
        "[00:01:20] SCREEN+: Q3 budget 1,310,000\n",
        "[00:01:10] SAID v1: we start with the budget\n"
        "[00:01:20] SCREEN+: Q3 budget 1,310,000\n[00:01:20] SAID v1: so the west number moved\n",
    ).replace(
        "[00:02:00] TILE: Dana Okafor\n",
        "[00:02:00] TILE: Dana Okafor\n[00:02:00] SAID v1: over to me then\n",
    )
    assert (
        second
        == WINDOW_2.replace("[00:05:00] NOTE:", "[00:05:00] SAID v1: that is all from me\n[00:05:00] NOTE:")
        + "[00:05:03] SAID v1: see you &lt;!-- next --> week\n"
    )
    assert window_errors(first) == [] and window_errors(second) == []
    assert (out / "00-index.md").read_text() == (package / "00-index.md").read_text()
    assert (out / "01-t000000.files" / "t000200.jpg").read_bytes() == FRAME
    assert sorted(p.relative_to(out) for p in out.rglob("*")) == sorted(
        p.relative_to(package) for p in package.rglob("*")
    )


@pytest.mark.parametrize(
    ("transcript", "window_2", "error"),
    [
        ("[00:05:12] after the end\n", WINDOW_2, "no window holds 00:05:12"),
        ("Dana: [00:01:00] hello\n", WINDOW_2, "transcript line 1: not '[HH:MM:SS] text'"),
        (
            "[00:05:01] hello\n",
            WINDOW_2.replace("00:02:00-00:05:12 · s002", "00:02:00-00:05:00 · s002"),
            "02-t000500.md: no state on screen at 00:05:01",
        ),
        (
            "[00:01:00] hello\n",
            WINDOW_2 + "\n[truncated: 90 of 100 bytes shown; the whole window is in full-text.txt]\n",
            "02-t000500.md: a cut window",
        ),
    ],
)
def test_splice_refuses_what_it_cannot_place_and_writes_nothing(
    tmp_path: Path, transcript: str, window_2: str, error: str
) -> None:
    package = _package(tmp_path, window_2=window_2)
    (tmp_path / "transcript-only.md").write_text(transcript)
    out = tmp_path / "spliced"
    with pytest.raises(splicer.SpliceError, match=re.escape(error)):
        splicer.splice(package, tmp_path / "transcript-only.md", out)
    assert not out.exists()


def test_splice_main_prints_a_count_and_refuses_a_folder_that_is_not_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _package(tmp_path)
    out = tmp_path / "spliced"
    assert splicer.main([str(package), str(tmp_path / "transcript-only.md"), str(out)]) == 0
    assert capsys.readouterr().out.startswith("spliced 6 speech line(s) into 2 window(s) under ")
    assert splicer.main([str(package), str(tmp_path / "transcript-only.md"), str(out)]) == 1
    assert "OUT exists and is not an empty folder" in capsys.readouterr().err
