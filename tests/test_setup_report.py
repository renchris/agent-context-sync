"""``agentsync setup-report`` (src/agentsync/setup_report.py): headings, the summary, the embedded friction
log, redaction, background-run decoding, never failing hard and the time limit.  Everything runs in the
tmp HOME conftest sets up, with a fake ``~/Library/CloudStorage/OneDrive-Contoso`` (plain folders: no File
Provider, no prompt), and a canned ``launchctl print`` (this Mac's real LaunchAgent is never consulted)."""

from __future__ import annotations

import ast
import functools
import itertools
import json
import logging
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from agentsync import (
    arm_local,
    cli,
    convert,
    cycle,
    gitops,
    governance,
    it_request,
    lints,
    loop,
    materialise,
    net,
    policy,
    setup_report,
)
from agentsync import manifest as manifest_mod
from agentsync.config import (
    DEFAULT_EXCLUDES,
    RETIRE_ADVICE,
    Config,
    ConvertConfig,
    load_config,
    set_source_exclude,
)
from agentsync.convert import image, media, msg, ocr, pdf, recording, speech, vtt
from agentsync.convert import registry as registry_mod
from agentsync.convert.cache import ConverterCache
from agentsync.convert.image import ImageConverter
from agentsync.convert.recording import RecordingConverter
from agentsync.convert.registry import Registry
from agentsync.errors import ConfigError
from agentsync.manifest import Manifest
from agentsync.model import CycleMode, PassKind
from agentsync.ops import doctor, launchd
from agentsync.paths import TEMPORARY_ROOTS, expand, is_temporary
from conftest import zone
from test_ocr import fake_engine, write_fake

ORG = "Contoso"
FOLDERS = ("FY26 Projects", "Client Alpha", "Budget Review")
LIBRARY = "Finance - Documents"
LOGIN = "jdoe"
FULL_NAME = "Jane Doe"
EMAIL = "jane.doe@contoso.com"
GUID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"
SERIAL = "C02TEST123XY"
HOST = "Janes-MacBook-Pro"
CDHASH = "4ccc0d88cc6e212027eed71ff4bb7744d9cd4df2"
DOCS_SHA = "5a1b37803761c0ffee0123456789abcdef012345"
NOT_LOADED = (113, 'Bad request.\nCould not find service "com.agentsync.poll" in domain for user gui: 501')


@pytest.fixture
def fake_mac(monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A tmp HOME with two OneDrive folders configured, an install.log and job logs that name the user, the
    org, the folders, an email, a GUID and the serial number."""
    home = Path.home()
    cloud = home / "Library" / "CloudStorage"
    one = cloud / f"OneDrive-{ORG}" / FOLDERS[0] / FOLDERS[1]
    two = cloud / f"OneDrive-SharedLibraries-{ORG}" / LIBRARY / FOLDERS[2]
    for d in (one, two):
        d.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    for folder in (one, two):
        assert cli.main(["add-source", str(folder), "--config", str(cfg)]) == 0
    setup = home / "agent-context" / "setup"
    setup.mkdir(mode=0o700)
    run = "20260929T100000Z-4242"
    (setup / "install.log").write_text(
        f"2026-09-29T10:00:00Z run={run} start install.sh commit=0123456789ab kind=checkout "
        f"source={home}/src/agent-context-sync args=--source-local {one} --source-local {two}\n"
        f"2026-09-29T10:00:00Z run={run} step=uv seconds=0 rc=0 result=skipped note=present\n"
        f"2026-09-29T10:00:00Z run={run} step=agentsync seconds=41 rc=0 result=done\n"
        f"2026-09-29T10:00:41Z run={run} step=launcher seconds=12 rc=0 result=done\n"
        f"2026-09-29T10:00:53Z run={run} step=config seconds=1 rc=0 result=done note=created\n"
        f"2026-09-29T10:00:54Z run={run} step=doctor seconds=3 rc=1 result=done note=fail-lines\n"
        f"2026-09-29T10:00:57Z run={run} step=agent seconds=0 rc=0 result=skipped note=not-requested\n"
        f"2026-09-29T10:00:57Z run={run} end rc=0 seconds=57\n",
        encoding="utf-8",
    )
    logs = home / "Library" / "Logs" / "agentsync"
    logs.mkdir(parents=True, mode=0o700)
    (logs / "com.agentsync.poll.err.log").write_text(
        f"2026-09-29T10:05:00Z agentsync-launcher[42]: TCC_PENDING reason=canary path={one}\n"
        f"2026-09-29 10:05:01,000 WARNING agentsync.cycle: {EMAIL} ({FULL_NAME}, {LOGIN}) owns {one}\n"
        f"2026-09-29 10:05:02,000 ERROR agentsync.cycle: drive {GUID} on {SERIAL} / {HOST}.local failed\n"
        "2026-09-29 10:05:03,000 INFO agentsync.cycle: not an error line\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_report, "_login_name", lambda: LOGIN)
    monkeypatch.setattr(setup_report, "_full_name", lambda: FULL_NAME)
    monkeypatch.setattr(setup_report, "_serial_number", lambda run: SERIAL)
    monkeypatch.setattr(setup_report, "_host_names", lambda: [f"{HOST}.local", HOST])
    monkeypatch.setattr(setup_report, "_computer_names", lambda run: [])
    monkeypatch.setattr(setup_report, "_launchctl_print", lambda run, label: NOT_LOADED)
    return {"home": home, "config": cfg, "one": one, "two": two, "logs": logs, "setup": setup}


@pytest.fixture
def clean_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI's status build without its FAIL checks (this tmp HOME has no launcher, so a check FAILs, and a
    doctor FAIL is part of the outcome)."""
    real = cli._status_checks

    def clean(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
        return [c for c in real(config, offline=offline) if c.ok or c.severity is not doctor.Severity.ERROR]

    monkeypatch.setattr(cli, "_status_checks", clean)


def report(tmp_path: Path, cfg: Path, *extra: str) -> tuple[int, str, float]:
    out = tmp_path / "report" / "setup-report.md"
    t0 = time.monotonic()
    rc = cli.main(["setup-report", "--out", str(out), "--config", str(cfg), *extra])
    return rc, out.read_text(encoding="utf-8"), time.monotonic() - t0


def section(text: str, title: str) -> str:
    return text.split(f"\n## {title}\n", 1)[1].split("\n## ", 1)[0]


_REAL_FULL_NAME = setup_report._full_name  # the fake_mac fixture replaces it

RAW = (ORG, ORG.lower(), *FOLDERS, LIBRARY, LOGIN, FULL_NAME, "Jane", EMAIL, GUID, SERIAL, HOST)


@pytest.fixture
def roomy_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A report time budget no load reaches, for a test that checks what the sections say: under load a
    section ran out of the 12 s budget and said "This section failed" instead of its facts (B13 round 2
    R98). The tests of the budget itself (marked ``timing``) keep the real one."""
    monkeypatch.setattr(setup_report, "TIME_BUDGET_S", 120.0)


@pytest.fixture
def roomy_calls(roomy_budget: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """``roomy_budget`` and per-call bounds no load reaches, for a test that checks what a section says: under
    load the 4 s Status build, the listing and the evidence reads ran out and printed "not measured (time
    limit)" (B13 round 8 J80). Apart from ``roomy_budget``, so a test of a real bound (the checks run in
    Doctor, not under Status's 4 s; H8-1's listing) keeps it."""
    for name in (
        "_LISTING_S",
        "_PROXY_S",
        "_STATUS_S",
        "_LOOP_NEXT_S",
        "_FOLDERS_S",
        "_PROBE_S",
        "EVIDENCE_BUDGET_S",
    ):
        monkeypatch.setattr(setup_report, name, 60.0)


@pytest.mark.timing
def test_the_report_finishes_inside_its_time_budget(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    rc, _text, elapsed = report(tmp_path, fake_mac["config"])
    assert rc == 0
    assert elapsed < 15, f"setup-report took {elapsed:.1f}s"


@pytest.mark.usefixtures("roomy_budget")
def test_headings_redaction_and_runtime(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    rc, text, _elapsed = report(tmp_path, fake_mac["config"])
    assert rc == 0
    assert text.startswith(setup_report.REPORT_TITLE + "\n")
    headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
    assert headings == list(setup_report.SECTION_TITLES)
    assert "No friction log found at ~/agent-context/setup/friction.md" in section(text, "Agent friction log")
    summary = section(text, "Summary")
    assert summary.lstrip().startswith(
        "- **outcome: worked with help** (computed: install.sh exit 0; no friction log, so the human turns "
        "are unknown)\n"
    ), "no doctor FAIL: with no LaunchAgent plist a missing launcher is INFO (KISS K11a)"
    assert "- friction log: none at ~/agent-context/setup/friction.md" in summary
    assert text.rstrip("\n").splitlines()[-1].startswith(setup_report.ISSUE_URL + "&title=Setup%20report")
    assert "- generated at: " in text and "- agentsync: " in text and "- install source: " in text
    for raw in RAW:
        assert raw not in text, f"{raw!r} leaked into the redacted report"
    assert str(fake_mac["home"]) not in text
    for placeholder in ("<org-1>", "<folder-1>", "<user>", "<name>", "<email-1>", "<guid-1>"):
        assert placeholder in text, placeholder
    assert "<serial>" in text and "<host>" in text and "~/Library/CloudStorage/OneDrive-<org-1>/" in text
    # the install log names the source folder as placeholders; a job log line never carries a path at all
    path_one = "~/Library/CloudStorage/OneDrive-<org-1>/<folder-1>/<folder-2>"
    assert path_one in section(text, "Installer")
    assert "<email-1> (<name>, <user>) owns <path>" in section(text, "Recent errors")
    # From install.log's args, not Doctor's lines: Doctor runs last, on what is left of the budget, and a
    # loaded machine timed it out (B13 R45). The leak check above still covers Doctor when it finishes.
    assert "~/Library/CloudStorage/OneDrive-SharedLibraries-<org-1>/<library-1>/<folder-3>" in section(
        text, "Installer"
    )
    assert not re.search(r"<folder-[4-9]>", text), "three folders, three placeholders"
    m = re.search(r"^(\d+) replacements of (\d+) values", section(text, "Redaction"), re.MULTILINE)
    assert m and int(m.group(1)) > 10 and 10 <= int(m.group(2)) < int(m.group(1))
    assert f"- redaction: {m.group(1)} replacements of {m.group(2)} values" in section(text, "Summary")


def test_recent_errors_never_carry_an_item_path_or_document_name(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """Field report 2026-10-05: names nested below a configured source were never registered with the
    Redactor, so "fetch of <rel_path> failed" lines and inbox .eml names reached a report meant for a
    public issue."""
    log = fake_mac["logs"] / "com.agentsync.poll.err.log"
    log.write_text(
        log.read_text(encoding="utf-8")
        + "2026-10-05 09:00:00,000 WARNING agentsync.cycle: src-x: fetch of Big Bank Merger/Q3 pricing for "
        "Globex.xlsx failed: [Errno 89] Operation canceled\n"
        + "2026-10-05 09:00:01,000 WARNING agentsync.cycle: inbox: Re Northwind pricing call.eml: "
        "read failed: Operation canceled\n"
        + "2026-10-05 09:00:02,000 ERROR agentsync.cycle: src-x: read failed: [Errno 89] Operation canceled: "
        "'/Users/someone/Library/CloudStorage/OneDrive-Acme/Clients/Initech Deal/term sheet.docx'\n"
        + "2026-10-05 09:00:03,000 WARNING agentsync.arm_local: src-x: directory 'Mooring Ledger' is unknown "
        "(EPERM: Operation not permitted)\n"
        + "2026-10-05 09:00:04,000 WARNING agentsync.arm_local: src-x: 2 folder(s) could not be listed: "
        "'Harbor Fees' (EPERM: Operation not permitted), 'Pier/Old Slips' (EDEADLK: Resource deadlock "
        "avoided)\n",
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    errors = section(text, "Recent errors")
    for name in (
        *("Big Bank", "Merger", "Globex", "Northwind", "Initech", "term sheet", "Clients", "Mooring"),
        *("Harbor", "Pier", "Slips"),
    ):
        assert name not in text, name
    assert "src-x: directory '<path>' is unknown (EPERM: Operation not permitted)" in errors
    assert (
        "src-x: 2 folder(s) could not be listed: '<path>' (EPERM: Operation not permitted), '<path>' "
        "(EDEADLK: Resource deadlock avoided)"
    ) in errors
    assert "src-x: <path>: [Errno 89] Operation canceled" in errors
    assert "inbox: <path>: read failed: Operation canceled" in errors
    assert "src-x: read failed: [Errno 89] Operation canceled: '<path>" in errors
    assert "(item paths and document names shown as <path>)" in errors


@pytest.fixture
def mac_zone() -> Iterator[None]:
    """This Mac's time zone is America/Denver (UTC-6 until 2026-11-01, then UTC-7): the report reads a Python
    log line's local time with it. Set here because the CI runners are in UTC, where the conversion changes
    nothing; ``tzset`` is run again on the way out, which monkeypatch would not do (``conftest.zone``, which
    test_cli.py's UTC log test shares: B13 round 9 K55)."""
    with zone("America/Denver"):
        yield


@pytest.mark.usefixtures("mac_zone")
def test_recent_errors_merge_a_line_repeated_in_a_row(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    """Field report 2026-10-07: one warning per empty folder, 5 a poll, filled the 40-line window. Lines of
    one file that read the same after their time, once item paths are ``<path>``, are one line with a count
    and the last time. The launcher's line between two polls does not split them, and another source's line
    of the same shape stays apart."""
    warning = "WARNING agentsync.arm_local: src-a: directory 'Mooring Ledger' has no children"
    other = warning.replace("Mooring Ledger", "Harbor Notes")
    log = fake_mac["logs"] / "com.agentsync.poll.err.log"
    log.write_text(
        log.read_text(encoding="utf-8")
        + f"2026-10-05 09:00:00,000 {warning}\n"
        + f"2026-10-05 09:00:00,100 {other}\n"
        + "2026-10-05T14:05:00Z agentsync-launcher[42]: CANARY_OK path=/tmp/canary\n"
        + f"2026-10-05 09:05:00,000 {warning}\n"
        + f"2026-10-05 09:05:00,100 {other}\n"
        + f"2026-10-05 09:15:00,000 {warning.replace('src-a', 'src-b')}\n"
        + f"2026-10-05 09:20:00,000 {warning}\n",
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    errors = section(text, "Recent errors")
    assert (
        "2026-10-05T15:00:00.000Z WARNING agentsync.arm_local: src-a: directory '<path>' has no children "
        "(4 times in a row, the last at 2026-10-05T15:05:00.100Z)\n" in errors
    )
    assert "15:05:00.000Z" not in errors and "Mooring" not in text and "Harbor" not in text
    assert "15:15:00.000Z WARNING agentsync.arm_local: src-b: directory '<path>' has no children\n" in errors
    assert "15:20:00.000Z WARNING agentsync.arm_local: src-a: directory '<path>' has no children\n" in errors
    assert "The last 5 of 5 WARNING/ERROR lines in " in errors and "KiB" not in errors
    assert (
        "(8 as logged: lines in a row that read the same here are shown once, with their count and last time)"
        in errors
    )


def test_recent_errors_name_their_window(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    """Bring-back 2026-10-10 (C4): the count is of the lines read, the last 64 KiB of each log file, so a
    count that fell between two reports read as a change in the Mac. When a file is longer than that, the
    heading says so (a shorter one keeps the plain heading: the merge test above)."""
    log = fake_mac["logs"] / "com.agentsync.poll.err.log"
    line = "2026-10-05 09:00:{:02d},000 WARNING agentsync.cycle: src-a: poll {} unstable\n"
    log.write_text("".join(line.format(i % 60, i) for i in range(3000)), encoding="utf-8")
    assert log.stat().st_size > setup_report._TAIL_BYTES
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    errors = section(text, "Recent errors")
    window = r"The last 40 of (\d+) WARNING/ERROR lines in the last 64 KiB of each log file in "
    head = re.search(window, errors)
    assert head and int(head[1]) == len(setup_report._tail(log)), errors[:300]
    assert "poll 2999 unstable" in errors


@pytest.mark.usefixtures("mac_zone")
def test_log_times_in_the_report_are_utc(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    """Field report 2026-10-07 (C12): ``logging`` writes the Mac's local time with no zone, and beside the
    report's UTC times it gave the Mac's UTC offset away. Recent errors and the install.out tail show a Python
    log line's time in UTC, read with this Mac's zone; the launcher's UTC line is unchanged. In the hour a
    fall-back repeats (01:00 to 02:00 on 2026-11-01 in Denver), a line written after the change reads as the
    later hour, not before the line above it."""
    log = fake_mac["logs"] / "com.agentsync.poll.err.log"
    log.write_text(
        log.read_text(encoding="utf-8")
        + "2026-10-05T14:05:00Z agentsync-launcher[42]: PROGRAM_REFUSED ERROR path=/tmp/x\n"
        + "2026-11-01 01:30:00,000 WARNING agentsync.cycle: src-a: before the change\n"
        + "2026-11-01 01:10:00,000 WARNING agentsync.cycle: src-a: after the change\n"
        + "0001-01-01 00:00:00,000 WARNING agentsync.cycle: src-a: a stamp no clock wrote\n",
        encoding="utf-8",
    )
    (fake_mac["setup"] / "install.out").write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n"
        "2026-09-29 04:00:20,000 WARNING agentsync.arm_local: src-x: not crossing into another volume\n"
        "warning: agentsync setup-report failed (stopped by SIGTERM (a tool timeout?)): "
        "2026-09-29 04:00:30,123 ERROR agentsync.cli: boom\n"
        "NEXT: review ~/agent-context/setup-report.md\n",
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    errors = section(text, "Recent errors")
    assert "com.agentsync.poll.err.log: 2026-09-29T16:05:01.000Z WARNING agentsync.cycle: <email-1>" in errors
    assert "com.agentsync.poll.err.log: 2026-09-29T16:05:02.000Z ERROR agentsync.cycle: drive" in errors
    assert "2026-10-05T14:05:00Z agentsync-launcher[42]: PROGRAM_REFUSED" in errors
    assert "2026-11-01T07:30:00.000Z WARNING agentsync.cycle: src-a: before the change\n" in errors
    assert "2026-11-01T08:10:00.000Z WARNING agentsync.cycle: src-a: after the change\n" in errors
    assert "0001-01-01 00:00:00,000 WARNING agentsync.cycle: src-a: a stamp no clock wrote\n" in errors
    inst = section(text, "Installer")
    assert "2026-09-29T10:00:20.000Z WARNING agentsync.arm_local: src-x: not crossing" in inst
    # The one line that quotes a log line has its time converted too (B13 round 2 R108).
    assert (
        "warning: agentsync setup-report failed (stopped by SIGTERM (a tool timeout?)): "
        "2026-09-29T10:00:30.123Z ERROR agentsync.cli: boom" in inst
    )
    assert "a log line's local time is shown in UTC" in inst
    assert not re.search(r"20\d\d-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}", text)


@pytest.mark.usefixtures("mac_zone")
def test_a_small_step_back_in_the_repeated_hour_keeps_its_reading() -> None:
    """Review of B13 N10: two threads log through one handler, so a line can be written a few milliseconds
    before the line above it. In the hour a fall-back repeats, that step back is not the clock going back:
    the line, and the first-pass lines after it, keep the earlier reading. A step back of more than a second
    still takes the later one."""
    shown = setup_report._utc_log_times(
        [
            "2026-11-01 01:30:05,000 WARNING agentsync.cycle: a",
            "2026-11-01 01:30:04,990 WARNING agentsync.recording: b",
            "2026-11-01 01:40:00,000 WARNING agentsync.cycle: c",
            "2026-11-01 01:59:00,000 WARNING agentsync.cycle: d",
            "2026-11-01 01:05:00,000 WARNING agentsync.cycle: e",
            "2026-11-01 01:04:59,500 WARNING agentsync.recording: f",
        ]
    )
    assert [ln.split(" ")[0] for ln in shown] == [
        "2026-11-01T07:30:05.000Z",
        "2026-11-01T07:30:04.990Z",
        "2026-11-01T07:40:00.000Z",
        "2026-11-01T07:59:00.000Z",
        "2026-11-01T08:05:00.000Z",
        "2026-11-01T08:04:59.500Z",
    ]


CONVERTED_SUFFIXES = sorted(
    {
        *Registry.default(ConvertConfig()).extensions(),
        *ImageConverter.extensions,
        *RecordingConverter.extensions,
    }
)
"""Every suffix a converter claims: the default registry's, the image converter's, which the registry holds
only on a Mac with an OCR engine, and the recording converter's, which it holds only with a media helper."""
LOG_HEAD = "com.agentsync.poll.err.log: 2026-10-05 09:00:00,000 WARNING agentsync."


@pytest.mark.parametrize("suffix", CONVERTED_SUFFIXES)
def test_a_log_line_never_names_a_file_a_converter_reads(suffix: str) -> None:
    """A file at the top of a source has no slash to know it by: its extension is all the scrub has. A file
    the cycle fetches and converts is a file its WARNING lines can name, so every claimed suffix is known."""
    assert len(CONVERTED_SUFFIXES) == 41 and {".bmp", ".heif", ".webp", ".yml", ".log", ".vsdm", ".potx"} <= {
        *CONVERTED_SUFFIXES
    }
    for name in (f"Contoso Roadmap{suffix}", f"Fabrikam Org Chart{suffix.upper()}"):
        fetch = f"{LOG_HEAD}cycle: src-x: fetch of {name} failed: timed out"
        assert setup_report._scrub_item_paths(fetch) == f"{LOG_HEAD}cycle: src-x: <path>: timed out"
        broke = f"{LOG_HEAD}convert: converter image-ocr broke its contract on {name}: unit whole: empty body"
        assert setup_report._scrub_item_paths(broke) == f"{LOG_HEAD}convert: <path>: unit whole: empty body"
        failed = f"{LOG_HEAD}convert.image: {name}: on-device OCR failed: the OCR helper ran out of time"
        assert setup_report._scrub_item_paths(failed) == (
            f"{LOG_HEAD}convert.image: <path>: on-device OCR failed: the OCR helper ran out of time"
        )


def test_the_lock_wait_warning_keeps_a_fixed_stage_word_and_redacts_a_source_stage() -> None:
    """B13 round 6 G36: the lock-wait WARNING quotes the holder's stage with ``%r``, and the quoted-value rule
    made ``stage 'acquired'`` read ``stage '<path>'``. A fixed stage word names no item and stays; a stage
    built from a source id is still redacted, and so is a fixed word quoted anywhere but after ``stage``."""
    head = f"{LOG_HEAD}cycle: another sync is running (lock held: pid 123 (a sync, its pass: poll) since "
    head += "2026-10-05T08:59:00Z, "
    tail = "); waiting for it to finish (30s of at most 300s)"
    for stage in (
        "acquired",
        "secrets",
        "curate",
        "surface",
        "land-gate",
        "commit",
        "retention",
        "recordings",
    ):
        line = f"{head}last beat 2026-10-05T08:59:30Z (30s ago), stage {stage!r}{tail}"
        assert setup_report._scrub_item_paths(line) == line, stage
    for stage in ("work:project-alpha", "source:project-alpha", "fetch project-alpha"):
        line = f"{head}stage {stage!r}{tail}"
        scrubbed = setup_report._scrub_item_paths(line)
        assert "project-alpha" not in scrubbed and "stage '<path>'" in scrubbed, scrubbed
    named = f"{LOG_HEAD}cycle: src-x: folder 'commit' is empty"
    assert setup_report._scrub_item_paths(named) == f"{LOG_HEAD}cycle: src-x: folder '<path>' is empty"


UTC_LOG_HEAD = "com.agentsync.poll.err.log: 2026-10-05T15:00:00.000Z WARNING agentsync."
"""A log line's head as agentsync writes it since B13 round 8 J26: UTC with a Z."""


@pytest.mark.parametrize("head", [LOG_HEAD, UTC_LOG_HEAD])
@pytest.mark.parametrize("module", ["pdf", "xlsx", "pptx", "eml", "markdown", "image"])
def test_a_logger_named_like_a_document_keeps_its_time_and_level(module: str, head: str) -> None:
    """A logger is named after its module: ``agentsync.convert.pdf`` is not a document, and a line that
    lost its first segment to ``<path>`` lost its time and its level with it. That holds for a line with
    the UTC time agentsync writes since B13 round 8 J26 as for an older log's local time."""
    line = f"{head}convert.{module}: Contoso Plan.pdf: comments not read on 1 page, first on page 2"
    assert setup_report._scrub_item_paths(line) == (
        f"{head}convert.{module}: <path>: comments not read on 1 page, first on page 2"
    )
    # Only a segment that is a whole log-line head is left alone: a file named like a logger is a file.
    for posing in (
        f"fetch of WARNING agentsync.convert.{module} failed",
        f"unreadable ERROR agentsync.convert.{module}",
    ):
        if module != "image":  # ``.image`` is no document extension: nothing to scrub in it
            line = f"{LOG_HEAD}cycle: src-x: {posing}: timed out"
            assert setup_report._scrub_item_paths(line) == f"{LOG_HEAD}cycle: src-x: <path>: timed out"


def test_a_joined_name_that_spells_any_report_word_leaves_the_word_alone() -> None:
    """B13 round 5 F93: the joined-word rule read a hand-picked list of 263 words, so "In Stall" turned every
    "install" into <name> and gave the joined name back. It reads report_words(): those and every word of the
    report's static text, read from its own string literals (no list to keep), never a docstring's example."""
    words = setup_report.report_words()
    assert words >= setup_report._JOINED_WORDS and words >= {"install", "content", "agentsync", "issue"}
    assert "janedoe" not in words and "contoso" not in words, "a docstring's example is not the report's text"
    assert "abel" not in words and "feedback" in words, "whole words only, a hyphenated one too"
    owner = setup_report._REPO_SLUG.partition("/")[0]
    assert owner not in words, "a locator's owner is a login run together, never a report word"
    red = setup_report.Redactor()
    red.add("name", "In Stall", fuzzy=True)
    assert red.redact("install.sh exited 0; the install log; INSTALL; InStall in-stall In Stall") == (
        "install.sh exited 0; the install log; INSTALL; <name> <name> <name>"
    )
    red = setup_report.Redactor()
    red.add("name", "Con Tent", fuzzy=True)
    assert red.redact("the content of the report; ConTent") == "the content of the report; <name>"
    red = setup_report.Redactor()
    red.add("name", "Jane Doe", fuzzy=True)
    assert red.redact("janedoe Jane Doe JaneDoe") == "<name> <name> <name>", "no report word: as before"


def test_a_one_word_organisation_that_is_a_report_word_is_matched_only_in_folder_and_domain_names() -> None:
    """B13 round 5 F42, F55: an entry OneDrive-Link registered the organisation "Link", fuzzy, so "a link's
    own attributes" and "issue link" read "<org-2>", and the placeholder in the report's fixed text gave
    the name back. Every entry is still registered (a symlink named after the tenant must not print it),
    but a one-word organisation that is a report word is matched only after an app's name or before a
    SharePoint or tenant domain; the bare word in prose stays, as Q72 keeps "home". Any other organisation
    is matched as before."""
    red = setup_report.Redactor()
    red.add("org", "Contoso", fuzzy=True)
    red.add("org", "Link", fuzzy=True)
    said = (
        "OneDrive-Link, OneDrive - Link, OneDrive-SharedLibraries-Link, link.sharepoint.com, "
        "link-my.sharepoint.com, link.onmicrosoft.com; a link's own attributes; link count; issue link; Link"
    )
    assert red.redact(said) == (
        "OneDrive-<org-2>, OneDrive - <org-2>, OneDrive-SharedLibraries-<org-2>, <org-2>.sharepoint.com, "
        "<org-2>-my.sharepoint.com, <org-2>.onmicrosoft.com; a link's own attributes; link count; "
        "issue link; Link"
    )
    assert (
        red.redact("Contoso contoso CONTOSO contoso.sharepoint.com")
        == "<org-1> <org-1> <org-1> <org-1>.sharepoint.com"
    )
    assert not red.word_named_shown, "matched only in a name, the placeholder reads back as nothing"
    # any app's name, and "OneDrive - " as install.log's %q and a URL write it
    assert red.redact("Egnyte-Link; OneDrive\\ -\\ Link; OneDrive%20-%20Link") == (
        "Egnyte-<org-2>; OneDrive\\ -\\ <org-2>; OneDrive%20-%20<org-2>"
    )


@pytest.mark.parametrize("org", ["Gateway", "Status", "Arm"])
def test_an_organisation_named_like_a_report_word_is_cut_out_of_a_longer_word_too(org: str) -> None:
    """B13 round 18 V1: an organisation that spells a report word fell back to whole-word matching, so
    "MyGatewayTeam", "Gateway2026" and "AcmeGateway" stayed in clear in a section said to be redacted. It is
    cut out of a longer word as any other one-word organisation is (M50); the word itself still makes the
    report private only. agentsync's own CamelCase names stay whole."""
    red = setup_report.Redactor()
    red.add("org", org, fuzzy=True)
    said = f"My{org}Team, {org}2026, Acme{org}; {org}; OneDrive, LaunchAgent, ~/agent-context"
    assert red.redact(said) == (
        "My<org-1>Team, <org-1>2026, Acme<org-1>; <org-1>; OneDrive, LaunchAgent, ~/agent-context"
    )
    assert red.private_only


def test_an_organisation_named_like_a_word_of_command_output_makes_the_report_private_only() -> None:
    """B13 round 18 V86 (with lane RB's V84): the report prints ``profiles status``' "Enrolled via DEP: No",
    ``uname -m``'s arm64 and net.PAC_UNSUPPORTED's sentence, words in none of its literals, so an organisation
    named Via read "Enrolled <org-1> DEP" in a report still offered for the public issue form. Those words
    (``_OUTPUT_WORDS``, install.sh's ``output_word``) make it private only, and change nothing else: the value
    is still cut out of a longer word, and they are no report words."""
    red = setup_report.Redactor()
    red.add("org", "Via", fuzzy=True)
    assert red.redact("- MDM enrollment: Enrolled via DEP: No; ViaGroup, AcmeVia") == (
        "- MDM enrollment: Enrolled <org-1> DEP: No; <org-1>Group, Acme<org-1>"
    )
    assert red.private_only
    pac = setup_report.Redactor()
    pac.add("org", "Unsupported", fuzzy=True)
    assert "<org-1>" in pac.redact(f"[warn] network.proxy — {net.PAC_UNSUPPORTED}") and pac.private_only
    control = setup_report.Redactor()
    control.add("org", "Contoso", fuzzy=True)
    assert control.redact("Enrolled via DEP: No") == "Enrolled via DEP: No" and not control.private_only
    words = setup_report.report_words()
    assert not ({"via", "dep", "enrolled", "unsupported"} & words), "no report words: only private only"
    printed = set(re.findall(r"[a-z]{3,}", net.PAC_UNSUPPORTED.lower()))
    assert printed - words <= setup_report._OUTPUT_WORDS, printed - words - setup_report._OUTPUT_WORDS


@pytest.mark.parametrize("link", [True, False], ids=["symlink", "folder"])
def test_a_cloudstorage_entry_named_like_a_report_word_keeps_the_reports_words(
    fake_mac: dict[str, Path], link: bool
) -> None:
    """B13 round 5 F42, F55 end to end: OneDrive-Link beside OneDrive-Contoso, as a symlink to it or as a
    folder. The organisation never prints, and the Environment line's own words keep "link"."""
    cloud = fake_mac["home"] / "Library" / "CloudStorage"
    entry = cloud / "OneDrive-Link"
    if link:
        entry.symlink_to(cloud / f"OneDrive-{ORG}")
        (cloud / "OneDrive-Fabrikam").symlink_to(cloud / f"OneDrive-{ORG}")
    else:
        entry.mkdir()
    cfg = fake_mac["config"]
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[graph]\ntenant = "link.com"\n', encoding="utf-8")
    text, _red = setup_report.build_report(cfg, hooks=setup_report.ReportHooks())
    assert ORG not in text and "Fabrikam" not in text and "OneDrive-Link" not in text
    assert "link.com" not in text, "a custom-domain tenant is registered whole"
    env = section(text, "Environment")
    assert "OneDrive-<org-" in env
    if link:
        # B13 round 6 G40; round 16 T40: the folder it points into, by its placeholder
        assert "(a symlink into OneDrive-<org-1>: read as that folder, " in env, env
    assert "<org-2>'s own" not in text and "issue <org-" not in text


@pytest.mark.parametrize("name", ["Link", "Aardvark"])
def test_a_provider_symlink_is_no_second_organization(fake_mac: dict[str, Path], name: str) -> None:
    """B13 round 16 T40: a CloudStorage symlink's name was registered as an organization, so the Environment
    line read as two (``OneDrive-<org-1> ..., OneDrive-<org-2> (a symlink ...)``), a link sorting first
    (``OneDrive-Aardvark``) took <org-1>, and the line never said which folder it points into. The link's
    whole name is a <link-N>, never printed and never an organization, and the line names its target by
    that folder's placeholder; a source configured through the link reads the same."""
    cloud = fake_mac["home"] / "Library" / "CloudStorage"
    entry = cloud / f"OneDrive-{name}"
    entry.symlink_to(cloud / f"OneDrive-{ORG}", target_is_directory=True)
    cfg = fake_mac["config"]
    through = entry / fake_mac["one"].relative_to(cloud / f"OneDrive-{ORG}")
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + f'\n[[source]]\nid = "tailspin-deal"\nkind = "local"\npath = "{through}"\n',
        encoding="utf-8",
    )
    text, _red = setup_report.build_report(cfg, hooks=setup_report.ReportHooks())
    env = section(text, "Environment")
    assert "<link-1> (a symlink into OneDrive-<org-1>: read as that folder, " in env, env
    assert "<org-2>" not in text, [ln for ln in text.splitlines() if "<org-2>" in ln]
    assert ORG not in text and f"OneDrive-{name}" not in text and name not in text.replace("link", ""), [
        ln for ln in text.splitlines() if name in ln
    ]
    assert "<link-N> a symlink's own name" in section(text, "Redaction")


def test_a_word_named_folder_makes_the_redaction_section_advise_sending_privately(
    fake_mac: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 5 F92: a folder named like a word the report prints is redacted everywhere, so the report's
    public template gives it back where its placeholder stands ("the <folder-3> of this report"). When such a
    placeholder is shown, the Redaction section says so and sends the report privately, not through the
    issue link. An organisation matched only in folder and domain names (F42), or any other name, does not."""
    plain = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())[0]
    assert "Private only:" not in plain and "Send it: the link on the last line opens" in plain
    folder = fake_mac["one"].parent / "Status"
    folder.mkdir()
    assert cli.main(["add-source", str(folder), "--config", str(fake_mac["config"])]) == 0
    text = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())[0]
    redaction = text.split("\n## Redaction\n", 1)[1]
    assert (
        "Private only: one of your folder, library or organization names is also a word this report prints, "
        "so where its placeholder stands in the report's own text it can be read back.\n" in redaction
    ), redaction
    assert (
        "Send it: privately (docs/deploy/setup-feedback.md in the agentsync repository), never as a public "
        "issue: it would show that name. Nothing is sent automatically." in redaction
    )
    assert "Send it: the link on the last line opens" not in redaction
    # and the report ends with no public issue link, as the shell report does (B13 round 7 H75)
    assert setup_report.issue_link(text) is None and setup_report.ISSUE_URL not in redaction, redaction
    assert text.endswith("\n") and not text.endswith("\n\n"), repr(text[-80:])
    assert "Read the report before sending it: anything else confidential" in redaction, "the caveat stays"
    # one spelling, the prompt's, in both reports (B13 round 11 M36; the shell twin is
    # test_the_shell_report_sends_a_report_with_a_word_named_folder_privately)
    assert "<org-N> organization" in redaction and "organisation" not in text and "capitalised" not in text
    # nor does `setup-report --out` print the issue link that offers the public issue (B13 round 6 G47, G65)
    out = tmp_path / "setup-report.md"
    capsys.readouterr()
    assert cli.main(["setup-report", "--out", str(out), "--config", str(fake_mac["config"])]) == 0
    printed = capsys.readouterr().out
    assert setup_report.ISSUE_LINK_LABEL not in printed and printed.startswith(f"wrote {out} ("), printed


def test_a_sync_alarm_that_names_agentsyncs_full_path_is_kept_whole() -> None:
    """B13 round 5 F52: Q49 put ``~/.local/bin/agentsync`` into cycle's alarms, and its ``/`` read as an item
    path, so the report cut whole alarms to ``<path>`` and lost the command they name. The binary is not an
    item path; a real path beside it still is."""
    bin_ = setup_report.AGENTSYNC_BIN
    assert bin_ == "~/.local/bin/agentsync"
    alarms = [  # cycle's own words (cycle.py: the breaker, the secret scan's purge, the credential page)
        "  alarm: deletion breaker TRIPPED: 3 absent file(s) held, nothing removed; if the deletion is real "
        f"run `{bin_} accept-deletions contoso-files` or retire the source",
        "  alarm: a published page matches the secret scan's new rules: 2026-10-12; 2 cached conversion(s) "
        f"deleted, purge queued {cycle._PURGE_QUEUE_STEPS}",
        f"  alarm: a page named after a credential was removed from the tree; `{bin_} purge` it from history",
    ]
    for alarm in alarms:
        assert setup_report._scrub_item_paths(alarm) == alarm
    beside = f"  alarm: Clients/Contoso/plan.docx could not be read; run `{bin_} sync`"
    assert "Contoso" not in setup_report._scrub_item_paths(beside), "an item path is still <path>"
    assert (
        setup_report._scrub_item_paths("  error: Clients/Contoso/plan.docx: timed out")
        == "  error: <path>: timed out"
    )


@pytest.mark.usefixtures("roomy_budget")
def test_the_pandoc_line_is_the_pandoc_the_converters_run(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 14 Q60: the report ran the wheel's pandoc while the converters ran the operator's native
    arm64 copy. It asks the converters' resolver, says when the copy is the one it ran, and gives the
    resolver's reason, not a traceback, when the wheel's binary is missing."""
    from agentsync.convert import pandoc as pandoc_mod  # noqa: PLC0415
    from agentsync.errors import ConversionError  # noqa: PLC0415

    copy = tmp_path / "pandoc-3.9-arm64" / "bin" / "pandoc"
    copy.parent.mkdir(parents=True)
    copy.write_text("#!/bin/sh\necho 'pandoc 3.9'\n", encoding="utf-8")
    copy.chmod(0o755)
    monkeypatch.setattr(pandoc_mod, "resolved_pandoc", lambda configured: (copy, "native"))
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    # B13 round 17 U48: the line is the doctor's own check of that pandoc, started once a report
    [line] = [ln for ln in section(text, "Environment").splitlines() if ln.startswith("- pandoc: ")]
    assert line.startswith("- pandoc: pandoc 3.9 at ") and line.endswith("/bin/pandoc (native arm64 copy)"), (
        line
    )

    def missing(configured: Path | None) -> tuple[Path, str]:
        raise ConversionError(
            "bundled pandoc not found at /nonexistent/pandoc (is pypandoc_binary installed?)"
        )

    monkeypatch.setattr(pandoc_mod, "resolved_pandoc", missing)
    monkeypatch.setattr(pandoc_mod, "_wheel_pandoc", lambda: Path("/nonexistent/pandoc"))
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    env = section(text, "Environment")
    # doctor's own words for the missing binary, never a traceback; its fix stays on Doctor's line
    said = (
        "/nonexistent/pandoc is missing or not executable (doctor's pandoc check: [FAIL]; its fix is in "
        "Doctor)"
    )
    assert f"- pandoc: {said}\n" in env, env


@pytest.mark.usefixtures("roomy_budget")
def test_sections_carry_the_facts(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    env = section(text, "Environment")
    for label in ("macOS", "architecture", "MDM enrollment", "Xcode Command Line Tools", "uv", "pandoc"):
        assert f"- {label}" in env, label
    assert "OneDrive.app:" in env and "Company Portal:" in env and "agentsync resolves:" in env
    assert "listable, 2 provider folders" in env and "~/.local/bin on PATH:" in env
    assert "- agentsync on PATH: " in env
    inst = section(text, "Installer")
    assert "1 install.sh run" in inst and "exit 0 after 57s" in inst and "57s in total" in inst
    assert re.search(r"^\| agentsync +\| +41 \| done +\|$", inst, re.MULTILINE), "the per-step table"
    assert re.search(
        r"^\| doctor +\| +3 \| done, rc 1 \(agentsync status reported \[FAIL\] lines\) +\|$",
        inst,
        re.MULTILINE,
    )
    raw = inst.split("<details>", 1)[1]
    assert "step=doctor seconds=3 rc=1" in raw.split("</details>", 1)[0], "raw log collapsed"
    assert "step=doctor seconds=3 rc=1" not in inst.split("<details>", 1)[0]
    conf = section(text, "Configuration")
    assert "sources: 3 (by kind: inbox 1, local 2" in conf  # init keeps the inbox (KISS K05)
    assert "2 under ~/Library/CloudStorage" in conf
    assert "client_id: not set" in conf and "a git repo" in conf
    assert "- [policy] label rule: off\n" in conf
    doctor = section(text, "Doctor")
    m = re.search(r"^(\d+) ok: (.+)$", doctor, re.MULTILINE)
    assert re.search(r"\d+ checks: \d+ ok", doctor) and m and "python" in m.group(2).split(", ")
    assert "[ok  ]" not in doctor, "only the lines that are not ok are listed"
    assert re.search(r"^\[(?:warn|FAIL)\s*\] \S+ ", doctor, re.MULTILINE), "non-ok lines are kept"
    optional = r"^\[info\s*\] launchd\.poll .* not installed \(optional background sync"  # KISS K11a
    assert re.search(optional, doctor, re.MULTILINE), "the INFO line, with no fix, is kept too"
    status = section(text, "Status")
    assert "lock: free" in status and "last runs: none" in status
    bg = section(text, "Background runs")
    assert "- com.agentsync.poll: not installed" in bg and "- com.agentsync.reconcile: not installed" in bg
    assert "TCC_PENDING reason=canary" in bg
    summary = section(text, "Summary")
    tags = re.search(r"(\d+) checks: \d+ ok, \d+ info, (\d+) warn, (\d+) FAIL", doctor)
    assert tags and f"- doctor: {tags[3]} FAIL, {tags[2]} warn ({tags[1]} checks) · " in summary
    assert (
        "- install.sh: 1 install run; the last exit 0 after 57s (uv 0s · agentsync 41s · launcher 12s · "
        "config 1s · doctor 3s · agent 0s)" in summary
    )
    assert "- background sync: poll not installed · reconcile not installed" in summary
    errors = section(text, "Recent errors")
    assert "The last 2 of 2 WARNING/ERROR lines" in errors and "not an error line" not in errors
    assert "~~~text" in text and "```" not in text, "no backtick fences (the issue form wraps it in some)"


def test_no_redact_and_friction_are_deleted_and_setup_report_is_hidden(
    fake_mac: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K16a: argparse rejects --no-redact and --friction (exit 2, nothing written); setup-report and its
    --out are hidden from help and still parse."""
    out = tmp_path / "r.md"
    for dropped in (["--no-redact"], ["--friction", str(tmp_path / "f.md")]):
        argv = ["setup-report", "--out", str(out), "--config", str(fake_mac["config"]), *dropped]
        assert cli.main(argv) == cli.EXIT_USAGE, dropped
    assert not out.exists()
    capsys.readouterr()
    assert cli.main(["--help"]) == cli.EXIT_OK
    assert not re.search(r"(?m)^    setup-report\b", capsys.readouterr().out), "hidden from help"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["setup-report", "--help"])
    assert "--out" not in capsys.readouterr().out
    assert cli.build_parser().parse_args(["setup-report"]).out == Path(setup_report.DEFAULT_OUT)


def test_the_friction_env_var_names_the_log_the_report_reads(
    fake_mac: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """KISS K16a: with --friction gone, $AGENTSYNC_FRICTION_LOG is the one way to point setup-report at
    another friction log (install.sh's friction_file() honours the same variable): present it is embedded,
    missing it is named in the section."""
    elsewhere = fake_mac["home"] / "elsewhere" / "friction-alt.md"
    elsewhere.parent.mkdir()
    monkeypatch.setenv(setup_report.FRICTION_ENV, str(elsewhere))
    write_friction(fake_mac, path=elsewhere)
    capsys.readouterr()
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0 and "; friction log embedded)" in capsys.readouterr().out
    assert "Embedded from ~/elsewhere/friction-alt.md as this report read it" in section(
        text, "Agent friction log"
    )

    missing = fake_mac["home"] / "elsewhere" / "none.md"
    monkeypatch.setenv(setup_report.FRICTION_ENV, str(missing))
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0 and f"no friction log found at {missing}" in capsys.readouterr().out
    assert "No friction log found at ~/elsewhere/none.md" in section(text, "Agent friction log")
    assert f"${setup_report.FRICTION_ENV} names another file" in section(text, "Agent friction log")


def test_a_broken_section_is_recorded_not_raised(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    logs = fake_mac["logs"]
    logs.chmod(0)
    try:
        rc, text, elapsed = report(tmp_path, fake_mac["config"])
    finally:
        logs.chmod(0o700)
    assert rc == 0 and elapsed < 15
    assert "This section failed: PermissionError" in section(text, "Recent errors")
    headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
    assert headings == list(setup_report.SECTION_TITLES)


@pytest.mark.timing
def test_a_crashing_or_hanging_hook_never_takes_the_report_down(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_report, "_DOCTOR_GRACE_S", 0.0)  # the bound itself, not the slow doctor's grace

    def crash(config: object) -> list[str]:
        raise RuntimeError("doctor exploded")

    def hang(config: object) -> list[str]:
        time.sleep(30)
        return []

    t0 = time.monotonic()
    text, _red = setup_report.build_report(
        fake_mac["config"], hooks=setup_report.ReportHooks(doctor=hang, status=crash), budget_s=4.0
    )
    assert time.monotonic() - t0 < 6, "the hung doctor was abandoned at the time limit"
    assert "This section failed: RuntimeError: doctor exploded" in section(text, "Status")
    assert "Cut off: the doctor call did not finish within " in section(text, "Doctor")


def test_missing_config_is_a_finding(tmp_path: Path) -> None:
    """B13 round 12 N11: a missing sources.toml read ``- loads: no: ConfigError: ...: not found; run
    `~/.local/bin/agentsync add-source <folder>` to create it`` and ``NEXT: not read (the config does not
    load)``: the prompt's finish reads "loads: no" as a config that fails, and tells the agent not to run
    add-source. No file yet is its own words; a file that exists and fails keeps "loads: no"."""
    rc = cli.main(["setup-report", "--out", str(tmp_path / "r.md"), "--config", str(tmp_path / "none.toml")])
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert rc == 0
    configuration = section(text, "Configuration")
    assert "- loads: pending: no sources.toml yet (setup's step 2 writes it)" in configuration, configuration
    assert "loads: no" not in configuration and "add-source" not in configuration, configuration
    assert "- Loop: unknown (no status loop line); NEXT: not read (no sources.toml yet)" in section(
        text, "Summary"
    )
    assert "Not run: no sources.toml yet." in section(text, "Doctor")
    assert "does not load" not in text and "add-source" not in text, text
    assert "No install log at" in section(text, "Installer")
    # a file that exists and does not load keeps its words, and the error's own
    (tmp_path / "bad.toml").write_text("[[source]]\nid = \n", encoding="utf-8")
    rc = cli.main(["setup-report", "--out", str(tmp_path / "r.md"), "--config", str(tmp_path / "bad.toml")])
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "- loads: no: ConfigError" in section(text, "Configuration")
    assert "Not run: the config does not load" in section(text, "Doctor")
    assert "NEXT: not read (the config does not load)" in section(text, "Summary")
    assert "no sources.toml yet" not in text


@pytest.mark.usefixtures("clean_doctor")
def test_a_config_the_sync_refuses_loads_but_says_so(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    """B13 round 15 S8, S55: a sources.toml that loads, but that every sync refuses with exit 78 and a
    ``configuration error:`` line, read ``- loads: yes`` with no reason, so v23's finish told the person it
    "does not load". A live check of the sync's own read-only refusals now says ``- loads: yes, but a sync
    refuses it: ...``: a source deleted from the file while its pages are mirrored (named by place, never by
    id), and a policy.toml beside it that does not pass (its table and key, never its words, which can name
    a label). A refusal the live check reads and no longer finds drops the line; one it cannot read is
    taken from step 2's first sync in install.out. No such line begins "loads: no" or "loads: not", the
    finish's keys for a file that does not load or was not checked."""
    cfg = fake_mac["config"]
    for folder in (fake_mac["one"], fake_mac["two"]):
        (folder / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(cfg)]) == 0
    whole = cfg.read_text(encoding="utf-8")
    gone = next(src.id for src in load_config(cfg).sources if src.path == fake_mac["two"])
    head, *blocks = whole.split("[[source]]")
    kept = [b for b in blocks if f'id = "{gone}"' not in b]
    assert len(kept) == len(blocks) - 1
    trimmed = "[[source]]".join([head, *kept])
    cfg.write_text(trimmed, encoding="utf-8")
    assert cli.main(["sync", "--config", str(cfg)]) == 78  # every sync refuses it; the file still loads
    load_config(cfg)

    def loads() -> str:
        rc, text, _ = report(tmp_path, cfg)
        assert rc == 0
        [line] = [ln for ln in section(text, "Configuration").splitlines() if ln.startswith("- loads: ")]
        assert not line.startswith(("- loads: no", "- loads: not")), line
        return line

    line = loads()
    # B13 round 18 V18: the folder add-source adds back under the same id, as Doctor's fix names it
    add_back = (
        "; or add its folder again, under the same id: ~/.local/bin/agentsync add-source "
        '"~/Library/CloudStorage/OneDrive-SharedLibraries-<org-1>/<library-1>/<folder-3>"'
    )
    assert line == (
        "- loads: yes, but a sync refuses it: a source (<folder-3>, not in the config; 1 file) is "
        "mirrored but no longer in sources.toml; retirement is explicit: put the [[source]] block back with "
        f"{RETIRE_ADVICE}, or restore it{add_back}"
    ), line
    tail = doctor.vanished_add_back_tail([Path("/x")], every=True)
    assert doctor.vanished_fix([Path("/x")], every=True).endswith(tail), "Doctor's fix ends with it"
    assert add_back.startswith(tail.removesuffix(' "/x"') + ' "~/'), "the loads line's tail is Doctor's"
    # a root that is gone has no add-back, as in Doctor
    fake_mac["two"].rename(tmp_path / "moved away")
    assert loads().endswith(f"{RETIRE_ADVICE}, or restore it")
    (tmp_path / "moved away").rename(fake_mac["two"])
    # the sync's order: a policy that does not pass is what it prints first, so the report gives that one
    (cfg.parent / "policy.toml").write_text("[policy]\nrefuse_unlabelled = 1\n", encoding="utf-8")
    assert loads().startswith(
        "- loads: yes, but a sync refuses it: [policy] refuse_unlabelled in policy.toml"
    )
    (cfg.parent / "policy.toml").unlink()
    # step 2's first sync refused it; the live check still reads the cause, so the line is the live one
    write_install_log(fake_mac, rc=1, first_sync="seconds=3 rc=78 result=failed")
    out = fake_mac["setup"] / "install.out"
    said = f"configuration error: source(s) {gone!r} (1 file(s)) are mirrored but no longer in sources.toml"
    out.write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n" + said + "\n",
        encoding="utf-8",
    )
    assert loads() == line
    # fixed since step 2: the live check finds nothing, and the cause is one it reads, so no line
    cfg.write_text(whole, encoding="utf-8")
    assert loads() == "- loads: yes"
    # a policy.toml beside it that does not pass: its key and file, not the value, which can name a label
    policy_toml = cfg.parent / "policy.toml"
    policy_toml.write_text('[policy]\nexclude_label_ids = ["Payroll Only"]\n', encoding="utf-8")
    assert cli.main(["sync", "--config", str(cfg)]) == 78
    _rc, text, _ = report(tmp_path, cfg)
    assert "Payroll" not in text
    # B13 round 17 U22, U43: the error's own words, its value a placeholder as in Doctor, and status named
    assert loads() == (
        "- loads: yes, but a sync refuses it: [policy] exclude_label_ids in policy.toml is not valid (a "
        "value in the error is a placeholder here; `~/.local/bin/agentsync status` prints it in full): "
        "[policy]: exclude_label_ids: <label> is not a label GUID"
    )
    assert "not shown" not in section(text, "Configuration")
    policy_toml.unlink()
    # a cause the live check does not read: step 2's own line, from install.out
    out.write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n"
        "configuration error: a refusal no report check reads\n",
        encoding="utf-8",
    )
    assert loads() == (
        "- loads: yes, but step 2's sync refused it (run 20260929T100000Z): configuration error: a refusal "
        "no report check reads"
    )
    # a policy.toml step 2 could not read, fixed since: the live check reads that cause, so no line
    out.write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n"
        f"configuration error: {cfg.parent}/policy.toml: cannot read the policy table: Expected '='\n",
        encoding="utf-8",
    )
    assert loads() == "- loads: yes"
    # a manifest the report cannot read: step 2's vanished-source line, its ids never in clear (this one is
    # also a folder's slug, so the Redactor knows it as that folder's placeholder: U23's one name)
    out.write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n" + said + "\n",
        encoding="utf-8",
    )
    cfg.write_text(trimmed, encoding="utf-8")
    db = expand(load_config(cfg).state_paths.db)
    db.write_bytes(b"not a database, " * 64)
    line = loads()
    assert line.startswith(
        "- loads: yes, but step 2's sync refused it (run 20260929T100000Z): configuration error: source(s) "
        "(<folder-3>, not in the config; 1 file(s)) are mirrored but no longer in sources.toml"
    ), line
    assert gone not in line
    # B13 round 18 V7: step 2's line ends with the add-back, a folder path with a space: it is cut before the
    # ids are labelled, so the folder is never labelled a source "not in the config", nor shown in clear
    readd = (
        f"; or add its folder again, under the same id: ~/.local/bin/agentsync add-source '{fake_mac['two']}'"
    )
    out.write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n"
        f"configuration error: {cycle.vanished_detail({gone: 1})}; {cycle.VANISHED_ADVICE}{readd}\n",
        encoding="utf-8",
    )
    line = loads()
    assert line.endswith(f"{cycle.VANISHED_ADVICE}; or add its folder again, under the same id"), line
    assert line.count("not in the config") == 1, line
    assert not any(w in line for w in (*FOLDERS, gone, str(fake_mac["home"]))), line
    write_install_log(fake_mac)  # step 2's first sync passed: nothing to say
    assert loads() == "- loads: yes"


def test_a_refusals_loads_line_gives_the_errors_words_with_its_values_redacted() -> None:
    """B13 round 17 U22, U43: the loads line said the error's words "are not shown here, as they can name a
    label or a host", while Doctor in the same report showed them with any label or remote as a placeholder
    (round 16 T3), and it named `agentsync sync`, which writes, where install.sh's NEXT names status. It
    gives Doctor's words, the file's path alone cut from their front, so every value pattern that redacts
    Doctor's line redacts them (lane B's U103 ``got <value>`` needs the ``[governance].<key>: `` before it),
    and names status for the full text. Round 18 V19: the clause about a placeholder is said only when the
    words hold one (an unknown key, a TOML error or a policy type error hold none, nor does a report with
    redaction off)."""
    cfg = Path("/Users/jdoe/agent-context/sources.toml")
    tail = "(a value in the error is a placeholder here; `~/.local/bin/agentsync status` prints it in full)"
    red = setup_report.Redactor()

    def refusal(message: str, file: str = cfg.name, redactor: setup_report.Redactor = red) -> str:
        said = setup_report._table_refusal(ConfigError(f"{cfg}: {message}"), file, cfg, redactor)
        return redactor.redact(said)

    said = refusal("[governance].history_days: expected an integer >= 1, got 'thirty'")
    assert said == (
        f"[governance] history_days in sources.toml is not valid {tail}: [governance].history_days: expected "
        "an integer >= 1, got <value>"
    )
    prefix = refusal("[governance].remote_url_prefixes: https://git.contoso.invalid/x carries a password")
    assert prefix == (
        f"[governance] remote_url_prefixes in sources.toml is not valid {tail}: "
        "[governance].remote_url_prefixes: <remote> carries a password"
    )
    unknown = refusal("[governance]: unknown key(s) foo; allowed: archive")
    assert unknown.startswith(
        "the [governance] table in sources.toml is not valid: [governance]: unknown key(s) foo;"
    ), unknown
    toml = refusal("not valid TOML: Expected '=' (at line 2)", "x")
    assert toml.endswith(" in x is not valid: not valid TOML: Expected '=' (at line 2)"), toml
    policy = refusal("[policy]: refuse_unlabelled must be true or false", "policy.toml")
    assert policy == (
        "[policy] refuse_unlabelled in policy.toml is not valid: [policy]: refuse_unlabelled must be true or "
        "false"
    ), policy
    off = setup_report.Redactor(enabled=False)
    plain = refusal("[governance].history_days: expected an integer >= 1, got 'thirty'", redactor=off)
    assert "placeholder" not in plain and "got 'thirty'" in plain, plain
    assert "Error" not in said + prefix + toml.replace("Expected", ""), "never a class name"


def test_a_governance_values_redaction_never_swallows_the_words_after_it() -> None:
    """B13 round 18 V45: the ``<value>`` match ran to the line's end, so ``[governance].hold: expected
    true/false, got "yes"; once that is fixed, a sync also refuses it: a source (...)`` lost the second
    refusal and its advice (U58's join), and a friction line lost its `` | `` fix column. The loads line's
    value is cut where the exception ends, and free text matches the repr's own shape first; a string or a
    list holding ``; `` or `` | `` is still replaced whole."""
    cfg = Path("/Users/jdoe/agent-context/sources.toml")
    red = setup_report.Redactor()
    hold = setup_report._table_refusal(
        ConfigError(f'{cfg}: [governance].hold: expected true/false, got "yes"'), cfg.name, cfg, red
    )
    later = (
        "; once that is fixed, a sync also refuses it: a source (<source-3>, not in the config; 5 files) is "
        "mirrored but no longer in sources.toml; retirement is explicit: put the [[source]] block back"
    )
    line = red.redact(f"- loads: yes, but a sync refuses it: {hold}{later}")
    assert line.endswith(f"got <value>{later}"), line
    reason = setup_report._table_refusal(
        ConfigError(
            f"{cfg}: [governance].hold_reason: expected a non-empty string, got "
            "['Contoso Legal; Zebra Matter']"
        ),
        cfg.name,
        cfg,
        red,
    )
    assert "Zebra" not in red.redact(reason) and red.redact(reason).endswith("got <value>"), reason
    for value in ("['Contoso Legal; Zebra Matter']", "'Zebra | Matter'", '"yes"', "30"):
        friction = (
            f"| 3 | step 2 | error | [governance].hold_reason: expected a non-empty string, got {value} | "
            "fix the value |"
        )
        shown = red.redact(friction)
        assert "Zebra" not in shown and shown.endswith("got <value> | fix the value |"), shown
    assert red.redact("[governance].history_days: expected an integer >= 1, got thirty days (fix: x)") == (
        "[governance].history_days: expected an integer >= 1, got <value> (fix: x)"
    ), "text of no repr shape runs to the fix, as before"
    # review: blank runs between nested closing brackets backtracked for minutes (about n^4)
    blanks = " " * 3000
    t0 = time.monotonic()
    red.redact(f"[governance].hold_reason: expected a non-empty string, got [[[1{blanks}]{blanks}]{blanks}")
    assert time.monotonic() - t0 < 5, "no catastrophic backtracking"


@pytest.mark.usefixtures("clean_doctor")
def test_a_vanished_sources_count_is_inside_its_labels_bracket(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 16 T7, T15: the loads line read ``a source (not in the config, 1) (4 files)``, two brackets
    side by side. The count now sits inside the label's own bracket, which keeps the label the evidence tables
    name the source by; so does step 2's line from install.out, an older build's ``file(s)`` included."""
    cfg = fake_mac["config"]
    for n in range(4):
        (fake_mac["two"] / f"notes-{n}.txt").write_text("notes\n", encoding="utf-8")
    (fake_mac["one"] / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(cfg)]) == 0
    gone = next(src.id for src in load_config(cfg).sources if src.path == fake_mac["two"])
    head, *blocks = cfg.read_text(encoding="utf-8").split("[[source]]")
    cfg.write_text("[[source]]".join([head, *(b for b in blocks if f'id = "{gone}"' not in b)]), "utf-8")
    _rc, text, _ = report(tmp_path, cfg)
    [line] = [ln for ln in section(text, "Configuration").splitlines() if ln.startswith("- loads: ")]
    assert line.startswith(
        "- loads: yes, but a sync refuses it: a source (<folder-3>, not in the config; 4 files) is "
    ), line
    assert ") (" not in line, line
    labels = setup_report._Labels(load_config(cfg), setup_report.Redactor())
    said = (
        "configuration error: sources 'a' (3 files), 'b' (1 file) are mirrored but no longer in sources.toml"
    )
    shown = setup_report._QUOTED_COUNT_RE.sub(
        lambda m: setup_report._with_count(labels.of(m.group(1)), m.group(2)), said
    )
    assert shown == (
        "configuration error: sources (not in the config, 1; 3 files), (not in the config, 2; 1 file) are "
        "mirrored but no longer in sources.toml"
    ), shown
    assert setup_report._with_count("<source-2>", "1 file") == "<source-2> (1 file)"


@pytest.mark.usefixtures("roomy_calls")
def test_a_vanished_sources_id_is_a_placeholder_in_every_section(
    fake_mac: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 16 T42, T53, T66: doctor's config.vanished_sources [FAIL] quotes the id of a source whose
    [[source]] block is gone, and the report registered only the configured ids, so Doctor (and step 2's
    refusal in the install.out tail) showed it in clear while Configuration named it by place. Every id the
    manifest or the purge queue holds that sources.toml no longer has is now registered, after the configured
    ones. An id sharing no word with any folder name, and no ``clean_doctor``: the [FAIL] line is the leak."""
    cfg = fake_mac["config"]
    gone = next(src.id for src in load_config(cfg).sources if src.path == fake_mac["two"])
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(f'id = "{gone}"', 'id = "zeta-ledger"'), "utf-8")
    for folder in (fake_mac["one"], fake_mac["two"]):
        (folder / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(cfg)]) == 0
    head, *blocks = cfg.read_text(encoding="utf-8").split("[[source]]")
    cfg.write_text("[[source]]".join([head, *(b for b in blocks if 'id = "zeta-ledger"' not in b)]), "utf-8")
    capsys.readouterr()
    assert cli.main(["sync", "--config", str(cfg)]) == 78
    [refused] = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("configuration error: ")]
    assert "'zeta-ledger'" in refused
    (fake_mac["setup"] / "install.out").write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n"
        f"{refused}\n[FAIL] config.vanished_sources — {cycle.vanished_detail({'zeta-ledger': 1})}\n",
        encoding="utf-8",
    )
    rc, text, _ = report(tmp_path, cfg)
    assert rc == 0
    assert "zeta" not in text.lower(), [ln for ln in text.splitlines() if "zeta" in ln.lower()]
    [fail] = [
        ln for ln in section(text, "Doctor").splitlines() if ln.startswith("[FAIL] config.vanished_sources")
    ]
    assert re.search(r"source '<source-\d+>' \(1 file\) is mirrored", fail), fail
    installer = section(text, "Installer")
    assert installer.count(re.search(r"<source-\d+>", fail).group(0)) >= 2, installer  # type: ignore[union-attr]
    # B13 round 17 U8, U23, U44: one source, one name: the loads line uses Doctor's placeholder
    token = re.search(r"<source-\d+>", fail).group(0)  # type: ignore[union-attr]
    conf = section(text, "Configuration")
    assert f"a source ({token}, not in the config; 1 file) is mirrored but no longer in sources.toml" in conf
    assert "(not in the config, " not in text, "no second name for it anywhere"


def test_an_unconfigured_id_the_redactor_does_not_know_keeps_its_fallback_label() -> None:
    """B13 round 17 U8, U23, U44: an id the Redactor never registered (an unreadable manifest, a timed-out
    read, a word the report prints) is never shown in clear: it keeps ``(not in the config, N)``, numbered
    among the fallback labels alone, and the legend says what that label is."""
    config = Config(config_path=Path("sources.toml"))
    red = setup_report.Redactor()
    red.add("source", "zeta-ledger")
    labels = setup_report._Labels(config, red)
    assert labels.of("zeta-ledger") == "(<source-1>, not in the config)"
    assert labels.of("northwind-archive") == "(not in the config, 1)"
    assert labels.of("tailspin-bridge") == "(not in the config, 2)"
    body = "a source (not in the config, 1; 3 files) is mirrored"
    assert setup_report._NOT_IN_CONFIG_LEGEND in "\n".join(
        setup_report._redaction_section(red, [], body=body)
    )
    assert setup_report._NOT_IN_CONFIG_LEGEND not in "\n".join(setup_report._redaction_section(red, []))


def test_neither_report_says_its_source_ids_number_as_the_others() -> None:
    """B13 round 18 V51: ``_unconfigured_ids`` said the shared ids "number alike in both reports" (U62), and
    install.sh that it registers them "as the Python report" does; the shell numbers ids by their table's
    place, the always-present inbox's too, which the Python report skips, so one vanished id is <source-1>
    there and <source-2> here. Neither claims a parity the code does not have."""
    report = Path(setup_report.__file__).read_text(encoding="utf-8")
    shell = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "number alike in both reports" not in report and "as the shell registers them" not in report
    assert "as the Python report\n\t# registers the manifest's ids" not in shell
    assert "so a number is this report's own (round 18 V51)" in shell


def test_a_vanished_id_numbers_before_a_retired_one_and_a_retired_report_word_stays_a_word(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 17 U62: every unconfigured id was registered in one sorted list, so a retired id with only
    tombstones (which no vanished line quotes, and the shell does not register) that sorts first moved the
    vanished id's ``<source-N>`` away from the shell report's; and a retired id that is a word the report
    prints ("setup") cut the report's own words. The ids a vanished line can quote come first, then the
    rest, a report word among them left out."""
    cfg = fake_mac["config"]
    (fake_mac["one"] / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(cfg)]) == 0
    config = load_config(cfg)
    one = next(src.id for src in config.sources if src.path == fake_mac["one"])
    conn = sqlite3.connect(expand(config.state_paths.db))
    try:  # copies of a synced source's rows under ids sources.toml lacks: no foreign keys on a raw connection
        conn.execute("CREATE TEMP TABLE t AS SELECT * FROM items WHERE source_id = ? AND is_dir = 0", (one,))
        for sid, state in (("zeta-ledger", None), ("aaa-old", "tombstone"), ("setup", "tombstone")):
            conn.execute("UPDATE t SET source_id = ?, state = coalesce(?, state)", (sid, state))
            conn.execute("INSERT INTO items SELECT * FROM t")
        conn.commit()
    finally:
        conn.close()
    vanished, rest = setup_report._unconfigured_ids(config)
    assert (vanished, rest) == (["zeta-ledger"], ["aaa-old", "setup"])
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    vanished_at = re.fullmatch(
        r"source '<source-(\d+)>' \(1 file\)", red.redact("source 'zeta-ledger' (1 file)")
    )
    retired_at = re.fullmatch(r"<source-(\d+)>", red.redact("aaa-old"))
    assert vanished_at and retired_at and int(vanished_at.group(1)) < int(retired_at.group(1))
    assert red.redact("the setup report") == "the setup report"


@pytest.mark.usefixtures("roomy_calls")
@pytest.mark.parametrize("under", [False, True], ids=["cli", "install-sh"])
@pytest.mark.parametrize("where", ["cloud", "local", "deep-cloud", "apostrophe"])
def test_a_vanished_sources_add_back_folder_is_never_in_the_report(
    fake_mac: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    where: str,
    under: bool,
) -> None:
    """B13 round 17 U46: doctor's config.vanished_sources fix now names the folder add-source adds back under
    the same id (``doctor.vanished_fix``), a path no configured source has any more. The report's Doctor
    section must show it by placeholder, never in clear: a cloud folder, and a local folder outside
    CloudStorage. No ``clean_doctor``: the [FAIL] line is the leak. Round 18 V7, V8: under install.sh the
    add-back is its --source-local, the folder in install.sh's double quotes, and that is redacted too."""
    cfg = fake_mac["config"]
    if where == "cloud":
        folder = fake_mac["two"]
    elif where == "deep-cloud":  # below the listing's level 3 (review)
        folder = fake_mac["one"] / "Zeta Deep" / "Specs Ledger"
        folder.mkdir(parents=True)
        assert cli.main(["add-source", str(folder), "--config", str(cfg)]) == 0
    else:  # V7's review: a ' in the name broke shlex.quote's form into pieces no value matched
        name = "Zeta's Ledger Notes" if where == "apostrophe" else "Zeta Ledger Notes"
        folder = fake_mac["home"] / "Projects" / name
        folder.mkdir(parents=True)
        assert cli.main(["add-source", str(folder), "--config", str(cfg)]) == 0
    (folder / "notes.txt").write_text("notes\n", encoding="utf-8")
    [gone] = [src.id for src in load_config(cfg).sources if src.path == folder]
    assert cli.main(["sync", "--config", str(cfg)]) == 0
    head, *blocks = cfg.read_text(encoding="utf-8").split("[[source]]")
    cfg.write_text("[[source]]".join([head, *(b for b in blocks if f'id = "{gone}"' not in b)]), "utf-8")
    capsys.readouterr()
    if under:
        monkeypatch.setenv(loop.NO_NEXT_HINT_ENV, "1")
    assert cli.main(["status", "--config", str(cfg)]) == 1
    [fail] = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("[FAIL] config.vanished")]
    said = f'--source-local "{folder}")' if under else f'add-source "{folder}")'
    assert said in fail, fail  # the leak this test guards
    rc, text, _ = report(tmp_path, cfg)
    assert rc == 0
    words = [*folder.name.split(), *(["Zeta"] if where in ("deep-cloud", "apostrophe") else []), gone]
    leaked = [ln for ln in text.splitlines() if any(w.lower() in ln.lower() for w in words)]
    assert not leaked, leaked
    [shown] = [ln for ln in section(text, "Doctor").splitlines() if ln.startswith("[FAIL] config.vanished")]
    cmd = (
        "~/src/agent-context-sync/scripts/install.sh --source-local "
        if under
        else "~/.local/bin/agentsync add-source "
    )
    assert f"; or add its folder again, under the same id: {cmd}" in shown, shown


def test_the_reports_vanished_source_check_is_the_cycles(fake_mac: dict[str, Path]) -> None:
    """B13 round 16 T52, T87: the report kept its own copy of the vanished-source query, rule and words, and
    its tests wrote the refusal in an older wording, so a change to the cycle's would not have turned them
    red. The report now runs the cycle's own on its read-only connection; this pins the two together."""
    cfg = fake_mac["config"]
    (fake_mac["one"] / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(cfg)]) == 0
    config = load_config(cfg)
    db = expand(config.state_paths.db)
    conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        assert manifest_mod.present_counts_on(conn) == Manifest(db).present_counts() != {}
    finally:
        conn.close()
    for vanished in ({"contoso-a": 1}, {"contoso-a": 3, "contoso-b": 1}):
        said = f"configuration error: {cycle.vanished_detail(vanished)}; {cycle.VANISHED_ADVICE}"
        assert setup_report._reads_again(said, True), said
        assert not setup_report._reads_again(said, False), "an unread manifest leaves it to step 2's line"
    assert cycle.vanished_from(["a"], {"a": 2, "b": 0, "c": 1}) == {"c": 1}


@pytest.mark.usefixtures("roomy_calls")
@pytest.mark.parametrize(
    ("policy", "governance", "secret", "said"),
    [
        (
            '[policy]\nexclude_label_ids = ["Contoso Restricted Zebra"]\n',
            "",
            "Zebra",
            "exclude_label_ids: <label> is not a label GUID",
        ),
        (
            '[policy]\nexclude_label_ids = ["Contoso\'s Zebra"]\n',
            "",
            "Zebra",
            "exclude_label_ids: <label> is not a label GUID",
        ),
        (
            "",
            'remote_url_prefixes = ["zeta-git.example"]',
            "zeta",
            "remote_url_prefixes: <remote> is not a remote URL",
        ),
        (
            "",
            'remote_url_prefixes = ["https://bob:pw@zeta-git.example/x"]',
            "zeta",
            "remote_url_prefixes: <remote> carries a password",
        ),
        # the error prints the prefix unstripped: a blank before it, a newline after it (review, T3)
        (
            "",
            'remote_url_prefixes = [" https://bob:pw@zeta-git.example/x"]',
            "zeta",
            "remote_url_prefixes: <remote> carries a password",
        ),
        (
            "",
            'remote_url_prefixes = ["https://bob:pw@zeta-git.example/x\\n"]',
            "zeta",
            "remote_url_prefixes: <remote>",
        ),
        # B13 round 17 U103: a type error's "got <repr>" (hold_owner, hold, history_days), and a value with no
        # organization word, which the residue check would not flag
        (
            "",
            'hold = true\nhold_reason = "x"\nhold_owner = ["Legal - Zeta Counsel"]',
            "Counsel",
            "[governance].hold_owner: expected a non-empty string, got <value>",
        ),
        ("", 'hold = ["Zeta Counsel"]', "Counsel", "[governance].hold: expected true/false, got <value>"),
        (
            "",
            'history_days = "Zeta Counsel"',
            "Counsel",
            "[governance].history_days: expected an integer >= 1, got <value>",
        ),
        (
            "",
            'hold = true\nhold_reason = "x"\nhold_owner = ["Legal Department"]',
            "Department",
            "[governance].hold_owner: expected a non-empty string, got <value>",
        ),
    ],
)
def test_a_policy_or_governance_errors_value_is_never_in_the_report(
    fake_mac: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    policy: str,
    governance: str,
    secret: str,
    said: str,
) -> None:
    """B13 round 16 T3: the Configuration line held a policy error's words back, as they can name a label or
    a host, while Doctor's [FAIL] line and the install.out tail showed them: the label's name, and a remote's
    host. The value is now a placeholder wherever the report shows the error, matched by pattern (a label
    name is often a word the report prints, so it is never registered as a value). No ``clean_doctor``: the
    check's own [FAIL] line is what leaked."""
    cfg = fake_mac["config"]
    if policy:
        (cfg.parent / "policy.toml").write_text(policy, encoding="utf-8")
    if governance:
        cfg.write_text(f"[governance]\n{governance}\n\n" + cfg.read_text(encoding="utf-8"), encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["sync", "--config", str(cfg)]) == 78
    [line] = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("configuration error: ")]
    assert secret in line, "the sync itself keeps the value: the person needs it to find the entry"
    (fake_mac["setup"] / "install.out").write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n" + line + "\n",
        encoding="utf-8",
    )
    rc, text, _ = report(tmp_path, cfg)
    assert rc == 0
    assert secret not in text and secret.lower() not in text.lower(), [
        ln for ln in text.splitlines() if secret.lower() in ln.lower()
    ]
    assert said in section(text, "Installer"), section(text, "Installer")
    [fail] = [ln for ln in section(text, "Doctor").splitlines() if said in ln]
    assert fail.startswith(("[FAIL] policy ", "[FAIL] governance.config ")), fail
    placeholder = "<label>" if "label" in said else "<remote>" if "remote" in said else "<value>"
    if placeholder == "<value>":  # the Doctor line keeps its fix after the value (U103)
        assert f"got <value> (fix: {cli.GOVERNANCE_FIX})" in fail, fail
    assert setup_report._LEGEND[placeholder[1:-1]] in section(text, "Redaction"), section(text, "Redaction")


def test_the_reports_governance_value_keys_are_governances() -> None:
    """B13 round 17 U103: the report's [governance] value pattern names every key a type error quotes."""
    from agentsync import governance  # noqa: PLC0415

    keys = set(governance._GOVERNANCE_KEYS) - {"remote_url_prefixes"}
    assert set(setup_report._GOVERNANCE_TYPED_KEYS) == keys
    red = setup_report.Redactor()
    line = (
        "[FAIL] governance.config — x: [governance].hold_owner: expected a non-empty string, got 3 (fix: y)"
    )
    assert red.redact(line).endswith("got <value> (fix: y)"), red.redact(line)
    other = "converter: expected a non-empty string, got 'Contoso Notes'"  # no [governance] key before it
    assert red.redact(other) == other


@pytest.mark.parametrize(
    ("url", "words"),
    [
        ("https://git.contoso-docs.invalid/team-zeta/agentsync-docs.git", ("contoso-docs", "team-zeta")),
        ("https://github.com/contoso-team/agentsync-docs.git", ("contoso-team",)),
        ("git@git.contoso-docs.invalid:team-zeta/agentsync-docs.git", ("contoso-docs", "team-zeta")),
    ],
)
def test_a_docs_repo_remotes_url_is_a_placeholder_wherever_a_governance_line_quotes_it(
    fake_mac: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    url: str,
    words: tuple[str, ...],
) -> None:
    """B13 round 17 U1, U15, U56, U93: a docs repo with a remote under ``allow_remote = false`` printed
    ``[FAIL] governance.remote — remote origin (<url>) exists but ...`` with the URL in full: governance.py
    masks only a password, no pattern matched the message, and no reader of git's config read the docs
    repo's. Any host (github.com and a public host are no proxy host) and the path both name the company.
    The URL is ``<remote>`` wherever a governance remote finding quotes it (doctor's [FAIL], init's
    ``governance:`` line, push_rewritten's ``refused:``), before the email pattern takes an ssh URL's front;
    ``agentsync status`` still prints it. No ``clean_doctor``: the check's own [FAIL] line is what leaked."""
    docs = fake_mac["home"] / "agent-context" / "docs"
    subprocess.run(["git", "-C", str(docs), "remote", "add", "origin", url], check=True, capture_output=True)
    capsys.readouterr()
    cli.main(["status", "--config", str(fake_mac["config"])])
    assert url in capsys.readouterr().out, "status keeps the URL: the person needs it to act on the fix"
    refused = f"refused: remote origin ({url}) is not tenant-owned per remote_url_prefixes"
    (fake_mac["setup"] / "install.out").write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n"
        f"governance: remote origin ({url}) exists but [governance] allow_remote = false: every clone\n"
        f"{refused}\n",
        encoding="utf-8",
    )
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    for word in words:
        assert word not in text.lower(), [ln for ln in text.splitlines() if word in ln.lower()]
    [fail] = [ln for ln in section(text, "Doctor").splitlines() if "governance.remote" in ln]
    assert "remote origin (<remote>) exists but [governance] allow_remote" in fail, fail
    inst = section(text, "Installer")
    assert "governance: remote origin (<remote>) exists but" in inst, inst
    assert "refused: remote origin (<remote>) is not tenant-owned per remote_url_prefixes" in inst, inst
    assert "<email-" not in fail, fail
    assert setup_report._LEGEND["remote"] in section(text, "Redaction"), section(text, "Redaction")


def test_a_docs_repo_remotes_host_is_a_proxy_placeholder_where_git_names_it(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 17 U93: a push or fetch error (a 403, a TLS error) names the docs repo's remote host
    without the governance words. The docs repo's git config is read last, after the checkout's, so no
    ``<proxy-N>`` the shell shares moves; its path is the configured docs_repo, not a guess."""
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    docs = fake_mac["home"] / "agent-context" / "docs"
    url = "https://git.contoso-docs.invalid/team-zeta/agentsync-docs.git"
    subprocess.run(["git", "-C", str(docs), "remote", "add", "origin", url], check=True, capture_output=True)
    assert setup_report._git_remotes() == []
    assert setup_report._git_remotes(docs) == [("git.contoso-docs.invalid", url)]
    red = setup_report._build_redactor(
        setup_report._Run(fake_mac["config"], setup_report.ReportHooks(), 12.0)
    )
    said = red.redact("fatal: unable to access 'https://git.contoso-docs.invalid/x': error: 403")
    assert "contoso-docs" not in said and re.search(r"'https://<proxy-\d+>/x'", said), said
    said = red.redact(f"fatal: unable to access '{url}/': error: 403")
    assert "team-zeta" not in said and re.search(r"'https://<proxy-\d+>/<proxy-\d+>/'", said), said
    # B13 round 18 V88: the host ends at a query or a fragment, as the shell cuts it
    for odd in ("https://git.contoso-docs.invalid?x", "https://git.contoso-docs.invalid#x"):
        subprocess.run(["git", "-C", str(docs), "remote", "set-url", "origin", odd], check=True)
        red = setup_report._build_redactor(
            setup_report._Run(fake_mac["config"], setup_report.ReportHooks(), 12.0)
        )
        said = red.redact(
            f"fatal: unable to access '{odd}/': Could not resolve host: git.contoso-docs.invalid"
        )
        assert "contoso-docs" not in said, said
    assert setup_report._rewrite_values("https://proxy.fabrikam.invalid?x") == (
        "proxy.fabrikam.invalid",
    ) * 2 + ("",)
    assert setup_report._rewrite_values("https://u@proxy.fabrikam.invalid:8443#y/team-zeta/") == (
        "proxy.fabrikam.invalid",
        "proxy.fabrikam.invalid",
        "/team-zeta",
    )


# ---- the v5 friction log -------------------------------------------------------------------------------


V5_HAPPY = (
    "Attempt: 2026-09-29T09:58:00Z\n"
    "Prompt: v5\n"
    "Agent: Claude Code, claude-opus-5-5\n"
    "2026-09-29T09:58:05Z | step 1 | start | preflight and code | -\n"
    "2026-09-29T09:58:40Z | step 1 | end | clone ok; install.sh --version: setup-prompt-compat 5 | -\n"
    "2026-09-29T09:58:41Z | step 2 | start | choose folders | -\n"
    f"2026-09-29T09:58:50Z | step 2 | click | {FULL_NAME} clicked Allow for Terminal on OneDrive-{ORG} | -\n"
    f"2026-09-29T09:59:30Z | step 2 | question | asked which folders; chose {FOLDERS[0]}/{FOLDERS[1]} | -\n"
    "2026-09-29T09:59:31Z | step 2 | end | 1 folder chosen | -\n"
    "2026-09-29T09:59:32Z | step 3 | start | install.sh --source-local <folder> --confirm-install-agent | -\n"
    "2026-09-29T10:00:30Z | step 3 | click | clicked Allow for agentsync-launcher | -\n"
    "2026-09-29T10:01:00Z | step 3 | end | install.sh exited 0 | -\n"
    "2026-09-29T10:01:01Z | step 4 | start | agentsync it-request | -\n"
    "2026-09-29T10:01:05Z | step 4 | end | draft written | -\n"
    "2026-09-29T10:01:06Z | end | finished\n"
)
"""Setup prompt v5 run through as its README block asks: one Attempt header, start/end per step, the folder
question, the two Allow clicks, the closing line. With install.log's rc 0 this is fully one command."""

CHANGED = ", or the --log-start value was changed"
"""What the Summary's prompt note adds after a stopped copy's reason, as install.sh's line does (B13 round 9
K22)."""
OLDER_COPY = f"(older than the installer's v8: the pasted copy was not the current README{CHANGED})"
"""What the Summary's prompt line adds for an attempt a v8 installer stopped: its copy was an older one."""


def write_friction(fake_mac: dict[str, Path], text: str = V5_HAPPY, path: Path | None = None) -> Path:
    target = path or fake_mac["setup"] / "friction.md"
    target.write_text(text, encoding="utf-8")
    return target


def write_install_log(
    fake_mac: dict[str, Path],
    *,
    start: str = "2026-09-29T10:00:00Z",
    rc: int = 0,
    first_sync: str = "seconds=34 rc=0 result=done",
    agent: str = "seconds=2 rc=0 result=done note=simulated launchd=simulated",
    simulated: bool = True,
    run: str = "20260929T100000Z-4242",
    append: bool = False,
) -> None:
    """One install.sh run in install.log as scripts/install.sh writes it (setup prompt v5's step 3)."""
    sim = " launchd=simulated" if simulated else ""
    lines = (
        f"{start} run={run} start install.sh compat=5 commit=0123456789ab kind=checkout source=-{sim} "
        f"args=--source-local x --confirm-install-agent\n"
        f"{start} run={run} step=uv seconds=0 rc=0 result=skipped note=present\n"
        f"{start} run={run} step=agentsync seconds=41 rc=0 result=done\n"
        f"{start} run={run} step=doctor seconds=3 rc=0 result=done\n"
        f"{start} run={run} step=first-sync {first_sync}\n"
        f"{start} run={run} step=agent {agent}\n"
        f"{start} run={run} end rc={rc} seconds=57\n"
        f"{start} run={run} step=report seconds=2 rc=0 result=done note=agentsync\n"
    )
    log = fake_mac["setup"] / "install.log"
    log.write_text((log.read_text(encoding="utf-8") if append else "") + lines, encoding="utf-8")


Lines = Callable[[Any], list[str]]


def summary_of(
    fake_mac: dict[str, Path], *, doctor: Lines | None = None, status: Lines | None = None
) -> tuple[str, str]:
    hooks = setup_report.ReportHooks(doctor=doctor, status=status)
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    return text, section(text, "Summary")


@pytest.mark.usefixtures("clean_doctor")
def test_v5_happy_path_is_fully_one_command(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    write_install_log(fake_mac)
    write_friction(fake_mac)
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    for raw in RAW:
        assert raw not in text, f"{raw!r} leaked into the redacted report"
    summary = section(text, "Summary")
    first = summary.strip().splitlines()[0]
    assert first == (
        "- **outcome: fully one command** (computed: install.sh exit 0; no turn beyond the unavoidable ones)"
    )
    assert "agent said" not in summary, "no Outcome: line, so nothing the agent said"
    assert "- attempt:" not in summary, "one attempt: no attempt line"
    assert (
        "- prompt: v5 · run: Sandbox with simulated launchd (computed: install.log says launchd=simulated; "
        "HOME is also under a temporary folder) · agent: Claude Code, claude-opus-5-5" in summary
    ), "the issue form's run-type label (K10); an older version alone says nothing about the copy"
    assert "WARNING" not in summary, "the attempt has its end | finished line"
    assert (
        "- human turns: 3 (1 question; 2 clicks logged, though none is possible; approvals: not observable)"
        in summary
    ), "L9: the short form, counted by kind"
    assert (
        "- expected turns: the folder question F8 · Allow click F7 (step 2) · Allow click F11 (step 3)"
        in (summary)
    ), "expected turns on their own line (K10)"
    # B13 round 8 J14: three zero counts need no "none of these changes the outcome by itself"
    assert "- agent friction: 0 deviation, 0 prompt, 0 error" in summary.splitlines()
    assert "- friction (attempt 1): 12 event lines: 4 start, 4 end, 1 question, 2 click, 1 finished" in (
        summary
    ), "every event line counted once, the finished line too (L9)"
    assert (
        "- time: session 3m06s (first to last timestamp); steps (start to end): 1 35s · 2 50s · 3 1m28s · "
        "4 4s" in summary
    )
    assert (
        "- install.sh: 1 install run during this attempt; the last exit 0 after 57s (uv 0s · agentsync 41s · "
        "doctor 3s · first-sync 34s · agent 2s)" in summary
    )
    assert "- first sync: done in 34s (install.log)" in summary
    assert "- background sync: launchd: simulated (no LaunchAgent of this setup ran)" in summary
    items = summary.split("Items that were not one command (attempt 1):", 1)[1].split("\n\n### Run", 1)[0]
    assert items.strip() == "- none", "the expected turns are not items (K10)"
    metadata = summary.split("\n### Run metadata\n", 1)[1]
    assert metadata.lstrip().startswith("- generated at: "), "run metadata under its own heading (V5)"
    assert "Agent friction (attempt 1" not in summary
    fr = section(text, "Agent friction log")
    assert re.search(
        r"Embedded from ~/agent-context/setup/friction\.md as this report read it: 15 lines, last "
        r"modified \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ",
        fr,
    ), "the report records what it embedded (J4)"
    assert (
        "- attempt 1 (lines 1-15; started 2026-09-29T09:58:00Z; prompt v5; agent Claude Code, "
        "claude-opus-5-5): fully one command; 12 event lines: 4 start, 4 end, 1 question, 2 click, 1 "
        "finished\n" in fr
    ), "a finished attempt says so once, in its counts (B13 round 6 G22)"
    assert "finished; finished" not in fr
    assert (
        "(a line written after that is not in this report: run "
        "`~/src/agent-context-sync/scripts/install.sh --report-only` again)" in fr
    ), "the prompt's command (G2)"
    assert "run setup-report" not in text
    assert "Each line is shown with its line number: the F<n> ids in the Summary are these numbers." in fr, (
        "the Summary cites F7, F8 and F11 (G10)"
    )
    assert "\n  7  2026-09-29T09:58:50Z | step 2 | click | " in fr, "numbered: F7 is line 7 (L9)"


def test_v5_turn_kinds_make_it_worked_with_help(fake_mac: dict[str, Path]) -> None:
    write_install_log(fake_mac)
    extra = (
        "2026-09-29T10:00:40Z | step 3 | question | asked whether to re-run | say it can take 5 min\n"
        "2026-09-29T10:00:45Z | step 3 | click | clicked Allow a second time | -\n"
        "2026-09-29T10:00:50Z | step 3 | deviation | re-ran install.sh | give a 10-minute timeout\n"
        "2026-09-29T10:00:55Z | step 3 | prompt | 'longest timeout' is unclear | say 600000 ms\n"
        "2026-09-29T10:00:56Z | step 3 | approval | you approved install.sh | pre-allow it\n"
        "2026-09-29T10:00:57Z | step 3 | hiccup | an untyped line | -\n"
    )
    text_in = V5_HAPPY.replace(
        "2026-09-29T10:01:00Z | step 3 | end", extra + "2026-09-29T10:01:00Z | step 3 | end"
    )
    write_friction(
        fake_mac,
        text_in.replace(
            "Agent: Claude Code, claude-opus-5-5\n", "Agent: Claude Code\nOutcome: fully one command\n"
        ),
    )
    _text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: worked with help** (computed: install.sh exit 0; 1 question beyond the folder "
        "question; 1 click beyond the Allow clicks; 1 approval)"
    ), "person-facing facts only: the deviation, prompt and untyped lines are agent friction (K4)"
    assert (
        "- agent friction: 1 deviation, 1 prompt, 0 error (F15, F16); 1 line without a prompt kind "
        "(untyped or v4), shown, not counted; none of these changes the outcome by itself" in summary
    )
    assert "- agent said: fully one command" in summary, (
        "the agent's own verdict, shown beside the computed one"
    )
    assert "- human turns: 6 (2 questions; 3 clicks logged, though none is possible; 1 approval)" in summary
    items, agent_side = summary.split("Items that were not one command (attempt 1):", 1)[1].split(
        "Agent friction (attempt 1; it does not change the outcome by itself):", 1
    )
    kinds = re.findall(r"^- F(\d+) · step \d · (\w+) · ", items, flags=re.MULTILINE)
    assert kinds == [("13", "question"), ("14", "click"), ("17", "approval")], (
        "the turns beyond the expected ones"
    )
    assert re.findall(r"^- F(\d+) · step \d · (\w+) · ", agent_side, flags=re.MULTILINE) == [
        ("15", "deviation"),
        ("16", "prompt"),
        ("18", "hiccup"),
    ]
    assert "→ give a 10-minute timeout" in agent_side


def test_approvals_are_counted_or_not_observable_by_tool(fake_mac: dict[str, Path]) -> None:
    write_install_log(fake_mac)
    for agent, expected in (
        ("GitHub Copilot CLI (gpt-5)", "approvals: not observable"),
        ("Cursor agent", "0 approvals"),
    ):
        write_friction(fake_mac, V5_HAPPY.replace("Claude Code, claude-opus-5-5", agent))
        _text, summary = summary_of(fake_mac)
        line = next(ln for ln in summary.splitlines() if ln.startswith("- human turns:"))
        assert expected in line, (agent, line)
    # No Agent: line names no tool that could vouch for zero approvals (B13 round 2 R24).
    write_friction(fake_mac, V5_HAPPY.replace("Agent: Claude Code, claude-opus-5-5\n", ""))
    _text, summary = summary_of(fake_mac)
    line = next(ln for ln in summary.splitlines() if ln.startswith("- human turns:"))
    assert "approvals: not observable" in line, line


def test_an_attempt_with_one_timestamp_says_so_on_its_time_line(fake_mac: dict[str, Path]) -> None:
    """A header-less attempt whose one event line is dated has a time and no duration: "no times" was false
    (B13 round 2 R26). An attempt with no time at all keeps "no times"."""
    write_install_log(fake_mac)
    write_friction(
        fake_mac,
        V5_HAPPY + "2026-10-11T23:19:51Z | step 1 | error | git pull failed (exit 1) | -\n",
    )
    _text, summary = summary_of(fake_mac)
    assert "- time: session: one timestamp (2026-10-11T23:19:51Z), no duration" in summary, summary
    write_friction(fake_mac, "Prompt: v4\nOutcome: in progress\nF1 | step 4 | failed | 3 | stub | -\n")
    _text, summary = summary_of(fake_mac)
    assert "- time: session: no times" in summary, summary


def test_installer_failure_is_failed_at_step_3(fake_mac: dict[str, Path]) -> None:
    write_install_log(
        fake_mac, rc=1, first_sync="seconds=9 rc=1 result=failed", agent="seconds=0 rc=0 result=skipped"
    )
    write_friction(
        fake_mac,
        V5_HAPPY.replace(
            "2026-09-29T10:01:00Z | step 3 | end | install.sh exited 0 | -\n",
            "2026-09-29T10:01:00Z | step 3 | error | install.sh exited 1 at the first sync | -\n",
        ),
    )
    text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: failed at step 3** (computed: install.sh exited 1: its first-sync step failed with "
        "rc 1)"
    )
    assert "- first sync: failed in 9s, rc 1 (install.log)" in summary
    assert "outcome=Failed%20at%20step%202%20%28install%20and%20start%29" in text.splitlines()[-1], (
        "a v5 attempt's step 3 is the current form's step 2"
    )
    # B13 round 11 M24, M41: a failed step line with no rc says so, and never prints "rc ?"
    write_install_log(
        fake_mac, rc=1, first_sync="seconds=9 result=failed", agent="seconds=0 rc=0 result=skipped"
    )
    _text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: failed at step 3** (computed: install.sh exited 1: its first-sync step failed with "
        "no rc logged)"
    )


def test_failure_before_the_installer_is_the_errors_step(fake_mac: dict[str, Path]) -> None:
    (fake_mac["setup"] / "install.log").unlink()
    write_friction(
        fake_mac,
        "Attempt: 2026-09-29T09:00:00Z\nPrompt: v5\nAgent: Claude Code\n"
        "2026-09-29T09:00:05Z | step 1 | start | preflight | -\n"
        "2026-09-29T09:00:06Z | step 1 | error | xcode-select -p exited 2 | -\n"
        "2026-09-29T09:00:20Z | step 5 | start | report | -\n",
    )
    _text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: failed at step 1** (computed: no install.sh run; step 1 logged an error)"
    )
    assert (
        '- WARNING: attempt 1 has no "end | finished" line (the agent\'s end line), so this report may be '
        "stale: it was written before prompt step 5" in summary
    )
    assert "- install.sh: no install log" in summary, "no log at all, as the shell report says (F29)"
    assert "- time: session 20s (first to last timestamp); steps (start to end): " not in summary
    assert "- time: session 20s (first to last timestamp)" in summary
    (fake_mac["setup"] / "install.log").write_text("")
    assert "- install.sh: no run in the install log" in summary_of(fake_mac)[1], "a log with no run in it"


def test_an_earlier_attempt_that_only_listed_the_folders_stopped_and_did_not_fail(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 17 U35: an earlier attempt with no event line, no end line and one --list-folders run that
    listed the folders (rc 0) read "failed at step 1", which is the designed wait for the folder answer (or a
    session closed there). It reads "stopped at step 1 (after its folder list, with no report)" in the
    attempt list and the ``earlier:`` clause. An attempt with an end line, a list that failed, and the same
    state as the latest attempt keep their words."""
    listed = (
        "2026-09-29T09:50:05Z run=20260929T095005Z-9 start install.sh compat=25 commit=0123456789ab "
        "kind=checkout source=- args=--list-folders\n"
        "2026-09-29T09:50:05Z run=20260929T095005Z-9 step=list-folders seconds=4 rc={rc} result={result} "
        "note={note}\n"
        "2026-09-29T09:50:09Z run=20260929T095005Z-9 end rc={rc} seconds=4\n"
    )
    first = "Attempt: 2026-09-29T09:50:00Z\nPrompt: v25\nAgent: Claude Code\n"
    stopped = "earlier: attempt 1 stopped at step 1 (after its folder list, with no report)"

    def summary(
        head: str, rc: int = 0, result: str = "done", note: str = "listed-6 synced=2"
    ) -> tuple[str, str]:
        write_install_log(fake_mac)
        log = fake_mac["setup"] / "install.log"
        log.write_text(listed.format(rc=rc, result=result, note=note) + log.read_text(encoding="utf-8"))
        write_friction(fake_mac, head + V5_HAPPY)
        text, summary = summary_of(fake_mac)
        return section(text, "Agent friction log"), summary

    fr, got = summary(first)
    assert f"- attempt: 2 of 2; {stopped}" in got, got
    assert "stopped at step 1 (after its folder list, with no report); 0 event lines" in fr, fr
    _fr, got = summary(first + "2026-09-29T09:51:00Z | end | finished\n")
    assert "- attempt: 2 of 2; earlier: attempt 1 failed at step 1" in got, "an end line: --report-only ran"
    _fr, got = summary(first, rc=3, result="failed", note="not-signed-in")
    assert stopped not in got and "earlier: attempt 1 " in got, got
    write_install_log(fake_mac)
    log = fake_mac["setup"] / "install.log"
    log.write_text(listed.format(rc=0, result="done", note="listed-6 synced=2"))
    write_friction(fake_mac, first)
    _text, got = summary_of(fake_mac)
    assert "after its folder list, with no report" not in got, "the latest attempt's outcome is unchanged"


def test_the_sync_clause_names_an_earlier_runs_sync_when_the_last_run_stopped_before_its_own(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 17 U42: the last install run failed its config step (rc 65) and logged no first sync, yet
    the Summary's sync clause gave an earlier run's sync, unnamed, with its counts as if they were the last
    run's. It says the last run has none and where it stopped, then the newest earlier run's sync, named; a
    --list-folders run after a completed sync leaves that sync's clause as it was."""
    stopped = (
        "2026-09-29T10:05:00Z run=20260929T100500Z-77 start install.sh compat=25 commit=0123456789ab "
        "kind=checkout source=- args=--source-local x\n"
        "2026-09-29T10:05:00Z run=20260929T100500Z-77 step=agentsync seconds=1 rc=0 result=done\n"
        "2026-09-29T10:05:01Z run=20260929T100500Z-77 step=config seconds=1 rc=65 result=failed\n"
        "2026-09-29T10:05:02Z run=20260929T100500Z-77 end rc=65 seconds=2\n"
    )
    log = fake_mac["setup"] / "install.log"
    write_friction(fake_mac, V5_HAPPY.replace("2026-09-29T10:01:06Z | end | finished\n", ""))
    for first_sync, said in (
        ("seconds=1 rc=0 result=done note=converted-2-deferred-0", "done in 1s"),
        ("seconds=0 rc=0 result=skipped note=status-failed", "skipped in 0s"),
    ):
        write_install_log(fake_mac, first_sync=first_sync)
        log.write_text(log.read_text(encoding="utf-8") + stopped, encoding="utf-8")
        _text, summary = summary_of(fake_mac)
        line = _line(summary, "- first sync: ")
        assert line.startswith(
            "- first sync: no first sync in the last install run (install.log: it stopped at its config "
            f"step) · the newest earlier run's sync: {said}"
        ), line
        assert "(install.log: the install run of 2026-09-29T10:00:00Z)" in line, line
        assert "(install.log)" not in line, "no clause reads as the last run's"
        if "converted" in first_sync:
            named = (
                "converted 2, deferred 0 online-only (install.log: the install run of 2026-09-29T10:00:00Z)"
            )
            assert named in line, line
    write_install_log(fake_mac, first_sync="seconds=1 rc=0 result=done note=converted-2-deferred-0")
    log.write_text(log.read_text(encoding="utf-8") + LIST_RUN.replace("09:58:0", "10:05:0"), encoding="utf-8")
    _text, summary = summary_of(fake_mac)
    assert _line(summary, "- first sync: ").startswith("- first sync: done in 1s (install.log)"), summary
    # B13 round 18 V48: the label is the shown run's; an earlier run that found sources.toml there ran a
    # sync, not a first sync, whatever the last run's config step (which failed with no note) logged
    write_install_log(fake_mac, first_sync="seconds=1 rc=0 result=done note=converted-2-deferred-0")
    earlier = log.read_text(encoding="utf-8").replace(
        "step=agentsync seconds=41 rc=0 result=done\n",
        "step=agentsync seconds=41 rc=0 result=done\n"
        "2026-09-29T10:00:00Z run=20260929T100000Z-4242 step=config seconds=1 rc=0 result=done "
        "note=add-source kept=2 added=0\n",
    )
    log.write_text(earlier + stopped, encoding="utf-8")
    _text, summary = summary_of(fake_mac)
    assert _line(summary, "- sync: ").startswith(
        "- sync: no first sync in the last install run (install.log: it stopped at its config step)"
    ), summary


def test_attempts_the_latest_is_judged_and_earlier_ones_listed(fake_mac: dict[str, Path]) -> None:
    write_install_log(fake_mac)
    first = (
        "Attempt: 2026-09-29T09:00:00Z\nPrompt: v5\nAgent: Claude Code\n"
        "2026-09-29T09:00:05Z | step 1 | start | preflight | -\n"
        "2026-09-29T09:00:06Z | step 1 | error | --version printed setup-prompt-compat 4 | pull first\n"
        "2026-09-29T09:00:30Z | end | finished\n"
    )
    write_friction(fake_mac, first + V5_HAPPY)
    text, summary = summary_of(fake_mac)
    assert summary.strip().startswith("- **outcome: fully one command**"), "the latest attempt is judged"
    assert "- attempt: 2 of 2; earlier: attempt 1 failed at step 1" in summary
    assert "Items that were not one command (attempt 2):" in summary
    fr = section(text, "Agent friction log")
    assert "2 attempts:" in fr
    assert (
        "- attempt 1 (lines 1-6; started 2026-09-29T09:00:00Z; prompt v5; agent Claude Code): failed at "
        "step 1" in fr
    )
    assert "- attempt 2 (lines 7-21; started 2026-09-29T09:58:00Z" in fr
    # the install run belongs to attempt 2 only: attempt 1 is not "worked with help" because of it
    assert "attempt 1 failed at step 1" in summary


def test_v4_logs_are_legacy_and_never_counted(fake_mac: dict[str, Path]) -> None:
    write_install_log(fake_mac)
    write_friction(
        fake_mac,
        "Prompt: v4\nAgent: Claude Code, claude-opus-5-5\nRun: sandbox; simulated steps: launchd\n"
        "Outcome: fully one command\n"
        "F1 | step 1 | clean | 1 | sw_vers and xcode-select -p exited 0 | -\n"
        "F2 | step 3 | needed help | 1 | asked which folders; I clicked Allow | say it first\n"
        "Prompt: v4\nOutcome: in progress\n"
        "F1 | step 4 | failed | 3 | install.sh exited 1 | -\n",
    )
    text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: worked with help** (computed: install.sh exit 0; human turns unknown: the attempt has "
        "no v5 event line (1 legacy v4 line))"
    ), "a second Prompt: block is a second attempt (J5); legacy lines never make it fully one command"
    assert "- agent said: in progress" in summary and "- attempt: 2 of 2; earlier: attempt 1 " in summary
    assert "- human turns: 0 (0 questions; clicks: none possible; approvals: not observable)" in summary, (
        "no keyword guessing (J1); an attempt with no Agent: line cannot vouch for 0 approvals (R24)"
    )
    assert (
        "- expected turns: the folder question: not logged · Allow clicks: none possible (launchd " in summary
    )
    fr = section(text, "Agent friction log")
    assert (
        "no Attempt: line, since it is in the v4 format" in fr
        and "2 legacy v4 lines (F<n> | ...), shown, not counted" in fr
    )
    assert "F2 | step 3 | needed help | 1 | asked which folders; I clicked Allow | say it first" in fr


def test_parse_friction_v5_lines() -> None:
    fr = setup_report.parse_friction(
        "Attempt: 2026-09-30T10:00:00Z\n- **Prompt:** v5\nAgent: Copilot CLI\n"
        "2026-09-30T10:00:01Z | step 2 | Question | which folders? | -\n"
        "`2026-09-30T10:00:02Z` | step 3 | error | `a | b | c` exited 2 | fix it\n"
        "2026-09-30T10:00:03Z | click | no step column\n"
        "not an event line\n"
        "2026-09-30T10:00:09Z | end | finished\n"
    )
    [att] = fr.attempts
    assert att.header == {"Prompt": "v5", "Agent": "Copilot CLI"}
    assert att.started == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    assert [(e.step, e.kind, e.what, e.fix) for e in att.events] == [
        (2, "question", "which folders?", ""),
        (3, "error", "`a | b | c` exited 2", "fix it"),
        (None, "click", "no step column", ""),
        (None, "finished", "finished", ""),
    ]
    assert att.finished and att.wall_seconds() == 9.0 and att.first_line == 1 and att.last_line == 8
    assert fr.latest is att and fr.line_count == 8
    none = setup_report.parse_friction("just prose\n")
    assert none.attempts == () and none.latest is None


STEP1_ONE_LINER = (
    'export GIT_CEILING_DIRECTORIES="$HOME/src" && sw_vers -productVersion && xcode-select -p && { if [ -f '
    "~/src/agent-context-sync/.git/HEAD ] || [ -f ~/src/agent-context-sync/.git ]; then git -C "
    "~/src/agent-context-sync pull --ff-only; else git clone "
    "https://github.com/renchris/agent-context-sync.git ~/src/agent-context-sync; fi; } && "
    "~/src/agent-context-sync/scripts/install.sh --version"
)
"""Step 1's whole one-liner (its start), which an agent may log as "the failed command" (B13 round 14 Q31)."""


def test_a_logged_command_keeps_its_bars_and_its_error(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 14 Q31: a step 1 error that logged step 1's whole one-liner was split on every ``|``, so its
    ``||`` came back as ``| |``, and the Items line, cut at 220 characters, ended inside the command with no
    word of git's error. The what-text is now kept as written, the fix is still the text after the last
    ``|``, and an error line too long for the cut keeps its error."""
    error = "fatal: unable to access 'https://github.com/': Could not resolve proxy: gw.contoso.invalid"
    what = f"{STEP1_ONE_LINER} failed (exit 1): {error}"
    line = f"2026-10-06T02:16:01Z | step 1 | error | {what} | set the proxy\n"
    [event] = setup_report.parse_friction(line).attempts[0].events
    assert (event.step, event.kind, event.fix) == (1, "error", "set the proxy")
    assert event.what == what, "the what-text is the line's own"
    assert "] || [" in event.what and "| |" not in event.what
    v6_install_log(fake_mac)
    write_friction(fake_mac, V7_HAPPY + line)
    monkeypatch.setattr(setup_report, "home_path", lambda: "/Users/jdoe")
    _text, summary = summary_of(fake_mac)
    [item] = [ln for ln in summary.splitlines() if ln.startswith("- F5 · step 1 · error · ")]
    assert "failed (exit 1): fatal: unable to access" in item, item
    assert (
        item.startswith("- F5 · step 1 · error · export GIT_CEILING_DIRECTORIES=")
        and "→ set the proxy" in item
    )
    room = setup_report._ERROR_WHAT_MAX
    returned = setup_report._item_what(f"{STEP1_ONE_LINER} returned exit code 128: {error}", "error")
    assert "returned exit code 128: fatal: unable to access" in returned and len(returned) <= room
    for shape in ("exit 1:", "exit code 1:", "exit status 128:", "rc=1:", "rc 1:"):
        # B13 round 15 S1: an exit code with no verb before it ends the command too.
        cut = setup_report._item_what(f"{STEP1_ONE_LINER} {shape} {error}", "error")
        assert f"{shape} fatal: unable to access" in cut and len(cut) <= room, (shape, cut)
    # B13 round 18 V34: a 221-character git error lost its last clause, the reason, at the 220 cut
    git = (
        "git -C ~/src/agent-context-sync pull --ff-only failed (exit 1): fatal: unable to access "
        "\u2019https://github.com/contoso/agent-context-sync.git/\u2019: Failed to connect to github.com "
        "port 443 after 0 ms: Couldn\u2019t connect to server"
    )
    assert len(git) > 220 and setup_report._item_what(git, "error") == git
    assert len(setup_report._item_what(git + " " + "x" * 400, "error")) <= room
    assert len(setup_report._item_what("y" * 400, "note")) <= 220, "only an error line has the room"
    short = "2026-10-06T02:16:01Z | step 1 | error | git pull failed (exit 1): a || b | -\n"
    [event] = setup_report.parse_friction(short).attempts[0].events
    assert (event.what, event.fix) == ("git pull failed (exit 1): a || b", "")


def test_run_type_is_computed(fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    write_friction(
        fake_mac, V5_HAPPY.replace("Agent: Claude Code, claude-opus-5-5\n", "Agent: x\nRun: real\n")
    )
    write_install_log(fake_mac, simulated=False, agent="seconds=2 rc=0 result=done")
    text, summary = summary_of(fake_mac)
    assert "· run: Sandbox (computed: HOME is under a temporary folder) ·" in summary, (
        "pytest's HOME is a tmp dir"
    )
    assert "clicks: none possible (sandbox)" not in summary, "clicks were logged"
    assert "- agent said run: real" in summary, "the agent's own Run line, shown, not used (J14)"
    monkeypatch.setattr(setup_report, "home_path", lambda: "/Users/jdoe")
    text, summary = summary_of(fake_mac)
    assert (
        "· run: Real Mac (computed: HOME is not under a temporary folder and launchd was not simulated) ·"
        in summary
    )
    assert "; 2 clicks; " in summary and "none possible" not in summary
    assert "run_type=Real%20Mac" in text.splitlines()[-1]
    assert setup_report.is_sandbox_home("/private/tmp/x") and setup_report.is_sandbox_home("/tmp")
    assert not setup_report.is_sandbox_home("/tmpfoo") and not setup_report.is_sandbox_home("/Users/tmp")
    # One rule, shared with the skill's $CLAUDE_CONFIG_DIR copy (agentsync.skill.config_dir_skipped).
    assert setup_report.SANDBOX_HOMES is TEMPORARY_ROOTS
    assert all(is_temporary(root) and is_temporary(f"{root}/x") for root in TEMPORARY_ROOTS)


def test_first_sync_doctor_warns_and_it_draft_in_the_summary(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    write_install_log(fake_mac)
    write_friction(fake_mac)

    def doctor(config: object) -> list[str]:
        return [
            "[ok  ] python             — fine",
            "[warn] launcher.signature — valid; identifier com.agentsync.launcher; ad hoc; hardened runtime: "
            "every rebuild is a new TCC subject (fix: a Developer ID build)",
            f'[warn] launcher.requirement — designated => cdhash H"{CDHASH}" (ad hoc: a PPPC CodeRequirement '
            "cannot pin a cdhash)",
            "[warn] launchd.poll       — com.agentsync.poll is not installed (no x.plist) (fix: ...)",
            "[warn] launchd.reconcile  — com.agentsync.reconcile is not installed (no y.plist)",
            "[warn] network.proxy      — something odd",
        ]

    def status(config: object) -> list[str]:
        return [
            "  client-alpha (local, live): baseline complete · complete yes · live 3 (dataless 0)",
            "  budget-review (local, live): baseline INCOMPLETE · complete no · live 0 (dataless 2)",
        ]

    draft = fake_mac["home"] / "agent-context" / "it-request-draft.md"
    _text, summary = summary_of(fake_mac, doctor=doctor, status=status)
    assert f"- IT draft: {setup_report.IT_DRAFT} not written (prompt step 4:" in summary, "v5's step 4"
    # the count names status's field and when it was read; "listed completely" read as all of them (N17)
    assert (
        "- first sync: done in 34s (install.log) · 1 of 2 sources with a complete baseline (status, read "
        "when this report was written)" in summary.splitlines()
    )
    assert "listed completely" not in summary
    assert (
        "- doctor: 0 FAIL, 5 warn (6 checks) · expected 4: launcher.signature, launcher.requirement (ad hoc "
        "launcher); launchd.poll, launchd.reconcile (LaunchAgents not installed yet) · unexpected 1: "
        "network.proxy warn" in summary
    )
    # the shape `agentsync it-request` writes: a header naming every field (filled ones too), ---, the email
    draft_text = (
        "You fill: <it-contact>, <requester-upn>, <team>, <wanted-by>\n"
        "IT fills (leave them as they are): <it-owner>, <tenant-id>, <app-client-id>\n"
        f"Filled from this Mac: <requester-name> = {FULL_NAME}, <serial> = {SERIAL}, <arch> = arm64, "
        f"<org> = {ORG}\n"
        "This is a draft: nothing has been sent.\n\n---\n\n"
        "To: <it-contact>\nSubject: agentsync on a managed Mac\n\n"
        f"**Requester:** {FULL_NAME}, `<requester-upn>`, `<team>`. **Device:** {SERIAL} (arm64 Mac).\n"
        "**Wanted by:** `<wanted-by>`.\n"
        "| `<serial>` | a legend row, not an unfilled field |\n"
        "Owners: `<requester-upn>` + `<it-owner>`; reply with `<tenant-id>` and `<app-client-id>`\n"
    )
    draft.write_text(draft_text, encoding="utf-8")
    noon = datetime(2026, 10, 1, 12, tzinfo=UTC).timestamp()  # noon: the same date in any time zone
    os.utime(draft, (noon, noon))
    text, summary = summary_of(fake_mac, doctor=doctor, status=status)
    assert (
        f"- IT draft: {setup_report.IT_DRAFT} exists; 4 fields left for you (<it-contact>, "
        "<requester-upn>, <team>, <wanted-by>); 3 left for IT" in summary
    )
    # no date = in its Filled line: the file's date; and its email is not what it-request writes (N29)
    command = f"~/.local/bin/agentsync it-request --out {setup_report.IT_DRAFT}"
    assert f"3 left for IT; file last changed 2026-10-01; differs from what `{command}` writes now" in summary
    for raw in RAW:
        assert raw not in text, raw
    # what it-request writes: dated by its Filled line, and no difference
    facts = it_request.MacFacts(
        requester_name=FULL_NAME, serial=SERIAL, arch="arm64", today=date(2026, 9, 30)
    )
    real = it_request.render(it_request.load_template(), facts).text
    draft.write_text(real, encoding="utf-8")
    text, summary = summary_of(fake_mac, doctor=doctor, status=status)
    assert "3 left for IT; written 2026-09-30\n" in summary and "differs from" not in summary
    for raw in RAW:
        assert raw not in text, raw
    # a page that changed since: the first line of the draft that is not what it-request writes now
    load_template = it_request.load_template
    page = load_template()
    newer = it_request.Template(
        page.page.replace("**Written:** `<date>`.", "**Written:** `<date>` by `<company>`.").replace(
            "| `<version>`, `<shared-mailbox-upn>` |", "| `<company>`, `<version>`, `<shared-mailbox-upn>` |"
        ),
        page.entra,
        "newer",
    )
    assert newer.page.count("`<company>`") == 2, "the page's Written line or its leave-them row moved"
    monkeypatch.setattr(it_request, "load_template", lambda: newer)
    n = next(i for i, ln in enumerate(real.split("\n"), 1) if ln.startswith("**Written:**"))
    _text, summary = summary_of(fake_mac, doctor=doctor, status=status)
    assert (
        f"3 left for IT; written 2026-09-30; differs from what `{command}` writes now from line {n} (an "
        "earlier agentsync wrote it, or it was edited there): unless that line is your own edit, run that "
        "command again before sending it; it keeps this copy as a .bak\n" in summary
    )

    def broken() -> it_request.Template:
        raise ValueError("Contoso")

    # a compare that fails costs its own clause, not the Summary
    monkeypatch.setattr(it_request, "load_template", broken)
    text, summary = summary_of(fake_mac, doctor=doctor, status=status)
    unexpected = "(an unexpected error: ValueError)"
    assert f"; not compared with what agentsync it-request writes now {unexpected}\n" in summary
    assert "- first sync: " in summary and "Contoso" not in text
    monkeypatch.setattr(it_request, "load_template", load_template)
    draft.write_text(draft_text.replace("`<team>`", "Finance").replace("`<wanted-by>`", "Friday"))
    _text, summary = summary_of(fake_mac, doctor=doctor, status=status)
    assert "exists; 2 fields left for you (<it-contact>, <requester-upn>); 3 left for IT" in summary
    # LaunchAgents really installed by the last run: "not installed" is no longer expected
    write_install_log(fake_mac, simulated=False, agent="seconds=2 rc=0 result=done")
    _text, summary = summary_of(fake_mac, doctor=doctor, status=status)
    assert "unexpected 3: launchd.poll warn, launchd.reconcile warn, network.proxy warn" in summary


@pytest.mark.usefixtures("clean_doctor")
def test_issue_link_is_the_last_line_and_carries_only_the_five_fields(
    fake_mac: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_install_log(fake_mac)
    write_friction(fake_mac, V5_HAPPY.replace("claude-opus-5-5", f"claude-opus-5-5 on {ORG.lower()}-{HOST}"))
    assert cli.main(["setup-report", "--config", str(fake_mac["config"])]) == 0
    text = expand(setup_report.DEFAULT_OUT).read_text(encoding="utf-8")
    link = text.rstrip("\n").splitlines()[-1]
    printed = capsys.readouterr().out.rstrip("\n").splitlines()[-1]
    assert setup_report.issue_link(text) == link and link.startswith(setup_report.ISSUE_URL + "&")
    assert printed == f"{setup_report.ISSUE_LINK_LABEL} {link}"
    query = parse_qs(urlsplit(link).query)
    assert query == {
        "template": ["setup-report.yml"],
        "title": ["Setup report: Fully one command · Sandbox with simulated launchd · v5"],
        "outcome": ["Fully one command"],
        "run_type": ["Sandbox with simulated launchd"],
        "prompt_version": ["v5"],
        "agent": ["Claude Code, claude-opus-5-5 on <org-1>-<host>"],
        "loop_stage": ["installed"],
    }, "no sync has run in this fixture: the loop stage is installed (KISS K16b)"
    for raw in RAW:
        assert raw not in link, raw
    assert "%20" in link and " " not in link, "URL-encoded"


def test_issue_fields_match_the_form() -> None:
    import yaml  # noqa: PLC0415

    form_path = Path(__file__).parents[1] / ".github" / "ISSUE_TEMPLATE" / "setup-report.yml"
    form = yaml.safe_load(form_path.read_text(encoding="utf-8"))
    fields = {item["id"]: item for item in form["body"] if "id" in item}
    for ours, form_id in setup_report.ISSUE_FIELDS.items():
        assert form_id in fields, f"the form has no field id {form_id!r} for {ours}"
    assert form["title"] == setup_report.ISSUE_TITLE
    outcome = fields[setup_report.ISSUE_FIELDS["outcome"]]["attributes"]["options"]
    labels = {setup_report.Outcome(k, None, ()).form_label for k in ("fully one command", "worked with help")}
    labels |= {setup_report.Outcome("failed", n, ()).form_label for n in setup_report.PROMPT_STEPS}
    assert None not in labels and labels <= set(outcome), labels - set(outcome)
    run_types = fields[setup_report.ISSUE_FIELDS["run_type"]]["attributes"]["options"]
    assert set(setup_report.ISSUE_RUN_TYPES.values()) <= set(run_types)


def test_redaction_variants_residue_and_pid(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    write_install_log(fake_mac)
    variants = (
        "fy26-projects",
        "FY26_PROJECTS",
        "Fy26Projects",
        "client alpha",
        "Client-Alpha",
        "CLIENT_ALPHA",
        "ClientAlpha",
        "jane doe",
        "JaneDoe",
        "JANE-DOE",
        "contoso",
        "CONTOSO",
        "finance-documents",
        "Finance_Documents",
    )
    write_friction(
        fake_mac,
        V5_HAPPY.replace(
            "| 1 folder chosen |",
            "| chose " + ", ".join(variants) + f"; also {FOLDERS[1]} Board Minutes |",
        ),
    )
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    lower = text.lower()
    for raw in (*variants, "jane", "fy26 projects", "budget review"):
        assert raw.lower() not in lower, f"{raw!r} leaked"
    red = section(text, "Redaction")
    assert (
        "Residue check: 1 capitalized or joined word next to a placeholder in this report (Agent friction "
        "log: Board); check it." in red
    ), "the whole report is checked, hits listed by section (K9)"
    assert "are template placeholders, not redactions" in red
    inst = section(text, "Installer")
    assert "run 20260929T100000Z: exit 0 after 57s" in inst and "4242" not in inst, "no process id (J21)"
    assert "run=20260929T100000Z start install.sh" in inst


def test_redactor_normalization_variants() -> None:
    red = setup_report.Redactor()
    red.add("folder", "Client Alpha", fuzzy=True)
    red.add("folder", "client-alpha", fuzzy=True)  # a variant: the same value, the same placeholder
    red.add("org", "Contoso", fuzzy=True)
    red.add("name", "Jane Doe", fuzzy=True)
    red.add("folder", "QuarterlyReview", fuzzy=True)
    text = (
        "Client Alpha client-alpha CLIENT_ALPHA ClientAlpha client  alpha Client Alphabet "
        "contoso CONTOSO contoso.sharepoint.com jane_doe JaneDoe janedoes quarterly review Quarterly-Review"
    )
    assert red.redact(text) == (
        "<folder-1> <folder-1> <folder-1> <folder-1> <folder-1> Client Alphabet "
        "<org-1> <org-1> <org-1>.sharepoint.com <name> <name> janedoes <folder-2> <folder-2>"
    )
    assert red.counts == {"folder": 7, "org": 3, "name": 2}
    off = setup_report.Redactor(enabled=False)
    off.add("org", "Contoso", fuzzy=True)
    assert off.redact("contoso") == "contoso" and off.scrub("contoso") == "<org-1>" and off.total == 0


def test_the_longest_match_wins_whatever_separators_a_value_is_written_with() -> None:
    """A fuzzy value also matches without its separators, so what it matches can be shorter than the value.
    Tried by its written length, a folder written "A - B - C" came before the id "a-b-c-x" and took only
    the id's front: ``<folder-N>-x``, which is a part of an id in clear."""
    red = setup_report.Redactor()
    red.add("folder", "Wingtip - Merger - Docs", fuzzy=True)  # 23 characters, and matches 19 of the id's 22
    red.add("source", "wingtip-merger-docs-hr")
    assert red.redact("wingtip-merger-docs-hr wingtip-merger-docs Wingtip - Merger - Docs") == (
        "<source-1> <folder-1> <folder-1>"
    )
    other = setup_report.Redactor()  # the other way round: an id that is the front of a folder's name
    other.add("source", "a-b-c-d")
    other.add("folder", "A B C D Ef", fuzzy=True)
    assert other.redact("a-b-c-d-ef a-b-c-d A_B_C_D_EF") == "<folder-1> <source-1> <folder-1>"


def test_simulated_launchd_from_the_install_log(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    log = fake_mac["setup"] / "install.log"
    log.write_text(
        log.read_text(encoding="utf-8").replace(
            "step=agent seconds=0 rc=0 result=skipped note=not-requested",
            "step=agent seconds=0 rc=0 result=skipped note=simulated launchd=simulated",
        ),
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "run 20260929T100000Z: exit 0 after 57s · launchd: simulated" in section(text, "Installer")
    assert section(text, "Background runs").lstrip().startswith("launchd: simulated")
    summary = section(text, "Summary")
    assert "run: Sandbox with simulated launchd (computed: install.log says launchd=simulated;" in summary
    assert "- background sync: launchd: simulated (no LaunchAgent of this setup ran)" in summary


def _launchctl_text(home: Path, *, exit_code: str, label: str = "com.agentsync.poll") -> str:
    app = f"{home}/Applications/AgentSyncLauncher.app/Contents/MacOS/agentsync-launcher"
    return (
        f"gui/501/{label} = {{\n\tactive count = 0\n\tpath = {home}/Library/LaunchAgents/{label}.plist\n"
        f"\ttype = LaunchAgent\n\tstate = not running\n\n\tprogram = {app}\n\targuments = {{\n\t\t{app}\n"
        f"\t\t--\n\t\t{home}/.local/share/uv/tools/agentsync/bin/python\n\t\t--config\n"
        f"\t\t{home}/agent-context/sources.toml\n\t}}\n\n"
        "\tenvironment = {\n\t\tPATH => /usr/bin:/bin\n\t}\n"
        f"\truns = 3\n\tpid = 4242\n\tlast exit code = {exit_code}\n}}\n"
    )


def test_background_runs_decode_exit_codes_and_ownership(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = fake_mac["home"]
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.agentsync.poll.plist").write_text("<plist/>", encoding="utf-8")  # reconcile's is absent
    canned = {
        "com.agentsync.poll": (0, _launchctl_text(home, exit_code="79")),
        "com.agentsync.reconcile": (
            0,
            _launchctl_text(home, exit_code="75: EX_TEMPFAIL", label="com.agentsync.reconcile"),
        ),
    }
    monkeypatch.setattr(setup_report, "_launchctl_print", lambda run, label: canned[label])
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    bg = section(text, "Background runs")
    assert (
        "- com.agentsync.poll: plist present · loaded: state = not running · runs = 3 · last exit code = 79 "
        "(TCC_PENDING: the launcher waited for the Allow prompt and timed out)" in bg
    )
    assert (
        "- com.agentsync.reconcile: loaded, but the plist is absent (removed without `launchctl bootout`"
        in bg
    )
    assert "75: EX_TEMPFAIL (skipped: another cycle held the lock; launchd retries)" in bg
    assert "pid" not in bg, "a pid is a fingerprint, not a finding"
    summary = section(text, "Summary")
    assert "- background sync: poll last exit 79 (TCC_PENDING" in summary
    assert "reconcile loaded without its plist" in summary

    other = "/Users/someone-else"
    canned["com.agentsync.poll"] = (0, _launchctl_text(Path(other), exit_code="0"))
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    bg = section(text, "Background runs")
    assert "- com.agentsync.poll: loaded, but the job belongs to another install" in bg
    assert "not this setup's result" in bg and "last exit code" not in bg.split("reconcile")[0]
    assert other not in text
    assert "poll belongs to another install" in section(text, "Summary")
    assert setup_report.decode_exit("(never exited)").startswith("(never exited: the first run")
    assert setup_report.decode_exit("0") == "0 (ok)" and setup_report.decode_exit("78").startswith(
        "78 (config"
    )
    # The launcher's own exit for a job it refuses, which had no meaning and read as a bare number.
    assert setup_report.decode_exit("64") == (
        "64 (the launcher refused the job: its program is not the one it starts, or an option is wrong)"
    )
    # B13 round 17 U55, U65: agentsync --help lists 65, the manifest it cannot read, which read as a bare 65
    assert setup_report.decode_exit("65") == (
        "65 (the state file (manifest.sqlite) cannot be read: the person's to fix)"
    )


def test_a_shadowing_agentsync_on_path_is_flagged(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = fake_mac["home"] / ".local" / "bin"
    other = tmp_path / "elsewhere" / "bin"
    for d in (mine, other):
        d.mkdir(parents=True)
        (d / "agentsync").write_text("#!/bin/sh\n", encoding="utf-8")
        (d / "agentsync").chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(other), str(mine), "/usr/bin", "/bin"]))
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    env = section(text, "Environment")
    shown = re.sub(r"^/(?:private/)?var/folders/[^/]+/[^/]+(?:/T(?=/))?", "<tmp>", str(other))  # L6
    assert f"- agentsync on PATH: {shown}/agentsync; ~/.local/bin/agentsync (this home folder)" in env
    assert f"SHADOW: the first agentsync on PATH is {shown}/agentsync" in env
    assert (
        f"- PATH: SHADOW: the first agentsync on PATH is {shown}/agentsync, not ~/.local/bin/agentsync "
        "(expected in a sandbox)" in section(text, "Summary")
    ), "pytest's HOME is a tmp dir: a sandbox run (K10)"
    monkeypatch.setattr(setup_report, "home_path", lambda: "/Users/jdoe")
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "not ~/.local/bin/agentsync\n" in section(text, "Summary"), (
        "on a real Mac a shadow is not expected"
    )

    monkeypatch.setenv("PATH", os.pathsep.join([str(mine), str(other), "/usr/bin"]))
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "SHADOW" not in text
    assert setup_report.agentsync_on_path(os.pathsep.join([str(mine), "", str(mine)])) == [
        str(mine / "agentsync")
    ]


@pytest.mark.usefixtures("roomy_calls")
def test_fingerprints_commits_and_listed_folders_are_redacted(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    home = fake_mac["home"]
    cloud = home / "Library" / "CloudStorage" / f"OneDrive-{ORG}"
    for d in ("Secret Project/Board Minutes", "Documents/Unlisted Depth Four/Too Deep", ".hidden/Inner"):
        (cloud / d).mkdir(parents=True)
    log = fake_mac["logs"] / "com.agentsync.reconcile.err.log"
    log.write_text(
        f"2026-09-29 11:00:00,000 WARNING agentsync.cycle: Secret Project and Board Minutes near Documents\n"
        f"2026-09-29 11:00:01,000 ERROR agentsync.cycle: commit {DOCS_SHA[:9]} and {DOCS_SHA} failed\n",
        encoding="utf-8",
    )

    def doctor(config: object) -> list[str]:
        return [
            "[ok  ] python — fine",
            f'[warn] launcher.requirement — designated => cdhash H"{CDHASH}" or cdhash H"{CDHASH.upper()}"',
        ]

    def status(config: object) -> list[str]:
        return [f"last runs: 2 poll ok {DOCS_SHA[:12]}; 1 poll ok 0badc0ffee12"]

    text, red = setup_report.build_report(
        fake_mac["config"], hooks=setup_report.ReportHooks(doctor=doctor, status=status)
    )
    for raw in (
        "Secret Project",
        "Board Minutes",
        "Unlisted Depth Four",
        CDHASH,
        CDHASH.upper(),
        DOCS_SHA[:7],
    ):
        assert raw not in text, raw
    assert "Too Deep" not in section(text, "Recent errors") and "Documents" in section(text, "Recent errors")
    assert 'cdhash H"<hash-1>" or cdhash H"<hash-1>"' in section(text, "Doctor")
    assert "last runs: 2 poll ok <commit-1>; 1 poll ok <commit-2>" in section(text, "Status")
    assert "commit <commit-1> and <commit-1> failed" in section(text, "Recent errors")
    assert re.search(r"<folder-\d+> and <folder-\d+> near Documents", section(text, "Recent errors"))
    assert red.counts["hash"] == 2 and red.counts["commit"] >= 3
    legend = section(text, "Redaction")
    assert "<hash-N> hex fingerprint" in legend and "<commit-N> docs-repo commit" in legend
    assert "<proxy-N>" not in legend, "the legend lists only the kinds used"


def test_a_listed_folder_named_like_a_coding_agent_does_not_redact_the_agent_name(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """Field report 2026-10-06: a listed cloud folder named Copilot turned "GitHub Copilot CLI" into
    "GitHub <folder-N> CLI" in the Summary and the issue link. A folder that is only listed keeps a product
    word, in any case; the agent string is still redacted like any other text."""
    cloud = fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{ORG}"
    for d in (cloud / "Documents" / "Copilot", cloud / "GitHub Copilot", cloud / "github-copilot"):
        d.mkdir(parents=True)
    v6_install_log(fake_mac)
    write_friction(fake_mac, V7_HAPPY.replace("Claude Code, claude-opus-5-5", "GitHub Copilot CLI x"))
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "· agent: GitHub Copilot CLI x\n" in section(text, "Summary")
    assert "agent GitHub Copilot CLI x)" in section(text, "Agent friction log")
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["agent"] == ["GitHub Copilot CLI x"]

    write_friction(fake_mac, V7_HAPPY.replace("Claude Code, claude-opus-5-5", f"{FOLDERS[1]} CLI x"))
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["agent"] == ["<folder-2> CLI x"], "the agent string is not exempt from redaction"
    assert FOLDERS[1] not in text


def test_a_configured_folder_named_like_a_coding_agent_is_still_a_placeholder(
    fake_mac: dict[str, Path],
) -> None:
    """Gemini, Codex, Cursor and Claude are also project codenames and first names. A configured folder with
    such a name is redacted like any other, and so is its source id; the residue check still lists the word
    next to a placeholder. The agent's name then shows the placeholder too: that is the stated limit."""
    configured = fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{ORG}" / "Programs" / "Gemini"
    configured.mkdir(parents=True)
    assert cli.main(["add-source", str(configured), "--config", str(fake_mac["config"])]) == 0
    sid = load_config(fake_mac["config"]).sources[-1].id
    assert "gemini" in sid

    def doctor(config: object) -> list[str]:
        return [f"[warn] source.{sid}.listable — cannot list {configured}"]

    def status(config: object) -> list[str]:
        return [f"  {sid} (local, live): baseline complete"]

    v6_install_log(fake_mac)
    write_friction(fake_mac, V7_HAPPY.replace("Claude Code, claude-opus-5-5", "Gemini CLI x"))
    text, summary = summary_of(fake_mac, doctor=doctor, status=status)
    assert "gemini" not in text.lower() and "Programs" not in text
    shown = re.search(r"source\.(<folder-\d+>)\.listable — cannot list (\S+)\n", section(text, "Doctor"))
    assert shown, "the id is the folder's name: one value, one placeholder"
    assert shown.group(2) == f"~/Library/CloudStorage/OneDrive-<org-1>/<folder-4>/{shown.group(1)}"
    assert f"  {shown.group(1)} (local, live): baseline complete" in section(text, "Status")
    assert re.search(r"· agent: <folder-\d+> CLI x\n", summary)
    assert setup_report.residue("<folder-1> Gemini <org-1> Codex <name>") == ["Gemini", "Codex"], (
        "a product word next to a placeholder is residue like any other"
    )


def test_run_ids_that_embed_host_names_are_redacted(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    log = fake_mac["setup"] / "install.log"
    log.write_text(
        log.read_text(encoding="utf-8").replace("run=20260929T100000Z-4242", f"run={HOST}.local-4242"),
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert HOST not in text and "run <host>: exit 0 after 57s" in section(text, "Installer")


def test_setup_report_help_names_the_12_s_budget() -> None:
    """Hidden since KISS K16a, so the budget is in ``setup-report --help`` (the description), not the list."""
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None) and "setup-report" in a.choices)
    description = sub.choices["setup-report"].description or ""
    assert f"under {setup_report.TIME_BUDGET_S:.0f} s" in description
    assert setup_report.TIME_BUDGET_S == 12.0
    contracts = (Path(__file__).parents[1] / "docs" / "design" / "CONTRACTS.md").read_text(encoding="utf-8")
    assert "TIME_BUDGET_S = 12.0" in contracts and "KISS K16a" in contracts


def test_unwritable_out_exits_1_and_prints_the_report(
    fake_mac: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    rc = cli.main(["setup-report", "--out", str(blocker / "r.md"), "--config", str(fake_mac["config"])])
    captured = capsys.readouterr()
    assert rc == 1
    assert "cannot write" in captured.err
    assert captured.out.startswith(setup_report.REPORT_TITLE)
    lines = captured.out.rstrip("\n").splitlines()
    assert lines[-1] == f"{setup_report.ISSUE_LINK_LABEL} {lines[-2]}", (
        "the fallback ends with the link too (K3)"
    )
    assert lines[-2].startswith(setup_report.ISSUE_URL + "&")


def test_out_prints_the_issue_link_last_and_no_next_hint(
    fake_mac: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Prompt v5 step 5: "Its last output line is a link that opens a pre-filled Setup report issue" (K3)."""
    write_install_log(fake_mac)
    write_friction(fake_mac)
    out = tmp_path / "setup-report.md"
    assert cli.main(["setup-report", "--out", str(out), "--config", str(fake_mac["config"])]) == 0
    printed = capsys.readouterr().out.rstrip("\n").splitlines()
    link = setup_report.issue_link(out.read_text(encoding="utf-8"))
    assert link is not None and printed[-1] == f"issue link (review the report first): {link}"
    assert printed[0].startswith(f"wrote {out} (") and len(printed) == 2
    assert not any(re.match(r"(?i)\s*next:", ln) for ln in printed)


def test_out_defaults_to_the_default_report(
    fake_mac: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K16a: without --out the report goes to ~/agent-context/setup-report.md (0600), not stdout."""
    assert cli.main(["setup-report", "--config", str(fake_mac["config"])]) == 0
    default = fake_mac["home"] / "agent-context" / "setup-report.md"
    assert expand(setup_report.DEFAULT_OUT) == default
    text = default.read_text(encoding="utf-8")
    assert text.startswith(setup_report.REPORT_TITLE) and ORG not in text
    assert default.stat().st_mode & 0o777 == 0o600
    printed = capsys.readouterr().out
    assert printed.startswith(f"wrote {default} (") and setup_report.REPORT_TITLE not in printed


def test_doctor_hook_makes_no_network_probe(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a live Graph source doctor would probe graph.microsoft.com; the setup-report hook never does."""

    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("network probe from setup-report")

    monkeypatch.setattr(net, "probe_reachability", boom)
    cfg = fake_mac["config"]
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text(
        text
        + '\n[graph]\nclient_id = "00000000-0000-0000-0000-000000000001"\n'
        + '\n[[source]]\nid = "mail"\nkind = "graph_mail"\nfolder = "inbox"\n',
        encoding="utf-8",
    )
    config = load_config(cfg)
    assert any(s.kind.is_graph for s in config.sources)
    lines = [ln for ln in cli._extra_checks(config, offline=True) if ln.name == "network.graph"]
    assert [ln.detail.split(" (")[0] for ln in lines] == ["not probed"]


@pytest.mark.usefixtures("roomy_budget")
def test_checks_render_once_from_one_offline_status_build(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KISS K08a: the checks run once, offline, in Doctor (under the rest of the budget, not Status's 4 s: a
    check build slower than that still renders); Status shows the loop line and the detail; no network probe,
    no NEXT line."""
    calls: list[bool] = []
    real = cli._status_checks

    def counted(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
        calls.append(offline)
        time.sleep(4.5)
        return real(config, offline=offline)

    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("network probe from setup-report")

    monkeypatch.setattr(cli, "_status_checks", counted)
    monkeypatch.setattr(net, "probe_reachability", boom)
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0 and calls == [True]
    assert "This section failed" not in section(text, "Status") + section(text, "Doctor")
    status = section(text, "Status")
    assert "loop: skill " in status and "NEXT:" not in status
    assert not re.search(r"^\[(ok|FAIL|warn|info)\s*\]", status, re.MULTILINE), (
        "the checks render in Doctor only"
    )
    assert re.search(r"\d+ checks: \d+ ok", section(text, "Doctor"))


@pytest.mark.usefixtures("roomy_calls")
def test_roomy_calls_lets_a_status_build_slower_than_its_bound_say_its_lines(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 8 J80: content tests failed under load on the report's per-call bounds (Status's 4 s, the
    listing's 3 s, the evidence reads), which ``roomy_budget`` left as literals. They are module constants
    now, and ``roomy_calls`` widens them, so a Status build slower than the real bound still says its
    lines."""

    def slow(_config: Path) -> list[str]:
        time.sleep(4.5)  # past the real 4 s Status bound
        return ["loop: skill current; a slow-status line"]

    _text, _summary = summary_of(fake_mac, status=slow)
    status = section(_text, "Status")
    assert "This section failed" not in status and "a slow-status line" in status


@pytest.mark.usefixtures("roomy_calls")
def test_a_broken_governance_table_still_renders_every_check(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """A part of status that raises becomes one line; Doctor still lists every check with the FAIL."""
    cfg = fake_mac["config"]
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[governance]\nhistory_days = "x"\n', encoding="utf-8")
    rc, text, _ = report(tmp_path, cfg)
    doctor_section, status = section(text, "Doctor"), section(text, "Status")
    assert rc == 0 and "This section failed" not in doctor_section + status
    assert "[FAIL] governance.config" in doctor_section and "loop: skill " in status
    assert "status: cannot read the state: the configuration is refused" in status


# ---- the redactor ---------------------------------------------------------------------------------------


def test_redactor_is_consistent_and_bounded() -> None:
    red = setup_report.Redactor()
    red.add("home", "/Users/jdoe")
    red.add("user", "jdoe")
    red.add("org", "Contoso", ignore_case=True)
    red.add("folder", "Alpha")
    red.add("folder", "Beta")
    red.add("folder", "Alpha")  # registered once: keeps <folder-1>
    red.add("folder", "x")  # too short to redact safely
    text = (
        "/Users/jdoe/a /Users/jdoe2/b jdoe jdoes Alpha/Beta/Alpha Alphabet CONTOSO contoso.sharepoint.com "
        "a@b.example A@B.EXAMPLE 1B4E28BA-2FA1-11D2-883F-0016D3CCA427 1b4e28ba-2fa1-11d2-883f-0016d3cca427 x"
    )
    assert red.redact(text) == (
        "~/a /Users/jdoe2/b <user> jdoes <folder-1>/<folder-2>/<folder-1> Alphabet <org-1> "
        "<org-1>.sharepoint.com <email-1> <email-1> <guid-1> <guid-1> x"
    )
    assert red.counts == {"home": 1, "user": 1, "folder": 3, "org": 2, "email": 2, "guid": 2}
    assert red.total == 11
    off = setup_report.Redactor(enabled=False)
    off.add("user", "jdoe")
    assert off.redact("jdoe a@b.example") == "jdoe a@b.example" and off.total == 0


# ---- K4, K6, K9, K10, K17 -------------------------------------------------------------------------------


def _insert_before(marker: str, lines: str, text: str = V5_HAPPY) -> str:
    assert marker in text
    return text.replace(marker, lines + marker, 1)


def test_agent_friction_never_changes_the_outcome_by_itself(fake_mac: dict[str, Path]) -> None:
    """Deviation, prompt and error lines are the agent's side: an outcome is what the person saw (K4)."""
    write_install_log(fake_mac)
    write_friction(
        fake_mac,
        _insert_before(
            "2026-09-29T10:01:00Z | step 3 | end",
            "2026-09-29T10:00:40Z | step 3 | deviation | ran install.sh in the background | -\n"
            "2026-09-29T10:00:41Z | step 3 | prompt | 'longest timeout' is unclear | say 600000 ms\n"
            "2026-09-29T10:00:42Z | step 3 | error | the first try hit the tool timeout (exit 124) | -\n",
        ),
    )
    _text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: fully one command** (computed: install.sh exit 0; no turn beyond the unavoidable ones)"
    )
    assert (
        "- agent friction: 1 deviation, 1 prompt, 1 error (F12, F13, F14); none of these changes the outcome "
        "by itself" in summary
    )
    items, agent_side = summary.split("Items that were not one command (attempt 1):", 1)[1].split(
        "Agent friction (attempt 1; it does not change the outcome by itself):", 1
    )
    assert items.strip() == "- none"
    assert re.findall(r"^- F(\d+) · step 3 · (\w+) · ", agent_side, flags=re.MULTILINE) == [
        ("14", "error"),
        ("12", "deviation"),
        ("13", "prompt"),
    ]


def test_an_error_that_stopped_the_run_fails_its_step(fake_mac: dict[str, Path]) -> None:
    write_install_log(fake_mac)
    write_friction(
        fake_mac,
        V5_HAPPY.replace(
            "2026-09-29T10:01:05Z | step 4 | end | draft written | -\n",
            "2026-09-29T10:01:05Z | step 4 | error | it-request exited 1: no template | -\n"
            "2026-09-29T10:01:06Z | step 5 | start | report | -\n",
        ).replace("2026-09-29T10:01:06Z | end | finished", "2026-09-29T10:01:07Z | end | finished"),
    )
    text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: failed at step 4** (computed: install.sh exit 0; step 4 logged an error (F14) and did "
        "not finish: no later end of that step and no later step before the report)"
    )
    assert "- agent friction: 0 deviation, 0 prompt, 1 error (F14); F14 stopped the run" in summary
    items = summary.split("Items that were not one command (attempt 1):", 1)[1]
    assert "- F14 · step 4 · error · it-request exited 1: no template" in items
    assert "outcome=Failed%20at%20step%203%20%28IT%20request%20and%20report%29" in text.splitlines()[-1], (
        "a v5 attempt's step 4 (IT request) is the current form's step 3"
    )
    # the same error, then the step finished after all: not a stop
    write_friction(
        fake_mac,
        _insert_before(
            "2026-09-29T10:01:05Z | step 4 | end",
            "2026-09-29T10:01:04Z | step 4 | error | first try: rc 1 | -\n",
        ),
    )
    assert summary_of(fake_mac)[1].strip().startswith("- **outcome: fully one command**")


def test_an_unexpected_doctor_fail_is_not_fully_one_command(fake_mac: dict[str, Path]) -> None:
    write_install_log(fake_mac)
    write_friction(fake_mac)

    def doctor(config: object) -> list[str]:
        return [
            "[ok  ] python — fine",
            "[FAIL] tcc    — agentsync-launcher was denied (fix: System Settings > Privacy & Security)",
        ]

    text, summary = summary_of(fake_mac, doctor=doctor)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: worked with help** (computed: install.sh exit 0; 1 unexpected doctor FAIL: tcc)"
    )
    fr = section(text, "Agent friction log")
    assert "- attempt 1 (lines 1-15;" in fr and "): worked with help;" in fr, (
        "the same outcome in both places"
    )


def test_list_folders_runs_are_not_install_runs(fake_mac: dict[str, Path]) -> None:
    """Step 2's --list-folders run is logged too; the Summary counts it apart (K10) and never judges it."""
    log = fake_mac["setup"] / "install.log"
    listing = (
        "2026-09-29T09:58:45Z run=20260929T095845Z-11 start install.sh compat=5 commit=0123456789ab "
        "kind=checkout source=- launchd=simulated args=--list-folders\n"
        "2026-09-29T09:58:45Z run=20260929T095845Z-11 step=list-folders seconds=4 rc=0 result=done\n"
        "2026-09-29T09:58:49Z run=20260929T095845Z-11 end rc=0 seconds=4\n"
    )
    write_install_log(fake_mac)
    log.write_text(listing + log.read_text(encoding="utf-8"), encoding="utf-8")
    no_clicks = "".join(ln for ln in V5_HAPPY.splitlines(keepends=True) if " | click | " not in ln)
    write_friction(fake_mac, no_clicks)
    _text, summary = summary_of(fake_mac)
    assert summary.strip().startswith("- **outcome: fully one command**")
    assert (
        "- install.sh: 1 install run (+1 --list-folders) during this attempt; the last exit 0 after 57s"
        in summary
    )
    assert "; clicks: none possible; " in summary
    assert (
        "- expected turns: the folder question F7 · Allow clicks: none possible (launchd simulated)"
        in summary
    )
    # a --list-folders run after the install run (the agent listed again) is still not what is judged
    log.write_text(
        log.read_text(encoding="utf-8")
        + listing.replace("09:58:4", "10:00:5").replace("095845Z-11", "100055Z-12"),
        encoding="utf-8",
    )
    _text, summary = summary_of(fake_mac)
    assert summary.strip().startswith("- **outcome: fully one command**")
    assert "- install.sh: 1 install run (+2 --list-folders) during this attempt; the last exit 0" in summary


def test_a_probe_a_signal_stopped_is_worded_as_the_converters_word_it() -> None:
    """The report's probes (sw_vers, profiles, xcode-select, the helpers' --version) print subprocess's
    negative code for a signal as "was stopped by SIGKILL (signal 9)", the phrase of ``ocr._ended``; a crash
    keeps the line it printed; a plain exit keeps its words byte for byte (B13 round 2 R81)."""
    assert setup_report._exited(-9, "partial output") == "was stopped by SIGKILL (signal 9)"
    assert setup_report._exited(-6, "boom") == "was stopped by SIGABRT (signal 6): boom"
    assert setup_report._exited(-9) == "was stopped by SIGKILL (signal 9)"
    assert setup_report._exited(2, "usage") == "exited 2: usage"
    assert setup_report._exited(2, "") == "exited 2: "
    assert setup_report._exited(1) == "exited 1"


def test_the_install_line_says_what_the_agentsync_steps_seconds_held(tmp_path: Path) -> None:
    """install.sh notes on its step=agentsync line what it saw of the background sync's lock; the Summary
    carries that note, so a 59 s step reads as a wait, not a slow uv (B13 round 2 R34). Other steps' notes
    stay out of this line."""
    run = "20261011T100000Z-4242"
    log = tmp_path / "install.log"
    log.write_text(
        f"2026-10-11T10:00:00Z run={run} start install.sh commit=0123456789ab kind=checkout source=- args=\n"
        f"2026-10-11T10:00:59Z run={run} step=agentsync seconds=59 rc=0 result=done "
        "note=waited-for-a-background-sync\n"
        f"2026-10-11T10:01:00Z run={run} step=config seconds=1 rc=0 result=done note=created\n"
        f"2026-10-11T10:01:00Z run={run} end rc=0 seconds=60\n",
        encoding="utf-8",
    )
    runs = setup_report.read_install_runs(log)
    r = setup_report._Run.__new__(setup_report._Run)
    r.install_runs = runs
    assert setup_report._install_line(r, runs, scoped=False) == (
        "- install.sh: 1 install run; the last exit 0 after 60s "
        "(agentsync 59s, waited for another sync to end · config 1s)"
    )
    # B13 round 9 K35: the install note does not say who started the sync (H33, round 11 M80), so neither line
    # says "background"; install.log keeps its token, and a note the map does not know is shown as it is
    table = "\n".join(setup_report._step_table(list(runs[-1].steps)))
    assert "| done (waited for another sync to end) |" in table and "background" not in table, table
    assert re.search(r"\| done \(created\) +\|", table), table
    assert setup_report._agentsync_note("some-future-note") == "some-future-note"
    # B13 round 10 L35: the two notes that carry a number read as words too, in the table and the Summary
    for token, words in (
        (
            "wait-ran-out-after-120s",
            "waited 120s for another sync, then reinstalled while it was still running",
        ),
        ("lock-not-taken-rc73", "could not take the sync lock, lockf exit 73; reinstalled without waiting"),
    ):
        noted = log.read_text(encoding="utf-8").replace("note=waited-for-a-background-sync", f"note={token}")
        log.write_text(noted, encoding="utf-8")
        runs = setup_report.read_install_runs(log)
        r.install_runs = runs
        table = "\n".join(setup_report._step_table(list(runs[-1].steps)))
        assert f"| done ({words}) |" in table and token not in table and "background" not in table, table
        line = setup_report._install_line(r, runs, scoped=False)
        assert f"(agentsync 59s, {words} · config 1s)" in line and token not in line, line
        log.write_text(noted.replace(f"note={token}", "note=waited-for-a-background-sync"), encoding="utf-8")


@pytest.mark.parametrize(
    "commit", ["commit=0123456789ab-dirty tree=a1b2c3d4e5f6", "commit=0123456789ab-dirty dirty a1b2c3d4e5f6"]
)
def test_install_source_names_the_dirty_tree(fake_mac: dict[str, Path], commit: str) -> None:
    write_install_log(fake_mac)
    log = fake_mac["setup"] / "install.log"
    log.write_text(log.read_text(encoding="utf-8").replace("commit=0123456789ab", commit), encoding="utf-8")
    _text, summary = summary_of(fake_mac)
    assert (
        "; last install.sh run: commit 0123456789ab-dirty tree a1b2c3d4e5f6 (the SHA-256 prefix of `git diff "
        "HEAD` in that checkout)" in summary
    ), "tree=<fp> (K6), and the start line written before it"


def test_install_source_skips_a_run_that_read_no_commit(fake_mac: dict[str, Path]) -> None:
    """A --list-folders run and a run a usage error stopped log ``commit=-`` before reading their source
    (N20): the install source's commit is the last run's that reached an install step, and a run that did
    and logged no commit (no developer tools) still says ``commit -``."""
    write_install_log(fake_mac)
    log = fake_mac["setup"] / "install.log"

    def append(text: str) -> str:
        log.write_text(log.read_text(encoding="utf-8") + text, encoding="utf-8")
        _text, summary = summary_of(fake_mac)
        return summary

    summary = append(
        "2026-09-29T10:05:00Z run=20260929T100500Z-12 start install.sh compat=10 commit=- kind=- source=- "
        "args=--list-folders\n"
        "2026-09-29T10:05:00Z run=20260929T100500Z-12 step=list-folders seconds=4 rc=0 result=done\n"
        "2026-09-29T10:05:04Z run=20260929T100500Z-12 end rc=0 seconds=4\n"
    )
    assert "; last install.sh run: commit 0123456789ab" in summary and "commit -" not in summary
    # usage errors after the source is resolved (kind=checkout) and before (``--list-folders x``): no step
    summary = append(
        "2026-09-29T10:06:00Z run=20260929T100600Z-13 start install.sh compat=10 commit=- kind=checkout "
        "source=- args=--source-local /nonexistent/Contoso\n"
        "2026-09-29T10:06:00Z run=20260929T100600Z-13 step=report seconds=1 rc=0 result=done note=agentsync\n"
        "2026-09-29T10:06:01Z run=20260929T100600Z-13 end rc=2 seconds=1\n"
        "2026-09-29T10:07:00Z run=20260929T100700Z-14 start install.sh compat=10 commit=- kind=- source=x "
        "args=x\n"
        "2026-09-29T10:07:00Z run=20260929T100700Z-14 end rc=2 seconds=0\n"
    )
    assert "; last install.sh run: commit 0123456789ab" in summary and "commit -" not in summary
    summary = append(
        "2026-09-29T10:08:00Z run=20260929T100800Z-15 start install.sh compat=10 commit=- kind=checkout "
        "source=- args=--source-local x\n"
        "2026-09-29T10:08:00Z run=20260929T100800Z-15 step=uv seconds=0 rc=0 result=skipped note=present\n"
        "2026-09-29T10:08:41Z run=20260929T100800Z-15 step=agentsync seconds=41 rc=0 result=done\n"
        "2026-09-29T10:08:42Z run=20260929T100800Z-15 step=report seconds=1 rc=0 result=done note=agentsync\n"
        "2026-09-29T10:08:43Z run=20260929T100800Z-15 end rc=0 seconds=43\n"
    )
    assert "; last install.sh run: no commit read" in summary and "commit 0123456789ab" not in summary


def test_a_login_that_is_the_full_name_run_together_is_user(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The login is registered before the fuzzy full name, so "janedoe" is <user>, as the legend says (K9)."""
    monkeypatch.setattr(setup_report, "_login_name", lambda: "janedoe")
    write_install_log(fake_mac)
    write_friction(
        fake_mac,
        V5_HAPPY.replace(
            "| 1 folder chosen |", "| janedoe chose; JaneDoe and Jane Doe and jane-doe agreed |"
        ),
    )
    text, _summary = summary_of(fake_mac)
    assert "| <user> chose; <name> and <name> and <name> agreed |" in section(text, "Agent friction log")
    assert "janedoe" not in text.lower() and "jane" not in text.lower()


def test_the_full_name_keeps_its_comma(monkeypatch: pytest.MonkeyPatch) -> None:
    """``id -F`` prints "Doe, Jane" whole; the GECOS field is not cut at its comma."""

    class Entry:
        pw_gecos = " Doe, Jane "

    monkeypatch.setattr(setup_report.pwd, "getpwuid", lambda _uid: Entry())
    assert setup_report._full_name() == "Doe, Jane"


def test_a_full_name_written_last_comma_first_registers_both_names(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS keeps the comma of "Doe, Jane" in the full name: both names, in either order and in every written
    form, are <name> (field report 2026-10-10: cut at the comma, the first name stayed in the report)."""

    class Entry:
        pw_gecos = " Doe, Jane "

    monkeypatch.setattr(setup_report, "_full_name", _REAL_FULL_NAME)
    monkeypatch.setattr(setup_report.pwd, "getpwuid", lambda _uid: Entry())
    write_install_log(fake_mac)
    write_friction(
        fake_mac,
        V5_HAPPY.replace(
            "| 1 folder chosen |", "| asked Jane; Doe, Jane approved; jane-doe, doe and DOE_JANE agreed |"
        ),
    )
    text, _summary = summary_of(fake_mac)
    friction = section(text, "Agent friction log")
    assert "| asked <name>; <name> approved; <name>, <name> and <name> agreed |" in friction, friction
    assert "jane" not in text.lower()


def test_the_module_docstring_describes_the_redaction_the_code_does() -> None:
    """B13 round 4 Q41: the redaction paragraph described round 2. It now names the LaunchAgent's proxy, the
    generic dotless host matched only in a URL, the unaccented spellings, the held placeholders and the kept
    locator, and every constant it cites by :data: is one the module has."""
    doc = " ".join((setup_report.__doc__ or "").split())
    for said in (
        "LaunchAgent's own (``launchctl setenv``) proxy",
        "is replaced only where a URL names it",
        "in NFC, NFD, mixed or unaccented spelling",
        "is left whole on a second pass",
        "own ``github.com`` locator is kept whole",
    ):
        assert said in doc, said
    cited = re.findall(r":data:`(\w+)`", doc)
    assert {"_GENERIC_PROXY_HOSTS", "_HELD_RE", "_JOINED_WORDS"} <= set(cited)
    assert all(hasattr(setup_report, name) for name in cited), cited


def test_a_short_names_joined_form_that_spells_a_report_word_leaves_the_word_alone(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 4 Q72: "Ho, Me" run together is "home", so every "home", "HOME" and "Home" in the report was
    <name>, the report's own sentences included. A joined form that spells a report word (_JOINED_WORDS) is
    matched with a separator or a CamelCase boundary only: the name is still <name> in every other form."""

    class Entry:
        pw_gecos = "Ho, Me"

    monkeypatch.setattr(setup_report, "_full_name", _REAL_FULL_NAME)
    monkeypatch.setattr(setup_report.pwd, "getpwuid", lambda _uid: Entry())
    write_install_log(fake_mac)
    said = "your home folder; HOME set; the Home key; homepage; Ho Me, HoMe, ho_me, ho-me, HO_ME, Me Ho, meho"
    write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", f"| {said} |"))
    text, _summary = summary_of(fake_mac)
    friction = section(text, "Agent friction log")
    shown = "your home folder; HOME set; the Home key; homepage; " + ", ".join(["<name>"] * 7)
    assert f"| {shown} |" in friction, friction
    assert "<name> folder" not in text and "<name> set" not in text

    red = setup_report.Redactor()
    for form in ("Li, Jo", "Li Jo", "Jo Li"):
        red.add("name", form, fuzzy=True)
    assert red.redact("LiJo li_jo lijo LIJO") == "<name> <name> <name> <name>", "no report word: as before"
    red = setup_report.Redactor()
    red.add("folder", "Set Up", fuzzy=True)
    assert red.redact("setup SetUp Set Up") == "setup <folder-1> <folder-1>", "any fuzzy kind"
    red = setup_report.Redactor()
    for form in ("Ho, Me", "Ho Me", "Me Ho"):
        red.add("name", form, fuzzy=True)
    red.add("folder", "Home", fuzzy=True)  # registered after the name: a folder of its own, in every case
    assert red.redact("home HOME Home HoMe") == "<folder-1> <folder-1> <folder-1> <folder-1>"
    assert {"home", "main", "done", "login"} <= setup_report._JOINED_WORDS


def test_a_short_first_part_of_a_comma_name_keeps_the_last_name_fuzzy(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Doe, Ed": the parts are joined and swapped whatever their length, as "Ed Doe" and "doe-ed"."""

    class Entry:
        pw_gecos = "Doe, Ed"

    monkeypatch.setattr(setup_report, "_full_name", _REAL_FULL_NAME)
    monkeypatch.setattr(setup_report.pwd, "getpwuid", lambda _uid: Entry())
    write_install_log(fake_mac)
    write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", "| Ed Doe, doe-ed and doe agreed |"))
    text, _summary = summary_of(fake_mac)
    assert "| <name>, <name> and <name> agreed |" in section(text, "Agent friction log")


def test_a_comma_name_with_a_suffix_keeps_both_orders_and_later_parts_stay_as_written(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Doe, Grant, Jr.": "Doe Grant" and "Grant Doe" are fuzzy whatever the suffix; "Grant" is a lone name,
    so the lower-case word "grant" in agentsync's own advice is kept."""

    class Entry:
        pw_gecos = "Doe, Grant, Jr."

    monkeypatch.setattr(setup_report, "_full_name", _REAL_FULL_NAME)
    monkeypatch.setattr(setup_report.pwd, "getpwuid", lambda _uid: Entry())
    write_install_log(fake_mac)
    write_friction(
        fake_mac, V5_HAPPY.replace("| 1 folder chosen |", "| doe-grant and GrantDoe said grant access |")
    )
    text, _summary = summary_of(fake_mac)
    assert "| <name> and <name> said grant access |" in section(text, "Agent friction log")


@pytest.mark.parametrize(
    ("gecos", "said", "shown"),
    [
        ("Li, Jane", "asked Li, LI; Jane Li, li-jane agreed", "asked <name>, <name>; <name>, <name> agreed"),
        ("Jane Li", "asked Li and LI; Jane Li agreed", "asked <name> and <name>; <name> agreed"),
        ("Do, Jane", "Do it, said Jane Do and DO-JANE", "Do it, said <name> and <name>"),
        ("Tanaka, Ai", "asked Ai; the AI agent agreed", "asked <name>; the AI agent agreed"),
        ("de la Cruz, Maria", "ran ls -la; Maria de la Cruz agreed", "ran ls -la; <name> agreed"),
    ],
    ids=["comma", "no-comma", "a-word", "an-abbreviation", "a-particle"],
)
def test_a_two_letter_name_part_is_a_lone_name_unless_the_report_uses_it(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch, gecos: str, said: str, shown: str
) -> None:
    """ "Li" of "Li, Jane" or "Jane Li" is <name> alone too, written and upper-case. "Do" is a word the report
    and the agent use, so it is <name> only inside the joined forms; "Ai" is a name, "AI" is not; a particle
    written in lower case ("la") is not registered alone (B13 H20)."""

    class Entry:
        pw_gecos = gecos

    monkeypatch.setattr(setup_report, "_full_name", _REAL_FULL_NAME)
    monkeypatch.setattr(setup_report.pwd, "getpwuid", lambda _uid: Entry())
    write_install_log(fake_mac)
    write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", f"| {said} |"))
    text, _summary = summary_of(fake_mac)
    assert f"| {shown} |" in section(text, "Agent friction log")


MDM_STATUS = (
    "Enrolled via DEP: No\nMDM enrollment: Yes (User Approved)\n"
    "MDM server: https://contosoltd.mdm.example:443/mdm/ServerURL\n"
)


def test_a_hosted_mdm_server_host_is_redacted(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hosted MDM's host can name the company where the OneDrive org is spelled otherwise: the whole host is
    <mdmhost>, no vendor suffix kept, registered before any section is redacted (field report 2026-10-10).
    Its own placeholder: <host> is the Mac's name, and one placeholder for both hid which a line names."""
    real = setup_report._Run.run

    def run(self: setup_report._Run, argv: Sequence[str], timeout: float = 5.0) -> tuple[int, str]:
        if list(argv[:2]) == ["/usr/bin/profiles", "status"]:
            return 0, MDM_STATUS
        return real(self, argv, timeout)

    monkeypatch.setattr(setup_report._Run, "run", run)
    write_install_log(fake_mac)
    write_friction(
        fake_mac,
        V5_HAPPY.replace("| 1 folder chosen |", f"| {HOST} enrolled with contosoltd.mdm.example |"),
    )
    text, _summary = summary_of(fake_mac)
    assert "MDM server: https://<mdmhost>:443/mdm/ServerURL" in section(text, "Environment")
    assert "| <host> enrolled with <mdmhost> |" in section(text, "Agent friction log")
    legend = "<host> this Mac's host or computer name · <mdmhost> MDM server's host"
    assert legend in section(text, "Redaction")
    assert "contosoltd" not in text.lower() and "mdm.example" not in text


def test_the_macos_line_gives_the_version_and_build_as_the_shell_report_does(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 12 N26: the Environment line read ``- macOS: macOS 15.5 (24F74)``, the product's name under a
    label that already is it, where the shell report prints ``- macOS: 15.5 (24F74)``."""
    real = setup_report._Run.run
    said = "ProductName:\tmacOS\nProductVersion:\t15.5\nBuildVersion:\t24F74\n"

    def run(self: setup_report._Run, argv: Sequence[str], timeout: float = 5.0) -> tuple[int, str]:
        if list(argv) == ["/usr/bin/sw_vers"]:
            return 0, said
        return real(self, argv, timeout)

    monkeypatch.setattr(setup_report._Run, "run", run)
    write_install_log(fake_mac)
    write_friction(fake_mac, V5_HAPPY)
    text, _summary = summary_of(fake_mac)
    assert "- macOS: 15.5 (24F74)\n" in section(text, "Environment"), section(text, "Environment")
    assert "macOS: macOS" not in text
    said = "ProductName:\tmacOS\nBuildVersion:\t24F74\n"  # no version: "?", as the shell's || echo '?'
    text, _summary = summary_of(fake_mac)
    assert "- macOS: ? (24F74)\n" in section(text, "Environment")


def test_the_residue_check_names_a_word_glued_to_the_organisation(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 12 N8: M50 cuts the organisation out of a longer word at a CamelCase boundary, so
    ``ContosoDrive`` reads ``<org-1>Drive``, and the Redaction section's residue check said no word was next
    to a placeholder: it matched only a word with a separator between."""
    write_install_log(fake_mac)
    write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", f"| chose the {ORG}Drive folder |"))
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    assert "<org-1>Drive" in section(text, "Agent friction log"), section(text, "Agent friction log")
    red = section(text, "Redaction")
    assert (
        "Residue check: 1 capitalized or joined word next to a placeholder in this report (Agent friction "
        "log: Drive); check it." in red
    ), red


def test_the_folder_legend_says_why_a_listed_generic_or_agent_name_stays_in_clear(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 12 N3: a friction line quoting "Copilot under Documents" showed both words in clear in both
    readers, and only the shell legend said why: the Python ``<folder-N>`` item read as if every CloudStorage
    folder became one. The clause is added when a listed name kept in clear (a generic one, or one made of
    coding-agent product words) stands in the body as a whole word, and only then; it adds no report word."""
    listed = Path.home() / "Library" / "CloudStorage" / f"OneDrive-{ORG}" / "Documents" / "Copilot"
    listed.mkdir(parents=True)
    write_install_log(fake_mac)
    item = setup_report._LEGEND["folder"][:-1]
    clause = (
        "; not a name every OneDrive has, such as Documents, nor one made of coding-agent product words, "
        "such as Copilot or GitHub Copilot, where only a folder listing names it)"
    )
    write_friction(fake_mac, V5_HAPPY)  # the role: no kept name in the body
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    red = section(text, "Redaction")
    assert item + ")" in red and clause not in red, red
    write_friction(
        fake_mac,
        V5_HAPPY.replace("| 1 folder chosen |", "| the folder list showed Copilot under Documents |"),
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "Copilot under Documents" in section(text, "Agent friction log")
    assert item + clause in section(text, "Redaction"), section(text, "Redaction")
    # only the kind the body shows, as the shell's since round 12 N45
    for quoted, which in (
        ("Copilot", "one made of coding-agent product words, such as Copilot or GitHub Copilot"),
        ("Documents", "a name every OneDrive has, such as Documents"),
    ):
        write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", f"| the list showed {quoted} |"))
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        assert f"{item}; not {which}, where only a folder listing names it)" in section(text, "Redaction")
    assert "google" not in setup_report.report_words()
    # B13 round 13 O10: the agent's own product name, in the friction log's Agent: line and the Summary's
    # agent: clause, is no folder a listing names
    write_friction(
        fake_mac, V5_HAPPY.replace("Agent: Claude Code, claude-opus-5-5", "Agent: GitHub Copilot CLI gpt-5")
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "Agent: GitHub Copilot CLI gpt-5" in text and "agent: GitHub Copilot CLI gpt-5" in section(
        text, "Summary"
    )
    red = section(text, "Redaction")
    assert item + ")" in red and "where only a folder listing names it" not in red, red
    assert all(w in setup_report.report_words() for w in re.findall(r"[a-z]{3,}", clause.lower())), (
        "the clause adds no report word"
    )


def test_every_placeholder_kind_is_one_the_report_reads_back() -> None:
    """A path component or source id that redacts to a placeholder is kept as one only if _PLACEHOLDER_RE
    reads it, and a hyphen there means a number: a kind spelled ``<mdm-host>`` would be cut to ``<path>`` or
    ``(source N)``."""
    for kind in setup_report._LEGEND:
        if kind != "home":
            shown = setup_report.Redactor()._placeholder(kind)
            assert setup_report._PLACEHOLDER_RE.fullmatch(shown), shown
            assert setup_report._PLACEHOLDERS_ONLY_RE.fullmatch(shown + shown), shown


@pytest.mark.parametrize("folder", ["MDM", "Host"])
def test_a_folder_named_mdm_or_host_leaves_the_mdm_placeholder_whole(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch, folder: str
) -> None:
    """The Summary's item lines are redacted twice (_item_line, then the whole report), so a listed folder
    named MDM or Host matched inside ``<mdm-host>`` (``<<folder-4>-host>``, a false residue word). One word,
    ``<mdmhost>``, has no word boundary inside it."""
    real = setup_report._Run.run

    def run(self: setup_report._Run, argv: Sequence[str], timeout: float = 5.0) -> tuple[int, str]:
        if list(argv[:2]) == ["/usr/bin/profiles", "status"]:
            return 0, MDM_STATUS
        return real(self, argv, timeout)

    monkeypatch.setattr(setup_report._Run, "run", run)
    (fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{ORG}" / folder).mkdir()
    write_install_log(fake_mac)
    end = "2026-09-29T10:01:00Z | step 3 | end | install.sh exited 0 | -\n"
    deviation = "2026-09-29T10:01:00Z | step 3 | deviation | enrolled with contosoltd.mdm.example | -\n"
    write_friction(fake_mac, V5_HAPPY.replace(end, end + deviation))
    text, _summary = summary_of(fake_mac)
    assert "· deviation · enrolled with <mdmhost>" in section(text, "Summary")
    assert "Summary:" not in section(text, "Redaction")


def test_an_mdm_host_with_no_dot_is_not_registered(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare host would redact the report's own words wherever they appear."""
    real = setup_report._Run.run

    def run(self: setup_report._Run, argv: Sequence[str], timeout: float = 5.0) -> tuple[int, str]:
        if list(argv[:2]) == ["/usr/bin/profiles", "status"]:
            return 0, "MDM enrollment: Yes (User Approved)\nMDM server: https://mdm:8443/mdm/ServerURL\n"
        return real(self, argv, timeout)

    monkeypatch.setattr(setup_report._Run, "run", run)
    write_install_log(fake_mac)
    text, _summary = summary_of(fake_mac)
    assert "MDM server: https://mdm:8443/mdm/ServerURL" in section(text, "Environment")
    assert "- MDM enrollment (" in section(text, "Environment")


@pytest.mark.parametrize("git_proxy", [False, True])
def test_a_pac_url_is_redacted_after_its_scheme(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch, git_proxy: bool
) -> None:
    """A cloud proxy's PAC path can carry the customer's domain (``/<domain>/proxy.pac``): the PAC URL after
    its scheme is one proxy value, so a quote without the scheme is covered too, and the bare host keeps its
    own placeholder (B13 H19). Git's proxies are registered after it, so a git-only proxy leaves its number
    as it was."""
    pac = "https://pac.cloudproxy.example/fabrikam.com/proxy.pac"
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(
        net, "system_proxy", lambda runner=None: net.SystemProxy(pac_enabled=True, pac_url=pac)
    )
    if git_proxy:
        with (Path.home() / ".gitconfig").open("a", encoding="utf-8") as f:
            f.write("[http]\n\tproxy = http://gitproxy.fabrikam.example:3128\n")
    write_install_log(fake_mac)
    quote = f"| PAC {pac} on host pac.cloudproxy.example, typed {pac.split('://', 1)[1]} |"
    write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", quote))
    text, _summary = summary_of(fake_mac)
    assert "fabrikam" not in text.lower() and "cloudproxy" not in text.lower()
    env = section(text, "Environment")
    assert "PAC: yes" in env and re.search(r"a PAC file \(https://<proxy-\d+>\)", env)
    friction = section(text, "Agent friction log")
    m = re.search(r"\| PAC https://(<proxy-\d+>) on host (<proxy-\d+>), typed (<proxy-\d+>) \|", friction)
    assert m and m.group(1) == m.group(3) != m.group(2), friction
    assert (m.group(2), m.group(1)) == ("<proxy-1>", "<proxy-2>"), friction


@pytest.mark.parametrize("where", ["xdg", "checkout"])
def test_a_proxy_in_any_git_config_file_is_redacted(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, where: str
) -> None:
    """A git error logged at step 1 names git's proxy (``Could not resolve proxy: <host>``), and a proxy set
    only in git's config files is in no environment variable: the report reads those files, with no git
    process (B13 H19, H1 skeptic C2). ``~/.gitconfig`` and the cloned checkout are
    ``test_a_proxy_only_git_knows_is_redacted``."""
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(net, "system_proxy", lambda runner=None: net.SystemProxy())
    host = "gitproxy.fabrikam.example"
    if where == "xdg":
        xdg = tmp_path / "xdg"
        (xdg / "git").mkdir(parents=True)
        config = f'[http "https://github.com/"]\n\tProxy = "{host}:3128" ; set by IT\n'
        (xdg / "git" / "config").write_text(config)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    else:
        checkout = tmp_path / "checkout"
        (checkout / ".git").mkdir(parents=True)
        (checkout / ".git" / "config").write_text(
            f'[remote "origin"]\n\tproxy=https://jdoe:secret@{host}:3128\n'
        )
        monkeypatch.setattr(setup_report, "_installed_from", lambda: (f"checkout {checkout}", checkout))
    write_install_log(fake_mac)
    line = f"| git pull: Could not resolve proxy: {host} |"
    write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", line))
    text, _summary = summary_of(fake_mac)
    assert "fabrikam" not in text.lower(), text
    friction = section(text, "Agent friction log")
    assert re.search(r"\| git pull: Could not resolve proxy: <proxy-\d+> \|", friction), friction


@pytest.mark.parametrize(
    ("gitconfig", "https_proxy"),
    [
        # `git config http.proxy ""` (git's way to turn a proxy off) writes "proxy = " with nothing after it
        (
            '[http]\n\tproxy = \n[user]\n\tname = Jane Doe\n[remote "origin"]\n\tproxy =\n[branch "main"]\n',
            "",
        ),
        ("", "http://[proxy.fabrikam.example"),  # a malformed value in the environment
    ],
    ids=["empty-git-proxy", "malformed-env-proxy"],
)
def test_an_unreadable_proxy_value_keeps_the_redaction(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch, gitconfig: str, https_proxy: str
) -> None:
    """A proxy value that is empty or no URL is skipped, not fatal: an error there used to drop the report
    to a home-only redactor, printing the org, login, folders, serial and host in clear (B13 H19 review)."""
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    if https_proxy:
        monkeypatch.setenv("HTTPS_PROXY", https_proxy)
    monkeypatch.setattr(net, "system_proxy", lambda runner=None: net.SystemProxy())
    (Path.home() / ".gitconfig").write_text(gitconfig, encoding="utf-8")
    write_install_log(fake_mac)
    text, _summary = summary_of(fake_mac)
    for raw in RAW:
        assert raw not in text, raw


@pytest.mark.usefixtures("clean_doctor")
def test_the_install_source_says_a_checkout_is_not_a_git_checkout(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B13 round 9 K29: agentsync installed from a folder with no .git read "checkout <path>" with no commit
    or origin and no reason, and a run that read no commit gave "last install.sh run: commit -"."""
    checkout = tmp_path / "copy"
    checkout.mkdir()
    monkeypatch.setattr(setup_report, "_installed_from", lambda: (f"checkout {checkout}", checkout))
    write_install_log(fake_mac)
    text, _summary = summary_of(fake_mac)
    [line] = [ln for ln in text.splitlines() if ln.startswith("- install source: ")]
    # B13 round 10 L28: not "checkout <path> (not a git checkout ...)", which called it a checkout first
    assert not line.startswith("- install source: checkout ") and line.endswith(
        "/copy, a copy that is not a git checkout (no .git, so no commit or origin); last install.sh run: "
        "commit 0123456789ab"
    ), line
    path = fake_mac["setup"] / "install.log"
    path.write_text(path.read_text().replace("commit=0123456789ab", "commit=-"))
    text, _summary = summary_of(fake_mac)
    [line] = [ln for ln in text.splitlines() if ln.startswith("- install source: ")]
    assert "commit -" not in line and line.endswith(
        ", a copy that is not a git checkout (no .git, so no commit or origin); last install.sh run: no "
        "commit read"
    ), line
    monkeypatch.setattr(
        setup_report, "_installed_from", lambda: (f"checkout {checkout} (editable)", checkout)
    )
    [line] = [ln for ln in summary_of(fake_mac)[0].splitlines() if ln.startswith("- install source: ")]
    assert "/copy (editable), a copy that is not a git checkout (" in line, line
    assert not line.startswith("- install source: checkout "), line


def test_the_install_source_says_git_refuses_a_checkout_another_account_owns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B13 round 10 L29: an installed checkout git refuses ("detected dubious ownership") read "git cannot
    read its .git", as an unreadable .git does. It says why, as the shell report does, naming no account."""
    repo = tmp_path / "checkout"
    repo.mkdir()
    for args in (("init", "-q", "-b", "main"), ("commit", "-q", "--allow-empty", "-m", "one")):
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    monkeypatch.setattr(setup_report, "_installed_from", lambda: (f"checkout {repo}", repo))
    run = setup_report._Run(tmp_path / "none.toml", setup_report.ReportHooks(), 12.0)
    monkeypatch.setattr(run, "devtools", lambda: True)
    assert setup_report._install_source(run).startswith(f"checkout {repo} @ ")
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    assert setup_report._install_source(run) == (
        f"checkout {repo} (git refuses its .git: the folder belongs to another user account)"
    )
    (repo / ".git" / "HEAD").write_text("garbage\n")
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    assert setup_report._install_source(run) == f"checkout {repo} (git cannot read its .git)"


def test_the_install_source_reads_no_commit_from_a_repository_around_the_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B13 round 11 M51: a checkout whose .git holds only HEAD, inside a home that is a git repository, read
    as ``@ <the home's commit>``: git searched upward past the .git it could not read. The checkout's parent
    is git's ceiling, so it reads "git cannot read its .git"."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "notes.txt").write_text("Contoso notes\n")
    git = ["git", "-C", str(home), "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    for args in (("init", "-q", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "one")):
        subprocess.run([*git, *args], check=True, capture_output=True)
    outer = subprocess.run(
        [*git, "rev-parse", "--short=12", "HEAD"], check=True, capture_output=True, text=True
    )
    repo = home / "src" / "checkout"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    monkeypatch.setattr(setup_report, "_installed_from", lambda: (f"checkout {repo}", repo))
    run = setup_report._Run(tmp_path / "none.toml", setup_report.ReportHooks(), 12.0)
    monkeypatch.setattr(run, "devtools", lambda: True)
    line = setup_report._install_source(run)
    assert line == f"checkout {repo} (git cannot read its .git)", line
    assert outer.stdout.strip() not in line


def test_an_empty_git_proxy_takes_no_token_from_the_next_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``proxy =`` with no value is git's "no proxy": the next line's key is not read as a proxy host."""
    monkeypatch.setattr(setup_report, "_installed_from", lambda: ("", None))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (Path.home() / ".gitconfig").write_text("[http]\n\tproxy =\n\tsslVerify = false\n", encoding="utf-8")
    assert "sslVerify" not in setup_report._git_proxy_urls()


def test_a_linked_worktrees_git_config_is_read_for_proxies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """In a linked worktree ``.git`` is a file naming its git dir: ``.git/config`` could not be opened, and a
    proxy in the repository's config went unredacted. The common dir's config is read, and the worktree's own
    ``config.worktree``; a plain clone reads as before (B13 round 2 R110)."""
    main, wt = tmp_path / "main", tmp_path / "wt"
    gitdir = main / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (main / ".git" / "config").write_text("[http]\n\tproxy = http://proxy.contoso.example:8080\n")
    (gitdir / "commondir").write_text("../..\n")
    (gitdir / "config.worktree").write_text("[http]\n\tproxy = http://wt.contoso.example:3128\n")
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {gitdir}\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for checkout in (wt, main):
        monkeypatch.setattr(setup_report, "_installed_from", lambda c=checkout: ("", c))
        urls = setup_report._git_proxy_urls()
        assert "http://proxy.contoso.example:8080" in urls, (checkout, urls)
    found = [f.resolve() for f in setup_report._checkout_git_configs(wt)]
    assert found == [(main / ".git" / "config").resolve(), (gitdir / "config.worktree").resolve()]
    (wt / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n")  # relative to the checkout
    assert setup_report._checkout_git_configs(wt)[0].resolve() == (main / ".git" / "config").resolve()
    monkeypatch.setattr(setup_report, "_installed_from", lambda: ("", wt))
    assert "http://wt.contoso.example:3128" in setup_report._git_proxy_urls()


def test_residue_check_covers_the_whole_report_by_section(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    log = fake_mac["logs"] / "com.agentsync.poll.err.log"
    log.write_text(
        log.read_text(encoding="utf-8")
        + f"2026-09-29 10:06:00,000 ERROR agentsync.cycle: {FOLDERS[1]} Roadmap failed\n",
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "<folder-2> Roadmap failed" in section(text, "Recent errors")
    assert (
        "Residue check: 1 capitalized or joined word next to a placeholder in this report (Recent errors: "
        "Roadmap); check it." in section(text, "Redaction")
    )
    assert setup_report.residue_by_section(
        "# t\n## A\n<folder-1> Alpha\n## B\nnone\n## C\nBeta <org-1>\n"
    ) == [
        ("A", ["Alpha"]),
        ("C", ["Beta"]),
    ]
    twice = setup_report._redaction_section(
        setup_report.Redactor(), [("A", ["Alpha", "Beta"]), ("C", ["Beta"])]
    )
    assert (
        "Residue check: 3 capitalized or joined words next to a placeholder in this report (A: Alpha, "
        "Beta; C: Beta)" in ("\n".join(twice))
    ), "the count is the number of words listed (field report 2026-10-06: 5 counted, 7 listed)"
    assert setup_report._shorten("word " * 60, 22) == "word word word word…", "never cut inside a word"
    assert setup_report._shorten("abcdefghij klmnopqrst uvwxyz", 12) == "abcdefghij…"
    assert setup_report._shorten("a" * 30, 12) == "a" * 11 + "…" and setup_report._shorten("short") == "short"


def test_installer_output_is_embedded_and_its_hints_counted(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """What the agent saw (K17): the tail of install.out, redacted, and its instruction-like lines."""
    write_install_log(fake_mac)
    write_friction(fake_mac)
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "No installer output at ~/agent-context/setup/install.out" in section(text, "Installer")
    assert "- installer output:" not in section(text, "Summary")
    lines = ["# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x"]
    lines += [f"filler {i}" for i in range(69)] + [
        f"source: {fake_mac['one']}",
        "next: agentsync doctor · agentsync sync --once",
        "[warn] launchd.poll — com.agentsync.poll is not installed (fix: agentsync install-agent)",
        "run: agentsync sync --once",
        "NEXT: review ~/agent-context/setup-report.md",
    ]
    (fake_mac["setup"] / "install.out").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    counted = (
        "1 NEXT: line in 1 run (exactly one per install.sh run is expected) and 3 other "
        "instruction-like lines (1 next:, 1 fix:, 1 run:) in the last 75 lines of install.out"
    )
    inst = section(text, "Installer")
    assert f"Installer output: {counted}." in inst
    assert f"- installer output: {counted}" in section(text, "Summary")
    head = "<details><summary>the last 60 lines of ~/agent-context/setup/install.out (what the agent saw)"
    shown = inst.split(head + "</summary>", 1)[1]
    assert "filler 13\n" not in shown and "filler 14\n" in shown and shown.rstrip().endswith("</details>")
    assert "4242" not in shown, "no process id"
    assert "source: ~/Library/CloudStorage/OneDrive-<org-1>/<folder-1>/<folder-2>" in shown
    for raw in RAW:
        assert raw not in text, raw
    assert setup_report.instruction_counts(
        ["  NEXT: x", "next: y", "a (fix: b)", "(run: c)", "a run: d"]
    ) == {
        "NEXT:": 1,
        "next:": 1,
        "fix:": 1,
        "run:": 1,
    }


def test_a_persons_fail_fix_is_not_counted_as_a_hint_for_the_agent(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 15 S5: the Summary called six [FAIL] fixes doctor gives the person "other
    instruction-like line(s)", which the Installer section calls a hint the agent may act on, while their
    run's NEXT (install.sh's ``persons_fails``) told the agent they are not its step. By run, as install.sh
    decides: a run whose stopping [FAIL] lines are all the person's counts their fixes apart; a mixed run's
    stay hints."""
    persons = [
        "[FAIL] source.contoso-plans.listable — /x/Plans does not exist (fix: fix path in [[source]] id = "
        '"contoso-plans" in sources.toml, or sync that folder in OneDrive)',
        "[FAIL] source.contoso-plans.volume — not mounted (fix: /Users/jdoe/Library/CloudStorage/"
        "OneDrive-Contoso is missing while OneDrive-Fabrikam is there: sign in to OneDrive as Contoso)",
        "[FAIL] tcc.contoso-plans — TCC_PENDING: the launcher waits for Allow (fix: click Allow)",
    ]
    chmod = "[FAIL] state_dir — mode 755 (fix: chmod 700 ~/.local/state/agentsync)"
    next_line = "NEXT: the [FAIL] lines above are the person's to fix (each names its fix: ...), not a step"
    run_one = ["# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh", *persons, next_line]
    run_two = ["# run=20260929T100100Z-4242 2026-09-29T10:01:00Z install.sh", persons[0], chmod, "NEXT: fix"]
    counts = setup_report.instruction_counts(run_one)
    # the TCC_PENDING line stops nothing, so its fix stays a hint; the NEXT that holds "fix:" is a NEXT:
    assert counts == {"NEXT:": 1, setup_report.PERSONS_FIX: 2, "fix:": 1}, counts
    assert setup_report.instruction_counts(run_two) == {"NEXT:": 1, "fix:": 2}, "a mixed run: both hints"
    both = setup_report.instruction_counts(run_one + run_two)
    assert both == {"NEXT:": 2, setup_report.PERSONS_FIX: 2, "fix:": 3}, both
    text = setup_report._instruction_text(setup_report.instruction_counts([*run_one[:3], next_line]), 4, 1)
    theirs = f"lines whose fix: is the person's ({doctor.PERSONS_KINDS}; not hints for the agent)"
    # B13 round 16 T8: an Oxford-comma list, not two "and"s; T47: what the rule reads, not what a NEXT says
    assert ", 0 other instruction-like lines, and 2 " + theirs + " in the last 4 lines" in text, text
    assert "NEXT says so" not in text, text
    write_install_log(fake_mac)
    write_friction(fake_mac)
    (fake_mac["setup"] / "install.out").write_text(
        "\n".join([*run_one[:3], next_line]) + "\n", encoding="utf-8"
    )
    _rc, report_text, _ = report(tmp_path, fake_mac["config"])
    inst = section(report_text, "Installer")
    assert "2 " + theirs in inst, inst
    assert "a fix: on a [FAIL] line of a run whose stopping [FAIL] lines are all the person's is not" in inst


def test_the_installer_output_lines_person_clause_reads_by_its_count_and_its_runs_next() -> None:
    """B13 round 16 T8, T49: the person's clause was "and 1 fix: on [FAIL] lines that are the person's" at
    a count of 1, after a second "and"; it is the last item of an Oxford-comma list, its noun by the count.
    With no person's fix the two-part form is as it was. T47: a run whose NEXT says something else (a first
    run with no folder ends on the no-folder NEXT) has no person's fixes; a run with no NEXT yet (the one
    writing the report) keeps the [FAIL]-only rule."""
    one = setup_report._instruction_text(Counter({"NEXT:": 1, "fix:": 1, setup_report.PERSONS_FIX: 1}), 5, 1)
    assert (
        "(exactly one per install.sh run is expected), 1 other instruction-like line (1 fix:), and 1 line "
        in (one)
    ), one
    assert "1 line whose fix: is the person's (" in one and "not a hint for the agent)" in one, one
    six = setup_report._instruction_text(Counter({"NEXT:": 2, setup_report.PERSONS_FIX: 6}), 81, 2)
    assert six.startswith(
        "2 NEXT: lines in 2 runs (exactly one per install.sh run is expected), 0 other instruction-like "
        "lines, and 6 lines whose fix: is the person's ("
    ), six
    assert six.endswith("not hints for the agent) in the last 81 lines of install.out"), six
    none = setup_report._instruction_text(Counter({"NEXT:": 1}), 5, 1)
    assert none == (
        "1 NEXT: line in 1 run (exactly one per install.sh run is expected) and 0 other instruction-like "
        "lines in the last 5 lines of install.out"
    ), none
    rosetta = (
        "[FAIL] pandoc — not runnable (fix: Rosetta is yours to install: softwareupdate --install-rosetta)"
    )
    head = "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh"
    no_folder = setup_report.instruction_counts([head, rosetta, "NEXT: no folder to sync yet: ..."])
    assert no_folder == {"NEXT:": 1, "fix:": 1}, no_folder
    pending = setup_report.instruction_counts([head, rosetta])
    assert pending == {setup_report.PERSONS_FIX: 1}, pending
    # B13 round 18 V87: the state file's error line is no [FAIL] line; in a run with no NEXT yet (the one
    # writing the report) its fix is the person's, as every exit-65 NEXT says; another NEXT keeps "fix:"
    state = [
        head,
        "first sync: running",
        f"state: <db> cannot be read: malformed (fix: {doctor.MANIFEST_FIX})",
    ]
    assert setup_report.instruction_counts(state) == {setup_report.PERSONS_FIX: 1}
    words = setup_report._instruction_text(setup_report.instruction_counts(state), 3, 1)
    assert "1 line whose fix: is the person's (" in words, words
    other = setup_report.instruction_counts([*state, "NEXT: fix the error above, then re-run: x"])
    assert other == {"fix:": 1, "NEXT:": 1}, other
    told = setup_report.instruction_counts(
        [
            *state,
            "NEXT: agentsync's state file cannot be read (...): fixing it is the person's, not a step for "
            "the agent",
        ]
    )
    assert told == {setup_report.PERSONS_FIX: 1, "NEXT:": 1}, told
    loop = setup_report.instruction_counts(
        [head, rosetta, "NEXT: background sync: done; the [FAIL] lines above are the person's to fix (...)"]
    )
    assert loop == {"NEXT:": 1, setup_report.PERSONS_FIX: 1}, loop


_COUNTED_S_RE = re.compile(r"\b\d+ [A-Za-z:. -]{0,30}\((?:s|es|ies)\)")
"""A number before a noun written with ``(s)``: the count is known, so the noun needs none (B13 round 15 S26,
S30, S31)."""


def test_the_persons_clause_names_every_kind_the_rule_reads() -> None:
    """B13 round 17 U11, U19, U45, U52, U86: the clause listed "a sign-in, a folder, a [[source]] path or
    block, Rosetta or a native pandoc copy" while the count also takes a [policy] or [governance] value and
    a state file that cannot be read (``PERSONS_FIX_RE``'s "the person "), so a run stopped on one of those
    was described by kinds that left it out. The clause reads doctor's one list, which cli's NEXT and
    install.sh's PERSONS_FAILS name too; the report's copy for a broken doctor is the same words."""
    assert setup_report._PERSONS_KINDS_COPY == doctor.PERSONS_KINDS == cli.PERSONS_KINDS
    head = "# run=20261019T100000Z-4242 2026-10-19T10:00:00Z install.sh"
    run = [
        head,
        f"[FAIL] state_dir.manifest — ~/x/manifest.sqlite cannot be read: x (fix: {doctor.MANIFEST_FIX})",
        f"[FAIL] policy — bad value (fix: {cli.POLICY_FIX})",
        "NEXT: the [FAIL] lines above are the person's to fix (each names its fix: ...), not a step for "
        "the agent",
    ]
    counts = setup_report.instruction_counts(run)
    assert counts == {"NEXT:": 1, setup_report.PERSONS_FIX: 2}, counts
    text = setup_report._instruction_text(counts, 4, 1)
    assert f"2 lines whose fix: is the person's ({doctor.PERSONS_KINDS}; not hints for the agent)" in text
    assert "a [policy] or [governance] value" in text and "a state file that cannot be read" in text, text


def test_a_persons_fix_on_an_error_line_is_not_a_hint_when_its_next_says_so() -> None:
    """B13 round 17 U6: a damaged state file stops init with ``state: ... cannot be read: ... (fix: the
    person decides, ...)`` and install.sh's NEXT "fixing it is the person's, not a step for the agent";
    with no [FAIL] line in the run, the Summary counted that fix: as a hint the agent may act on. A run
    whose NEXT gives the stop to the person counts a person's fix on any line apart; a run whose NEXT says
    something else keeps it a hint."""
    db = "/Users/jdoe/.local/state/agentsync/manifest.sqlite"
    state = f"state: {db} cannot be read: x (fix: {doctor.MANIFEST_FIX})"
    told = (
        "NEXT: agentsync's state file cannot be read (the error above, with its fix; ~/.local/bin/agentsync "
        "status prints it again): fixing it is the person's, not a step for the agent, so show them the "
        "error and go on to the report; once it is fixed, the next run is: ~/x/install.sh"
    )
    runs = [
        "# run=20261019T100000Z-4242 2026-10-19T10:00:00Z install.sh",
        "NEXT: run ~/x/install.sh --list-folders",
        "# run=20261019T100100Z-4242 2026-10-19T10:01:00Z install.sh",
        state,
        "error: init failed (exit 69)",
        told,
    ]
    counts = setup_report.instruction_counts(runs)
    assert counts == {"NEXT:": 2, setup_report.PERSONS_FIX: 1}, counts
    text = setup_report._instruction_text(counts, 6, 2)
    assert "0 other instruction-like lines, and 1 line whose fix: is the person's (" in text, text
    other = setup_report.instruction_counts([*runs[:4], "NEXT: fix the error above, then re-run"])
    assert other == {"NEXT:": 2, "fix:": 1}, "a NEXT that asks the agent to act keeps the fix a hint"
    hint = "a (fix: run chmod 700 ~/.local/state/agentsync)"
    assert setup_report.instruction_counts([*runs[2:], hint]) == {
        "NEXT:": 1,
        setup_report.PERSONS_FIX: 1,
        "fix:": 1,
    }, "a fix that is not the person's stays a hint under the person's NEXT"


def _report_prose(text: str) -> list[str]:
    """The report's own lines: not the fenced blocks or ``<details>`` bodies, which quote other programs."""
    out: list[str] = []
    fence = details = False
    for line in text.splitlines():
        if line.startswith(("~~~", "```")):
            fence = not fence
        elif line.startswith("<details>"):
            details = True
            out.append(line)  # its summary line is the report's
        elif line == "</details>":
            details = False
        elif not fence and not details:
            out.append(line)
    return out


def test_a_report_writes_no_s_after_a_count_it_knows(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    """B13 round 15 S26, S30, S31: the report wrote "(s)" after some 60 counts it knows (``1 attempt:``,
    ``1 event line``, ``2 NEXT: lines in 2 runs``, ``1 provider folder``, ``42 checks:``,
    ``84 replacements of 7 values``), beside lines Q39 had moved to the count's noun. One attempt with an
    error and its end line, two install runs, the fake Mac's provider folders: no count in the report's own
    lines carries ``(s)``."""
    write_install_log(fake_mac)
    with (fake_mac["setup"] / "install.log").open("a", encoding="utf-8") as log:
        log.write(
            "2026-09-29T10:05:00Z run=20260929T100500Z-4243 start install.sh compat=10 commit=0123456789ab "
            "kind=checkout source=- args=\n"
            "2026-09-29T10:05:00Z run=20260929T100500Z-4243 end rc=0 seconds=1\n"
        )
    write_friction(
        fake_mac,
        V11_HAPPY.replace(
            "| end | finished",
            "| step 2 | error | git pull exit 1: a | -\n2026-09-29T10:01:10Z | end | finished",
        ),
    )
    (fake_mac["setup"] / "install.out").write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh\nNEXT: review the report\n",
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    found = [(ln, m.group(0)) for ln in _report_prose(text) for m in _COUNTED_S_RE.finditer(ln)]
    assert found == [], found
    friction = section(text, "Agent friction log")
    assert "1 attempt:" in friction and "2 event lines: " in friction, friction


def out_run(number: int, filler: int) -> list[str]:
    """One run in install.out as install.sh writes it: its header, what it printed, one doctor fix and its
    one NEXT: line, last."""
    return [
        f"# run=2026092{number}T100000Z-4242 2026-09-2{number}T10:00:00Z install.sh --source-local x",
        *(f"run {number} filler line {i:04d} of what the installer printed" for i in range(filler)),
        "[warn] launchd.poll — com.agentsync.poll is not installed (fix: agentsync install-agent)",
        f"NEXT: run {number} is done",
    ]


def test_installer_output_counts_next_lines_and_runs_over_the_same_whole_runs(
    fake_mac: dict[str, Path],
) -> None:
    """Field report 2026-10-07: "6 NEXT: lines in 5 runs", and no run had printed two. The report reads
    the last 64 KiB of install.out by bytes, which started inside a run: that run's NEXT: line (its last
    line) was read and its header (its first) was not. The count now starts at the first header read. The
    embedded tail is still the file's last 60 lines."""
    write_install_log(fake_mac)
    out = fake_mac["setup"] / "install.out"
    lines = [*out_run(1, 2000), *out_run(2, 100), *out_run(3, 100)]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    window = setup_report._tail(out)
    assert out.stat().st_size > setup_report._TAIL_BYTES and not window[0].startswith("# run=")
    cut = next(i for i, ln in enumerate(window) if ln.startswith("# run="))
    assert cut > 2 and window[cut - 1] == "NEXT: run 1 is done", "the read starts inside run 1"
    assert setup_report.instruction_counts(window)["NEXT:"] == 3, "read as it was: 3 NEXT: in 2 runs"
    counted = (
        "2 NEXT: lines in 2 runs (exactly one per install.sh run is expected) and 2 other "
        "instruction-like lines (2 fix:) in the last 206 lines of install.out (whole runs only: the "
        f"{cut} lines read before the first run header are not counted)"
    )
    text, summary = summary_of(fake_mac)
    inst = section(text, "Installer")
    assert f"Installer output: {counted}." in inst
    assert f"- installer output: {counted}" in summary
    shown = inst.split("<details><summary>the last 60 lines of ", 1)[1].split("~~~text\n", 1)[1]
    assert shown.split("\n~~~", 1)[0].splitlines() == [
        ln.replace("-4242", "") for ln in lines[-setup_report.INSTALL_OUT_TAIL :]
    ]

    # One run longer than what is read: no header in it, so nothing is left out and no run is counted.
    out.write_text("\n".join(out_run(1, 3000)) + "\n", encoding="utf-8")
    window = setup_report._tail(out)
    assert not any(ln.startswith("# run=") for ln in window)
    text, summary = summary_of(fake_mac)
    counted = (
        "1 NEXT: line (exactly one per install.sh run is expected) and 1 other instruction-like line "
        f"(1 fix:) in the last {len(window)} lines of install.out"
    )
    assert f"- installer output: {counted}\n" in summary + "\n"
    assert f"Installer output: {counted}. An instruction-like line" in section(text, "Installer")

    # install.sh keeps the file's last 2000 lines, which cuts a run the same way in a file read whole.
    lines = [*out_run(1, 5)[1:], *out_run(2, 5)]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _text, summary = summary_of(fake_mac)
    assert (
        "- installer output: 1 NEXT: line in 1 run (exactly one per install.sh run is expected) and 1 "
        "other instruction-like line (1 fix:) in the last 8 lines of install.out (whole runs only: the "
        "7 lines read before the first run header are not counted)" in summary
    )


def test_the_report_install_sh_writes_at_its_exit_expects_one_next_line_fewer(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """install.sh writes the report at its exit and prints that run's NEXT: line after it (N21): its header
    is read, its NEXT: is not. It names the run (AGENTSYNC_REPORT_RUN), and when that is the last run counted
    the line says one fewer is expected. An earlier run named (capture failed) or none: as before."""
    write_install_log(fake_mac)
    (fake_mac["setup"] / "install.out").write_text(
        "\n".join([*out_run(1, 5), *out_run(2, 5)[:-1]]) + "\n", encoding="utf-8"
    )
    monkeypatch.setenv(setup_report.REPORT_RUN_ENV, "20260922T100000Z-4242")
    pending = (
        "1 NEXT: line in 2 runs (exactly one per install.sh run is expected; the run writing this "
        "report prints its NEXT: after the report, so 1 expected here) and 2 other instruction-like lines "
        "(2 fix:) in the last 15 lines of install.out"
    )
    text, summary = summary_of(fake_mac)
    assert f"- installer output: {pending}\n" in summary + "\n"
    assert f"Installer output: {pending}." in section(text, "Installer")
    today = (
        "1 NEXT: line in 2 runs (exactly one per install.sh run is expected) and 2 other "
        "instruction-like lines (2 fix:) in the last 15 lines of install.out"
    )
    for named in ("20260921T100000Z-4242", ""):
        monkeypatch.setenv(setup_report.REPORT_RUN_ENV, named)
        _text, summary = summary_of(fake_mac)
        assert f"- installer output: {today}\n" in summary + "\n", named


def test_a_marked_folder_list_in_the_installer_output_names_no_folder(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """``install.sh --list-folders`` on a Mac that already syncs folders prints one line that counts them and
    a mark before each (field report 2026-10-07), and a mark of its own before a listed folder inside one
    and before one that holds one. A synced folder the list does not reach is printed first, with the path
    the config has: one deeper than the list goes, and one outside ~/Library/CloudStorage. All reach
    install.out, whose tail the report embeds: the count and the marks are kept, and every folder is a
    placeholder, a marked one like every other listed one and an unlisted one because it is a source."""
    write_install_log(fake_mac)
    write_friction(fake_mac)
    library, mine_too = fake_mac["one"].parents[1], fake_mac["one"].parent
    whole, other, unsynced = library / "Harbor Works", mine_too / "Quay Drafts", mine_too / "Open Berth"
    deep, notes = other / "Tide Tables", fake_mac["home"] / "Documents" / "Ledger Notes"
    for d in (whole / "Pier Nine", deep, unsynced, notes):
        d.mkdir(parents=True)
    for folder in (whole, deep, notes):
        assert cli.main(["add-source", str(folder), "--config", str(fake_mac["config"])]) == 0
    first = "already synced on this Mac: 5 folders (marked [synced] below: first the 2 outside the list, "
    keep = (
        "NEXT: this Mac already syncs 5 folders, and a re-run keeps them: run "
        f'{fake_mac["home"]}/src/agent-context-sync/scripts/install.sh, with one --source-local "<folder>" '
        "for each unmarked folder to add from the list above (none is needed)"
    )
    lines = [
        "# run=20260929T095805Z-11 2026-09-29T09:58:05Z install.sh --list-folders",
        first + "then the list)",
        f"[synced] {notes}",
        f"[synced] {deep}",
        f"[contains a synced folder] {mine_too}",
        f"[synced] {fake_mac['one']}",
        str(unsynced),
        f"[contains a synced folder] {other}",
        f"[synced] {whole}",
        f"[inside a synced folder] {whole / 'Pier Nine'}",
        f"[contains a synced folder] {fake_mac['two'].parent}",
        f"[synced] {fake_mac['two']}",
        keep,
    ]
    (fake_mac["setup"] / "install.out").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    shown = section(text, "Installer").split("install.out (", 1)[1].split("</summary>", 1)[1]
    assert f"\n{first}then the list)\n" in shown
    cloud, name = "~/Library/CloudStorage", r"<folder-\d>"
    mine, shared = f"{cloud}/OneDrive-<org-1>", f"{cloud}/OneDrive-SharedLibraries-<org-1>/<library-1>"
    for line in (
        rf"\[synced\] ~/Documents/{name}",  # outside ~/Library/CloudStorage
        rf"\[synced\] {mine}/{name}/{name}/{name}",  # deeper than the list goes
        rf"\[synced\] {mine}/{name}/{name}",
        rf"\[synced\] {mine}/{name}",
        rf"\[synced\] {shared}/{name}",
        rf"\[inside a synced folder\] {mine}/{name}/{name}",
        rf"\[contains a synced folder\] {mine}/{name}",
        rf"\[contains a synced folder\] {mine}/{name}/{name}",
        rf"\[contains a synced folder\] {shared}",
        rf"{mine}/{name}/{name}",  # the folder this Mac does not sync, a placeholder too
    ):
        assert len(re.findall(rf"(?m)^{line}$", shown)) == 1, line
    assert "NEXT: this Mac already syncs 5 folders, and a re-run keeps them: run ~/src/" in shown
    for raw in (*RAW, "Harbor", "Works", "Pier", "Nine", "Quay", "Drafts", "Tide", "Berth", "Ledger"):
        assert raw not in text, raw


# ---- field report 2026-10-06: leaks in the redacted section, one test per leak class ----------------------

PROJECT = "quay-ledger"
PROJECT_ID = "nw-client-alpha-mail"
"""A source id that embeds a registered folder name (``Client Alpha``): unregistered, the folder rule left it
half-redacted."""


def add_project_source(fake_mac: dict[str, Path]) -> Path:
    """An inbox source in a project checkout under the home folder (outside ~/Library/CloudStorage), and one
    with a generic id beside the docs repo."""
    path = fake_mac["home"] / "Development" / PROJECT / "docs-source" / "correspondence" / "mail"
    beside = fake_mac["home"] / "agent-context" / "mailbox"
    for d in (path, beside):
        d.mkdir(parents=True)
    cfg = fake_mac["config"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + f'\n[[source]]\nid = "{PROJECT_ID}"\nkind = "inbox"\npath = "{path}"\n'
        + f'\n[[source]]\nid = "mail"\nkind = "inbox"\npath = "{beside}"\n',
        encoding="utf-8",
    )
    return path


def test_shell_escaped_folder_names_are_redacted(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    """install.sh writes its arguments with ``printf %q``: a folder name's spaces arrive as ``\\ `` and a
    ``&`` or a bracket inside a word as ``\\&``, in install.log's ``args=`` and in install.out's header."""
    cloud = fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{ORG}"
    (cloud / "R&D Notes (old)").mkdir()
    one = str(fake_mac["one"]).replace(" ", "\\ ")
    args = f"--source-local {one} --source-local {cloud}/R\\&D\\ Notes\\ \\(old\\)"
    run = "20260929T100000Z-4242"
    (fake_mac["setup"] / "install.log").write_text(
        f"2026-09-29T10:00:00Z run={run} start install.sh commit=0123456789ab kind=checkout source=- "
        f"args={args}\n2026-09-29T10:00:57Z run={run} end rc=0 seconds=57\n",
        encoding="utf-8",
    )
    (fake_mac["setup"] / "install.out").write_text(
        f"# run={run} 2026-09-29T10:00:00Z install.sh {args}\nNEXT: re-run: scripts/install.sh {args}\n",
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    inst = section(text, "Installer")
    shown = "--source-local ~/Library/CloudStorage/OneDrive-<org-1>/<folder-1>/<folder-2> --source-local "
    assert inst.count("args=" + shown) == 1 and inst.count("install.sh " + shown) == 2
    for raw in ("Projects", "Alpha", "Notes", "R\\&D", "(old", "\\ "):
        assert raw not in text, raw
    red = setup_report.Redactor()
    red.add("folder", "R&D Notes (old)", fuzzy=True)
    assert (
        red.redact("R\\&D\\ Notes\\ \\(old\\) r&d-notes-(old) R&D Notes") == "<folder-1> <folder-1> R&D Notes"
    )
    red.add("folder", "Tide Tables/mail drop", ignore_case=True)  # a project path: one literal, any case
    assert (
        red.redact("~/Development/Tide\\ Tables/mail\\ drop and TIDE TABLES/MAIL DROP")
        == "~/Development/<folder-2> and <folder-2>"
    )


def test_a_source_local_argument_keeps_its_quotes_in_the_installer_embed() -> None:
    """B13 round 17 U10: the install.out embed, "what the agent saw", dropped every --source-local
    argument's quotes, so installer guidance that quotes a spaced folder (``--source-local "<folder>"``)
    read unquoted. The shown text keeps the word's quote style; ``$'...'`` is shown in double quotes, so
    ``$'`` never reaches the report; a quote left open keeps its opening one only; a bare word stays bare."""
    red = setup_report.Redactor()
    red.add("home", "/Users/jdoe")
    red.add("org", "Contoso")
    red.add("folder", "Project Beta - General", fuzzy=True)
    root = "/Users/jdoe/Library/CloudStorage/OneDrive-Contoso"
    shown = "~/Library/CloudStorage/OneDrive-<org-1>/<folder-1>"
    lines = [
        'NEXT: run install.sh --source-local "<folder>" (one --source-local per folder)',
        f'(one --source-local "{root}/Project Beta - General")',
        f"--source-local '{root}/Project Beta - General'",
        f"--source-local $'{root}/Project Beta - General'",
        f'--source-local "{root}/Project Beta - General',
        f"--source-local {root}/Project\\ Beta\\ -\\ General",
        "--source-local $HOME/Library/x",
    ]
    assert setup_report._redact_lines(red, lines).splitlines() == [
        'NEXT: run install.sh --source-local "<folder>" (one --source-local per folder)',
        f'(one --source-local "{shown}")',
        f"--source-local '{shown}'",
        f'--source-local "{shown}"',
        f'--source-local "{shown}',
        f"--source-local {shown}",
        "--source-local <path>",
    ]


def test_a_source_local_argument_never_shows_a_folder_the_redactor_does_not_know(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """bash 3.2's ``printf %q`` writes an argument holding a byte it finds non-printable as ``$'...'``: spaces
    bare, bytes as octal (every non-ASCII name under LC_ALL=C; an em dash even under UTF-8, its first byte
    left raw). No pattern for the plain name matches that. The argument is unquoted before it is matched,
    and a path component still unknown is cut to <path>: also a folder that no longer exists."""
    cloud = fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{ORG}"
    for name in ("Zürich Büro", "Tide — Tables"):
        (cloud / name).mkdir()
    base = str(cloud).encode()
    args = (
        b"--source-local $'" + base + b"/Z\\303\\274rich B\\303\\274ro'"
        b" --source-local $'" + base + b"/Tide \xe2\\200\\224 Tables'"
        b" --source-local " + base + b"/Gone\\ Folder/Lower\\ Deck"
    )
    run = b"20260929T100000Z-4242"
    (fake_mac["setup"] / "install.log").write_bytes(
        b"2026-09-29T10:00:00Z run=" + run + b" start install.sh commit=0123456789ab kind=checkout source=- "
        b"args=" + args + b"\n2026-09-29T10:00:57Z run=" + run + b" end rc=0 seconds=57\n"
    )
    (fake_mac["setup"] / "install.out").write_bytes(
        b"# run=" + run + b" 2026-09-29T10:00:00Z install.sh " + args + b"\n"
        b"NEXT: re-run: scripts/install.sh " + args + b"\n"
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    inst = section(text, "Installer")
    under = "--source-local ~/Library/CloudStorage/OneDrive-<org-1>/"
    # B13 round 17 U10: a $'...' word is shown in double quotes, as decoded; a bare one stays bare
    quoted = '--source-local "~/Library/CloudStorage/OneDrive-<org-1>/'
    shown = re.search(rf'args={quoted}(<folder-\d+>)" {quoted}<path>" {under}<path>\n', inst)
    assert shown, "the decoded name is a known folder; the other two are cut below the provider folder"
    assert inst.count(f'install.sh {quoted}{shown.group(1)}" {quoted}<path>" {under}<path>\n') == 2
    for raw in ("rich", "Tide", "Tables", "Gone", "Deck", "\\303", "\\200", "$'", "\ufffd"):
        assert raw not in text, raw

    red = setup_report.Redactor()
    red.add("home", "/Users/jdoe")
    red.add("folder", "Client Alpha", fuzzy=True)
    typed = (
        'ran install.sh --source-local "/Users/jdoe/Library/CloudStorage/Dropbox/Client Alpha/Old Bids" twice'
    )
    quoted = "I ran (`install.sh --source-local /Users/jdoe/Development/Client\\ Alpha/Notes\\ \\(old\\)`)."
    assert setup_report._redact_lines(red, [typed, "the --source-local option is unclear", quoted]) == (
        'ran install.sh --source-local "~/Library/CloudStorage/Dropbox/<folder-1>/<path>" twice\n'
        "the --source-local option is unclear\n"
        "I ran (`install.sh --source-local ~/Development/<folder-1>/<path>`)."
    )


def test_installer_output_log_lines_never_carry_a_nested_name(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """Directory names nested below a source are registered nowhere: agentsync's own WARNING and ERROR lines
    in the install.out tail get the Recent errors scrub, and so do the alarm and error lines of a failed
    sync's report, which install.sh prints in full. A quoted name is scrubbed whatever it looks like: a
    folder one level below the source root has no slash and no extension. install.sh's and git's own error
    lines keep their path or URL, and so does every other line (the tail is what the agent saw)."""
    (fake_mac["setup"] / "install.out").write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n"
        "2026-09-29 10:00:20,000 WARNING agentsync.arm_local: src-x: directory 'Wharf Plans/04 - Tide Tables/"
        "Old Charts' has zero children in a cloud tree; treated as unknown\n"
        "2026-09-29 10:00:21,000 ERROR agentsync.cycle: src-x: fetch of Wharf Plans/Lighthouse budget.xlsx "
        "failed: [Errno 89] Operation canceled\n"
        "2026-09-29 10:00:22,000 WARNING agentsync.arm_local: src-x: directory 'Mooring Ledger' is unknown "
        "(EPERM: Operation not permitted)\n"
        "2026-09-29 10:00:23,000 WARNING agentsync.arm_local: src-x: not crossing into another volume at "
        '"Pilot\'s Berth"\n'
        "  src-x: full · INCOMPLETE · cursor held\n"
        "    alarm: 2 unknown dir(s) — permission denied (TCC), provider error, or zero children in a cloud "
        "tree: 'Fabrikam Bids/Old', 'Tailspin'; enumeration incomplete, no deletions this pass\n"
        "    error: OSError: [Errno 13] Permission denied: 'Breakwater'\n"
        "fatal: unable to access 'https://example.com/agent-context-sync.git/': Could not resolve host\n"
        "error: the sync step failed; see ~/agent-context/setup/install.out\n"
        "[ok  ] disk.docs            — 120 GiB free at ~/agent-context/docs\n"
        "NEXT: review ~/agent-context/setup-report.md\n",
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    inst = section(text, "Installer")
    for name in ("Wharf", "Tide Tables", "Old Charts", "Lighthouse", "Mooring", "Berth", "Fabrikam"):
        assert name not in text, name
    assert "Tailspin" not in text and "Breakwater" not in text
    assert "src-x: directory '<path>' has zero children in a cloud tree; treated as unknown\n" in inst
    assert "src-x: directory '<path>' is unknown (EPERM: Operation not permitted)\n" in inst
    assert 'src-x: not crossing into another volume at "<path>"\n' in inst
    assert "zero children in a cloud tree: '<path>', '<path>'; enumeration incomplete, no deletions" in inst
    assert "    error: OSError: [Errno 13] Permission denied: '<path>'\n" in inst
    assert "  src-x: full · INCOMPLETE · cursor held\n" in inst
    assert "ERROR agentsync.cycle: src-x: <path>: [Errno 89] Operation canceled\n" in inst
    assert "fatal: unable to access 'https://example.com/agent-context-sync.git/': Could not resolve" in inst
    assert "error: the sync step failed; see ~/agent-context/setup/install.out\n" in inst
    assert "[ok  ] disk.docs — 120 GiB free at ~/agent-context/docs\n" in inst
    assert "WARNING and ERROR log lines, and a sync's alarm and error lines, show item paths and" in inst


def test_every_configured_source_id_is_a_placeholder(fake_mac: dict[str, Path]) -> None:
    """Only cloud folders' ids were registered: an inbox or project source's id was shown as typed, or
    half-redacted by a folder name inside it. A generic id (agentsync's own word) is kept, so ``graph_mail``
    and "the docs repo" stay readable."""
    add_project_source(fake_mac)

    def doctor(config: object) -> list[str]:
        return [
            f"[warn] heartbeat.{PROJECT_ID}    — no completed pass recorded yet",
            f"[warn] {'source.mail.sentinel':<{len(PROJECT_ID) + 13}} — no sentinel; graph_mail needs none; "
            "the docs repo is fine",
        ]  # padded as doctor pads: every name to the longest

    def status(config: object) -> list[str]:
        return [f"  {PROJECT_ID} (inbox): baseline complete", "  mail (inbox): baseline complete"]

    text, _summary = summary_of(fake_mac, doctor=doctor, status=status)
    assert PROJECT_ID not in text and "nw-" not in text and "-mail" not in text
    source = re.search(r"heartbeat\.(<source-\d+>) — no completed pass", section(text, "Doctor"))
    assert source, "the whole id is one placeholder, not a folder placeholder inside it; no padding is left"
    assert f"  {source.group(1)} (inbox): baseline complete" in section(text, "Status")
    assert "source.mail.sentinel — no sentinel; graph_mail needs none; the docs repo is fine" in text
    assert "  mail (inbox): baseline complete" in section(text, "Status")
    assert "sources: 5 (by kind: inbox 3, local 2" in section(text, "Configuration")


@pytest.mark.usefixtures("roomy_calls")
def test_a_project_path_under_the_home_folder_is_redacted(fake_mac: dict[str, Path]) -> None:
    """A source folder outside ~/Library/CloudStorage: the path from the project's folder down is one
    placeholder and the project's name another, so a sibling path does not show it either. Ordinary words in
    the path (``correspondence``) are not registered on their own."""
    path = add_project_source(fake_mac)
    sibling = path.parents[2] / "notes"

    def doctor(config: object) -> list[str]:
        return [
            f"[warn] source.x.listable — cannot list {path}",
            f"[warn] source.y.listable — cannot list {sibling}; internal correspondence is fine",
            "[warn] source.z.listable — cannot list ~/Development/Quay-Ledger/docs-source/correspondence/"
            "mail",
        ]

    text, _summary = summary_of(fake_mac, doctor=doctor)
    shown = section(text, "Doctor")
    assert PROJECT not in text.lower() and "docs-source" not in text
    whole = re.search(r"source\.x\.listable — cannot list ~/Development/(<folder-\d+>)\n", shown)
    name = re.search(r"cannot list ~/Development/(<folder-\d+>)/notes; internal correspondence is", shown)
    assert whole and name and whole.group(1) != name.group(1)
    assert f"source.z.listable — cannot list ~/Development/{whole.group(1)}\n" in shown, "any case, ~/ form"
    assert setup_report._LEGEND["folder"] in section(text, "Redaction"), (
        "the legend covers the folder it has just named outside CloudStorage"
    )
    assert "~/agent-context/setup/friction.md" in text, "agentsync's own folder is not a project"


def test_a_project_below_an_agent_named_or_shared_container_is_redacted(fake_mac: dict[str, Path]) -> None:
    """A clone folder named like a coding agent (GitHub Desktop's ~/Documents/GitHub) is a container, not the
    project: the project below it is registered and "GitHub" is not, so the agent's name stays readable. A
    source folder that itself has such a name is registered like any other. With a hand-set docs_repo
    straight in ~/Documents, a project beside it is not agentsync's own; the inbox kept there still is."""
    home = fake_mac["home"]
    clone = home / "Documents" / "GitHub" / PROJECT / "notes"
    named = home / "Development" / "Cursor"
    shared = home / "Documents" / "tide-tables" / "mail"
    cfg = fake_mac["config"]
    cfg.write_text(
        f'[agentsync]\ndocs_repo = "{home}/Documents/agent-docs"\n\n'
        + cfg.read_text(encoding="utf-8").replace("agent-context/inbox", "Documents/inbox")
        + "".join(
            f'\n[[source]]\nid = "{sid}"\nkind = "inbox"\npath = "{path}"\n'
            for sid, path in (("clone-notes", clone), ("cursor", named), ("tides", shared))
        ),
        encoding="utf-8",
    )
    assert setup_report._project_values(clone) == [f"{PROJECT}/notes", PROJECT]
    assert setup_report._project_values(named) == ["Cursor"]

    def doctor(config: object) -> list[str]:
        return [
            f"[warn] source.a.listable — cannot list {clone}",
            f"[warn] source.b.listable — cannot list {named}",
            f"[warn] source.c.listable — cannot list {shared}",
            f"[warn] source.d.listable — cannot list {home}/Documents/inbox",
        ]

    v6_install_log(fake_mac)
    write_friction(fake_mac, V7_HAPPY.replace("Claude Code, claude-opus-5-5", "GitHub Copilot CLI x"))
    text, summary = summary_of(fake_mac, doctor=doctor)
    shown = section(text, "Doctor")
    assert PROJECT not in text.lower() and "tide-tables" not in text
    assert re.search(r"source\.a\.listable — cannot list ~/Documents/GitHub/<folder-\d+>\n", shown)
    assert re.search(r"source\.b\.listable — cannot list ~/Development/<folder-\d+>\n", shown)
    assert re.search(r"source\.c\.listable — cannot list ~/Documents/<folder-\d+>\n", shown)
    assert "source.d.listable — cannot list ~/Documents/inbox\n" in shown, "the kept inbox is agentsync's"
    assert "· agent: GitHub Copilot CLI x\n" in summary


def test_friction_prose_gets_the_same_redaction(fake_mac: dict[str, Path]) -> None:
    """The agent's own words name what it synced: a source id, a shell-escaped folder name and a project
    path are replaced in the embedded log and in the Summary's item lines alike."""
    add_project_source(fake_mac)
    v6_install_log(fake_mac)
    said = (
        f"added {PROJECT_ID} from ~/Development/{PROJECT}/docs-source/correspondence/mail and "
        f"{FOLDERS[0]}/Client\\ Alpha"
    )
    write_friction(
        fake_mac,
        _insert_before(
            "2026-09-29T10:01:10Z | end",
            f"2026-09-29T10:01:00Z | step 2 | deviation | {said} | quote {PROJECT_ID} less\n",
            V7_HAPPY,
        ),
    )
    text, summary = summary_of(fake_mac)
    for raw in (PROJECT_ID, PROJECT, "docs-source", "Alpha", "Projects"):
        assert raw not in text, raw
    item = re.search(
        r"- F4 · step 2 · deviation · added (<source-\d+>) from ~/Development/<folder-\d+> and "
        r"<folder-1>/<folder-2> → quote (<source-\d+>) less",
        summary,
    )
    assert item and item.group(1) == item.group(2)
    assert f"| deviation | added {item.group(1)} from ~/Development/" in section(text, "Agent friction log")


# ---- setup prompt v6: install.sh --log-start / --log / --log-end, L3, L6-L10 ------------------------------


def test_the_summary_counts_the_installer_output_of_the_latest_attempts_runs_only(
    fake_mac: dict[str, Path],
) -> None:
    """v10 E3.b: install.out holds every run, earlier attempts' too, and a reader took the three ``fix:``
    lines in the Summary (one per install run read) as this attempt's. Every other Summary line is the
    latest attempt's, so this one counts from the attempt's first run header and says how many earlier runs
    it left out. The Installer section still counts every whole run read (B13 H21)."""
    old, new = "20261001T100000Z-11", "20261009T100100Z-22"
    write_install_log(fake_mac, start="2026-10-01T10:00:00Z", run=old)
    write_install_log(fake_mac, start="2026-10-09T10:01:00Z", run=new, append=True)
    first = V6_HAPPY.replace("2026-09-29T", "2026-10-01T")
    second = V10_HAPPY.replace("2026-09-29T09:58:00Z", "2026-10-09T09:59:00Z").replace(
        "2026-09-29T10:01:10Z", "2026-10-09T10:02:00Z"
    )
    write_friction(fake_mac, first + second)

    def block(run: str, fixes: int) -> list[str]:
        return [
            f"# run={run} {run[:4]}-{run[4:6]}-{run[6:8]}T10:00:00Z install.sh --source-local x",
            "what the installer printed",
            "and more of it",
            *["[warn] launchd.poll — com.agentsync.poll is not installed (fix: agentsync install-agent)"]
            * fixes,
            "NEXT: done",
        ]

    out = fake_mac["setup"] / "install.out"
    out.write_text("\n".join([*block(old, 2), *block(new, 1)]) + "\n", encoding="utf-8")
    whole = (
        "2 NEXT: lines in 2 runs (exactly one per install.sh run is expected) and 3 other "
        "instruction-like lines (3 fix:) in the last 11 lines of install.out"
    )
    text, summary = summary_of(fake_mac)
    assert f"Installer output: {whole}." in section(text, "Installer")
    assert (
        "- installer output: 1 NEXT: line in 1 run (exactly one per install.sh run is expected) and 1 "
        "other instruction-like line (1 fix:) in the last 5 lines of install.out (this attempt's runs "
        "only; the Installer section also counts the 1 earlier run in the lines read)\n" in summary
    )
    write_friction(fake_mac, first)  # attempt 1 is the latest and owns both runs
    _text, summary = summary_of(fake_mac)
    assert f"- installer output: {whole}\n" in summary and "this attempt's runs only" not in summary
    (fake_mac["setup"] / "install.log").write_text("", encoding="utf-8")  # no run to tell them apart by
    _text, summary = summary_of(fake_mac)
    assert f"- installer output: {whole}\n" in summary and "no run of this attempt" not in summary
    write_install_log(fake_mac, start="2026-10-01T10:00:00Z", run=old)
    write_install_log(fake_mac, start="2026-10-09T10:01:00Z", run=new, append=True)
    write_friction(fake_mac, first + second)
    out.write_text("\n".join(block(old, 2)) + "\n", encoding="utf-8")
    _text, summary = summary_of(fake_mac)
    assert (
        "- installer output: no run of this attempt in the last 6 lines of install.out (the Installer "
        "section counts the 1 earlier run in the lines read)\n" in summary
    )
    # B13 round 5 F20: the tail it embeds is not "what the agent saw" then, but earlier attempts' output
    saw = "<summary>the last 6 lines of ~/agent-context/setup/install.out ("
    text, _summary = summary_of(fake_mac)
    installer = section(text, "Installer")
    assert saw + "earlier attempts' install.sh output: none of this attempt's runs is in it)" in installer
    assert "(what the agent saw)" not in installer
    write_install_log(fake_mac, start="2026-10-01T10:00:00Z", run=old)  # a stopped copy: attempt 2 ran none
    text, summary = summary_of(fake_mac)
    assert "no run of this attempt" in summary
    installer = section(text, "Installer")
    assert saw + "earlier attempts' install.sh output: this attempt ran no install.sh)</summary>" in installer
    assert "(what the agent saw)" not in installer
    out.write_text("\n".join([*block(old, 2), *block(new, 1)]) + "\n", encoding="utf-8")
    write_install_log(fake_mac, start="2026-10-09T10:01:00Z", run=new, append=True)
    assert "(what the agent saw)</summary>" in section(summary_of(fake_mac)[0], "Installer"), "its own run"


V6_HAPPY = (
    "Attempt: 2026-09-29T09:58:00Z\n"
    "Prompt: v6\n"
    "Agent: Claude Code, claude-opus-5-5\n"
    "2026-09-29T10:01:10Z | end | finished\n"
)
"""Setup prompt v6 run through with nothing to log: install.sh --log-start's header, then --log-end's line.
The folder question and the announced Allow clicks are not logged (the prompt's kinds exclude them)."""

LIST_RUN = (
    "2026-09-29T09:58:05Z run=20260929T095805Z-11 start install.sh compat=6 commit=0123456789ab "
    "kind=checkout source=- launchd=simulated args=--list-folders\n"
    "2026-09-29T09:58:05Z run=20260929T095805Z-11 step=list-folders seconds=4 rc=0 result=done\n"
    "2026-09-29T09:58:09Z run=20260929T095805Z-11 end rc=0 seconds=4\n"
)


def v6_install_log(fake_mac: dict[str, Path], **kwargs: Any) -> Path:
    """install.log as install.sh writes it for prompt v6: step 1's --list-folders run, then step 2's install
    run (its first sync's note is the sync's counts; the report step follows the end line)."""
    kwargs.setdefault("first_sync", "seconds=34 rc=0 result=done note=converted-3-deferred-1")
    write_install_log(fake_mac, **kwargs)
    log = fake_mac["setup"] / "install.log"
    log.write_text(
        LIST_RUN + log.read_text(encoding="utf-8").replace("compat=5", "compat=6"), encoding="utf-8"
    )
    return log


@pytest.mark.usefixtures("clean_doctor")
def test_an_open_attempts_warning_names_the_command_that_ends_it_and_its_time_takes_in_install_log(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 7 H1, H2: before its end line, an attempt's report said to run ``agentsync setup-report``
    again, which closes no attempt (only ``install.sh --report-only`` writes the end line), and timed the
    session over the friction log alone: 6s beside a 57s install run. The WARNING now names install.sh's
    command as step 3's last one, never to be run before it, and the span takes in the attempt's install.log
    runs, "so far" while the attempt is open."""
    v6_install_log(fake_mac)
    write_friction(
        fake_mac,
        "Attempt: 2026-09-29T09:58:00Z\nPrompt: v15\nAgent: Claude Code, claude-opus-5-5\n"
        "2026-09-29T09:58:06Z | step 2 | deviation | waited for the install | -\n",
    )
    _text, summary = summary_of(fake_mac)
    [warning] = [ln for ln in summary.splitlines() if ln.startswith("- WARNING: attempt 1 ")]
    assert warning == (
        '- WARNING: attempt 1 has no "end | finished" line, so this report may be stale: it was written '
        "before prompt step 3 finished, or the agent stopped early. The attempt ends, and this report is "
        "rewritten, when step 3's last command `~/src/agent-context-sync/scripts/install.sh --report-only` "
        "runs; if the session stopped early, run that command (`~/.local/bin/agentsync setup-report` closes "
        "no attempt)."
    ), warning
    assert "setup-report` again" not in summary
    [time] = [ln for ln in summary.splitlines() if ln.startswith("- time: ")]
    assert time.startswith(
        "- time: session 2m57s so far (first to last timestamp, friction log and install.log); install.sh "
    ), time

    # With its end line, the friction log's span covers the runs: the wording is as before
    with (fake_mac["setup"] / "friction.md").open("a", encoding="utf-8") as fh:
        fh.write("2026-09-29T10:01:10Z | end | finished\n")
    _text, summary = summary_of(fake_mac)
    assert "- time: session 3m10s (first to last timestamp); install.sh (install.log): " in summary, summary
    assert "WARNING" not in summary


@pytest.mark.usefixtures("clean_doctor")
def test_v6_happy_path_is_fully_one_command(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    v6_install_log(fake_mac)
    write_friction(fake_mac, V6_HAPPY)
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    summary = section(text, "Summary")
    assert summary.strip().splitlines()[0] == (
        "- **outcome: fully one command** (computed: install.sh exit 0; no turn beyond the unavoidable ones)"
    )
    assert "- prompt: v6 · run: Sandbox with simulated launchd" in summary
    assert "- human turns: 1 (1 question; clicks: none possible; approvals: not observable)" in summary, (
        "the folder question is a turn although v6 does not log it (L9)"
    )
    assert (
        "- expected turns: the folder question (step 1; not logged as a question) · Allow clicks: none "
        "possible (launchd simulated)" in summary
    )
    assert "- friction (attempt" not in summary, "only its end line: it adds nothing (B13 round 12 N20)"
    assert f"- IT draft: {setup_report.IT_DRAFT} not written (prompt step 3:" in summary, "v6's step 3"
    assert "WARNING" not in summary, "--log-end's line closes the attempt"
    assert (
        "- time: session 3m10s (first to last timestamp); install.sh (install.log): step 1 --list-folders 4s "
        "· step 2 install 57s" in summary
    ), "v6 logs no step lines: install.log times the steps"
    assert (
        "- first sync: done in 34s (install.log) · converted 3, deferred 1 online-only (install.log; "
        "background sync downloads and converts the 1)" in summary
    ), "L3: what the first sync converted and what it left to background sync"
    assert "- install.sh: 1 install run (+1 --list-folders) during this attempt; the last exit 0" in summary
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["outcome"] == ["Fully one command"] and link["prompt_version"] == ["v6"]
    inst = section(text, "Installer")
    assert "The report step is logged after this run's end line" in inst, "the ordering is explained (V4)"
    fr = section(text, "Agent friction log")
    assert "  4  2026-09-29T10:01:10Z | end | finished" in fr
    fail = setup_report.Outcome("failed", 2, (), 6)
    assert fail.text == "failed at step 2" and fail.form_label == "Failed at step 2 (install and start)"


@pytest.mark.usefixtures("clean_doctor")
def test_first_sync_clause_carries_the_read_again_count(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    """The installer's first-sync note ends ``-reread-R`` when its sync read R files again (R > 0), and the
    clause shows ``, read again R``; a note without it (R = 0, or an older install.log) reads as before.  With
    no note, the sync's own line in install.out gives the counts, its ``read again R`` too."""
    write_friction(fake_mac, V6_HAPPY)
    for note, out, want in (
        (
            "note=converted-0-deferred-0-reread-2",
            "",
            "converted 0, deferred 0 online-only, read again 2 (install.log)",
        ),
        ("note=converted-3-deferred-0", "", "converted 3, deferred 0 online-only (install.log)"),
        ("", "first sync: converted 1, deferred 0 online-only, read again 4", "read again 4 (install.out)"),
        (  # §16.45 R2: install.log's note has no breakdown; install.out's line of the same sync does
            "note=converted-3-deferred-0",
            "first sync: converted 3, deferred 0 online-only (of the converted: 1 unreadable, 0 failed)",
            # B13 round 4 Q1: the source tag names install.out for the split, which install.log's note lacks
            "converted 3, deferred 0 online-only; of the converted: 1 unreadable, 0 failed "
            "(install.log; the unreadable/failed split: install.out)",
        ),
        (  # B13 round 5 F8, F32: both clauses of the sync's line (F34's form), and the first read is kept
            "note=converted-2-deferred-0-reread-2",
            "first sync: converted 2, deferred 0 online-only, read again 2 (2 of them a first read: a stub "
            "whose bytes had never been read, now in the count above); of the converted: 1 unreadable, 0 "
            "failed",
            # B13 round 10 L34: every file read again was a first read, so the head says "first read"
            "converted 2, deferred 0 online-only, first read 2: stubs whose bytes had never been read, also "
            "in the converted count; of the converted: 1 unreadable, 0 failed (install.log; the first-read "
            "count and the unreadable/failed split: install.out)",
        ),
        (  # B13 round 10 L16: the bracket names the converted count; the same two clauses (L34's head)
            "note=converted-2-deferred-0-reread-2",
            "first sync: converted 2, deferred 0 online-only, read again 2 (2 of them a first read: a stub "
            "whose bytes had never been read, so also counted as converted); of the converted: 1 unreadable, "
            "0 failed",
            "converted 2, deferred 0 online-only, first read 2: stubs whose bytes had never been read, also "
            "in the converted count; of the converted: 1 unreadable, 0 failed (install.log; the first-read "
            "count and the unreadable/failed split: install.out)",
        ),
        (  # an older agentsync's two brackets in a row: the same two clauses
            "note=converted-2-deferred-0-reread-2",
            "first sync: converted 2, deferred 0 online-only, read again 2 (2 of them read for the first "
            "time, also counted above) (of the converted: 1 unreadable, 0 failed)",
            "first read 2: stubs whose bytes had never been read, also in the converted count; of the "
            "converted: 1 unreadable, 0 failed (install.log; the first-read count and the unreadable/failed "
            "split: install.out)",
        ),
        (  # the first read alone; nothing else changes
            "",
            "first sync: converted 1, deferred 0 online-only, read again 1 (1 of them a first read: a stub "
            "whose bytes had never been read, now in the count above)",
            "converted 1, deferred 0 online-only, first read 1: a stub whose bytes had never been read, also "
            "in the converted count (install.out)",
        ),
        (  # B13 round 10 L34: the CLI's own first-read head, from install.out and with install.log's note
            "",
            "first sync: converted 2, deferred 0 online-only, first read 2 (stubs whose bytes had never been "
            "read, now in the count above); of the converted: 1 unreadable, 0 failed, 0 refused",
            "converted 2, deferred 0 online-only, first read 2: stubs whose bytes had never been read, also "
            "in the converted count; of the converted: 1 unreadable, 0 failed (install.out)",
        ),
        (
            "note=converted-2-deferred-0-reread-2",
            "first sync: converted 2, deferred 0 online-only, first read 2 (stubs whose bytes had never been "
            "read, now in the count above)",
            "converted 2, deferred 0 online-only, first read 2: stubs whose bytes had never been read, also "
            "in the converted count (install.log; the first-read count: install.out)",
        ),
        (  # some read again, some a first read: "read again" holds, and no comma before "of a stub"
            "note=converted-2-deferred-0-reread-3",
            "first sync: converted 2, deferred 0 online-only, read again 3 (1 of them a first read: a stub "
            "whose bytes had never been read, now in the count above)",
            "converted 2, deferred 0 online-only, read again 3: 1 of them a first read of a stub whose bytes "
            "had never been read, also in the converted count (install.log; the first-read count: "
            "install.out)",
        ),
        (  # B13 round 9 K34: an older agentsync's words in install.out still give the first-read count
            "",
            "first sync: converted 1, deferred 0 online-only, read again 1 (1 of them read for the first "
            "time, also counted above)",
            "converted 1, deferred 0 online-only, first read 1: a stub whose bytes had never been read, also "
            "in the converted count (install.out)",
        ),
        (  # B13 round 6 G35: the refused count, and a split with no unreadable stub, are kept; the tag
            # names refused once its count is printed (round 7 H46)
            "note=converted-4-deferred-0",
            "first sync: converted 4, deferred 0 online-only (of the converted: 0 unreadable, 0 failed, 3 "
            "refused)",
            "converted 4, deferred 0 online-only; of the converted: 0 unreadable, 0 failed, 3 refused "
            "(install.log; the unreadable/failed/refused split: install.out)",
        ),
        (  # B13 round 11 M82: K of the R refused say withheld, as on the CLI's line (K + W say withheld)
            "note=converted-4-deferred-0",
            "first sync: converted 4, deferred 0 online-only (of the converted: 0 unreadable, 0 failed, 1 "
            "refused, 1 of them withheld by the label rule); 1 withheld unread by the label rule (a file "
            "whose label cannot be read: an image, or a .msg not on this Mac)",
            "converted 4, deferred 0 online-only; of the converted: 0 unreadable, 0 failed, 1 refused, 1 of "
            "them withheld by the label rule; 1 withheld unread by the label rule (install.log; the "
            "unreadable/failed/refused split and the withheld-unread count: install.out)",
        ),
        (  # B13 round 8 J39: the files refused without a read, after the split as on the CLI's line
            "note=converted-4-deferred-0",
            "first sync: converted 4, deferred 0 online-only (of the converted: 0 unreadable, 0 failed, 3 "
            "refused); 5 refused without a read (no converter for the type, or a rule that needs no bytes)",
            "converted 4, deferred 0 online-only; of the converted: 0 unreadable, 0 failed, 3 refused; 5 "
            "refused without a read (install.log; the unreadable/failed/refused split and the "
            "refused-without-a-read count: install.out)",
        ),
        (  # B13 round 8 J29's clause before it, the files a label rule withheld unread: carried too
            "note=converted-4-deferred-0",
            "first sync: converted 4, deferred 0 online-only; 2 withheld unread by the label rule (a file "
            "whose label cannot be read: an image, or a .msg not on this Mac); 5 refused "
            "without a read (no converter for the type, or a rule that needs no bytes)",
            "converted 4, deferred 0 online-only; 2 withheld unread by the label rule; 5 refused without a "
            "read (install.log; the withheld-unread count and the refused-without-a-read count: install.out)",
        ),
        (  # the count alone, with no split
            "note=converted-4-deferred-0",
            "first sync: converted 4, deferred 0 online-only; 5 refused without a read (no converter for the "
            "type, or a rule that needs no bytes)",
            "converted 4, deferred 0 online-only; 5 refused without a read (install.log; the "
            "refused-without-a-read count: install.out)",
        ),
        (  # another sync's line (its converted count differs) is not this one's
            "note=converted-3-deferred-0",
            "first sync: converted 4, deferred 0 online-only; 5 refused without a read (no converter)",
            "converted 3, deferred 0 online-only (install.log)",
        ),
        (  # a first-read count of another sync (its read-again count differs) is not this one's
            "note=converted-1-deferred-0-reread-3",
            "first sync: converted 1, deferred 0 online-only, read again 1 (1 of them read for the first "
            "time, also counted above)",
            "converted 1, deferred 0 online-only, read again 3 (install.log)",
        ),
    ):
        v6_install_log(fake_mac, first_sync=f"seconds=9 rc=0 result=done {note}".rstrip())
        (fake_mac["setup"] / "install.out").write_text(
            f"# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh\n{out}\n", encoding="utf-8"
        )
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- first sync: ")]
        assert line.startswith("- first sync: done in 9s (install.log) · converted ") and want in line, line
        assert ") (" not in line, "B13 round 6 G34: the source tag is the clause's one bracket"


def test_the_converted_line_parses_with_the_read_again_clause_in_either_form() -> None:
    """B13 round 5 F34: with the Q28 clause, sync now joins the split with "; " so two parentheses never
    stand side by side; an earlier sync's two-parenthesis line persists in install.out. Both give the
    counts and the split, which the old pattern lost after the Q28 clause."""
    for line in (
        "converted 3, deferred 0 online-only, read again 2 (2 of them a first read: a stub whose bytes had "
        "never been read, now in the count above); of the converted: 1 unreadable, 0 failed",
        "converted 3, deferred 0 online-only, read again 2 (2 of them read for the first time, also counted "
        "above); of the converted: 1 unreadable, 0 failed",
        "converted 3, deferred 0 online-only, read again 2 (2 of them read for the first time, also counted "
        "above) (of the converted: 1 unreadable, 0 failed)",
        "converted 3, deferred 0 online-only, read again 2 (of the converted: 1 unreadable, 0 failed)",
        "converted 3, deferred 0 online-only (of the converted: 1 unreadable, 0 failed)",
    ):
        match = setup_report._CONVERTED_LINE_RE.search(line)
        assert match is not None and match.group("c", "d") == ("3", "0"), line
        split = setup_report._SPLIT_RE.search(match.group("tail"))
        assert split is not None and split.groups() == ("1", "0", None, None), line
    match = setup_report._CONVERTED_LINE_RE.search("converted 1, deferred 0 online-only, read again 4")
    assert match is not None and match.group("c", "d", "r", "tail") == ("1", "0", "4", "")
    assert setup_report._SPLIT_RE.search(match.group("tail")) is None
    # B13 round 10 L34: R after "first read" when every file read again was a first read
    match = setup_report._CONVERTED_LINE_RE.search(
        "converted 3, deferred 0 online-only, first read 2 (stubs whose bytes had never been read, now in "
        "the count above); of the converted: 1 unreadable, 0 failed"
    )
    assert match is not None and match.group("c", "d", "r", "r1") == ("3", "0", None, "2")
    split = setup_report._SPLIT_RE.search(match.group("tail"))
    assert split is not None and split.groups() == ("1", "0", None, None)


@pytest.mark.usefixtures("clean_doctor")
def test_first_sync_clause_says_the_sync_waited_for_another(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """install.sh ends the first-sync note ``-waited-for-the-lock`` when another sync held the config's lock
    as it started: the counts still read, and the seconds are named as a wait (B13 round 4 Q29)."""
    write_friction(fake_mac, V6_HAPPY)
    for note, want in (
        (
            "converted-0-deferred-0-waited-for-the-lock",
            "- first sync: done in 9s, after waiting for another sync to end (install.log) · converted 0, "
            "deferred 0 online-only",
        ),
        (
            "converted-2-deferred-1-reread-3-waited-for-the-lock",
            "- first sync: done in 9s, after waiting for another sync to end (install.log) · converted 2, "
            "deferred 1 online-only, read again 3",
        ),
    ):
        v6_install_log(fake_mac, first_sync=f"seconds=9 rc=0 result=done note={note}")
        (fake_mac["setup"] / "install.out").write_text("", encoding="utf-8")
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- first sync: ")]
        assert line.startswith(want), line
    # The note alone, when the sync printed no counts (B13 round 5 F59): no raw token; install.out's counts
    v6_install_log(fake_mac, first_sync="seconds=9 rc=0 result=done note=waited-for-the-lock")
    (fake_mac["setup"] / "install.out").write_text("converted 4, deferred 2 online-only\n", encoding="utf-8")
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- first sync: ")]
    assert line.startswith(
        "- first sync: done in 9s, after waiting for another sync to end (install.log) · "
    ), line
    assert "(waited-for-the-lock)" not in line and "converted 4, deferred 2 online-only" in line, line


def test_a_prompt_newer_than_the_reports_agentsync_says_so(fake_mac: dict[str, Path]) -> None:
    """B13 round 7 H23: a scenario-D Mac's report (the pasted prompt newer than the installed agentsync, whose
    step names and checks it then uses) said nothing of it. The prompt line says the prompt is newer than this
    report's agentsync, naming no cause (the pull may have worked, or never run), the run metadata names the
    build's prompt, and the issue keeps the attempt's version."""
    v6_install_log(fake_mac, rc=1)
    newer, mine = setup_report.PROMPT_VERSION + 1, setup_report.PROMPT_VERSION
    note = f"newer than this report's agentsync, which is for prompt v{mine}"

    def report(friction: str) -> tuple[str, str]:
        write_friction(fake_mac, friction)
        now = datetime(2026, 9, 29, 10, 30, tzinfo=UTC)
        text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks(), now=now)
        return text, section(text, "Summary")

    stated = f"2026-09-29T10:20:00Z | step 1 | error | prompt v{newer}, Contoso Bot: git pull failed | -\n"
    header = f"Attempt: 2026-09-29T09:58:00Z\nPrompt: v{newer}\nAgent: Contoso Bot\n"
    for friction, prompt in (
        # B13 round 8 J23, J40: one bracket, where the two notes had one each, side by side
        (stated, f"- prompt: v{newer} (from the step 1 error line; {note}) · run: "),
        (header, f"- prompt: v{newer} ({note}) · run: "),
    ):
        text, summary = report(friction)
        assert prompt in summary, summary
        assert ") (" not in summary.split("\n- prompt: ", 1)[1].split(" · run: ", 1)[0]
        assert "pull" not in summary.split("\n- prompt: ", 1)[1].split(" · run: ", 1)[0]
        link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
        assert link["title"][0].endswith(f" · v{newer}") and link["prompt_version"] == [f"v{newer}"], link
        assert f"- agentsync: {setup_report.__version__}, setup prompt v{mine} (python " in text
    _text, summary = report(header.replace(f"v{newer}", f"v{mine}"))
    assert f"- prompt: v{mine} · run: " in summary and "newer than this report" not in summary


@pytest.mark.usefixtures("clean_doctor")
def test_a_first_sync_that_printed_no_counts_borrows_none_from_an_earlier_run(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 7 H29: a skipped first sync (lock-busy, no-sources) prints no counts, and nor does one whose
    note has none, so the line took install.out's last counts, an earlier run's, as its own ("skipped in 600s,
    rc 75 (lock-busy) · converted 7"). The counts are read only from the shown run's own output."""
    earlier, later = "20260929T100000Z-4242", "20260929T101000Z-4343"
    out = (
        f"# run={earlier} 2026-09-29T10:00:00Z install.sh --source-local x\n"
        "sync: converted 7, deferred 3 online-only\n"
        f"# run={later} 2026-09-29T10:10:00Z install.sh --source-local x\n"
    )
    for first_sync, printed in (
        ("seconds=600 rc=75 result=skipped note=lock-busy", "sync: skipped, lock busy\n"),
        ("seconds=0 rc=0 result=skipped note=no-sources", ""),
        ("seconds=9 rc=0 result=done", ""),
    ):
        write_install_log(fake_mac, first_sync="seconds=34 rc=0 result=done note=converted-7-deferred-3")
        write_install_log(
            fake_mac, start="2026-09-29T10:10:00Z", run=later, first_sync=first_sync, append=True
        )
        (fake_mac["setup"] / "install.out").write_text(out + printed, encoding="utf-8")
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- first sync: ")]
        assert "converted 7" not in line and "deferred 3" not in line, (first_sync, line)

    # a run whose own counts are under its own header still shows them (an older note with none)
    (fake_mac["setup"] / "install.out").write_text(
        out + "sync: converted 2, deferred 1 online-only\n", encoding="utf-8"
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- first sync: ")]
    assert line.startswith(
        "- first sync: done in 9s (install.log) · converted 2, deferred 1 online-only (install.out"
    ), line


def _n25_run(minute: int, config: str | None, first_sync: str) -> str:
    """One install.sh run in install.log: its start, uv, the config step (none when ``config`` is None),
    status, first-sync and end lines."""
    at, run = f"2026-09-29T09:{minute:02d}:00Z", f"20260929T09{minute:02d}00Z-{4200 + minute}"
    lines = [
        f"{at} run={run} start install.sh compat=6 commit=0123456789ab kind=checkout source=- "
        "launchd=simulated args=--source-local x",
        f"{at} run={run} step=uv seconds=0 rc=0 result=skipped note=present",
        *([f"{at} run={run} step=config {config}"] if config is not None else []),
        f"{at} run={run} step=status seconds=1 rc=0 result=done",
        f"{at} run={run} step=first-sync {first_sync}",
        f"{at} run={run} end rc={80 if 'rc=80' in first_sync else 0} seconds=20",
    ]
    return "".join(f"{line}\n" for line in lines)


_N25_DONE = "seconds=9 rc=0 result=done note=converted-6-deferred-0"


@pytest.mark.usefixtures("clean_doctor")
@pytest.mark.parametrize(
    ("runs", "want"),
    [
        ([(59, "seconds=0 rc=0 result=done note=created kept=0 added=1", _N25_DONE)], "first sync"),
        ([(59, "seconds=0 rc=0 result=done note=add-source kept=2 added=0", _N25_DONE)], "sync"),
        ([(59, "seconds=0 rc=0 result=skipped note=exists kept=2 added=0", _N25_DONE)], "sync"),
        ([(59, None, _N25_DONE)], "first sync"),
        (
            [
                (
                    58,
                    "seconds=0 rc=0 result=done note=created kept=0 added=1",
                    "seconds=3 rc=80 result=failed",
                ),
                (59, "seconds=0 rc=0 result=skipped note=exists kept=1 added=0", _N25_DONE),
            ],
            "sync",
        ),
    ],
    ids=["created", "add-source", "exists", "no-config-step", "created-then-exists"],
)
def test_the_first_sync_line_says_sync_when_the_config_existed(
    fake_mac: dict[str, Path], tmp_path: Path, runs: list[tuple[int, str | None, str]], want: str
) -> None:
    """install.sh says ``sync:`` rather than ``first sync:`` when sources.toml was there before its run (its
    config step logs ``add-source`` or ``exists``), and the Summary's line now says the same, from the run
    whose first-sync step it reads (N25). ``created``, or no config step, keeps ``first sync``."""
    write_friction(fake_mac, V6_HAPPY)
    log = "".join(_n25_run(minute, config, sync) for minute, config, sync in runs)
    (fake_mac["setup"] / "install.log").write_text(log, encoding="utf-8")
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    lines = [
        ln for ln in section(text, "Summary").splitlines() if ln.startswith(("- first sync: ", "- sync: "))
    ]
    assert len(lines) == 1, lines
    assert lines[0].startswith(f"- {want}: done in 9s (install.log) · converted 6, deferred 0 "), lines[0]


V7_HAPPY = V6_HAPPY.replace("Prompt: v6", "Prompt: v7")
"""Setup prompt v7 run through with nothing to log (KISS K04/K16b): the same header and closing line as v6."""

V8_HAPPY = V7_HAPPY.replace("Prompt: v7", "Prompt: v8")
"""Setup prompt v8: v7's three steps and rules. Its ``Prompt:`` line is the version the pasted copy gave
``install.sh --log-start``, so a saved v7 copy still reads ``v7`` (or ``v7 or older``)."""

V9_HAPPY = V8_HAPPY.replace("Prompt: v8", "Prompt: v9")
"""Setup prompt v9: the same steps and rules. On a Mac that already syncs folders its step 1 asks at most
whether to add one, and with nobody to ask it goes on."""

V10_HAPPY = V9_HAPPY.replace("Prompt: v9", "Prompt: v10")
"""Setup prompt v10: the same steps and rules, three sentences reworded."""

V11_HAPPY = V10_HAPPY.replace("Prompt: v10", "Prompt: v11")
"""Setup prompt v11: the same steps and rules, wording only (B13)."""

V12_HAPPY = V11_HAPPY.replace("Prompt: v11", "Prompt: v12")
"""Setup prompt v12: the same steps and rules, wording only (B13 round 3)."""


def test_prompt_layout_is_picked_by_explicit_version() -> None:
    """KISS K16b: v5 and earlier read as v5, v6 as v6, v7 as v7. The version moves with every change of the
    prompt's text and a layout only when a step moves, so v8 to v28 and every later version are read with
    the newest layout at or below them, as an attempt that states no version is."""
    versions = (
        0,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        None,
        99,
    )
    read_as = [setup_report.prompt_layout(v).version for v in versions]
    assert read_as == [5, 5, 5, 6, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7]
    assert (
        setup_report.PROMPT_VERSION == 28 and setup_report.PROMPT_VERSION not in setup_report.PROMPT_LAYOUTS
    )
    for version in (
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
    ):  # none moved a step: each gets v7's every rule
        assert setup_report.prompt_layout(version) is setup_report.PROMPT_LAYOUTS[7]
    v6, v7 = setup_report.PROMPT_LAYOUTS[6], setup_report.PROMPT_LAYOUTS[7]
    assert (v7.install_step, v7.report_step) == (v6.install_step, v6.report_step) == (2, 3)
    assert max(v7.steps) == 3 and max(v6.steps) == 4
    # B13 round 7 H11: the field names the step the person had, now an option of the form; v6 keeps its own
    for version in (7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18):
        label = setup_report.Outcome("failed", 3, (), version).form_label
        assert label == "Failed at step 3 (sync loop and report)", version
        assert label in setup_report.FORM_OUTCOME_OPTIONS
    assert setup_report.Outcome("failed", 3, ()).form_label == label, "the default is this build's prompt"
    v6_label = setup_report.Outcome("failed", 3, (), 6).form_label
    assert (
        v6_label == "Failed at step 3 (IT request and report)"
        and v6_label in setup_report.FORM_OUTCOME_OPTIONS
    )
    assert setup_report.Outcome("failed", 4, (), 5).form_label == "Failed at step 3 (IT request and report)"
    # B13 round 6 G12: the title names the attempt's own step, and since round 7 H11 so does the field
    for version in (7, 14, None):
        failed = setup_report.Outcome("failed", 3, (), version)
        assert failed.title_label == "Failed at step 3 (sync loop and report)"
        link = setup_report.build_issue_url(
            outcome=failed.form_label,
            title_outcome=failed.title_label,
            run_type="Sandbox",
            prompt="v14",
            agent=None,
        )
        query = parse_qs(urlsplit(link).query)
        assert query["title"] == ["Setup report: Failed at step 3 (sync loop and report) · Sandbox · v14"]
        assert query[setup_report.ISSUE_FIELDS["outcome"]] == ["Failed at step 3 (sync loop and report)"]
    for step, name in setup_report.PROMPT_STEPS.items():
        assert setup_report.Outcome("failed", step, (), 6).title_label == f"Failed at step {step} ({name})"
    assert setup_report.Outcome("fully one command", None, (), 14).title_label == "Fully one command"


V8_BUSY_LINES = (
    "2026-09-29T09:58:30Z | step 1 | question | asked which terminal app this is | say it\n"
    "2026-09-29T10:00:20Z | step 2 | click | clicked Allow on a Keychain prompt | -\n"
    "2026-09-29T10:01:00Z | step 2 | prompt | the prompt does not say what a second run prints | say it\n"
    "2026-09-29T10:01:00Z | step 2 | error | Exit 0, no failure: the link shows a placeholder | -\n"
    "2026-09-29T10:01:00Z | step 3 | deviation | wrote the questions in three parts | -\n"
)
"""Lines that meet each rule keyed on the prompt version: a question and a click beyond the expected turns,
and a step 2 error logged after install.sh exited 0, which is agent friction (v6 and v7 alike)."""


@pytest.mark.usefixtures("clean_doctor", "roomy_calls")
def test_a_v8_attempt_is_judged_like_v7_and_a_version_alone_says_nothing_about_the_copy(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """The same log under ``Prompt: v7``, ``Prompt: v8`` and ``Prompt: v9``: the outcome, the turns, the
    expected turns, the agent friction, the times and the missing IT line are the same, line for line, and
    the Summary's prompt line differs by the number alone. The issue link carries each attempt's own version.

    The line once added "older than this installer's v8: the pasted copy was not the current README" to
    every attempt below this build's number. But a v7 installer wrote "Prompt: v7" from its own number, so
    a Mac set up with the then-current v7 prompt, whose report is written again by this build, was called
    a stale copy. And "newer than this installer's" was said of a v9 attempt a v9 installer had accepted,
    by the v8 agentsync an earlier install left (what ``install.sh --report-only`` runs). The number is
    no evidence either way; the installer's stop line is (the next test)."""
    v6_install_log(fake_mac, simulated=False, agent="seconds=2 rc=0 result=done")
    busy = _insert_before("2026-09-29T10:01:10Z | end", V8_BUSY_LINES, V7_HAPPY)
    summaries: dict[int, list[str]] = {}
    links: dict[int, dict[str, list[str]]] = {}
    for version in (7, 8, 9):
        friction = busy.replace("Prompt: v7", f"Prompt: v{version}")
        write_friction(fake_mac, friction)
        rc, text, _ = report(tmp_path, fake_mac["config"])
        assert rc == 0
        body = section(text, "Summary").split(setup_report.RUN_METADATA_HEADING, 1)[0]
        summaries[version] = body.strip().splitlines()
        links[version] = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
        [attempt] = setup_report.parse_friction(friction).attempts
        assert attempt.version == version and attempt.layout is setup_report.PROMPT_LAYOUTS[7]
        runs = setup_report.read_install_runs(fake_mac["setup"] / "install.log")
        assert setup_report.stopping_error(attempt, runs) is None, "an install run that ended 0 resolves it"
        assert setup_report.compute_outcome(attempt, runs) == setup_report.Outcome(
            "worked with help",
            None,
            (
                "install.sh exit 0",
                "1 question beyond the folder question",
                "1 click beyond the Allow clicks",
            ),
            7,
        )

    def prompt_line(version: int) -> str:
        [line] = [ln for ln in summaries[version] if ln.startswith("- prompt: ")]
        return line

    assert prompt_line(8).startswith("- prompt: v8 · run: ")
    for version in (7, 9):
        assert prompt_line(version) == prompt_line(8).replace("- prompt: v8 · ", f"- prompt: v{version} · ")
        assert "older than" not in prompt_line(version) and "installer" not in prompt_line(version)
    same = [[ln for ln in summaries[v] if not ln.startswith("- prompt: ")] for v in (7, 8, 9)]
    assert same[0] == same[1] == same[2] and len(same[0]) > 10
    said = "\n".join(same[1])
    assert "- **outcome: worked with help** (computed: install.sh exit 0; 1 question beyond" in said
    assert "- expected turns: the folder question (step 1; not logged as a question) · " in said
    assert "- agent friction: 1 deviation, 1 prompt, 1 error (F6, F7, F8); none of these changes" in said
    assert "IT draft" not in said, "since v7 there is no IT request step"
    assert links[8]["prompt_version"] == ["v8"] and links[7]["prompt_version"] == ["v7"]
    assert {k: v for k, v in links[7].items() if k not in ("prompt_version", "title")} == {
        k: v for k, v in links[8].items() if k not in ("prompt_version", "title")
    }


@pytest.mark.usefixtures("clean_doctor")
def test_a_stopped_log_start_value_the_session_ran_again_is_a_rerun_not_a_failure(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 8 J9: an agent that changed the --log-start value (v61 for v16) is stopped, told to run the
    command again as written, and does, seconds later. The report kept that attempt as "failed at step 1",
    which blames the checkout or the copy. It reads "stopped at step 1 (rerun: ...)" when the next attempt
    names the stopping installer's own version and the same agent within two minutes. A copy fetched again
    and pasted into a new session (the remedy for a stale copy) stays a failure, as does a next attempt of
    another version or another agent. B13 round 9 K11: a stale copy fetched again took 23 s, inside the
    window, so the stop of an older copy, which reads both ways, stays a failure whose note names both
    readings; only a copy newer than the installer, which no fresh copy explains, is a rerun."""
    mine = setup_report.PROMPT_VERSION
    (fake_mac["setup"] / "install.log").unlink()
    stop = (
        "Attempt: 2026-09-29T09:58:00Z\nPrompt: {said}\nAgent: {agent}\n"
        "2026-09-29T09:58:00Z | step 1 | error | install.sh --log-start: the pasted setup prompt is {said} "
        "and this installer is for setup prompt v{installer}: {why}; setup stopped (exit 2) | copy the "
        "prompt again from README.md on the main branch\n"
        "2026-09-29T09:58:00Z | end | finished\n"
    )
    newer = "this checkout is older than the prompt, or the --log-start value was changed"
    older = "the pasted copy is not the current one"
    again = "Attempt: {at}\nPrompt: v{version}\nAgent: {agent}\n"
    rerun = "stopped at step 1 (rerun: --log-start said {said}; the next attempt, {gap} later, said v{to})"
    both = (
        # the stopped attempt's own causes, then the next attempt (B13 round 10 L21)
        "failed at step 1 (an older copy, or a changed --log-start value; the next attempt, {gap} later, "
        "said v{to})"
    )
    for said, why, at, version, agent, earlier in (
        (
            "v61",
            newer,
            "2026-09-29T09:58:18Z",
            mine,
            "Contoso Bot",
            rerun.format(said="v61", gap="18s", to=mine),
        ),
        (
            "v61",
            newer,
            "2026-09-29T10:00:00Z",
            mine,
            "Contoso Bot",
            rerun.format(said="v61", gap="2m00s", to=mine),
        ),
        (
            "v7 or older",
            older,
            "2026-09-29T09:58:18Z",
            mine,
            "Contoso Bot",
            both.format(gap="18s", to=mine),
        ),
        # an older copy, then the installer's version 1 s or 23 s later: a corrected value or a fresh copy
        (f"v{mine - 1}", older, "2026-09-29T09:58:01Z", mine, "Contoso Bot", both.format(gap="1s", to=mine)),
        (f"v{mine - 1}", older, "2026-09-29T09:58:23Z", mine, "Contoso Bot", both.format(gap="23s", to=mine)),
        (f"v{mine - 1}", older, "2026-09-29T10:00:01Z", mine, "Contoso Bot", "failed at step 1"),
        # copied again from the README and pasted into a new session: a real failure
        (f"v{mine - 1}", older, "2026-09-29T10:13:00Z", mine, "Contoso Bot", "failed at step 1"),
        ("v61", newer, "2026-09-29T10:00:01Z", mine, "Contoso Bot", "failed at step 1"),
        # the checkout was updated and the same v61 pasted again: not the stopping installer's version
        ("v61", newer, "2026-09-29T09:58:18Z", 61, "Contoso Bot", "failed at step 1"),
        ("v61", newer, "2026-09-29T09:58:18Z", mine, "Fabrikam Bot", "failed at step 1"),
    ):
        write_friction(
            fake_mac,
            stop.format(said=said, agent="Contoso Bot", installer=mine, why=why)
            + again.format(at=at, version=version, agent=agent),
        )
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        summary = section(text, "Summary")
        assert f"- attempt: 2 of 2; earlier: attempt 1 {earlier}" in summary.splitlines(), (at, summary)
        # B13 round 11 M31: the outcome's own note bracket is the only one on the line
        assert "))" not in next(ln for ln in summary.splitlines() if ln.startswith("- attempt: ")), summary
        assert f"): {earlier}; 2 event lines: 1 error, 1 finished" in text, text
    # the stopped attempt alone, or as the latest one, is as before: there is no next attempt to read
    write_friction(fake_mac, stop.format(said="v61", agent="Contoso Bot", installer=mine, why=newer))
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert section(text, "Summary").strip().splitlines()[0].startswith("- **outcome: failed at step 1** (")


@pytest.mark.usefixtures("clean_doctor")
def test_an_attempt_a_stopped_copy_of_the_prompt_started_says_so_in_the_summary(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """``install.sh --log-start`` stops a copy of the prompt that is not its own, and logs the attempt with
    the version that copy gave: "v7 or older" for a copy from before v8, which names none. The report of
    that attempt reads it with v7's steps, fails it at step 1 by the installer's own error line, and its
    prompt line says the copy was older than the installer's. A copy newer than the installer reads the
    other way round. The installer's version and its reason come from that line, never from the number of
    the build that writes the report: here the installer was v8 or v29, whatever this build is. Only the
    installer's fixed words are shown."""
    what = "install.sh --log-start: the pasted setup prompt is {said} and this installer is for setup prompt"
    (fake_mac["setup"] / "install.log").unlink()  # the command stopped at --log-start: no install.sh run
    assert setup_report.PROMPT_VERSION < 29
    for header, installer, shown, why in (
        ("v7 or older", 8, f"v7 or older {OLDER_COPY}", "the pasted copy is not the current one"),
        ("v7", 8, f"v7 {OLDER_COPY}", "the pasted copy is not the current one"),
        ("v7 or older, said Fabrikam", 8, f"v7 {OLDER_COPY}", "the pasted copy is not the current one"),
        ("v9", 8, f"v9 (newer than the installer's v8{CHANGED})", "this checkout is older than the prompt"),
        # B13 round 8 J9: install.sh's line names the other reading after its reason; the notes are the same
        # and name it too (B13 round 9 K22)
        (
            "v9",
            8,
            f"v9 (newer than the installer's v8{CHANGED})",
            "this checkout is older than the prompt, or the --log-start value was changed",
        ),
        (
            "v7",
            8,
            f"v7 {OLDER_COPY}",
            "the pasted copy is not the current one, or the --log-start value was changed",
        ),
        (
            "v9",
            29,
            f"v9 (older than the installer's v29: the pasted copy was not the current README{CHANGED})",
            "the pasted copy is not the current one",
        ),
    ):
        write_friction(
            fake_mac,
            f"Attempt: 2026-09-29T09:58:00Z\nPrompt: {header}\nAgent: Claude Code, claude-opus-5-5\n"
            f"2026-09-29T09:58:00Z | step 1 | error | {what.format(said=header)} v{installer}: {why}; setup "
            "stopped | copy the prompt again from README.md on the main branch\n"
            "2026-09-29T09:58:40Z | end | finished\n",
        )
        rc, text, _ = report(tmp_path, fake_mac["config"])
        summary = section(text, "Summary")
        [line] = [ln for ln in summary.splitlines() if ln.startswith("- prompt: ")]
        assert rc == 0 and line.startswith(f"- prompt: {shown} · run: "), line
        assert "Fabrikam" not in line, "the line's own words are free text: only the version is shown"
        assert summary.strip().splitlines()[0] == (
            "- **outcome: failed at step 1** (computed: no install.sh run; step 1 logged an error)"
        )
        link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
        assert link["outcome"] == ["Failed at step 1 (preflight)"]
        assert link["prompt_version"] == [shown.split(" ", 1)[0]]
    # B13 round 6 G20: a newer copy's cause is in the installer's fix column (newer_copy_fix), never in its
    # error line, which says "this checkout is older than the prompt" in every case.
    canon = (
        'copy the prompt again from README.md ("Set up on a new Mac: one prompt") and paste it into a new '
        "session: this Mac's checkout follows that branch, so a copy newer than it came from another branch "
        "or a fork"
    )
    fork = (
        "point this Mac's checkout at main of https://github.com/contoso/agent-context-sync (it follows "
        "another remote or branch, so a fresh copy of the prompt would stop here again), or move the "
        "checkout aside so step 1 clones it, then paste the same prompt into a new session"
    )
    for fix, said in (
        (canon, "this Mac's checkout follows main, so the pasted copy came from another branch or a fork"),
        (fork, "this Mac's checkout follows another remote or branch"),
        ("", None),
    ):
        write_friction(
            fake_mac,
            "Attempt: 2026-09-29T09:58:00Z\nPrompt: v15\nAgent: Claude Code, claude-opus-5-5\n"
            f"2026-09-29T09:58:00Z | step 1 | error | {what.format(said='v15')} v14: this checkout is older "
            f"than the prompt; setup stopped | {fix or '-'}\n",
        )
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- prompt: ")]
        cause = f": {said}" if said else ""
        assert line.startswith(f"- prompt: v15 (newer than the installer's v14{cause}{CHANGED}) · run: "), (
            line
        )
        assert "is older than the pasted copy" not in text and "contoso/agent-context-sync" not in line
    # The header's fixed words with no stop line (a log cut short): only the installer's stop writes them.
    write_friction(fake_mac, "Attempt: 2026-09-29T09:58:00Z\nPrompt: v7 or older\nAgent: x\n")
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- prompt: ")]
    assert line.startswith("- prompt: v7 or older (the installer stopped this copy: it named no version) · ")
    # An error line of the agent's own that only looks like the installer's is no evidence.
    write_friction(
        fake_mac,
        "Attempt: 2026-09-29T09:58:00Z\nPrompt: v7\nAgent: x\n"
        "2026-09-29T09:58:00Z | step 2 | error | the pasted setup prompt is old, said the person | -\n"
        "2026-09-29T09:58:40Z | end | finished\n",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- prompt: ")]
    assert line.startswith("- prompt: v7 · run: ")


@pytest.mark.usefixtures("clean_doctor")
def test_the_installers_stop_line_keeps_both_remedies_in_the_report(fake_mac: dict[str, Path]) -> None:
    """B13 round 9 K18: the stop line's fix column now names the remedy of each of its two readings; the
    item line cut every fix at 160 characters, which hid the second. The installer's stop line keeps its
    fix to 400 (640 since B13 round 10 L41: test_every_newer_copy_stop_line_keeps_its_second_remedy...)."""
    clause = (
        '; or, if the first line of the prompt says "setup prompt v9": run step 1\'s command again once, '
        "exactly as the prompt writes it"
    )
    write_friction(fake_mac, STOPPED_V8.replace("on the main branch\n", f"on the main branch{clause}\n"))
    text, _summary = summary_of(fake_mac)
    assert "exactly as the prompt writes it" in text.split("## Agent friction log", 1)[0], text
    other = STOPPED_V8.replace(
        "install.sh --log-start: the pasted setup prompt is v8", "the person pasted v8"
    ).replace("on the main branch\n", f"on the main branch{clause}\n")
    write_friction(fake_mac, other)
    text, _summary = summary_of(fake_mac)
    assert "exactly as the prompt writes it" not in text.split("## Agent friction log", 1)[0], (
        "any other line's fix is cut as before"
    )


@pytest.mark.usefixtures("clean_doctor")
def test_every_newer_copy_stop_line_keeps_its_second_remedy_in_the_summary(fake_mac: dict[str, Path]) -> None:
    """B13 round 10 L41: K18's test put the second remedy after a 50-character stand-in fix, so it passed;
    install.sh's own newer-copy fixes are 431 to about 583 characters with it, and the 400 cut dropped it from
    the Summary's item in every newer-copy case. The fix columns here are built from install.sh's own words,
    for each branch of ``newer_copy_fix``."""
    script = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    found = re.search(r'^CANON_REPO="([^"]*)"', script, re.M)
    compat = re.search(r"^SETUP_PROMPT_COMPAT=(\d+)", script, re.M)
    assert found is not None and compat is not None
    canon = found.group(1)
    copy = (
        f"copy the prompt again from README.md on the main branch of {canon} "
        '("Set up on a new Mac: one prompt") and paste it into a new session'
    )
    other = (
        "(it follows another remote or branch, so a fresh copy of the prompt would stop here again), or move "
        "the checkout aside so step 1 clones it, then paste the same prompt into a new session"
    )
    fixes = [
        f"{copy}: this Mac's checkout follows that branch, so a copy newer than it came from another branch "
        "or a fork",
        f"point this Mac's checkout at main of {canon} {other}",
    ]
    for how in (
        "git could not say which branch it follows",
        "it is on no branch",
        "it follows no upstream branch",
    ):
        fixes.append(
            f"{copy}; if that copy stops here too, this Mac's checkout does not follow main of {canon} "
            f"({how}): point it there, or move it aside so step 1 clones it, then paste the prompt again"
        )
    for words in (
        "this Mac's checkout follows that branch, so a copy newer than it came from another branch or a fork",
        other,
        "if that copy stops here too, this Mac's checkout $follows",
        "point it there, or move it aside so step 1 clones it, then paste the prompt again",
        "git could not say which branch it follows",
    ):
        assert words in script, ("the words are install.sh's", words)
    said, ours = int(compat.group(1)) + 1, compat.group(1)
    # the second remedy as install.sh writes it since B13 round 10 L20, with no cause
    clause = (
        f'; or, if the first line of the prompt says "setup prompt v{ours}": run step 1\'s command again '
        "once, exactly as the prompt writes it"
    )
    assert '": run step 1\'s command again once, exactly as the prompt writes it"' in script
    # past the 400 that cut the second remedy (L41), measured, not a stand-in
    assert max(len(fix + clause) for fix in fixes) > 500, "the longest form is measured, not a stand-in"
    for fix in fixes:
        write_friction(
            fake_mac,
            f"Attempt: 2026-09-29T11:00:00Z\nPrompt: v{said}\nAgent: Claude Code, claude-opus-5-5\n"
            "2026-09-29T11:00:00Z | step 1 | error | install.sh --log-start: the pasted setup prompt is "
            f"v{said} and this installer is for setup prompt v{ours}: this checkout is older than the "
            f"prompt, or the --log-start value was changed; setup stopped (exit 2) | {fix}{clause}\n"
            "2026-09-29T11:00:00Z | end | finished\n",
        )
        text, _summary = summary_of(fake_mac)
        above = text.split("## Agent friction log", 1)[0]
        assert "exactly as the prompt writes it" in above, (len(fix + clause), fix)


STOPPED_V8 = (
    "Attempt: 2026-09-29T11:00:00Z\nPrompt: v8\nAgent: Claude Code, claude-opus-5-5\n"
    "2026-09-29T11:00:00Z | step 1 | error | install.sh --log-start: the pasted setup prompt is v8 and this "
    "installer is for setup prompt v9: the pasted copy is not the current one; setup stopped | copy the "
    "prompt again from README.md on the main branch\n"
    "2026-09-29T11:00:00Z | end | finished\n"
)
"""The friction log of the v9 rehearsal's third scenario: a saved v8 copy pasted on a Mac that already ran an
install an hour before. ``install.sh --log-start`` stopped it, so step 1's command never reached
``--list-folders``."""
NO_TURN = (
    "- expected turns: none (the installer stopped this copy of the prompt in step 1, before the folder "
    "list: no folder question and no Allow click)"
)


def test_the_first_sync_line_cites_no_install_log_when_there_is_none(fake_mac: dict[str, Path]) -> None:
    """B13 round 12 N24: on a Mac with no install.log the Summary read ``- install.sh: no install log`` and on
    the next line ``- first sync: no install.sh run during this attempt (install.log)``: the tag names the log
    only when there is one, as the install.sh line's check does."""
    (fake_mac["setup"] / "install.log").unlink(missing_ok=True)
    write_friction(fake_mac, STOPPED_V8)  # an attempt: the scoped branch
    _text, summary = summary_of(fake_mac)
    assert "- install.sh: no install log" in summary, summary
    [line] = [ln for ln in summary.splitlines() if ln.startswith("- first sync: ")]
    assert line.startswith("- first sync: no install.sh run during this attempt (no install log)"), line
    assert "(install.log" not in line, line
    (fake_mac["setup"] / "friction.md").unlink()  # no attempt: the unscoped branch
    _text, summary = summary_of(fake_mac)
    [line] = [ln for ln in summary.splitlines() if ln.startswith("- first sync: ")]
    assert line.startswith("- first sync: no install.sh run (no install log)"), line
    assert "(install.log" not in line, line


@pytest.mark.usefixtures("clean_doctor")
def test_an_attempt_that_ran_no_installer_names_the_earlier_run_its_sync_line_reads(
    fake_mac: dict[str, Path],
) -> None:
    """The Summary of an attempt that ran no install.sh fell back to the newest earlier run's first-sync step
    and printed it as this attempt's. It still shows it, named by that run's start and "before this attempt",
    with the live baseline clause as it was; with no earlier sync step it says this attempt ran none (B13
    round 2 R19 and R28, one defect from two finders)."""
    write_install_log(fake_mac, first_sync="seconds=34 rc=0 result=done note=converted-3-deferred-1")
    write_friction(fake_mac, STOPPED_V8)
    _text, summary = summary_of(fake_mac)
    [line] = [ln for ln in summary.splitlines() if ln.startswith(("- first sync: ", "- sync: "))]
    assert line.startswith(
        "- first sync: done in 34s (install.log: the install run of 2026-09-29T10:00:00Z, before this "
        "attempt; this attempt ran none) · converted 3, deferred 1 online-only (install.log: that earlier "
        "run; background sync downloads and converts the 1)"
    ), line
    write_friction(fake_mac, V5_HAPPY)
    _text, summary = summary_of(fake_mac)
    assert "- first sync: done in 34s (install.log) · converted 3, deferred 1 online-only (install.log;" in (
        summary
    ), "this attempt's own run keeps its tags"
    log = fake_mac["setup"] / "install.log"  # the earlier run without a first-sync step
    log.write_text(
        "".join(ln for ln in log.read_text(encoding="utf-8").splitlines(True) if "first-sync" not in ln)
    )
    write_friction(fake_mac, STOPPED_V8)
    _text, summary = summary_of(fake_mac)
    assert "- first sync: no install.sh run during this attempt (install.log)" in summary, summary


@pytest.mark.usefixtures("clean_doctor")
def test_an_attempt_whose_install_stopped_before_its_sync_shows_no_earlier_runs_counts_as_its_own(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 11 M6, M22, M38 (one defect from three finders): an attempt whose install run stopped at its
    config step, after an earlier attempt's run synced, printed "no install.sh run with a first-sync step
    (install.log) · converted 7, deferred 3 online-only (install.out)": the earlier run's counts, untagged,
    though install.log has that run. install.out's line is the attempt's only under one of its own headers;
    an earlier run's is named as such, and a headerless install.out cannot say whose it is."""
    write_install_log(fake_mac)  # 10:00, synced
    stopped = (
        "2026-09-29T11:05:00Z run=20260929T110500Z-4343 start install.sh compat=10 commit=0123456789ab "
        "kind=checkout source=- args=--source-local x\n"
        "2026-09-29T11:05:00Z run=20260929T110500Z-4343 step=uv seconds=0 rc=0 result=skipped note=present\n"
        "2026-09-29T11:05:00Z run=20260929T110500Z-4343 step=agentsync seconds=0 rc=0 result=skipped "
        "note=current\n"
        "2026-09-29T11:05:01Z run=20260929T110500Z-4343 step=config seconds=1 rc=78 result=failed\n"
        "2026-09-29T11:05:01Z run=20260929T110500Z-4343 end rc=1 seconds=1\n"
    )
    log = fake_mac["setup"] / "install.log"
    earlier = log.read_text(encoding="utf-8")
    log.write_text(earlier + stopped, encoding="utf-8")
    write_friction(
        fake_mac, "Attempt: 2026-09-29T11:00:00Z\nPrompt: v11\nAgent: Claude Code, claude-opus-5-5\n"
    )
    sync = "first sync: converted 7, deferred 3 online-only\n"
    early = "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh\n"
    late = "# run=20260929T110500Z-4343 2026-09-29T11:05:00Z install.sh\n"
    out = fake_mac["setup"] / "install.out"

    def first_sync(text: str, *, outcome: bool = True) -> str:
        out.write_text(text, encoding="utf-8")
        _text, summary = summary_of(fake_mac)
        assert not outcome or summary.strip().splitlines()[0] == (
            "- **outcome: failed at step 2** (computed: install.sh exited 1: its config step failed with "
            "rc 78)"
        ), summary
        [line] = [ln for ln in summary.splitlines() if ln.startswith(("- first sync: ", "- sync: "))]
        return line

    head = (
        "- first sync: no install run of this attempt reached its first sync (install.log: the last stopped "
        "at its config step) · converted 7, deferred 3 online-only (install.out"
    )
    later = "background sync downloads and converts the 3)"
    assert first_sync(early + sync + late + "error: config\n") == (
        f"{head}: the install run of 2026-09-29T10:00:00Z, before this attempt; {later}"
    )
    # an install.sh that printed its sync under this attempt's own header and logged no first-sync step
    assert first_sync(early + late + sync) == (
        "- first sync: no install run of this attempt logged a first-sync step (install.log) · converted 7, "
        f"deferred 3 online-only (install.out; {later}"
    )
    # an install.out with no header (an older install.sh) cannot say whose the line is
    assert first_sync(sync) == f"{head}: which run printed it is not recorded; {later}"
    # B13 round 13 O32: a newer earlier run whose sync was skipped is the Mac's latest sync, not the older
    # counts install.out still holds
    skipped = (
        "2026-09-29T10:30:00Z run=20260929T103000Z-4300 start install.sh compat=10 commit=0123456789ab "
        "kind=checkout source=- args=--source-local x\n"
        "2026-09-29T10:30:00Z run=20260929T103000Z-4300 step=config seconds=1 rc=0 result=done note=exists\n"
        "2026-09-29T10:40:01Z run=20260929T103000Z-4300 step=first-sync seconds=600 rc=75 result=skipped "
        "note=lock-busy\n"
        "2026-09-29T10:40:01Z run=20260929T103000Z-4300 end rc=0 seconds=601\n"
    )
    log.write_text(earlier + skipped + stopped, encoding="utf-8")
    mid = "# run=20260929T103000Z-4300 2026-09-29T10:30:00Z install.sh\nsync: skipped, lock busy\n"
    line = first_sync(early + sync + mid + late + "error: config\n")
    lock = setup_report._first_sync_note("lock-busy")
    assert line == (
        "- first sync: no install run of this attempt reached its first sync (install.log: the last stopped "
        f"at its config step) · the newest earlier run's sync: skipped in 600s, rc 75, {lock} (install.log: "
        "the install run of 2026-09-29T10:30:00Z, before this attempt)"
    ), line
    assert "converted 7" not in line
    log.write_text(earlier + stopped, encoding="utf-8")
    # an attempt that ran only step 1's list never reaches a first-sync step
    listed = LIST_RUN.replace("2026-09-29T09:58:0", "2026-09-29T11:01:0").replace("T095805Z-", "T110105Z-")
    log.write_text(earlier + listed, encoding="utf-8")
    assert first_sync(early + sync, outcome=False).startswith(
        "- first sync: this attempt ran only install.sh --list-folders, no first sync (install.log) · "
        "converted 7, deferred 3 online-only (install.out: the install run of 2026-09-29T10:00:00Z, before "
        "this attempt;"
    )


def test_an_attempt_with_no_installer_run_is_given_no_folder_question(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """Attempt 4 of the round 2 rehearsal: --log-start then --report-only, nothing else. With no install.sh
    run in the attempt no folder list was printed, so nobody was asked: no question counted and no turn
    expected. A logged question still counts (B13 round 2 R15)."""
    header = "Attempt: 2026-09-29T11:00:00Z\nPrompt: v11\nAgent: Claude Code, claude-opus-5-5\n"
    write_friction(fake_mac, header)
    rc, text, _ = report(tmp_path, fake_mac["config"])
    summary = section(text, "Summary").splitlines()
    assert rc == 0
    assert "- human turns: 0 (0 questions; clicks: none possible; approvals: not observable)" in summary
    assert (
        "- expected turns: none (no install.sh --list-folders run in this attempt: no folder question and no "
        "Allow click)" in summary
    )
    write_friction(
        fake_mac, header + "2026-09-29T11:00:20Z | step 1 | question | asked which Mac this is | -\n"
    )
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "- human turns: 1 (1 question; clicks: none possible; approvals: not observable)" in (
        section(text, "Summary").splitlines()
    )
    # B13 round 18 V38: --log-start, then step 2 straight away, which stopped at its config step: no list
    # ran and no run logged a count, so nobody was asked either
    write_friction(fake_mac, header.replace("Prompt: v11", "Prompt: v26"))
    at, run = "2026-09-29T11:00:05Z", "20260929T110005Z-4242"
    stopped = (
        f"{at} run={run} start install.sh compat=26 commit=0123456789ab kind=checkout source=- args=\n"
        f"{at} run={run} step=uv seconds=0 rc=0 result=skipped note=present\n"
        f"{at} run={run} step=agentsync seconds=1 rc=0 result=done\n"
        f"{at} run={run} step=config seconds=1 rc=78 result=failed{{}}\n"
        f"{at} run={run} end rc=1 seconds=5\n"
    )
    log = fake_mac["setup"] / "install.log"
    log.write_text(stopped.format(""), encoding="utf-8")
    rc, text, _ = report(tmp_path, fake_mac["config"])
    summary = section(text, "Summary")
    assert _line(summary, "- human turns: ").startswith("- human turns: 0 (0 questions; "), summary
    assert _line(summary, "- expected turns: ").startswith(
        "- expected turns: none (no install.sh --list-folders run in this attempt: "
    ), summary
    log.write_text(stopped.format(" kept=2"), encoding="utf-8")
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "no folder question (2 folders already synced" in _line(
        section(text, "Summary"), "- expected turns: "
    )


def test_an_attempt_stopped_before_the_folder_list_is_given_no_question(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """The v9 rehearsal (2026-10-07): the report of an attempt that an out-of-date copy of the prompt started
    said ``human turns: 1 (1 question; ...)`` and ``expected turns: the folder question (step 1; not
    logged)``. Nobody was asked: the installer stopped the copy before the folder list. The report now
    counts no question for it and expects no turn. A question the agent logged is still counted, and an
    attempt that reached a list after all is read by the usual rules."""

    def turns() -> tuple[str, str, str]:
        rc, text, _ = report(tmp_path, fake_mac["config"])
        lines = section(text, "Summary").strip().splitlines()
        assert rc == 0 and lines[0].startswith(
            "- **outcome: failed at step 1** (computed: no install.sh run; "
        )
        [human] = [ln for ln in lines if ln.startswith("- human turns: ")]
        [expected] = [ln for ln in lines if ln.startswith("- expected turns: ")]
        [runs] = [ln for ln in lines if ln.startswith("- install.sh: ")]
        return human, expected, runs

    write_friction(fake_mac, STOPPED_V8)
    human, expected, runs = turns()
    assert human == "- human turns: 0 (0 questions; clicks: none possible; approvals: not observable)"
    assert expected == NO_TURN
    assert runs == "- install.sh: no run during this attempt (1 earlier in the install log)"

    # What the agent logged still counts: here it asked the person something before it stopped.
    asked = "2026-09-29T11:00:20Z | step 1 | question | asked whether to go on with the old copy | -\n"
    write_friction(fake_mac, _insert_before("2026-09-29T11:00:00Z | end", asked, STOPPED_V8))
    human, expected, _runs = turns()
    assert human == "- human turns: 1 (1 question; clicks: none possible; approvals: not observable)"
    assert expected == NO_TURN

    # A copy from before v8 named no version: the installer's header alone says it stopped it.
    write_friction(fake_mac, "Attempt: 2026-09-29T11:00:00Z\nPrompt: v7 or older\nAgent: x\n")
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0 and NO_TURN in section(text, "Summary").splitlines()

    # The agent went on to the folder list all the same: a list ran, so the question may have been asked.
    with (fake_mac["setup"] / "install.log").open("a", encoding="utf-8") as log:
        log.write(
            "2026-09-29T11:00:30Z run=20260929T110030Z-11 start install.sh compat=9 args=--list-folders\n"
            "2026-09-29T11:00:30Z run=20260929T110030Z-11 step=list-folders seconds=2 rc=0 result=done "
            "note=listed-6\n"
            "2026-09-29T11:00:32Z run=20260929T110030Z-11 end rc=0 seconds=2\n"
        )
    write_friction(fake_mac, STOPPED_V8)
    rc, text, _ = report(tmp_path, fake_mac["config"])
    summary = section(text, "Summary").splitlines()
    assert "- human turns: 1 (1 question; clicks: none possible; approvals: not observable)" in summary
    assert f"- expected turns: {ASKED} · Allow clicks: none possible (sandbox)" in summary

    [stopped] = setup_report.parse_friction(STOPPED_V8).attempts
    runs_read = setup_report.read_install_runs(fake_mac["setup"] / "install.log")
    in_attempt = setup_report.runs_for_attempt(setup_report.parse_friction(STOPPED_V8), 0, runs_read)
    assert [run.args for run in in_attempt] == ["--list-folders"]
    assert setup_report._stopped_before_the_list(stopped, []) is True
    assert setup_report._stopped_before_the_list(stopped, in_attempt) is False
    [current] = setup_report.parse_friction(V11_HAPPY).attempts
    assert setup_report._stopped_before_the_list(current, []) is False, "only the installer's stop counts"


def test_an_unanswered_folder_question_is_a_deviation_not_a_question(fake_mac: dict[str, Path]) -> None:
    """The v10 rehearsal: step 1 orders "log a deviation, stop and wait for my answer" when the folder
    question comes back unanswered, and the expected-turns line said "(step 1; not logged)" two lines above
    that deviation. It is never logged as a ``question``, which is what the line says."""
    v6_install_log(fake_mac)
    unanswered = (
        "2026-09-29T09:58:30Z | step 1 | deviation | the folder question came back unanswered; no folder "
        "chosen, stopped to wait for the answer | -\n"
    )
    end = "2026-09-29T10:01:10Z | end | finished\n"
    write_friction(fake_mac, V11_HAPPY.replace(end, unanswered + end))
    _text, summary = summary_of(fake_mac)
    assert "- expected turns: the folder question (step 1; not logged as a question) · " in summary
    assert "- agent friction: 1 deviation, 0 prompt, 0 error (F4); none of these changes" in summary


# ---- a Mac that is already set up is not asked for its folders again (field report 2026-10-07) -----------

ASKED = "the folder question (step 1; not logged as a question)"
NOT_ASKED = "no folder question ({} already synced: step 1 asks at most whether to add one; not logged)"


def set_up_log(
    fake_mac: dict[str, Path],
    *installs: tuple[str, str],
    listed: str = " synced=2",
    list_step: str | None = "done note=listed-25",
) -> Path:
    """install.log of one attempt as scripts/install.sh writes it since it counts folders: step 1's
    ``--list-folders`` run (``list_step``: its result and note, None for an attempt with no such run;
    ``listed``: what follows its note), then one install run per ``(arguments, the config step's result and
    what follows it)``."""
    text = ""
    if list_step is not None:
        text = LIST_RUN.replace(" launchd=simulated", "").replace(
            "result=done\n", f"result={list_step}{listed}\n"
        )
    for n, (args, config) in enumerate(installs):
        at, run = f"2026-09-29T10:0{n}:00Z", f"20260929T100{n}00Z-4242"
        text += (
            f"{at} run={run} start install.sh compat=10 commit=0123456789ab kind=checkout source=- "
            f"args={args}\n"
            f"{at} run={run} step=uv seconds=0 rc=0 result=skipped note=present\n"
            f"{at} run={run} step=agentsync seconds=41 rc=0 result=done\n"
            f"{at} run={run} step=launcher seconds=0 rc=0 result=skipped note=not-requested\n"
            f"{at} run={run} step=config seconds=1 rc=0 result={config}\n"
            f"{at} run={run} step=status seconds=3 rc=0 result=done\n"
            f"{at} run={run} step=first-sync seconds=9 rc=0 result=done note=converted-0-deferred-0\n"
            f"{at} run={run} step=agent seconds=0 rc=0 result=skipped note=not-requested\n"
            f"{at} run={run} step=wait seconds=0 rc=0 result=skipped note=not-requested\n"
            f"{at} run={run} end rc=0 seconds=57\n"
            f"{at} run={run} step=report seconds=2 rc=0 result=done note=agentsync\n"
        )
    log = fake_mac["setup"] / "install.log"
    log.write_text(text, encoding="utf-8")
    return log


def _line(summary: str, start: str) -> str:
    [line] = [ln for ln in summary.splitlines() if ln.startswith(start)]
    return line


@pytest.mark.usefixtures("clean_doctor")
def test_a_mac_already_set_up_has_no_folder_question_and_says_what_it_kept(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup prompt v8 asked a Mac that already synced two folders for them again, and an unattended agent
    stopped. install.sh now logs how many folders the config already syncs (step 1's list) and what the
    install run kept and added. With folders already synced the folder question is not a turn the report
    expects or counts, and the Summary says what became of them, in counts."""
    set_up_log(fake_mac, ("", "skipped note=exists kept=2 added=0"))
    write_friction(fake_mac, V11_HAPPY)
    monkeypatch.setattr(setup_report, "home_path", lambda: "/Users/jdoe")  # a real Mac: a click is possible
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    summary = section(text, "Summary")
    assert summary.strip().splitlines()[0] == (
        "- **outcome: fully one command** (computed: install.sh exit 0; no turn beyond the unavoidable ones)"
    )
    assert _line(summary, "- human turns: ") == (
        "- human turns: 0 (0 questions; 0 clicks beyond the announced Allow click (not logged); approvals: "
        "not observable)"
    ), "nobody had to be asked: the unlogged folder question is not counted"
    assert _line(summary, "- expected turns: ") == (
        f"- expected turns: {NOT_ASKED.format('2 folders')} · the announced Allow click (step 1; not logged)"
    )
    assert _line(summary, "- folders: ") == (
        "- folders: kept the 2 already synced (none added) · 0 named with --source-local (install.log)"
    )
    lines = summary.splitlines()
    assert lines.index(_line(summary, "- folders: ")) == lines.index(_line(summary, "- install.sh: ")) + 1
    for raw in RAW:
        assert raw not in text, raw


@pytest.mark.usefixtures("clean_doctor")
def test_the_folders_line_counts_what_was_kept_added_and_named(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """Each case is one attempt's install.log: what step 1's list logged, then its install runs. The
    counts are install.sh's own (the config step's ``kept`` and ``added``); the named ones are the
    ``--source-local`` options of the run's arguments, whose folders are never read, so a name with the
    option's own text in it is one folder. The folder question is expected, or not, by what the Mac synced
    when the attempt began: its first run that says so."""
    one, two = (str(fake_mac[k]).replace(" ", "\\ ") for k in ("one", "two"))
    odd = "/x/Plans\\ --source-local\\ old"
    cases: list[tuple[str, list[tuple[str, str]], str, str | None]] = [
        (
            " synced=2",
            [(f"--source-local {one} --source-local {two}", "done note=add-source kept=2 added=1")],
            NOT_ASKED.format("2 folders"),
            "1 added to the 2 already synced · 2 named with --source-local",
        ),
        (
            " synced=1",
            [(f"--source-local {one}", "done note=add-source kept=1 added=0")],
            NOT_ASKED.format("1 folder"),
            "kept the 1 already synced (none added) · 1 named with --source-local",
        ),
        (
            "",  # a new Mac has no config for step 1 to read
            [(f"--source-local {one} --source-local {odd}", "done note=created kept=0 added=2")],
            ASKED,
            "2 added (none was synced before) · 2 named with --source-local",
        ),
        (
            " synced=0",
            [("", "done note=created kept=0 added=0")],
            ASKED,
            "none synced and none added · 0 named with --source-local",
        ),
        (
            " synced=0",  # a new Mac whose install ran twice: the person was asked before the first
            [
                (f"--source-local {one}", "done note=created kept=0 added=1"),
                ("", "skipped note=exists kept=1 added=0"),
            ],
            ASKED,
            # B13 round 15 S9: the earlier run added it, not the last (which said kept=1 added=0)
            "1 added (none was synced before; by run 20260929T100000Z) · 1 named with --source-local",
        ),
        (
            "",  # the list finished with no count: it printed no mark, so the question was asked
            [("", "skipped note=exists kept=3 added=0")],
            ASKED,
            "kept the 3 already synced (none added) · 0 named with --source-local",
        ),
        # An installer from before the counts, or a config agentsync could not read: nothing to say.
        ("", [(f"--source-local {one}", "done note=add-source")], ASKED, None),
        ("", [("", "skipped note=exists kept=many added=0")], ASKED, None),
    ]
    for listed, installs, question, folders in cases:
        set_up_log(fake_mac, *installs, listed=listed)
        write_friction(fake_mac, V11_HAPPY)
        rc, text, _ = report(tmp_path, fake_mac["config"])
        summary = section(text, "Summary")
        assert rc == 0 and _line(summary, "- expected turns: ").startswith(f"- expected turns: {question} · ")
        asked = 1 if question == ASKED else 0
        assert _line(summary, "- human turns: ").startswith(f"- human turns: {asked} ({asked} question")
        found = [ln for ln in summary.splitlines() if ln.startswith("- folders: ")]
        assert found == ([f"- folders: {folders} (install.log)"] if folders else []), (listed, installs)
        for raw in (*RAW, "Plans"):
            assert raw not in text, raw
    # B13 round 15 S9: the attempt's runs, not the last one alone. RT's run that added a folder and exited
    # 1 at status, then a run with no options; a retry naming the same folder; a run after the end line
    # (10:01:10) is the last run's alone; counts that do not chain (a source removed by hand between runs)
    # say so.
    by_first = "1 added to the 2 already synced (by run 20260929T100000Z, which exited 1) · 1 named with "
    for installs, rc0, folders in (
        (
            [
                (f"--source-local {one}", "done note=add-source kept=2 added=1"),
                ("", "skipped note=exists kept=3 added=0"),
            ],
            1,
            by_first + "--source-local",
        ),
        (
            [
                (f"--source-local {one}", "done note=add-source kept=2 added=1"),
                (f"--source-local {one}", "done note=add-source kept=3 added=0"),
            ],
            1,
            by_first + "--source-local",
        ),
        (
            [
                (f"--source-local {one}", "done note=add-source kept=2 added=1"),
                ("", "skipped note=exists kept=3 added=0"),
                (f"--source-local {two}", "done note=add-source kept=3 added=1"),
            ],
            0,
            "1 added to the 3 already synced · 1 named with --source-local",
        ),
        (
            [
                (f"--source-local {one}", "done note=add-source kept=2 added=1"),
                ("", "skipped note=exists kept=2 added=0"),
            ],
            0,
            "kept the 2 already synced (none added) · 0 named with --source-local (last run;",
        ),
        # B13 round 17 U40: add-source wrote the folder's block, then agentsync refused a [governance] value
        # (exit 78): that run added it, and the retry after the person's fix kept it
        (
            [
                (f"--source-local {one}", "failed note=added-then-refused kept=2 added=1"),
                (f"--source-local {one}", "done note=add-source kept=3 added=0"),
            ],
            1,
            by_first + "--source-local",
        ),
    ):
        log = set_up_log(fake_mac, *installs, listed=" synced=2")
        first = "2026-09-29T10:00:00Z run=20260929T100000Z-4242 end rc=0 "
        log.write_text(log.read_text(encoding="utf-8").replace(first, first.replace("rc=0", f"rc={rc0}")))
        write_friction(fake_mac, V11_HAPPY)
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        line = _line(section(text, "Summary"), "- folders: ")
        assert line.startswith(f"- folders: {folders}"), (installs, line)
    # B13 round 18 V37: a retry after the end line that added nothing (the person's fix, then the same
    # command) joins the chain, so the run that added the folder is named; one that adds stays alone (above)
    log = set_up_log(
        fake_mac,
        (f"--source-local {one}", "failed note=added-then-refused kept=2 added=1"),
        (f"--source-local {one}", "done note=add-source kept=3 added=0"),
        listed=" synced=2",
    )
    first = "2026-09-29T10:00:00Z run=20260929T100000Z-4242 end rc=0 "
    log.write_text(
        log.read_text(encoding="utf-8")
        .replace(first, first.replace("rc=0", "rc=1"))
        .replace("2026-09-29T10:01:00Z", "2026-09-29T10:02:30Z")
    )
    write_friction(fake_mac, V11_HAPPY)
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert _line(section(text, "Summary"), "- folders: ").startswith(f"- folders: {by_first}--source-local")
    # B13 round 16 T44: with no friction log, or an attempt with no begin time, the runs are the whole
    # install.log, so the sum read a re-run a month later as "2 added (none was synced before)": only an
    # attempt with a begin time sums its runs; otherwise the last run's alone, as the install line scopes
    for friction in (None, "Prompt: v11\nAgent: Contoso Bot\n"):
        log = set_up_log(
            fake_mac,
            (f"--source-local {one} --source-local {two}", "done note=created kept=0 added=2"),
            ("", "skipped note=exists kept=2 added=0"),
            listed="",
            list_step=None,
        )
        log.write_text(
            log.read_text(encoding="utf-8").replace("2026-09-29T10:01", "2026-10-29T10:01"), "utf-8"
        )
        found = fake_mac["setup"] / "friction.md"
        if friction is None:
            found.unlink(missing_ok=True)
        else:
            write_friction(fake_mac, friction)
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        assert _line(section(text, "Summary"), "- folders: ") == (
            "- folders: kept the 2 already synced (none added) · 0 named with --source-local (install.log)"
        ), friction
    runs = setup_report.read_install_runs(set_up_log(fake_mac, ("--source-local x", "done note=created")))
    assert [run.args for run in runs] == ["--list-folders", "--source-local x"]
    assert setup_report.synced_before(runs) == 2 and setup_report.synced_before(runs[1:]) is None
    assert setup_report.synced_before([]) is None


@pytest.mark.usefixtures("clean_doctor")
def test_a_finished_list_says_by_itself_whether_the_folder_question_was_asked(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """The folder question is asked, or not, by what step 1's list printed. A list that finished with no
    ``synced=`` printed no "already synced" line (no config yet, or its warning: no installed agentsync
    loaded the config), so the agent asked, or stopped when it could not. The report took ``kept=2`` from
    the install run that followed and said "no folder question", 0 questions and "fully one command" over
    a correct stop (review, 2026-10-07). The same happened to an older attempt when install.sh was run
    again by hand: its list run came from an installer without the counts.

    A list that ended on a click says so too when it printed the line (``synced=`` on a failed step).
    Only without a finished list, and without that count, does an install run's ``kept`` decide."""
    kept = ("", "skipped note=exists kept=2 added=0")
    not_asked = NOT_ASKED.format("2 folders")
    cases: list[tuple[str | None, int, str]] = [
        ("done note=listed-25", 0, ASKED),  # no mark was printed: asked, whatever step 2 kept
        ("done", 0, ASKED),  # a list run of an installer from before the counts
        ("done note=listed-25 synced=0", 0, ASKED),
        ("done note=listed-25 synced=2", 2, not_asked),
        ("failed note=denied synced=2", 2, not_asked),  # ended on a click, with the line and the marks
        ("failed note=tcc-pending", 2, not_asked),  # ended on a click and says nothing: the next run does
        (None, 2, not_asked),  # install.sh alone, with no list
    ]
    for list_step, synced, question in cases:
        runs = setup_report.read_install_runs(set_up_log(fake_mac, kept, listed="", list_step=list_step))
        assert setup_report.synced_before(runs) == synced, list_step
        write_friction(fake_mac, V11_HAPPY)
        rc, text, _ = report(tmp_path, fake_mac["config"])
        summary = section(text, "Summary")
        assert rc == 0 and _line(summary, "- expected turns: ").startswith(
            f"- expected turns: {question} · "
        ), list_step
        asked = 1 if question == ASKED else 0
        assert _line(summary, "- human turns: ").startswith(f"- human turns: {asked} ({asked} question")
        assert _line(summary, "- folders: ").startswith("- folders: kept the 2 already synced (none added)")
    # Two lists in one attempt: the first that finished, or that printed the line, decides.
    log = set_up_log(fake_mac, kept, listed="", list_step="failed note=denied")
    again = LIST_RUN.replace(" launchd=simulated", "").replace("T09:58:0", "T09:59:0")
    log.write_text(
        log.read_text(encoding="utf-8").replace(
            "2026-09-29T10:00:00Z run=20260929T100000Z-4242 start",
            again.replace("20260929T095805Z-11", "20260929T095905Z-12").replace(
                "result=done\n", "result=done note=listed-25\n"
            )
            + "2026-09-29T10:00:00Z run=20260929T100000Z-4242 start",
        ),
        encoding="utf-8",
    )
    runs = setup_report.read_install_runs(log)
    assert [run.list_only for run in runs] == [True, True, False]
    assert setup_report.synced_before(runs) == 0, "denied, then listed with no mark after the click: asked"

    # B13 round 7 H17, H28: a list that could not read the config says so; when step 2 then kept folders,
    # the question was asked (human turns 1) but was no expected turn: agentsync caused it
    unread = "done note=listed-25 synced=unreadable"
    runs = setup_report.read_install_runs(set_up_log(fake_mac, kept, listed="", list_step=unread))
    assert (setup_report.synced_before(runs), setup_report.unread_synced(runs)) == (0, 2)
    write_friction(fake_mac, V11_HAPPY)
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    summary = section(text, "Summary")
    assert summary.strip().splitlines()[0] == (
        "- **outcome: worked with help** (computed: install.sh exit 0; the folder question: step 1's list "
        "could not read the 2 folders this Mac already syncs)"
    ), summary
    assert _line(summary, "- human turns: ").startswith("- human turns: 1 (1 question")
    assert _line(summary, "- expected turns: ").startswith(
        "- expected turns: no folder question (2 folders already synced; step 1's list could not read them, "
        "so it was asked: see Items) · "
    )
    items = summary.split("Items that were not one command (attempt 1):\n\n", 1)[1]
    assert items.startswith(
        "- install.sh · step 1 · the folder list could not read the 2 folders this Mac already syncs; the "
        "folder question was a turn agentsync caused\n"
    ), items
    # the Items line whatever the outcome, since the expected turns point there (a reviewer's finding)
    failing = ("", "skipped note=exists kept=2 added=0")
    log = set_up_log(fake_mac, failing, listed="", list_step=unread)
    log.write_text(log.read_text(encoding="utf-8").replace("end rc=0", "end rc=1"), encoding="utf-8")
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    summary = section(text, "Summary")
    assert not summary.strip().startswith("- **outcome: worked with help**"), summary
    assert "- install.sh · step 1 · the folder list could not read the 2 folders" in summary, summary
    # only the first install run's count: a later run's can hold folders added after the question
    first = ("", "done note=created")
    runs = setup_report.read_install_runs(set_up_log(fake_mac, first, failing, listed="", list_step=unread))
    assert setup_report.unread_synced(runs) is None
    # a config that synced nothing: the question was due, as before
    first = ("", "done note=created kept=0 added=1")
    runs = setup_report.read_install_runs(set_up_log(fake_mac, first, listed="", list_step=unread))
    assert (setup_report.synced_before(runs), setup_report.unread_synced(runs)) == (0, None)
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    summary = section(text, "Summary")
    assert summary.strip().startswith("- **outcome: fully one command** "), summary
    assert _line(summary, "- expected turns: ").startswith(f"- expected turns: {ASKED} · ")


def test_from_prompt_v18_a_list_that_could_not_read_the_config_costs_no_turn(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 9 K9, K20, K32 (one defect, built once): v17's step 1 went on unattended only after an
    "already synced on this Mac:" line, so where the list could not read the config the agent waited, and the
    report called the folder question a turn agentsync caused. v18 treats that list as one that marked the
    folders: it asks at most whether to add one, and with nobody to ask it adds none. So a v18 attempt gets
    no outcome reason, no Item and no counted question, and its expected turns say so; v17 and older keep
    the old reading, and so does a v18 attempt whose agent logged a step 1 deviation (it waited all the
    same)."""
    kept = ("", "skipped note=exists kept=2 added=0")
    unread = "done note=listed-25 synced=unreadable marks=no-tool"
    runs = setup_report.read_install_runs(set_up_log(fake_mac, kept, listed="", list_step=unread))
    caused = "the folder question: step 1's list could not read the 2 folders this Mac already syncs"
    item = "- install.sh · step 1 · the folder list could not read the 2 folders this Mac already syncs"
    waited = "2026-09-29T09:58:30Z | step 1 | deviation | waited for the folder answer | -\n"
    for prompt, extra, asked in (("v17", "", True), ("v18", "", False), ("v18", waited, True)):
        friction = V11_HAPPY.replace("Prompt: v11", f"Prompt: {prompt}").replace(
            "2026-09-29T10:01:10Z | end", f"{extra}2026-09-29T10:01:10Z | end"
        )
        write_friction(fake_mac, friction)
        [attempt] = setup_report.parse_friction(friction).attempts
        assert setup_report.unread_asked(attempt, runs) == (2 if asked else None), prompt
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        summary = section(text, "Summary")
        first = summary.strip().splitlines()[0]
        expected = _line(summary, "- expected turns: ")
        assert (caused in first) is asked and (item in summary) is asked, (prompt, summary)
        assert (
            "- note: step 1's folder list could not read which folders sources.toml already syncs" in summary
        )
        if asked:
            assert _line(summary, "- human turns: ").startswith("- human turns: 1 (1 question"), prompt
            assert "so it was asked: see Items) · " in expected, prompt
            continue
        assert first.startswith("- **outcome: fully one command** "), summary
        assert _line(summary, "- human turns: ").startswith("- human turns: 0 (0 questions"), summary
        # the step asks, not its list (B13 round 10 L23)
        assert expected.startswith(
            "- expected turns: no folder question (2 folders already synced; step 1's list could not read "
            "them, so step 1 asks at most whether to add one; not logged) · "
        ), expected
        assert "it asked at most" not in summary


def test_from_prompt_v18_an_unread_config_the_install_could_not_count_costs_no_turn(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 10 L7: a v18 list that could not read the config, then an install whose config step failed
    and so logged no ``kept=``: nobody answered anything, yet the report counted the unlogged folder question
    (human turns 1, "the folder question (step 1; not logged as a question)"), since only a ``kept=N`` carried
    v18's rule. With no count either way the question is not counted from v18 on; v17, a v18 agent that
    logged it waited, and an install that read the config and kept 0 keep the old reading."""
    failed = ("", "failed note=config-error")
    unread = "done note=listed-25 synced=unreadable marks=failed:ConfigError"
    waited = "2026-09-29T09:58:30Z | step 1 | deviation | waited for the folder answer | -\n"
    quiet = (
        "- expected turns: no folder question (step 1's list could not read the config, so step 1 asks at "
        "most whether to add one; not logged) · "
    )
    for prompt, extra, config, asked in (
        ("v18", "", failed, False),
        ("v17", "", failed, True),
        ("v18", waited, failed, True),
        ("v18", "", ("", "done note=created kept=0 added=1"), True),
    ):
        log = set_up_log(fake_mac, config, listed="", list_step=unread)
        if config is failed:
            log.write_text(log.read_text(encoding="utf-8").replace("end rc=0", "end rc=1"), encoding="utf-8")
        runs = setup_report.read_install_runs(log)
        assert setup_report.unread_unknown(runs) is (config is failed), prompt
        friction = V11_HAPPY.replace("Prompt: v11", f"Prompt: {prompt}").replace(
            "2026-09-29T10:01:10Z | end", f"{extra}2026-09-29T10:01:10Z | end"
        )
        write_friction(fake_mac, friction)
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        summary = section(text, "Summary")
        turns, expected = _line(summary, "- human turns: "), _line(summary, "- expected turns: ")
        if config is failed:
            assert "failed at step 2" in summary.strip().splitlines()[0], summary
        if asked:
            assert turns.startswith("- human turns: 1 (1 question"), (prompt, extra, config, turns)
            assert not expected.startswith(quiet), expected
            continue
        assert turns.startswith("- human turns: 0 (0 questions"), turns
        assert expected.startswith(quiet), expected
        assert "Allow clicks: none possible" in expected or "the announced" in expected, expected
    # no install run: a stop at step 1 stays expected
    runs = setup_report.read_install_runs(set_up_log(fake_mac, listed="", list_step=unread))
    assert not setup_report.unread_unknown(runs)
    # B13 round 13 O8: from v21, when the installed agentsync is already the checkout's build (step 2 does
    # not reinstall it), step 1 asks nothing at all; one that step 2 reinstalls keeps "asks at most"
    stuck = (
        "- expected turns: no folder question (step 1's list could not read the config, and the installed "
        "agentsync is already this checkout's build, so step 2 does not reinstall it: step 1 asks nothing, "
        "since no folder can be added until the config is fixed; not logged) · "
    )
    for built, prompt, want in (
        ("skipped note=current", "v21", stuck),
        ("done note=installed", "v21", quiet),
        ("skipped note=current", "v20", quiet),
    ):
        write_friction(fake_mac, V11_HAPPY.replace("Prompt: v11", f"Prompt: {prompt}"))
        log = set_up_log(fake_mac, failed, listed="", list_step=unread)
        log.write_text(
            log.read_text(encoding="utf-8")
            .replace("end rc=0", "end rc=1")
            .replace(
                "step=agentsync seconds=41 rc=0 result=done", f"step=agentsync seconds=41 rc=0 result={built}"
            ),
            encoding="utf-8",
        )
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        expected = _line(section(text, "Summary"), "- expected turns: ")
        assert expected.startswith(want), (built, prompt, expected)
    # a list that timed out (no failure of the build) keeps the old words, current build or not
    log = set_up_log(
        fake_mac, failed, listed="", list_step=unread.replace("marks=failed:ConfigError", "marks=timeout")
    )
    log.write_text(
        log.read_text(encoding="utf-8").replace(
            "step=agentsync seconds=41 rc=0 result=done",
            "step=agentsync seconds=41 rc=0 result=skipped note=current",
        ),
        encoding="utf-8",
    )
    write_friction(fake_mac, V11_HAPPY.replace("Prompt: v11", "Prompt: v21"))
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert _line(section(text, "Summary"), "- expected turns: ").startswith(quiet)


def test_a_folder_list_run_again_before_the_install_is_what_step_1_printed(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 10 L12: step 1's list timed out, its re-run marked two folders and printed "already synced
    on this Mac: 2 folder(s)", yet the Summary said the list "marked none" and "could not read them", as the
    first list run alone decided. A re-run before the install that read the config decides now; the note
    keeps the first run's reason and says what the re-run read. Both runs unreadable still say "so it marked
    none", and a list run after the install changes nothing."""
    unread = "done note=listed-6 synced=unreadable marks=timeout"

    def log_with_lists(*listed: str, after: str | None = None) -> Path:
        log = set_up_log(fake_mac, ("", "skipped note=exists kept=2 added=0"), listed="", list_step=unread)
        text, start = log.read_text(encoding="utf-8"), "2026-09-29T10:00:00Z run=20260929T100000Z-4242 start"
        extra = "".join(
            LIST_RUN.replace(" launchd=simulated", "")
            .replace("T09:58:0", f"T09:59:{n}")
            .replace("20260929T095805Z-11", f"20260929T0959{n}5Z-1{n}")
            .replace("result=done\n", f"result=done note=listed-6{fields}\n")
            for n, fields in enumerate(listed)
        )
        text = text.replace(start, extra + start)
        if after is not None:
            text += (
                LIST_RUN.replace(" launchd=simulated", "")
                .replace("T09:58:0", "T10:05:0")
                .replace("20260929T095805Z-11", "20260929T100505Z-19")
                .replace("result=done\n", f"result=done note=listed-6{after}\n")
            )
        log.write_text(text, encoding="utf-8")
        return log

    note = (
        "- note: step 1's folder list could not read which folders sources.toml already syncs (the reader "
        "took over 10 s)"
    )
    write_friction(fake_mac, V11_HAPPY.replace("Prompt: v11", "Prompt: v18"))
    runs = setup_report.read_install_runs(log_with_lists(" synced=2"))
    assert [run.list_only for run in runs] == [True, True, False]
    assert (setup_report.synced_before(runs), setup_report.unread_synced(runs)) == (2, None)
    assert setup_report.rerun_synced(runs) == 2 and not setup_report.unread_unknown(runs)
    summary = section(report(tmp_path, fake_mac["config"])[1], "Summary")
    assert f"{note}; its re-run read it: 2 already synced" in summary.splitlines(), summary
    expected = _line(summary, "- expected turns: ")
    assert expected.startswith(
        "- expected turns: no folder question (2 folders already synced: step 1 asks at most whether to add "
        "one; not logged) · "
    ), expected
    assert "could not read them" not in expected
    assert _line(summary, "- human turns: ").startswith("- human turns: 0 (0 questions"), summary
    # a re-run that read a config syncing nothing: the question was due
    runs = setup_report.read_install_runs(log_with_lists(" synced=0"))
    assert (setup_report.synced_before(runs), setup_report.rerun_synced(runs)) == (0, 0)
    summary = section(report(tmp_path, fake_mac["config"])[1], "Summary")
    assert f"{note}; its re-run read it and marked none" in summary.splitlines(), summary
    # both unreadable (probe A3): as before
    for runs_after in ((" synced=unreadable marks=timeout",), ()):
        runs = setup_report.read_install_runs(log_with_lists(*runs_after))
        assert setup_report.rerun_synced(runs) is None
        assert (setup_report.synced_before(runs), setup_report.unread_synced(runs)) == (0, 2)
        summary = section(report(tmp_path, fake_mac["config"])[1], "Summary")
        assert f"{note}, so it marked none" in summary.splitlines(), summary
    # a finished list that logged no synced= at all decides by itself, as in synced_before (a reviewer's find)
    runs = setup_report.read_install_runs(log_with_lists("", " synced=2"))
    assert (setup_report.synced_before(runs), setup_report.unread_synced(runs)) == (0, 2)
    assert setup_report.rerun_synced(runs) is None
    # an install run before the lists that logged no kept= is passed over by both (a reviewer's find)
    log = log_with_lists(" synced=2")
    text = log.read_text(encoding="utf-8")
    early = "".join(
        ln.replace("2026-09-29T10:00:00Z", "2026-09-29T09:50:00Z")
        .replace("20260929T100000Z-4242", "20260929T095000Z-4241")
        .replace("result=skipped note=exists kept=2 added=0", "result=failed note=config-error")
        for ln in text.splitlines(keepends=True)
        if "20260929T100000Z-4242" in ln
    )
    log.write_text(early + text, encoding="utf-8")
    runs = setup_report.read_install_runs(log)
    assert [run.list_only for run in runs] == [False, True, True, False]
    assert (setup_report.synced_before(runs), setup_report.rerun_synced(runs)) == (2, 2)
    # a list run after the install reads a config the install changed: not what step 1 showed
    runs = setup_report.read_install_runs(log_with_lists(after=" synced=3"))
    assert [run.list_only for run in runs] == [True, False, True]
    assert setup_report.rerun_synced(runs) is None and setup_report.unread_synced(runs) == 2
    summary = section(report(tmp_path, fake_mac["config"])[1], "Summary")
    assert f"{note}, so it marked none" in summary.splitlines(), summary


def test_the_summary_says_why_a_folder_list_could_not_read_the_config(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 7 H8, H16: install.sh logs ``marks=<why>`` beside ``synced=unreadable``, and the Summary has
    a note for it whatever followed. The Item of H17 is there only when an install run then kept a folder,
    so a list on a Mac with nothing to keep, or a session that stopped at the question, showed the failed
    reader nowhere but in the agent's own words. Of the log's ``marks=`` the note shows fixed words and at
    most one identifier."""
    whys = {
        " marks=no-tool": "no agentsync was installed",
        " marks=timeout": "the reader took over 10 s",
        " marks=failed": "the installed agentsync failed while reading it",
        " marks=failed:ConfigError": "the installed agentsync failed while reading it and raised ConfigError",
        " marks=failed:<org-1>": "the installed agentsync failed while reading it",
        " marks=older:provider_link_resolved": (
            "the installed agentsync was older than the installer and had no provider_link_resolved"
        ),
        " marks=older:a-b": "the installed agentsync was older than the installer",
        "": "the installed agentsync could not run the reader",  # an installer that logged no marks=
    }
    new_mac = ("--source-local a", "done note=created kept=0 added=1")
    for marks, why in whys.items():
        step = f"done note=listed-6 synced=unreadable{marks}"
        runs = setup_report.read_install_runs(set_up_log(fake_mac, new_mac, listed="", list_step=step))
        assert setup_report.unread_why(runs) == why, marks
        assert "(" not in why and ")" not in why, "the note puts it in a bracket of its own (B13 round 8 J27)"
        assert setup_report.unread_synced(runs) is None, "nothing was kept: the question was needed"
    write_friction(fake_mac, V11_HAPPY)
    note = "- note: step 1's folder list could not read which folders sources.toml already syncs ("
    # B13 round 8 J16: the words sat in the note's bracket with a bracket of their own, "(... (ConfigError))"
    for marks in (" marks=failed:ConfigError", " marks=older:provider_link_resolved"):
        set_up_log(fake_mac, new_mac, listed="", list_step=f"done note=listed-6 synced=unreadable{marks}")
        lines = section(report(tmp_path, fake_mac["config"])[1], "Summary").splitlines()
        assert f"{note}{whys[marks]}), so it marked none" in lines, marks
        inner = whys[marks]
        assert "(" not in inner and ")" not in inner, inner
    set_up_log(fake_mac, new_mac, listed="", list_step="done note=listed-6 synced=unreadable")
    summary = section(report(tmp_path, fake_mac["config"])[1], "Summary")
    assert (
        f"{note}the installed agentsync could not run the reader), so it marked none" in summary.splitlines()
    )
    for step in ("done note=listed-6", "done note=listed-6 synced=2", "failed note=denied"):
        runs = setup_report.read_install_runs(set_up_log(fake_mac, new_mac, listed="", list_step=step))
        assert setup_report.unread_why(runs) is None, step
        assert note not in section(report(tmp_path, fake_mac["config"])[1], "Summary")


def test_loop_stage_and_next_text() -> None:
    stage = setup_report.loop_stage
    assert stage(None, None, False) == "installed"
    assert stage("missing", 0, False) == "installed"
    assert stage("missing", 0, True) == "synced"
    assert stage("draft", 0, True) == "baseline drafted"
    assert stage("confirmed", 0, True) == "baseline confirmed"
    assert stage("before", 0, True) == "before run"
    assert stage("before", 3, True) == "topics 3"
    assert stage("after", 25, True) == "after run"
    text = setup_report.loop_next_text
    assert text(
        "NEXT: no folder is synced yet: ask the operator which folders to sync "
        "(`~/src/agent-context-sync/scripts/install.sh --list-folders` lists them), then run "
        '`~/.local/bin/agentsync add-source "<folder>"` for each, then `/Users/x/.local/bin/agentsync sync`'
    ) == (
        "NEXT: no folder is synced yet: ask the operator which folders to sync (`install.sh --list-folders` "
        'lists them), then run `~/.local/bin/agentsync add-source "<folder>"` for each, then `agentsync sync`'
    ), "agentsync's own ~/.local/bin/agentsync stays whole; every other path, an absolute one too, is cut"
    assert setup_report.AGENTSYNC_BIN == loop.BIN == "~/.local/bin/agentsync"
    assert text("NEXT: keep about 10 in _eval/questions.md") == "NEXT: keep about 10 in _eval/questions.md"
    # On its own, loop_next_text keeps the folder's last part; the Loop line names it in words first
    # (_eval_by_name).
    wait = "WAITING ON YOU: the baseline questions are a draft: in {}, keep up to about 10 in questions.md"
    for folder in ("~/agent-context/docs/_eval", "/Volumes/Work/kb/docs/_eval"):
        assert text(wait.format(folder)) == wait.format("_eval")


@pytest.mark.usefixtures("clean_doctor")
def test_v7_sync_only_reads_synced_and_draft_the_baseline_questions(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """KISS K16b: a v7 setup that installed and synced but did not draft the baseline questions keeps its
    outcome (the install was one command) and says on its Loop line where the loop stopped, with the loop's
    NEXT and no path."""
    for folder in (fake_mac["one"], fake_mac["two"]):
        (folder / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(fake_mac["config"])]) == 0
    v6_install_log(fake_mac)
    write_friction(fake_mac, V7_HAPPY)
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    summary = section(text, "Summary")
    assert summary.strip().splitlines()[0] == (
        "- **outcome: fully one command** (computed: install.sh exit 0; no turn beyond the unavoidable ones)"
    )
    [loop] = [ln for ln in summary.splitlines() if ln.startswith("- Loop: ")]
    assert loop.startswith("- Loop: synced; NEXT: draft the baseline questions"), loop
    # every path cut but agentsync's own fixed one, kept whole (B13 round 4 Q12)
    cut = loop.replace(setup_report.AGENTSYNC_BIN, "")
    assert "~/" not in cut and "/Users/" not in loop and "`~/.local/bin/agentsync sync`" in loop
    assert "- prompt: v7 · " in summary, "a v7 attempt is still judged, and nothing says its copy was old"
    assert "IT draft" not in summary, "v7 has no IT request step"
    assert "NEXT:" not in section(text, "Status")
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["loop_stage"] == ["synced"] and link["prompt_version"] == ["v7"]
    assert link["outcome"] == ["Fully one command"]


def test_the_loop_line_carries_the_note_of_an_unfinished_re_read(fake_mac: dict[str, Path]) -> None:
    """The setup prompt syncs again before the report while sync's note says "sync again". A report whose
    Loop line still shows that note was written at the cap, before the re-read finished; one that shows the
    other wording, after a sync that read none of the files left. No other note is on the line."""
    online = "note: 2 online-only file(s) in one wait for a later sync's download budget; they do not block"
    again = (
        "note: sync again: 412 file(s) in one are still to be read again, once, for what this build's "
        "converters have gained (each sync reads about 2 minutes' worth); it does not block the next step"
    )
    stuck = (
        "note: 3 files in one wait to be read again, and the last sync read none of them (a converter or "
        "on-device OCR that cannot run, or a folder that could not be listed): another sync does not clear "
        "it; it does not block the next step"
    )
    step = (
        "NEXT: stop: the operator confirms the baseline questions (see the WAITING ON YOU lines); session "
        "done"
    )
    wait = "WAITING ON YOU: the baseline questions are a draft"
    for note in (again, stuck):
        hooks = setup_report.ReportHooks(loop_next=lambda _config, note=note: [step, wait, online, note])
        text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
        [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
        assert line.endswith(f"; {step}; {wait}; {note}"), line
        assert "online-only" not in line
    # Two sources, and the last sync did not get to one of them: the loop has a note for each, and both show.
    hooks = setup_report.ReportHooks(loop_next=lambda _config: [step, wait, online, again, stuck])
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert line.endswith(f"; {step}; {wait}; {again}; {stuck}") and "online-only" not in line
    hooks = setup_report.ReportHooks(loop_next=lambda _config: [step, wait, online])
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert line.endswith(f"; {step}; {wait}") and "note:" not in line


def test_a_loop_stage_status_could_not_read_is_not_read_not_installed(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 17 U7: when status could not read the state (a damaged manifest, a [governance] value that
    does not load), it printed no ``last runs:`` line and the report's Loop line and issue link said
    ``installed`` on a Mac that had synced, while the loop hook's DatabaseError left the Loop line no NEXT.
    The stage is not read then, and left out of the link; a stage the baseline or the topics settle, read
    from disk, still stands; and the hook falls back to the NEXT status prints, rule 1's from the FAIL.
    Round 18 V11, V54: status's words follow "status says", in no second bracket. V10: a [governance] that
    does not load hides only status's HOLD and retention lines, so a synced home reads ``Loop: synced``."""
    stage = setup_report.loop_stage
    assert stage(None, None, None) is None and stage("missing", 0, None) is None
    assert stage("draft", 0, None) == "baseline drafted" and stage("missing", 3, None) == "topics 3"
    unread = "status: cannot read the state: the state file cannot be read: file is not a database"
    hooks = setup_report.ReportHooks(status=lambda c: ["loop: skill current · baseline missing", unread])
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    loop = _line(section(text, "Summary"), "- Loop: ")
    assert loop.startswith(
        "- Loop: not read: status says the state file cannot be read: file is not a database;"
    )
    assert "loop_stage=" not in text, "no stage in the issue link"
    hooks = setup_report.ReportHooks(status=lambda c: ["loop: skill current · baseline draft", unread])
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    assert _line(section(text, "Summary"), "- Loop: ").startswith("- Loop: baseline drafted;")
    # V10: a refused [governance] beside "manifest: none yet" is a Mac that never synced, not unread
    refused = "status: cannot read the state: the configuration is refused: the governance.config [FAIL] line"
    none_yet = "manifest: none yet (no sync has run)"
    hooks = setup_report.ReportHooks(
        status=lambda c: ["loop: skill current · baseline missing", f"{refused} says why", none_yet]
    )
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    assert _line(section(text, "Summary"), "- Loop: ").startswith("- Loop: installed;")
    # MF: a home that synced, then its manifest overwritten
    (fake_mac["one"] / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(fake_mac["config"])]) == 0
    expand(load_config(fake_mac["config"]).state_paths.db).write_bytes(b"not a database, " * 256)
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    loop = _line(section(text, "Summary"), "- Loop: ")
    assert loop.startswith("- Loop: not read: status says the state file cannot be read: "), loop
    assert (
        "NEXT: the state_dir.manifest check failed: its fix on the [FAIL] line below is the person's" in loop
    )
    assert "loop_stage=" not in text and "Loop: installed" not in text
    stage_words = loop.removeprefix("- Loop: ").split("; NEXT: ", 1)[0]
    depth = max(itertools.accumulate({"(": 1, ")": -1}.get(ch, 0) for ch in stage_words), default=0)
    assert depth <= 1, stage_words
    # GV: a home that synced, then a [governance] value that does not load
    db = expand(load_config(fake_mac["config"]).state_paths.db)
    db.unlink()
    assert cli.main(["sync", "--config", str(fake_mac["config"])]) == 0
    cfg = fake_mac["config"]
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[governance]\nhold = "yes"\n', encoding="utf-8")
    _rc, text, _ = report(tmp_path, cfg)
    status = section(text, "Status")
    assert (
        "status: cannot read the state: the configuration is refused: the governance.config [FAIL] line "
        "says why" in status
    ), status
    assert "last runs: " in status and "retention:" not in status, status
    assert _line(section(text, "Summary"), "- Loop: ").startswith("- Loop: synced;")
    assert "loop_stage=synced" in text


def test_a_part_the_report_could_not_read_says_why_in_words_not_a_class_name(
    fake_mac: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 17 U21: round 16 T15 dropped the class name from two lines only; with a damaged state file
    and a policy.toml that does not pass, the report still said ``not read (ConfigError)``, ``not measured
    (DatabaseError)`` and status ``cannot read the state: DatabaseError: ...``. Each says why in words and
    where to look; an error no setup should meet keeps a short marker with its class."""
    cfg = fake_mac["config"]
    (fake_mac["one"] / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(cfg)]) == 0
    expand(load_config(cfg).state_paths.db).write_bytes(b"not a database, " * 256)
    (cfg.parent / "policy.toml").write_text('[policy]\nrefuse_unlabelled = "maybe"\n', encoding="utf-8")
    capsys.readouterr()
    cli.main(["status", "--config", str(cfg)])
    out = capsys.readouterr().out
    assert "status: cannot read the state: the state file cannot be read: file is not a database" in out, out
    _rc, text, _ = report(tmp_path, cfg)
    unread = re.compile(r"not read|not measured|not compared|cannot read|cannot be read")
    said = [ln for ln in text.splitlines() if unread.search(ln) and not ln.startswith(("[FAIL]", "[warn]"))]
    assert said, "the fixture's parts are not read"
    named = [ln for ln in said if re.search(r"\b[A-Z]\w*Error\b", ln)]
    assert named == [], named
    assert "not read (the configuration is refused: see Configuration)" in text
    # B13 round 18 V15, V35: the evidence line said to write the report again when idle, which cannot help
    evidence = _line(section(text, "Summary"), "- evidence: ")
    assert "because the state file cannot be read" in evidence and "state_dir.manifest line" in evidence
    assert "idle" not in evidence, evidence
    assert setup_report._why_not_read(sqlite3.DatabaseError("x")) == (
        "the state file cannot be read: see Doctor's state_dir.manifest line"
    )
    assert setup_report._why_not_read(RuntimeError("/Users/jdoe/x")) == "an unexpected error: RuntimeError"


def _slow_pandoc(tmp_path: Path, cfg: Path, seconds: int) -> Path:
    """A ``[convert] pandoc_path`` that takes ``seconds`` to answer ``--version``."""
    exe = tmp_path / "bin" / "pandoc"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(f"#!/bin/sh\ntouch '{exe}.ran'\nsleep {seconds}\necho 'pandoc 3.9'\n", encoding="utf-8")
    exe.chmod(0o755)
    cfg.write_text(
        f'[convert]\npandoc_path = "{exe}"\n\n' + cfg.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return exe


def test_the_reports_pandoc_line_is_the_doctors_check_and_never_starves_doctor(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 17 U48: the Environment section started pandoc first (up to 5 s), and on a busy Mac a slow
    first start took that much of the doctor's share: the first report of a session had no Doctor section
    (`did not finish within 1.9s`), its Summary said `doctor: not run`, and the line read `exited 124`.
    pandoc is started once a report, by the doctor's own check, and the Environment line is filled from it."""
    exe = _slow_pandoc(tmp_path, fake_mac["config"], 30)

    def doctor_fn(config: object) -> list[str]:
        return ["[ok] pandoc — pandoc 3.9 at /opt/pandoc", "[warn] logs — something odd"]

    hooks = setup_report.ReportHooks(doctor=doctor_fn)
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    assert not Path(f"{exe}.ran").exists(), "the report started no pandoc of its own"
    assert "- pandoc: pandoc 3.9 at /opt/pandoc\n" in section(text, "Environment")
    assert "exited 124" not in text and "[warn] logs — something odd" in section(text, "Doctor")


def test_a_doctor_the_time_limit_cuts_off_is_said_so_and_a_slow_pandoc_is_no_exit_code(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 17 U48: a doctor the report's time limit cut off read ``doctor: not run (see Doctor)`` and
    dropped install.sh's own doctor line, which does not depend on this run's; with no doctor hook, a
    pandoc that took past 5 s read ``exited 124: timed out after 5.0s``. Round 18 V81: a configured pandoc's
    reason is a busy Mac's, never the wheel's slow first start (round 15 S29)."""
    monkeypatch.setattr(setup_report, "_DOCTOR_GRACE_S", 0.0)
    write_install_log(fake_mac)
    (fake_mac["setup"] / "install.out").write_text(
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n"
        "[warn] logs — something odd\nfirst sync: converted 1, deferred 0 online-only\n",
        encoding="utf-8",
    )

    def hung(config: object) -> list[str]:
        time.sleep(30)
        return []

    hooks = setup_report.ReportHooks(doctor=hung)
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks, budget_s=4.0)
    line = _line(section(text, "Summary"), "- doctor: ")
    assert line.startswith("- doctor: cut off by the report's time limit after "), line
    assert (
        "(see Doctor) · install.sh's doctor before its first sync (run 20260929T100000Z): 1 warn: logs"
        in line
    )
    assert "- pandoc: not checked: the doctor call was cut off by the report's time limit" in text
    _slow_pandoc(tmp_path, fake_mac["config"], 30)
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    [pandoc] = [ln for ln in section(text, "Environment").splitlines() if ln.startswith("- pandoc: ")]
    assert re.search(
        r"did not answer within \d+\.\ds \(any start can take a minute on a busy Mac\)", pandoc
    ), pandoc
    assert "first start" not in pandoc and "exited 124" not in pandoc, pandoc


@pytest.mark.parametrize(
    ("kind", "which", "why"),
    [
        ("native", " (native arm64 copy)", "any start can take a minute on a busy Mac"),
        ("wheel", "", "a new copy's first start can take a minute, and any start can on a busy Mac"),
    ],
)
def test_the_reports_own_pandoc_time_out_gives_the_first_start_reason_for_the_bundled_copy_only(
    fake_mac: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    which: str,
    why: str,
) -> None:
    """B13 round 18 V81: with no doctor hook the Environment's own pandoc start gave Rosetta's first-start
    reason for every kind, a native arm64 copy too; doctor gives it for the wheel's bundled Intel binary only
    (doctor.py's ``_PANDOC_SLOW_START if kind == "wheel"``), as §16.132 U104 keeps it."""
    from agentsync.convert import pandoc as pandoc_mod  # noqa: PLC0415

    exe = tmp_path / "bin" / "pandoc"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(pandoc_mod, "resolved_pandoc", lambda _path: (exe, kind))
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks(), budget_s=2.5)
    [pandoc] = [ln for ln in section(text, "Environment").splitlines() if ln.startswith("- pandoc: ")]
    assert re.search(
        rf"/bin/pandoc{re.escape(which)} did not answer within \d+\.\ds \({re.escape(why)}\)$", pandoc
    ), pandoc
    assert ("first start" in pandoc) is (kind == "wheel"), pandoc


def _seconds(pattern: str, text: str) -> float:
    found = re.search(pattern, text)
    assert found is not None, (pattern, text)
    return float(found.group(1))


@pytest.mark.timing
def test_the_summarys_doctor_cut_off_seconds_are_the_limit_the_doctor_section_names(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 18 V3, V49: the Summary's ``cut off ... after N s`` was what was left after the cut (about
    3.0 s whatever the budget: the Loop floor and the reserve), while Doctor said ``did not finish within
    5.2s``. Both are now the wait the call was given, one float; a doctor the budget never started says so."""
    monkeypatch.setattr(setup_report, "_DOCTOR_GRACE_S", 0.0)

    def hung(config: object) -> list[str]:
        time.sleep(30)
        return []

    text, _red = setup_report.build_report(
        fake_mac["config"], hooks=setup_report.ReportHooks(doctor=hung), budget_s=8.0
    )
    summary = _seconds(r"- doctor: cut off by the report's time limit after (\d+\.\d)s \(see Doctor\)", text)
    doctor = _seconds(r"did not finish within (\d+\.\d)s", section(text, "Doctor"))
    assert summary == doctor and summary > 3.5, (summary, doctor)
    r = setup_report._Run(fake_mac["config"], setup_report.ReportHooks(doctor=hung), 0.5)
    assert setup_report._doctor(r) == [
        f"Not run: the report's time budget was used up before doctor started; {setup_report._IDLE_AGAIN}."
    ]
    r.facts.doctor = None
    assert setup_report._doctor_line(r, []).startswith(
        "- doctor: not started: the report's time budget was used up (see Doctor)"
    )


@pytest.mark.timing
def test_a_slow_doctor_gets_a_bounded_grace_and_a_cut_off_one_asks_for_the_report_again(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 18 V4: on a busy Mac the setup's last report (load ~260) lost its Doctor section and the
    pandoc line, and nothing asked for it again; the same command 3 minutes later had both. The doctor gets
    at least ``_DOCTOR_GRACE_S``, the deadline moving by the difference so the Loop line keeps its share,
    ``took`` says so, and a doctor cut off past the grace says to write the report again when idle."""
    monkeypatch.setattr(setup_report, "_DOCTOR_GRACE_S", 6.0)

    def slow(config: object) -> list[str]:
        time.sleep(4.0)
        return ["[ok] pandoc — pandoc 3.9 at /opt/pandoc", "[warn] logs — something odd"]

    def loop(config: object) -> list[str]:
        return ["NEXT: draft the baseline questions: run `~/.local/bin/agentsync sync`"]

    t0 = time.monotonic()
    text, _red = setup_report.build_report(
        fake_mac["config"], hooks=setup_report.ReportHooks(doctor=slow, loop_next=loop), budget_s=4.0
    )
    assert time.monotonic() - t0 < 12
    assert "[warn] logs — something odd" in section(text, "Doctor")
    assert "- pandoc: pandoc 3.9 at /opt/pandoc\n" in section(text, "Environment")
    assert re.search(r"^- took: [0-9.]+s \(time limit 4s, [0-9.]+s more for a slow doctor\)$", text, re.M), (
        text
    )
    assert "NEXT: draft the baseline questions" in _line(section(text, "Summary"), "- Loop: ")

    def hung(config: object) -> list[str]:
        time.sleep(60)
        return []

    text, _red = setup_report.build_report(
        fake_mac["config"], hooks=setup_report.ReportHooks(doctor=hung, loop_next=loop), budget_s=4.0
    )
    assert section(text, "Doctor").strip() == (
        f"Cut off: the doctor call did not finish within 6.0s, the report's time limit; "
        f"{setup_report._IDLE_AGAIN}."
    )
    line = _line(section(text, "Summary"), "- doctor: ")
    assert line == (
        f"- doctor: cut off by the report's time limit after 6.0s (see Doctor) · {setup_report._IDLE_AGAIN}"
    ), line
    assert "--report-only" in line
    assert "NEXT: draft the baseline questions" in _line(section(text, "Summary"), "- Loop: ")


def test_the_loop_line_survives_a_missing_or_broken_hook(fake_mac: dict[str, Path]) -> None:
    def crash(config: object) -> list[str]:
        raise RuntimeError("loop exploded")

    _text, summary = summary_of(fake_mac)
    assert "- Loop: unknown (no status loop line); NEXT: not read (no loop hook)" in summary
    hooks = setup_report.ReportHooks(status=lambda c: ["loop: skill current · baseline draft · topics 0"])
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    assert "- Loop: baseline drafted; NEXT: not read (no loop hook)" in section(text, "Summary")
    hooks = setup_report.ReportHooks(loop_next=crash)
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    said = "NEXT: not read (an unexpected error: RuntimeError)"
    assert said in section(text, "Summary"), "the message may hold a path"


def test_the_loop_line_relays_the_first_wait_and_took_counts_the_hook(fake_mac: dict[str, Path]) -> None:
    """KISS K16b review: a WAIT is where setup stopped (a held listing's Allow click), so the Loop line
    carries it, path-free, and every other wait after it; the hook runs before ``took`` is measured."""

    def slow(config: object) -> list[str]:
        time.sleep(1.0)
        return [
            "NEXT: draft the baseline questions: run `~/.local/bin/agentsync sync`",
            "WAITING ON YOU: macOS held the listing of one for a privacy prompt: click Allow on the macOS "
            "prompt (it can sit behind other windows), then run `/Users/x/.local/bin/agentsync sync`",
            "WAITING ON YOU: 2 queued purge(s): preview with `~/.local/bin/agentsync purge --queue "
            "--dry-run` (writes nothing) before you run `~/.local/bin/agentsync purge --queue` (rewrites "
            "history)",
            "note: 3 online-only file(s) in one wait for a later sync's download budget",
        ]

    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks(loop_next=slow))
    [loop] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert loop.endswith(
        "; NEXT: draft the baseline questions: run `~/.local/bin/agentsync sync`; WAITING ON YOU: macOS held "
        "the listing of one for a privacy prompt: click Allow on the macOS prompt (it can sit behind other "
        "windows), then run `agentsync sync`; WAITING ON YOU: 2 queued purge(s): preview with "
        "`~/.local/bin/agentsync purge --queue --dry-run` (writes nothing) before you run "
        "`~/.local/bin/agentsync purge --queue` (rewrites history)"
    ), loop
    took = re.search(r"^- took: ([0-9.]+)s", text, flags=re.MULTILINE)
    assert took is not None and float(took.group(1)) >= 1.0, "took counts the Loop line's hook"


def test_an_exclude_line_never_carries_its_folder_names_into_the_report(fake_mac: dict[str, Path]) -> None:
    """Bring-back S11: the wait for an empty cloud folder prints a ready-to-paste exclude line naming folders
    below a source root, which the Redactor has never seen. The report shows agentsync's own globs as written
    and every other entry as <path>, so a reviewer can check the advice kept the defaults (B13 round 2 R8); a
    hand-written bare folder name is <path> too, and a line cut inside the list is all <path>."""
    wait = (
        "WAITING ON YOU: 2 empty cloud folder(s) keep the listing of one incomplete (deletions held; another "
        'sync does not clear it): if they are meant to be empty, set exclude = ["~$*", "/Fabrikam Bids/", '
        '"/Plans/Tailspin [[]old]/"] in [[source]] id = "one" in ~/agent-context/sources.toml; an excluded '
        "folder is not mirrored if it later gains files"
    )
    hooks = setup_report.ReportHooks(loop_next=lambda config: ["NEXT: session done", wait])
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    [loop] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    # The Loop line cuts a path to its last part first (KISS K16b): "/Plans/..." is no longer anchored.
    shown = 'set exclude = ["~$*", "/<path>/", "<path>"] in [[source]] id = '
    assert shown in loop and loop.endswith("later gains files"), loop
    assert "Fabrikam" not in text and "Tailspin" not in text
    red = setup_report.Redactor()
    kept = (
        'set exclude = ["*.tmp", "Contoso Payroll", ".~lock.*#", "/Empty Subfolder/"] in [[source]] id = "x"'
    )
    assert setup_report._redact_lines(red, [kept]) == (
        'set exclude = ["*.tmp", "<path>", ".~lock.*#", "/<path>/"] in [[source]] id = "x"'
    )
    cut = 'fix: set exclude = ["~$*", "/Fabrikam Bi'
    assert setup_report._redact_lines(red, [cut]) == "fix: set exclude = [<path>]"

    # The v10 rehearsal: the exclude wait, then the draft under rule 5's NEXT. The draft is the one the loop
    # stopped on and comes first; the exclude wait follows it, still without its folder names.
    draft = (
        "WAITING ON YOU: the baseline questions are a draft: in _eval, keep up to about 10 in questions.md "
        "(all of them if there are fewer), correct the answers in answers.md, and change both files to "
        "status: confirmed"
    )
    stop = (
        "NEXT: stop: the operator confirms the baseline questions (see the WAITING ON YOU lines); session "
        "done"
    )
    hooks = setup_report.ReportHooks(loop_next=lambda config: [stop, wait, draft])
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    [loop] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    # B13 round 4 Q13: the command printed the draft after the exclude wait, and the line says so
    moved = "; also waiting, which the command prints before it: WAITING ON YOU: "
    assert f"; {draft}{moved}2 empty cloud folder(s) keep the listing of " in loop, loop
    assert shown in loop and loop.endswith("later gains files"), loop
    assert "Fabrikam" not in text and "Tailspin" not in text
    # The draft already first: the command's order, and no clause
    hooks = setup_report.ReportHooks(loop_next=lambda config: [stop, draft, wait])
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    [loop] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert f"; {draft}; WAITING ON YOU: 2 empty cloud folder(s) keep the listing of " in loop, loop
    assert "also waiting" not in loop
    # B13 round 5 F9: the draft in the middle of three, so only the first of the others comes before it
    hooks = setup_report.ReportHooks(
        loop_next=lambda config: [stop, wait, draft, wait.replace("2 empty", "3 empty")]
    )
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    [loop] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert (
        f"; {draft}; also waiting (the command prints the first of these before it): WAITING ON YOU: 2 empty"
        in (loop)
    ), loop
    assert "wait(s)" not in loop and "below" not in loop and "the 1 " not in loop


@pytest.mark.usefixtures("clean_doctor")
def test_a_draft_baseline_shows_its_wait_on_the_loop_line(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    """Rule 5's NEXT names the waits: the Loop line carries the one it stopped on."""
    for folder in (fake_mac["one"], fake_mac["two"]):
        (folder / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(fake_mac["config"])]) == 0
    evals = expand(load_config(fake_mac["config"]).docs_repo) / "_eval"
    evals.mkdir(parents=True, exist_ok=True)
    (evals / "questions.md").write_text("status: draft\n\n1. Who approved it?\n", encoding="utf-8")
    (evals / "answers.md").write_text("status: draft\n\n1. Finance.\n", encoding="utf-8")
    write_friction(fake_mac, V7_HAPPY)
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    [loop] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert loop == (
        "- Loop: baseline drafted; NEXT: stop: the operator confirms the baseline questions (see the WAITING "
        "ON YOU line); session done; WAITING ON YOU: the baseline questions are a draft: in the docs repo's "
        "_eval folder, keep up to about 10 in questions.md (all of them if there are fewer), correct the "
        "answers in answers.md, and change both files to status: confirmed"
    ), loop
    # Path-free but findable: the Configuration part names the docs repo the Summary's words point at.
    config_part = section(text, "Configuration").splitlines()
    assert [ln for ln in config_part if ln.startswith("- docs repo: ~/")], text


@pytest.mark.parametrize("under_home", [True, False])
def test_the_loop_line_keeps_no_part_of_a_docs_repo_path_with_spaces(
    fake_mac: dict[str, Path], tmp_path: Path, under_home: bool
) -> None:
    """Review of the v9 rehearsal fixes (2026-10-07): the draft baseline's wait names the docs repo's
    ``_eval`` folder by its path, and the Loop line names it in words (the docs repo's ``_eval`` folder). A
    path was read up to its first space, so a docs repo at "~/Client Alpha/kb docs" left ``in Client
    Alpha/kb docs/_eval`` in a line that holds no path. The folder is now taken out as the loop wrote it,
    with the loop's own lines here: under the home folder (``~/...``) and outside it (the full path)."""
    docs = (fake_mac["home"] if under_home else tmp_path) / "Client Alpha" / "kb docs"
    cfg = fake_mac["config"]
    cfg.write_text(
        f'[agentsync]\ndocs_repo = "{docs}"\n\n' + cfg.read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert cli.main(["add-source", str(fake_mac["one"]), "--config", str(cfg)]) == 0  # makes the docs repo
    evals = docs / "_eval"
    evals.mkdir(parents=True, exist_ok=True)
    (evals / "questions.md").write_text("status: draft\n\n1. Who approved it?\n", encoding="utf-8")
    (evals / "answers.md").write_text("status: draft\n\n1. Finance.\n", encoding="utf-8")
    written = loop.next_lines(load_config(cfg), count_queue=False)
    shown = "~/Client Alpha/kb docs/_eval" if under_home else f"{docs}/_eval"
    draft = "WAITING ON YOU: the baseline questions are a draft: in {}, keep up to about 10 in questions.md "
    assert [ln for ln in written if ln.startswith(draft.format(shown))], written

    hooks = setup_report.ReportHooks(loop_next=lambda config: loop.next_lines(config, count_queue=False))
    text, _red = setup_report.build_report(cfg, hooks=hooks)
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert line.endswith(
        f"; {draft.format(setup_report._EVAL_SHOWN)}(all of them if there are fewer), correct the answers in "
        "answers.md, and change both files to status: confirmed"
    ), line
    assert "Client" not in line and "kb docs" not in line and "/" not in line.split("; WAITING ON YOU: ")[1]

    # Rules 4, 6 and 8 name the docs repo's AGENTS.md by its path (B13 N34): in words, "the docs repo's
    # AGENTS.md", since a bare AGENTS.md would be the agent's own project's (B13 round 7 H3).
    rule4 = f"NEXT: draft the baseline questions: follow step 1 (Draft) of {loop._baseline(docs)}, then run x"
    hooks = setup_report.ReportHooks(loop_next=lambda config: [rule4])
    text, _red = setup_report.build_report(cfg, hooks=hooks)
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert '"Baseline questions" section (also in the docs repo\'s AGENTS.md), then run x' in line, line
    assert "Client" not in line and "kb docs" not in line, line

    # The waits for empty and unlisted folders name the config file by its path (§16.36): "the config file"
    # and its name, beside the docs repo's files named in words (B13 round 9 K43).
    spaced = docs.parent / "ctx" / "sources.toml"
    spaced.parent.mkdir()
    spaced.write_text(cfg.read_text(encoding="utf-8"), encoding="utf-8")
    where = loop._shown(load_config(spaced).config_path)
    assert where == ("~/Client Alpha/ctx/sources.toml" if under_home else str(spaced))
    hooks = setup_report.ReportHooks(
        loop_next=lambda config: [
            "NEXT: none",
            f"WAITING ON YOU: {arm_local.exclude_advice(config.sources[0], ['Empty'], where)}",
            "WAITING ON YOU: one or more folders in work could not be listed (no access, or a missing "
            "folder or sentinel; another sync does not clear it): it; grant Files and Folders access, or add "
            f"each one to that source's exclude in {where}",
        ]
    )
    text, _red = setup_report.build_report(spaced, hooks=hooks)
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert " in the config file sources.toml; an excluded folder is not mirrored" in line, line
    assert line.endswith(" or add each one to that source's exclude in the config file sources.toml"), line
    assert "Client" not in line and "ctx" not in line


@pytest.mark.usefixtures("clean_doctor")
def test_the_loop_line_shows_the_wait_the_loop_stopped_on_not_the_first_one(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """Field report 2026-10-07: the Loop line read "NEXT: stop: the operator confirms the baseline questions
    (WAITING ON YOU below)" and then showed the purge queue's wait with "(+2 more)". The loop prints the
    queued purges first, so the first wait hid the one its own NEXT points at. A listing macOS holds for an
    Allow click was hidden the same way. Both are shown here from the real loop, in its own order."""
    for folder in (fake_mac["one"], fake_mac["two"]):
        (folder / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(fake_mac["config"])]) == 0
    config = load_config(fake_mac["config"])
    held = next(src.id for src in config.sources if src.path == fake_mac["one"])
    selector = governance.PurgeSelector(source_id=held, path_glob="*")
    assert governance.enqueue_purge(config.state_paths.root, selector, governance.PurgeReason.OPERATOR)
    purge = (
        "WAITING ON YOU: 1 queued purge: preview with `~/.local/bin/agentsync purge --queue --dry-run` "
        "(writes nothing) before you run `~/.local/bin/agentsync purge --queue` (rewrites history)"
    )

    def loop_line() -> str:
        rc, text, _ = report(tmp_path, fake_mac["config"])
        assert rc == 0
        [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
        return line

    def waits() -> list[str]:
        return [ln for ln in loop.next_lines(config, count_queue=False) if ln.startswith(loop.WAIT_PREFIX)]

    # No wait is the loop's step: the first one is shown, as before.
    assert len(waits()) == 1 and loop_line().endswith(f"; {purge}")

    # A held listing: the loop prints it after the purge queue, and it is the click every sync waits for.
    with Manifest(config.state_paths.db) as manifest:
        run_id = manifest.begin_run(CycleMode.POLL, host="mac", pid=1)
        manifest.record_source_pass(
            run_id,
            held,
            pass_kind=PassKind.FULL,
            enumeration_complete=False,
            cursor_reset=False,
            counts={},
            skipped_reason=cycle.LISTING_HELD + "click Allow on the macOS prompt",
            error=None,
        )
        manifest.finish_run(run_id, status="ok", commit_sha=None, counts={"converted": 0})
    found = waits()
    assert len(found) == 2 and "queued purge" in found[0] and found[1].startswith(setup_report._WAIT_HELD)
    line = loop_line()
    assert re.search(
        r"; WAITING ON YOU: macOS held the listing of <(?:source|folder)-\d+> for a privacy prompt: click "
        r"Allow on the macOS prompt \(it can sit behind other windows\), then run "
        r"`~/\.local/bin/agentsync sync`; also waiting, which the command prints before it: WAITING ON "
        r"YOU: 1 queued purge: preview with "
        r"`~/\.local/bin/agentsync purge --queue --dry-run` \(writes nothing\) before you run "
        r"`~/\.local/bin/agentsync purge --queue` \(rewrites history\)$",
        line,
    ), line
    assert line.index("macOS held") < line.index("queued purge") and setup_report._WAIT_NAMED not in line

    # A draft baseline: rule 5's NEXT points at the waits (see the WAITING ON YOU lines) without saying where
    # they are, and the loop prints the draft wait last.
    evals = expand(config.docs_repo) / "_eval"
    evals.mkdir(parents=True, exist_ok=True)
    (evals / "questions.md").write_text("status: draft\n\n1. Who approved it?\n", encoding="utf-8")
    (evals / "answers.md").write_text("status: draft\n\n1. Finance.\n", encoding="utf-8")
    found = waits()
    assert len(found) == 3 and found[-1].startswith(setup_report._WAIT_DRAFT)
    assert setup_report._WAIT_NAMED in loop.next_lines(config, count_queue=False)[0]
    line = loop_line()
    assert re.search(
        r"; NEXT: stop: the operator confirms the baseline questions \(see the WAITING ON YOU lines\); "
        r"session done; "
        r"WAITING ON YOU: the baseline questions are a draft: in the docs repo's _eval folder, keep up to "
        r"about 10 in questions\.md \(all of them if there are fewer\), "
        r"correct the answers in answers\.md, and change both files to status: confirmed; also waiting, "
        r"which the command prints before it: WAITING ON YOU: 1 "
        r"queued purge: preview with `~/\.local/bin/agentsync purge --queue --dry-run` \(writes "
        r"nothing\) before you run `~/\.local/bin/agentsync purge --queue` \(rewrites history\); WAITING ON "
        r"YOU: macOS held the listing of "
        r"<(?:source|folder)-\d+> .*$",
        line,
    ), line


def test_the_wait_the_loop_stopped_on_is_picked_by_what_its_next_line_says() -> None:
    """The rule by itself, on lines in the loop's order. The draft wait is shown only when the NEXT line
    points at a wait; without that a held listing comes before it; and a NEXT that points at a wait the
    lines do not have falls back to the same order."""
    purge = (
        "WAITING ON YOU: 14 queued purge(s): preview with `~/.local/bin/agentsync purge --queue --dry-run` "
        "(writes nothing) before you run `~/.local/bin/agentsync purge --queue` (rewrites history)"
    )
    breaker = "WAITING ON YOU: the deletion breaker tripped on work (3 file(s) gone)"
    held = "WAITING ON YOU: macOS held the listing of work for a privacy prompt: click Allow"
    draft = "WAITING ON YOU: the baseline questions are a draft: in ~/agent-context/docs/_eval, keep up to"
    rule5 = (
        "NEXT: stop: the operator confirms the baseline questions (see the WAITING ON YOU lines); session "
        "done"
    )
    other = "NEXT: run `~/.local/bin/agentsync curate` and follow its NEXT line"
    pick = setup_report._stopped_wait
    assert pick(rule5, [purge, breaker, held, draft]) == draft
    assert pick(other, [purge, breaker, held, draft]) == held
    assert pick(other, [purge, breaker, draft]) == purge
    assert pick(rule5, [purge, breaker, held]) == held
    assert pick(rule5, [purge, breaker]) == purge
    assert pick(None, [breaker]) == breaker


@pytest.mark.usefixtures("roomy_calls")
@pytest.mark.parametrize(
    ("fix", "step"),
    [
        ("rm {home}/state.db", "do what the fix on its [FAIL] line below says"),
        # B13 round 14 Q1, Q33: a person's fix reads as theirs, as install.sh's NEXT at the finish says
        (
            'fix path in [[source]] id = "contoso", or put the folder back under that name in OneDrive, or '
            "sync that folder in OneDrive",
            f"its fix on the [FAIL] line below is the person's ({cli.PERSONS_KINDS}), not a step for the "
            "agent: show it to them",
        ),
    ],
)
def test_a_doctor_fail_is_the_loop_lines_next_and_no_friction_log_hides_the_it_draft(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fix: str, step: str
) -> None:
    """The CLI wiring: the doctor hook's ERROR FAILs reach ``loop_next`` as rule 1 (Doctor runs first). With
    no friction log the report reads the newest prompt (v7), which has no IT request step."""
    real = cli._status_checks

    def failing(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
        broken = doctor.CheckResult(
            "manifest.integrity", False, "broken", doctor.Severity.ERROR, fix.format(home=fake_mac["home"])
        )
        return [*(c for c in real(config, offline=offline) if c.ok), broken]

    assert cli.main(["sync", "--config", str(fake_mac["config"])]) == 0  # writes the skill (rule 1's first)
    monkeypatch.setattr(cli, "_status_checks", failing)
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    summary = section(text, "Summary")
    [loop] = [ln for ln in summary.splitlines() if ln.startswith("- Loop: ")]
    assert f"; NEXT: the manifest.integrity check failed: {step}" in loop
    assert "~/" not in loop and "/Users/" not in loop and "state.db" not in loop, "a fix is never copied"
    assert "IT draft" not in summary, "no friction log reads as v7, which has no IT request step"


def test_v6_every_logged_question_and_click_is_beyond_the_expected_ones(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    v6_install_log(fake_mac, simulated=False, agent="seconds=2 rc=0 result=done")
    write_friction(
        fake_mac,
        _insert_before(
            "2026-09-29T10:01:10Z | end",
            "2026-09-29T09:58:30Z | step 1 | question | asked whether to install the CLT | say it first\n"
            "2026-09-29T10:00:20Z | step 2 | click | clicked Allow on a Keychain prompt | -\n"
            "2026-09-29T10:00:30Z | step 2 | approval | approved install.sh | pre-allow it\n",
            V6_HAPPY,
        ),
    )
    monkeypatch.setattr(setup_report, "home_path", lambda: "/Users/jdoe")
    _text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: worked with help** (computed: install.sh exit 0; 1 question beyond the folder "
        "question; 1 click beyond the Allow clicks; 1 approval)"
    )
    assert (
        "- human turns: 4 (2 questions; 1 click beyond the announced Allow clicks (not logged); 1 approval)"
        in summary
    )
    assert (
        "- expected turns: the folder question (step 1; not logged as a question) · the announced Allow "
        "clicks (steps 1 and 2; not logged)" in summary
    )
    items = summary.split("Items that were not one command (attempt 1):", 1)[1].split("### Run", 1)[0]
    assert re.findall(r"^- F(\d+) · step (\d) · (\w+) · ", items, flags=re.MULTILINE) == [
        ("4", "1", "question"),
        ("5", "2", "click"),
        ("6", "2", "approval"),
    ]


def test_v7_announces_one_allow_click_in_step_1(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """KISS K11b review: v7's step 2 starts no launcher, so the only announced Allow click is step 1's."""
    v6_install_log(fake_mac, simulated=False, agent="seconds=2 rc=0 result=done")
    write_friction(
        fake_mac,
        _insert_before(
            "2026-09-29T10:01:10Z | end",
            "2026-09-29T10:00:20Z | step 2 | click | clicked Allow on a Keychain prompt | -\n",
            V7_HAPPY,
        ),
    )
    monkeypatch.setattr(setup_report, "home_path", lambda: "/Users/jdoe")
    _text, summary = summary_of(fake_mac)
    assert "1 click beyond the announced Allow click (not logged)" in summary
    assert (
        "- expected turns: the folder question (step 1; not logged as a question) · the announced Allow "
        "click (step 1; not logged)" in summary
    )
    assert "steps 1 and 2" not in summary


def test_v6_an_install_step_error_is_resolved_by_an_install_run_that_ended_0(
    fake_mac: dict[str, Path],
) -> None:
    """v6 logs no step ends: install.log's successful install run resolves an error (L-series). Its step 2 is
    the one install.sh command, as v7's (with ``--confirm-install-agent`` it ends 0 only once the background
    job runs), so a step 2 error logged after that run ended 0 is agent friction too (B13 H21; v10 E3.h: the
    field Mac's v6 attempt read "failed at step 2" in every later report)."""
    v6_install_log(fake_mac)
    early = _insert_before(
        "2026-09-29T10:01:10Z | end",
        "2026-09-29T09:59:00Z | step 2 | error | my tool stopped install.sh at 2 minutes (exit 124) | -\n",
        V6_HAPPY,
    )
    write_friction(fake_mac, early)
    _text, summary = summary_of(fake_mac)
    assert summary.strip().startswith("- **outcome: fully one command**"), "the 10:00 re-run ended rc 0"
    assert (
        "- agent friction: 0 deviation, 0 prompt, 1 error (F4); none of these changes the outcome" in summary
    )
    late = _insert_before(
        "2026-09-29T10:01:10Z | end",
        "2026-09-29T10:01:00Z | step 2 | error | Exit 0, no failure: a made-up complaint | -\n",
        V6_HAPPY,
    )
    write_friction(fake_mac, late)
    text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: fully one command** (computed: install.sh exit 0; no turn beyond the unavoidable ones)"
    )
    assert (
        "- agent friction: 0 deviation, 0 prompt, 1 error (F4); none of these changes the outcome" in summary
    )
    assert parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)["outcome"] == ["Fully one command"]
    [attempt] = setup_report.parse_friction(late).attempts
    assert attempt.layout is setup_report.PROMPT_LAYOUTS[6]
    runs = setup_report.read_install_runs(fake_mac["setup"] / "install.log")
    assert setup_report.stopping_error(attempt, runs) is None
    assert setup_report.stopping_error(attempt, [r for r in runs if r.list_only]) is attempt.events[0], (
        "only an install run resolves it"
    )
    [step_1] = setup_report.parse_friction(late.replace("| step 2 | error |", "| step 1 | error |")).attempts
    assert setup_report.stopping_error(step_1, runs) is step_1.events[0], (
        "a step 1 error still needs a run started after it"
    )
    v6_install_log(fake_mac, rc=1)
    assert (
        summary_of(fake_mac)[1]
        .strip()
        .startswith("- **outcome: failed at step 2** (computed: install.sh exited 1")
    ), "a failed install is still a failed step 2"
    # The field shape: that v6 attempt, then a v10 attempt the next day.
    v6_install_log(fake_mac)
    write_install_log(fake_mac, start="2026-09-30T10:00:00Z", run="20260930T100000Z-77", append=True)
    write_friction(fake_mac, late + V10_HAPPY.replace("2026-09-29T", "2026-09-30T"))
    _text, summary = summary_of(fake_mac)
    assert "- attempt: 2 of 2; earlier: attempt 1 fully one command" in summary
    (fake_mac["setup"] / "install.log").write_text(LIST_RUN, encoding="utf-8")
    write_friction(
        fake_mac,
        _insert_before(
            "2026-09-29T10:01:10Z | end",
            "2026-09-29T09:58:20Z | step 1 | error | git clone failed: proxy (exit 128) | set HTTPS_PROXY\n",
            V6_HAPPY,
        ),
    )
    assert (
        summary_of(fake_mac)[1]
        .strip()
        .startswith("- **outcome: failed at step 1** (computed: no install.sh run; step 1 logged an error)")
    )


LATE_LINE = "2026-10-02T08:00:00Z | step 1 | error | git pull --ff-only exited 1 | -\n"
"""Logged by a session whose step 1 stopped before ``install.sh --log-start``: no ``Attempt:`` header."""


def test_a_line_between_a_finished_attempt_and_the_next_header_is_its_own_attempt(
    fake_mac: dict[str, Path],
) -> None:
    """Field report 2026-10-06: a session days later logged one error with no header. Folded into the
    finished attempt before it, it made that attempt "failed at step 1" although its install had ended 0."""
    v6_install_log(fake_mac)
    write_install_log(fake_mac, start="2026-10-03T09:00:30Z", run="20261003T090030Z-7", append=True)
    third = V7_HAPPY.replace("2026-09-29T09:58:00Z", "2026-10-03T09:00:00Z").replace(
        "2026-09-29T10:01:10Z", "2026-10-03T09:03:00Z"
    )
    write_friction(fake_mac, V6_HAPPY + LATE_LINE + third)
    text, summary = summary_of(fake_mac)
    fr = section(text, "Agent friction log")
    assert "3 attempts:" in fr
    assert "- attempt 1 (lines 1-4; started 2026-09-29T09:58:00Z; prompt v6; " in fr
    assert re.search(r"^- attempt 1 \(.*\): fully one command; 1 event line: 1 finished", fr, re.M)
    assert (
        # one line is "line 5", and no bracket sits inside the attempt's own (B13 round 10 L32)
        "- attempt 2 (line 5; no Attempt: line, since it was logged after the previous attempt finished; "
        "prompt not stated; agent not stated): failed at step 1; 1 event line: 1 error; "
        'no "end | finished" line' in fr
    )
    assert "- attempt 3 (lines 6-9; started 2026-10-03T09:00:00Z; prompt v7; " in fr
    assert "- attempt: 3 of 3; earlier: attempt 1 fully one command, attempt 2 failed at step 1" in summary
    assert "- friction (attempt" not in summary, "only its end line: it adds nothing (B13 round 12 N20)"


def test_a_line_logged_after_the_closing_line_stays_and_cannot_stop_the_run(
    fake_mac: dict[str, Path],
) -> None:
    """A line logged within minutes of the closing line is the same session's (the report says "run
    setup-report again" for it), a step 1 error included: it must not become a header-less latest attempt,
    and it did not stop a finished run."""
    v6_install_log(fake_mac)
    late = "2026-09-29T10:02:00Z | step 1 | error | noticed the listing was slow (exit 0) | -\n"
    write_friction(fake_mac, V7_HAPPY + late)
    [attempt] = setup_report.parse_friction(V7_HAPPY + late).attempts
    assert attempt.finished and attempt.last_line == 5
    assert setup_report.stopping_error(attempt, ()) is None
    text, summary = summary_of(fake_mac)
    assert "1 attempt:" in section(text, "Agent friction log") and "- attempt:" not in summary
    assert "**outcome: failed" not in summary and "- prompt: v7 · " in summary
    assert "0 prompt, 1 error (F5); none of these changes the outcome by itself" in summary


def test_a_step_1_error_logged_long_after_the_closing_line_is_the_latest_attempt(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session whose step 1 stopped before ``install.sh --log-start``, and whose agent logged the error,
    writes its own report: it has no ``Attempt:`` header, and none follows. That
    session failed at step 1; folded into the attempt before it, the report read "fully one command"."""
    v6_install_log(fake_mac)
    late = "2026-10-06T02:16:01Z | step 1 | error | install.sh --version exited 127 | -\n"
    write_friction(fake_mac, V7_HAPPY + late)
    monkeypatch.setattr(setup_report, "home_path", lambda: "/Users/jdoe")  # a real Mac: a click is possible
    text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: failed at step 1** (computed: no install.sh run; step 1 logged an error)"
    )
    assert "- attempt: 2 of 2; earlier: attempt 1 fully one command" in summary
    assert (
        "- note: attempt 2 has no Attempt: line. It holds a step 1 error (F5) logged after attempt 1"
        in summary
    )
    assert "WARNING" not in summary, "install.sh --report-only cannot close an attempt that has no header"
    assert "- F5 · step 1 · error · install.sh --version exited 127" in summary
    assert "- human turns: 0 (0 questions; 0 clicks; " in summary, "stopped before the list: no click"
    assert (
        "- expected turns: none (step 1's command stopped before install.sh --log-start, before the folder "
        "list: no folder question and no Allow click)" in summary
    )
    assert (
        # one line is "line 5", and no bracket sits inside the attempt's own (B13 round 10 L32)
        "- attempt 2 (line 5; no Attempt: line, since it was logged after the previous attempt finished; "
        "prompt not stated; agent not stated): failed at step 1; 1 event line: 1 error; "
        'no "end | finished" line' in section(text, "Agent friction log")
    )
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["outcome"] == ["Failed at step 1 (preflight)"] and "prompt_version" not in link

    def attempts(minute: int) -> int:
        line = f"2026-09-29T10:{minute}:00Z | step 1 | error | git pull --ff-only exited 1 | -\n"
        return len(setup_report.parse_friction(V7_HAPPY + line).attempts)

    assert (attempts(11), attempts(12)) == (1, 2), "the closing line is 10:01:10: ten minutes is the line"


@pytest.mark.usefixtures("clean_doctor")
def test_a_proxy_only_git_knows_is_redacted(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    """B13 H1 (skeptic C2): since v11 step 1's git stop logs git's error, which names the proxy git used
    (``Could not resolve proxy: <host>``). A proxy set only in git's config (``~/.gitconfig``, or the
    checkout's ``.git/config`` under a URL section) was not registered and stood in the report in clear. Only
    the redacted direction is asserted: the Mac's own /etc/gitconfig is read too."""
    home = Path.home()
    gitconfig = "[http]\n\tproxy = http://proxy.fabrikam.invalid:3128\n"
    (home / ".gitconfig").write_text(gitconfig, encoding="utf-8")
    checkout = home / "src" / "agent-context-sync" / ".git"
    checkout.mkdir(parents=True)
    section = '[http "https://github.com/"]\n\tProxy = "gw.fabrikam-two.invalid:8080" ; corporate\n'
    (checkout / "config").write_text(section, encoding="utf-8")
    v6_install_log(fake_mac)
    lines = (
        "2026-09-29T10:01:20Z | step 1 | error | git pull: Could not resolve proxy: proxy.fabrikam.invalid"
        " (exit 1) | -\n"
        "2026-09-29T10:01:21Z | step 1 | error | git pull: Could not resolve proxy: gw.fabrikam-two.invalid"
        " (exit 1) | -\n"
    )
    write_friction(fake_mac, V11_HAPPY + lines)
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "fabrikam" not in text.lower()
    assert len(re.findall(r"Could not resolve proxy: <proxy-\d+> \(exit 1\)", text)) >= 2, text


def test_a_git_url_rewrite_host_and_path_are_redacted(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 7 H20: v15's git stop names a URL rewrite as a cause, and git's error then names the
    rewrite's host, which the report printed in clear (only a proxy was registered). The bases of
    ``[url "<base>"] insteadOf`` are read as files, with no git process: an https base and an scp base by
    host and by their path, never GitHub's own host; the Environment line counts them."""
    (Path.home() / ".gitconfig").write_text(
        '[url "https://jdoe@mirror.northwind.invalid:8443/northwind-team/"]\n'
        "\tinsteadOf = https://github.com/\n"
        '[url "git@scp.northwind-two.invalid:northwind-scp/"]\n'
        "\tInsteadOf = https://github.com/someone/\n"
        '[url "git@github.com:"]\n'
        "\tinsteadOf = https://github.com/\n"
        '[url "https://push.northwind-three.invalid/"]\n'
        "\tpushInsteadOf = https://github.com/\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", str(tmp_path / "no-tools"))  # step 1's Xcode stop: no git at all
    assert setup_report._git_rewrite_bases() == [
        "https://jdoe@mirror.northwind.invalid:8443/northwind-team/",
        "git@scp.northwind-two.invalid:northwind-scp/",
        "git@github.com:",
    ]
    v6_install_log(fake_mac)
    lines = (
        "2026-09-29T10:01:20Z | step 1 | error | git clone https://mirror.northwind.invalid:8443/northwind-team/"
        "someone/agent-context-sync.git/: Could not resolve host: mirror.northwind.invalid (exit 128) | -\n"
        "2026-09-29T10:01:21Z | step 1 | error | git pull git@scp.northwind-two.invalid:northwind-scp/x.git: "
        "Could not resolve hostname scp.northwind-two.invalid (exit 1) | -\n"
    )
    write_friction(fake_mac, V11_HAPPY + lines)
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "northwind" not in text.lower(), [ln for ln in text.splitlines() if "northwind" in ln.lower()]
    for part in (section(text, "Summary"), section(text, "Agent friction log")):
        # B13 round 8 J1, J20: the host is one placeholder in the URL and in git's resolver error, its port
        # stays, and the company-named path is a value of its own, after its own "/" (B13 round 9 K10: the
        # base read as one written without its slash); scripts/install.sh writes the same line
        assert (
            "git clone https://<proxy-1>:8443/<proxy-2>/someone/agent-context-sync.git/: Could not resolve "
            "host: <proxy-1> (exit 128)" in part
        ), part
        # git@host is an email address to the redactor; the scp base's path is a value of its own
        assert re.search(r"git pull <email-\d+>:<proxy-\d+>/x\.git: ", part), part
    assert "URL rewrite in git's config: set (3)" in section(text, "Environment")
    assert text.rstrip("\n").splitlines()[-1].startswith(setup_report.ISSUE_URL), "GitHub's host is kept"
    # a path of a server's generic words is no value of its own: it would cut /usr/bin/git (a reviewer's
    # finding); a URL base's path goes with its host
    red = setup_report.Redactor()
    for base in ("https://mirror.northwind-four.invalid/git/", "git@scm.northwind-five.invalid:scm/repos/"):
        found = setup_report._rewrite_values(base)
        assert found is not None and found[2] == "", (base, found)
        host, value, _tail = found
        red.add("proxy", host, ignore_case=True)
        red.add("proxy", value, ignore_case=True)
    shown = red.redact(
        "/usr/bin/git; /opt/homebrew/bin/git; ~/src/agent-context-sync/.git/config; /Git/hooks"
    )
    assert shown == "/usr/bin/git; /opt/homebrew/bin/git; ~/src/agent-context-sync/.git/config; /Git/hooks"
    assert setup_report._rewrite_values("https://GitHub.com/") is None
    assert setup_report._rewrite_values("ssh://git@GITHUB.com/") is None
    # B13 round 8 J1, J20: a generic path is left in clear and the host keeps one number; a path of report
    # words (tools, dev) is no value either, so the report's own words stay; a company path is its own value
    for base, said, shown in (
        (
            "https://gitmirror.northwind-six.invalid/scm/",
            "git clone https://gitmirror.northwind-six.invalid/scm/jdoe/x.git/: Could not resolve host: "
            "gitmirror.northwind-six.invalid; /usr/bin/git",
            "git clone https://<proxy-1>/scm/jdoe/x.git/: Could not resolve host: <proxy-1>; /usr/bin/git",
        ),
        (
            "https://dev.northwind-seven.invalid/tools/",
            "https://dev.northwind-seven.invalid/tools/x: Xcode command line tools missing; > /dev/null",
            "https://<proxy-1>/tools/x: Xcode command line tools missing; > /dev/null",
        ),
        (
            "https://dev.example.invalid/northwind-team/",
            "https://dev.example.invalid/northwind-team/jdoe/x: Could not resolve host: dev.example.invalid",
            "https://<proxy-1>/<proxy-2>/jdoe/x: Could not resolve host: <proxy-1>",
        ),
        # B13 round 9 K46: a base that ends at a repository name; git prints it before .git, and an agent
        # before -tools, _x or .v2: the path is redacted there too, as the shell's flag e did
        (
            "https://mirror.northwind.invalid/northwind-team",
            "https://mirror.northwind.invalid/northwind-team.git and .../northwind-team-tools and "
            "/northwind-team_x and /northwind-team.v2 and /northwind-teamwork",
            "https://<proxy-1>/<proxy-2>.git and .../<proxy-2>-tools and /<proxy-2>_x and /<proxy-2>.v2 and "
            "/northwind-teamwork",
        ),
        # B13 round 9 K77, built in round 10 as L69: gh is a generic path word, so a /gh/ rewrite path is no
        # value and /opt/homebrew/bin/gh stays in clear; the tool-path skip stays rejected (it leaks a
        # company's /opt/<company>/bin)
        (
            "https://dev.example.invalid/gh/",
            "https://dev.example.invalid/gh/tools.git/; /opt/homebrew/bin/gh auth status; "
            "~/.config/gh/hosts.yml; gh run list; high; x:/gh",
            "https://<proxy-1>/gh/tools.git/; /opt/homebrew/bin/gh auth status; ~/.config/gh/hosts.yml; gh "
            "run list; high; x:/gh",
        ),
        # a path with a company word is still a value; an escaped form keeps its escape and separator (K10)
        (
            "https://dev.example.invalid/contoso-gh/",
            "https://dev.example.invalid/contoso-gh/x; /opt/homebrew/bin/gh; "
            "https:\\/\\/dev.example.invalid\\/contoso-gh\\/x.git",
            "https://<proxy-1>/<proxy-2>/x; /opt/homebrew/bin/gh; https:\\/\\/<proxy-1>\\/<proxy-2>\\/x.git",
        ),
    ):
        red = setup_report.Redactor()
        found = setup_report._rewrite_values(base)
        assert found is not None, base
        host, value, tail = found
        setup_report._add_proxy_host(red, host)
        if value.lower() != host.lower():
            red.add("proxy", value, ignore_case=True)
        if tail:
            red.add("proxy", tail, ignore_case=True, keep_lead=True)
        assert red.redact(said) == shown, (base, red.redact(said))
    # B13 round 8 J2: the legend says a rewrite is a <proxy-N> too
    legend = section(text, "Redaction")
    assert "<proxy-N> proxy host, git URL rewrite (insteadOf) host and its company-named path, or the " in (
        legend
    ), legend
    (Path.home() / ".gitconfig").unlink()
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "URL rewrite in git's config: not set" in section(text, "Environment")


@pytest.mark.usefixtures("clean_doctor")
def test_a_go_ahead_in_the_chat_is_a_turn_and_what_it_allowed_is_agent_friction(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """2026-10-07 U4 (B13 N43): since v11 a go-ahead given in the chat is a question line of its own beside
    the deviation it allowed: the question is a turn beyond the folder question, the deviation stays agent
    friction."""
    v6_install_log(fake_mac)
    lines = (
        "2026-09-29T10:00:40Z | step 3 | question | the person gave a go-ahead for the printed fix | -\n"
        "2026-09-29T10:00:41Z | step 3 | deviation | ran the printed fix, which the prompt does not say | -\n"
    )
    happy = V11_HAPPY.replace("Prompt: v11", f"Prompt: v{setup_report.PROMPT_VERSION}")
    write_friction(fake_mac, _insert_before("2026-09-29T10:01:10Z | end", lines, happy))
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    summary = section(text, "Summary")
    assert summary.strip().splitlines()[0] == (
        "- **outcome: worked with help** (computed: install.sh exit 0; 1 question beyond the folder question)"
    )
    assert "- human turns: 2 (2 questions; clicks: none possible; approvals: not observable)" in summary
    items, agent_side = summary.split("Items that were not one command (attempt 1):", 1)[1].split(
        "Agent friction (attempt 1; it does not change the outcome by itself):", 1
    )
    assert re.findall(r"^- F\d+ · step 3 · (\w+) · ", items, flags=re.MULTILINE) == ["question"]
    assert re.findall(r"^- F\d+ · step 3 · (\w+) · ", agent_side, flags=re.MULTILINE) == ["deviation"]


def test_a_report_long_after_the_last_attempt_says_no_attempt_was_started_since(
    fake_mac: dict[str, Path],
) -> None:
    """Prompt v10's step 1 stops (no Xcode tools, a git failure) come before ``install.sh --log-start`` and
    order no line (since v11 they log the failed command as a step 1 error, B13 H1; an old copy, or an agent
    that does not, still logs nothing), and step 3 still runs the report. That report judged the attempt that
    had finished before it and said nothing else. It cannot tell such a session from a report written again
    by hand, so it says what it read: nothing was started since attempt N finished. install.log is what it
    read, and it does not log ``install.sh --report-only``, the run that writes such a report."""
    v6_install_log(fake_mac)
    write_friction(fake_mac, V7_HAPPY)  # its end line is 2026-09-29T10:01:10Z

    def summary_at(minute: int) -> tuple[str, str]:
        now = datetime(2026, 9, 29, 10, minute, tzinfo=UTC)
        text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks(), now=now)
        return text, section(text, "Summary")

    note = "- note: no attempt was started and install.log has no run that began since attempt 1 finished at "
    assert note not in summary_at(5)[1], "within ten minutes it is the same session's report"
    text, summary = summary_at(12)
    assert (
        f"{note}2026-09-29T10:01:10Z, more than 10 minutes before this report (install.log does not log "
        "`install.sh --report-only`), so this report is of attempt 1. A later session whose step 1 command "
        "stopped before `install.sh --log-start` (no Xcode tools, a git failure) and logged nothing is not "
        "in it." in summary
    )
    assert "no install.sh run began" not in text, (
        "--report-only writes this report and install.log does not log it"
    )
    assert summary.strip().splitlines()[0].startswith("- **outcome: fully one command**")
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["outcome"] == ["Fully one command"], "the outcome and the link are the attempt's, unchanged"
    write_install_log(fake_mac, start="2026-09-29T10:05:00Z", run="20260929T100500Z-7", append=True)
    assert note not in summary_at(12)[1], "install.log has a run that began after the end line"
    v6_install_log(fake_mac)
    write_friction(fake_mac, V7_HAPPY + "2026-09-29T10:12:00Z | step 1 | error | git pull exited 1 | -\n")
    summary = summary_at(20)[1]
    assert "- note: attempt 2 has no Attempt: line." in summary and note not in summary


@pytest.mark.parametrize(
    ("friction", "has_log", "outcome", "line"),
    [
        (  # a stopped copy: a step 1 error and the end line --report-only wrote, no install run
            "2026-09-29T10:20:00Z | step 1 | error | prompt v12, Contoso Bot: git pull exited 1 | -\n"
            "2026-09-29T10:21:00Z | end | finished\n",
            False,
            "failed at step 1",
            None,
        ),
        (  # the same with a header
            V12_HAPPY.replace(
                "2026-09-29T10:01:10Z | end | finished\n",
                "2026-09-29T10:00:00Z | step 1 | error | git pull exited 1 | -\n"
                "2026-09-29T10:01:10Z | end | finished\n",
            ),
            False,
            "failed at step 1",
            None,
        ),
        (V12_HAPPY, True, "fully one command", None),  # clean: only the end line
        (  # a question is more than the agent-friction line says: the line stays, its counts add up
            V12_HAPPY.replace(
                "2026-09-29T10:01:10Z | end | finished\n",
                "2026-09-29T10:00:00Z | step 1 | question | asked which terminal app this is | say it\n"
                "2026-09-29T10:01:10Z | end | finished\n",
            ),
            True,
            None,
            "- friction (attempt 1): 2 event lines: 1 question, 1 finished",
        ),
    ],
    ids=["stopped-copy", "headed-stop", "clean", "question"],
)
def test_the_friction_line_is_left_out_when_it_adds_only_the_end_line(
    fake_mac: dict[str, Path], friction: str, has_log: bool, outcome: str | None, line: str | None
) -> None:
    """B13 round 12 N20: M34 left the ``- friction (attempt N)`` line out only for an attempt with no end
    line, so every attempt that reached a report printed it to add "1 finished", and a stopped copy put "1
    finished" beside "failed at step 1". The end line is not counted toward printing it; when the line
    prints, its counts still include it."""
    if has_log:
        write_install_log(fake_mac)
    else:
        (fake_mac["setup"] / "install.log").unlink(missing_ok=True)
    write_friction(fake_mac, friction)
    _text, summary = summary_of(fake_mac)
    if outcome is not None:
        assert summary.strip().splitlines()[0].startswith(f"- **outcome: {outcome}**"), summary
    if line is None:
        assert "- friction (attempt" not in summary, summary
    else:
        assert line in summary.splitlines(), summary


def test_a_new_sessions_step_1_line_before_its_error_leaves_the_finished_attempt(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 12 N23: a step 1 deviation 71 s after a finished attempt's end line, then a stated step 1
    error, was counted in the finished attempt: the error opened the new attempt at any gap, but the lines
    before it moved only past the 10-minute gap. The finished session runs nothing after its --report-only,
    so the unbroken run of step 1 lines before the error moves with it whatever the gap; a late step 1 line
    no such error follows still stays where it is."""
    head = "Attempt: 2026-10-16T15:50:00Z\nPrompt: v20\nAgent: Contoso Agent\n"
    end = "2026-10-16T15:57:18Z | end | finished\n"
    error = "2026-10-16T15:58:30Z | step 1 | error | prompt v20, Contoso Agent: git pull exited 1 | -\n"
    for deviation in ("prompt v20, Contoso Agent: waited", "waited"):  # devfirst and devplain
        log = head + "2026-10-16T15:52:00Z | step 2 | start | x | -\n" + end
        log += f"2026-10-16T15:58:29Z | step 1 | deviation | {deviation} | -\n" + error
        first, second = setup_report.parse_friction(log).attempts
        assert [e.kind for e in first.events] == ["start", "finished"], first.events
        assert [e.kind for e in second.events] == ["deviation", "error"], second.events
        assert (second.first_line, second.last_line) == (6, 7), (second.first_line, second.last_line)
        write_friction(fake_mac, log)
        _text, summary = summary_of(fake_mac)
        assert "- agent friction: 1 deviation, 0 prompt, 1 error (F6, F7); F7 stopped the run" in summary, (
            summary
        )
    # no error after it: the late line stays in the finished attempt
    [only] = setup_report.parse_friction(
        head + end + "2026-10-16T15:58:29Z | step 1 | deviation | x | -\n"
    ).attempts
    assert [e.kind for e in only.events] == ["finished", "deviation"], only.events
    # B13 round 13 O24: nor did the run move before the new session's own Attempt: header, at any gap (71 s,
    # 13 minutes); the new attempt still begins at its Attempt: time
    new = "Attempt: {}\nPrompt: v21\nAgent: Contoso Agent\n"
    for asked, started in (
        ("2026-10-16T15:58:29Z", "2026-10-16T15:59:00Z"),
        ("2026-10-16T16:10:18Z", "2026-10-16T16:11:00Z"),
    ):
        log = head + "2026-10-16T15:52:00Z | step 2 | start | x | -\n" + end
        log += f"{asked} | step 1 | question | asked which folders | -\n" + new.format(started)
        first, second = setup_report.parse_friction(log).attempts
        assert [e.kind for e in first.events] == ["start", "finished"], (asked, first.events)
        assert [e.kind for e in second.events] == ["question"], (asked, second.events)
        assert second.started == setup_report.parse_time(started) and second.first_line == 6
        write_friction(fake_mac, log)
        _text, summary = summary_of(fake_mac)
        assert "- human turns: 1 (1 question; " in summary, summary
    # two headers with no end line between them: the line stays where it was logged
    log = head + "2026-10-16T15:58:29Z | step 1 | question | asked | -\n" + new.format("2026-10-16T15:59:00Z")
    first, second = setup_report.parse_friction(log).attempts
    assert [e.kind for e in first.events] == ["question"] and second.events == ()


def test_a_header_less_step_1_error_states_its_prompt_and_agent(fake_mac: dict[str, Path]) -> None:
    """B13 round 2 R25: a session whose step 1 stopped before ``install.sh --log-start`` wrote no header, so
    the Summary, the friction log section and the issue link said "not stated" for a v11 paste by a known
    agent. Since v12 the step 1 error line starts with ``prompt v12, <agent>: ``, and that attempt alone is
    read with it: the line's own text is the rest. A line without the words reads as before, and an attempt
    with a header, or a late line that stays in its attempt, never takes them."""
    v6_install_log(fake_mac)
    agent = "GitHub Copilot CLI, model-x1"
    stop = f"2026-09-29T10:20:00Z | step 1 | error | prompt v12, {agent}: git pull exited 1 | -\n"

    def report(friction: str) -> tuple[str, str, str, dict[str, list[str]]]:
        write_friction(fake_mac, friction)
        now = datetime(2026, 9, 29, 10, 30, tzinfo=UTC)
        text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks(), now=now)
        link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
        return text, section(text, "Summary"), section(text, "Agent friction log"), link

    _text, summary, fr, link = report(V12_HAPPY + stop)
    assert "- prompt: v12 (from the step 1 error line) · run: " in summary
    assert summary.split(" · agent: ", 1)[1].startswith(agent)
    assert "approvals: not observable" in summary, "the stated agent's tool does not show approvals"
    assert re.search(r"· step 1 · error · git pull exited 1\b", summary), "the item is the line's own text"
    assert "prompt v12, " not in summary.split(" · agent: ", 1)[1]
    assert f"prompt v12, from the step 1 error line; agent {agent}" in fr
    assert link["prompt_version"] == ["v12"] and link["agent"] == [agent]
    assert "v12" in link["title"][0]
    parsed = setup_report.parse_friction(V12_HAPPY + stop).attempts
    assert parsed[0].stated == {} and parsed[1].header == {}, "the derived words are not a header"
    assert parsed[1].events[0].what == "git pull exited 1" and parsed[1].version == 12
    # Read as --log-start reads its value: any of " ,:;-" after the number. Quotes an agent kept around the
    # words (the friction rule has it write a curly quote inside its single quotes) and no space after the
    # colon are no reason to lose them. The agent ends at its first colon: the prompt says to write none.
    said = f"prompt v12, {agent}: git pull exited 1"
    for written, agent_said, what in (
        ("prompt v12 - Claude Code: git pull exited 1", "Claude Code", "git pull exited 1"),
        ("prompt v12, Claude Code:git pull exited 1", "Claude Code", "git pull exited 1"),
        ("\u2019prompt v12, Claude Code: \u2019 git pull exited 1", "Claude Code", "git pull exited 1"),
        ("Prompt v12, Claude Code: git: exit 128", "Claude Code", "git: exit 128"),
    ):
        [_first, later] = setup_report.parse_friction(V12_HAPPY + stop.replace(said, written)).attempts
        assert (later.said("Prompt"), later.said("Agent"), later.events[0].what) == ("v12", agent_said, what)
    for written in ("prompt v12: git pull exited 1", "prompt v123x, Claude Code: x"):
        [_first, later] = setup_report.parse_friction(V12_HAPPY + stop.replace(said, written)).attempts
        assert later.stated == {} and later.events[0].what == written, written

    _text, summary, fr, link = report(V11_HAPPY + stop.replace(f"prompt v12, {agent}: ", ""))
    assert "- prompt: not stated · run: " in summary and " · agent: not stated" in summary
    assert (
        "prompt not stated; agent not stated" in fr and "prompt_version" not in link and "agent" not in link
    )

    # Within 10 minutes and in the attempt's own words, after its header: another session's (B13 round 5
    # F23). The prefix is written only before --log-start, which writes the header, so the session that
    # wrote the header never writes one; the new attempt is read from the line.
    own = "prompt v11, Claude Code, claude-opus-5-5: "
    late = stop.replace("10:20:00Z", "10:05:00Z").replace(f"prompt v12, {agent}: ", own)
    first, second = setup_report.parse_friction(V11_HAPPY + late).attempts
    assert first.stated == {} and second.header == {}
    assert second.stated == {"Prompt": "v11", "Agent": "Claude Code, claude-opus-5-5"}
    assert second.events[0].what == "git pull exited 1"
    end = "2026-09-29T10:01:10Z | end"
    headed = V11_HAPPY.replace(end, f"2026-09-29T10:00:00Z | step 1 | error | {own}x | -\n{end}")
    first, second = setup_report.parse_friction(headed).attempts
    assert first.said("Agent") == "Claude Code, claude-opus-5-5" and first.events == ()
    assert second.stated["Prompt"] == "v11" and second.events[0].what == "x"

    # B13 round 4 Q22, Q38, Q67: the step 1 error is the log's first line (a checkout cloned by hand, with no
    # friction.md until this line). Its words are read, and the attempt is no v4 log and no stale one.
    [only] = setup_report.parse_friction(stop).attempts
    assert only.stated == {"Prompt": "v12", "Agent": agent} and only.events[0].what == "git pull exited 1"
    _text, summary, fr, link = report(stop)
    assert "- prompt: v12 (from the step 1 error line) · run: " in summary
    assert summary.split(" · agent: ", 1)[1].startswith(agent)
    assert re.search(r"· step 1 · error · git pull exited 1\b", summary)
    assert link["prompt_version"] == ["v12"] and link["agent"] == [agent]
    assert f"prompt v12, from the step 1 error line; agent {agent}" in fr
    assert "no Attempt: line, since its session stopped before `install.sh --log-start`" in fr, fr
    assert "v4 format" not in fr and "may be stale" not in summary
    assert (
        "It holds a step 1 error (F1) logged before any attempt was started: attempt 1's session stopped"
        in summary
    )
    # B13 round 11 M34: the friction line would only repeat the agent-friction line's one error
    assert "- agent friction: 0 deviation, 0 prompt, 1 error (F1)" in summary
    assert "- friction (attempt" not in summary, summary
    # A first line of another step or kind keeps its text whole, and a log of F<n> lines is still v4.
    for first in (
        stop.replace("step 1 | error", "step 2 | error"),
        stop.replace("| error |", "| deviation |"),
    ):
        [att] = setup_report.parse_friction(first).attempts
        assert att.stated == {} and att.events[0].what.startswith("prompt v12, "), first
    _text, _summary, fr, _link = report("F1 | step 2 | the pull failed | -\n")
    assert "no Attempt: line, since it is in the v4 format" in fr, fr


def test_a_step_1_line_before_the_stated_step_1_error_keeps_the_header_less_label(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 11 M30, M73: round 10's L26 reads the stated words from a step 1 error with step 1 lines
    before it, but the label still looked at the first line alone: a deviation, then the error, read "since it
    is in the v4 format" (0 legacy lines), lost the Summary's note and gained a stale WARNING that names a
    command with no attempt to close. After a finished attempt the note said the attempt starts at the error,
    though it starts at the deviation."""
    v6_install_log(fake_mac)
    agent = "Contoso Agent model-b"
    lines = (
        f"2026-09-29T10:19:00Z | step 1 | deviation | prompt v19, {agent}: the pull asked for a key | -\n"
        f"2026-09-29T10:20:00Z | step 1 | error | prompt v19, {agent}: git pull exited 1 | -\n"
    )

    def report(friction: str) -> tuple[str, str]:
        write_friction(fake_mac, friction)
        now = datetime(2026, 9, 29, 10, 30, tzinfo=UTC)
        text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks(), now=now)
        return section(text, "Agent friction log"), section(text, "Summary")

    fr, summary = report(lines)
    assert "no Attempt: line, since its session stopped before `install.sh --log-start`" in fr, fr
    assert "v4 format" not in fr and "WARNING" not in summary, summary
    assert (
        "- note: attempt 1 has no Attempt: line. It holds a step 1 error (F2) logged before any attempt was "
        "started: attempt 1's session stopped" in summary
    ), summary
    # after a finished attempt: the note names the error, never "starts at" it
    fr, summary = report(V7_HAPPY + lines.replace("2026-09-29T10:", "2026-10-06T10:"))
    # B13 round 12 N28: the session by its own attempt, not "that session", which read as attempt 1's
    finished = "It holds a step 1 error (F6) logged after attempt 1 finished: attempt 2's session stopped"
    assert finished in summary, summary
    assert "that session" not in summary, summary
    assert "starts at a step 1 error" not in summary
    # an error and then a later step's line is still the session's own; a later step's line first is not
    error = lines.splitlines(keepends=True)[1]
    fr, summary = report(error + "2026-09-29T10:21:00Z | step 2 | deviation | x | -\n")
    assert "since its session stopped before `install.sh --log-start`" in fr, fr
    fr, summary = report("2026-09-29T10:18:00Z | step 2 | deviation | x | -\n" + error)
    assert "since it is in the v4 format" in fr, fr
    fr, summary = report("F1 | step 1 | the pull failed | -\n" + error)
    assert "since it is in the v4 format" in fr, fr


def test_a_step_1_error_with_another_sessions_words_leaves_an_attempt_with_no_end_line() -> None:
    """B13 round 4 Q21: a session that stopped at the folder question leaves no end line, so the next
    session's step 1 error, logged before its own ``--log-start``, joined that attempt and the report gave
    the earlier session's prompt and agent. A line whose words name another version or agent, or that comes
    more than the gap after the attempt's latest time, is another session's, end line or not; the same
    session's line within the gap stays."""
    agent = "Claude Code, claude-opus-5-5"
    open_v11 = V11_HAPPY.replace("2026-09-29T10:01:10Z | end | finished\n", "")
    stop = (
        "2026-09-30T10:20:00Z | step 1 | error | prompt v12, Contoso Agent model-b: git pull exited 1 | -\n"
    )
    first, second = setup_report.parse_friction(open_v11 + stop).attempts
    assert first.version == 11 and not first.finished
    assert second.stated == {"Prompt": "v12", "Agent": "Contoso Agent model-b"} and second.version == 12
    assert second.events[0].what == "git pull exited 1"
    fr = setup_report.parse_friction(open_v11 + stop)
    assert setup_report._headerless_kind(fr, 1) == "open" and setup_report._headerless_kind(fr, 0) is None
    # After a header, the same version and agent 30 seconds on (probe P3b) is another session too (B13 round
    # 5 F23): the prefix is written only before --log-start.
    open_v12 = V12_HAPPY.replace("2026-09-29T10:01:10Z | end | finished\n", "")
    same = "2026-09-29T09:58:30Z | step 1 | error | prompt v12, claude code,  Claude-Opus-5-5: x | -\n"
    headed, new = setup_report.parse_friction(open_v12 + same).attempts
    assert headed.said("Agent") == agent and not headed.finished
    assert new.said("Agent") == "claude code,  Claude-Opus-5-5" and new.events[0].what == "x"
    # A header-less attempt with stated words: a second stated line is another session's at any gap, since
    # a session writes the prefix once, at its Xcode or git stop, then goes to its report (round 14 Q28; the
    # same words 30 seconds on stayed). Its prefix is stripped as the first's is.
    words = "2026-09-29T09:58:00Z | step 1 | error | prompt v12, Claude Code, claude-opus-5-5: y | -\n"
    one, two = setup_report.parse_friction(words + same).attempts
    assert one.said("Agent") == agent and [e.what for e in one.events] == ["y"]
    assert two.said("Agent") == "claude code,  Claude-Opus-5-5" and [e.what for e in two.events] == ["x"]
    assert len(setup_report.parse_friction(words + same.replace("09:58:30Z", "10:08:01Z")).attempts) == 2
    other = same.replace("claude code,  Claude-Opus-5-5", "Contoso Agent")
    assert len(setup_report.parse_friction(words + other).attempts) == 2
    # A stopped v11 copy closes its attempt; a v12 paste two minutes later is its own, within the gap too.
    copy = (
        "Attempt: 2026-09-29T10:00:00Z\nPrompt: v11\nAgent: x\n2026-09-29T10:00:00Z | step 1 | error | "
        "install.sh --log-start: the pasted setup prompt is v11; setup stopped | -\n"
        "2026-09-29T10:00:00Z | end | finished\n"
    )
    later = "2026-09-29T10:02:00Z | step 1 | error | prompt v12, x: git pull exited 1 | -\n"
    assert [a.stated for a in setup_report.parse_friction(copy + later).attempts] == [
        {},
        {"Prompt": "v12", "Agent": "x"},
    ]


PRX_LOG = (
    "Attempt: 2026-10-17T16:40:00Z\nPrompt: v22\nAgent: Claude Code, claude-opus-5-5\n"
    "2026-10-17T16:58:00Z | end | finished\n"
    "2026-10-17T17:03:50Z | step 1 | error | prompt v22, Claude Code claude-opus-5-5: xcode-select -p exited "
    "2 | -\n"
    "2026-10-17T17:08:26Z | step 1 | error | prompt v22, Claude Code claude-opus-5-5: git -C "
    "~/src/agent-context-sync pull exited 1 | -\n"
)
"""Probe prx (B13 round 14 Q28): a finished attempt, then two sessions that each stopped at step 1, 4m36s
apart, each writing the prefix once as the prompt says, then going to the report."""


def test_two_sessions_that_each_stop_at_step_1_within_the_gap_are_two_attempts(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 14 Q28: the second stated step 1 error joined the first's header-less attempt within the
    10-minute gap: one 4m36s session, the first stop read as friction, and the second line kept its
    ``prompt v22, <agent>:`` prefix. Each is its own attempt now, its prefix stripped."""
    attempts = setup_report.parse_friction(PRX_LOG).attempts
    assert len(attempts) == 3, attempts
    whats = ("xcode-select -p exited 2", "git -C ~/src/agent-context-sync pull exited 1")
    for att, what in zip(attempts[1:], whats, strict=True):
        assert att.stated == {"Prompt": "v22", "Agent": "Claude Code claude-opus-5-5"}
        assert [e.what for e in att.events] == [what]
    write_friction(fake_mac, PRX_LOG)
    now = datetime(2026, 10, 17, 17, 30, tzinfo=UTC)
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks(), now=now)
    summary, fr = section(text, "Summary"), section(text, "Agent friction log")
    assert "4m36s" not in summary + fr and "3 attempts:" in fr, fr
    assert "· step 1 · error · git -C ~/src/agent-context-sync pull exited 1" in summary, summary
    assert "prompt v22, Claude Code" not in summary, summary


def test_a_prefixed_step_1_error_after_a_finished_attempt_is_reported_as_its_own(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 5 F23, probe P8: a finished attempt, then the same agent's prefixed step 1 error two minutes
    later. It joined the finished attempt, which then read "failed at step 1" with its header's prompt; it is
    another session's attempt, read from the line."""
    v6_install_log(fake_mac)
    late = "2026-09-29T10:03:10Z | step 1 | error | prompt v12, Claude Code, claude-opus-5-5: git pull failed"
    late += " | -\n"
    write_friction(fake_mac, V12_HAPPY + late)
    now = datetime(2026, 9, 29, 10, 30, tzinfo=UTC)
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks(), now=now)
    summary, fr = section(text, "Summary"), section(text, "Agent friction log")
    assert "- prompt: v12 (from the step 1 error line) · run: " in summary, summary
    assert re.search(r"· step 1 · error · git pull failed\b", summary)
    assert "2 attempts:" in fr, fr
    [first] = [ln for ln in fr.splitlines() if ln.startswith("- attempt 1 ")]
    assert "failed at step 1" not in first, first


def test_a_late_line_that_is_not_a_step_1_error_never_starts_an_attempt(fake_mac: dict[str, Path]) -> None:
    """A deviation logged after ``--report-only``, or a complaint days later, is no failed session. With a
    later ``Attempt:`` header in the log it became an attempt of its own with no install run and no error,
    which read "failed at step 3"."""
    v6_install_log(fake_mac)
    write_install_log(fake_mac, start="2026-10-03T09:00:30Z", run="20261003T090030Z-7", append=True)
    later = V7_HAPPY.replace("2026-09-29T09:58:00Z", "2026-10-03T09:00:00Z").replace(
        "2026-09-29T10:01:10Z", "2026-10-03T09:03:00Z"
    )
    late = (
        "2026-09-29T10:01:30Z | step 3 | deviation | wrote the questions in three parts | -\n"
        "2026-10-01T08:00:00Z | step 2 | error | the link shows a placeholder | -\n"
        "2026-10-01T08:00:05Z | step 3 | prompt | the last step is unclear | say what comes next\n"
    )
    write_friction(fake_mac, V7_HAPPY + late + later)
    text, summary = summary_of(fake_mac)
    fr = section(text, "Agent friction log")
    assert "2 attempts:" in fr and "failed at step" not in fr
    assert re.search(r"^- attempt 1 \(lines 1-7; .*\): fully one command; 4 event lines: ", fr, re.M)
    assert "- attempt: 2 of 2; earlier: attempt 1 fully one command" in summary


@pytest.mark.usefixtures("clean_doctor")
def test_v7_a_step_2_error_logged_after_the_install_ended_0_is_agent_friction(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """Field report 2026-10-06: v7's step 2 is the one install.sh command. The agent logged its lines in a
    batch after that command exited 0, one of them a step 2 ``error`` that said "Exit 0, no failure", and
    the outcome read "failed at step 2". Any install run of the attempt that ended 0 resolves it; the
    Summary and the issue link agree. v6 the same since B13."""
    v6_install_log(fake_mac)
    batch = (
        "2026-09-29T10:01:00Z | step 2 | prompt | the prompt does not say what a second run prints | say it\n"
        "2026-09-29T10:01:00Z | step 2 | error | Exit 0, no failure: the link shows a placeholder | -\n"
        "2026-09-29T10:01:00Z | step 3 | deviation | wrote the questions in three parts | -\n"
    )
    friction = _insert_before("2026-09-29T10:01:10Z | end", batch, V7_HAPPY)
    write_friction(fake_mac, friction)
    rc, text, _ = report(tmp_path, fake_mac["config"])
    summary = section(text, "Summary")
    assert rc == 0 and summary.strip().splitlines()[0] == (
        "- **outcome: fully one command** (computed: install.sh exit 0; no turn beyond the unavoidable ones)"
    )
    assert (
        "- agent friction: 1 deviation, 1 prompt, 1 error (F4, F5, F6); none of these changes the outcome "
        "by itself" in summary
    )
    assert "stopped the run" not in summary and "- F5 · step 2 · error · Exit 0, no failure" in summary
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["outcome"] == ["Fully one command"] and link["prompt_version"] == ["v7"]

    [attempt] = setup_report.parse_friction(friction).attempts
    runs = setup_report.read_install_runs(fake_mac["setup"] / "install.log")
    assert setup_report.stopping_error(attempt, runs) is None
    assert setup_report.stopping_error(attempt, [r for r in runs if r.list_only]) is attempt.events[1], (
        "only an install run resolves it"
    )
    step_1 = friction.replace("| step 2 | error |", "| step 1 | error |")
    [attempt] = setup_report.parse_friction(step_1).attempts
    assert setup_report.stopping_error(attempt, runs) is attempt.events[1], (
        "a step 1 error still needs a run started after it"
    )
    v6_install_log(fake_mac, rc=1)
    _text, summary = summary_of(fake_mac)
    assert summary.strip().startswith("- **outcome: failed at step 2** (computed: install.sh exited 1"), (
        "a failed install is still a failed step 2"
    )


@pytest.mark.usefixtures("clean_doctor")
def test_a_persons_step_2_stop_the_agent_logged_is_the_one_item(fake_mac: dict[str, Path]) -> None:
    """B13 round 14 Q7: step 2 exits 1 with a NEXT: that says the [FAIL] lines are the person's, "not a step
    for the agent", and ends with the re-run once fixed. v22 read two ways on whether to log it; since v23 the
    prompt says to log it as a step 2 error (``PERSONS_NEXT`` in tests/test_deploy_pack.py). That line is the
    attempt's one item, beside "failed at step 2". Without it the report has no installer-side item for a step
    2 stop (it makes one only for step 1's exit 3), so Items would read none beside the failure."""
    v6_install_log(fake_mac, rc=1)
    logged = (
        "2026-09-29T10:01:00Z | step 2 | error | install.sh exited 1: its NEXT says the [FAIL] lines are the "
        "person's to fix (a synced folder no longer on disk), not a step for the agent | -\n"
    )
    write_friction(fake_mac, _insert_before("2026-09-29T10:01:10Z | end", logged, V7_HAPPY))
    _text, summary = summary_of(fake_mac)
    assert summary.strip().startswith("- **outcome: failed at step 2** (computed: install.sh exited 1")
    items = summary.split("Items that were not one command (attempt 1):", 1)[1].split("\n\n### Run", 1)[0]
    found = [ln for ln in items.splitlines() if ln.startswith("- ")]
    assert len(found) == 1 and found[0].startswith("- F4 · step 2 · error · install.sh exited 1:"), items
    write_friction(fake_mac, V7_HAPPY)  # the same stop with no line logged: the installer's own item
    _text, summary = summary_of(fake_mac)
    assert summary.strip().startswith("- **outcome: failed at step 2** (computed: install.sh exited 1")
    items = summary.split("Items that were not one command (attempt 1):", 1)[1].split("\n\n### Run", 1)[0]
    assert not [ln for ln in items.splitlines() if ln.startswith("- F")], items
    # B13 round 15 S40: not "- none" beside "failed at step 2", the shape round 13 O13 fixed for step 1
    found = [ln for ln in items.splitlines() if ln.startswith("- ")]
    assert found == [
        "- install.sh · step 2 · the install stopped: install.sh exited 1 (not logged: the agent logged no "
        "line for this stop)"
    ], items


@pytest.mark.parametrize("shape", ["logged after a run that ended 0", "a run with no end line"])
def test_an_install_step_stop_item_is_not_made_for_a_logged_line_or_a_killed_run(
    fake_mac: dict[str, Path], shape: str
) -> None:
    """B13 round 15 S40, review: the unlogged step 2 stop's item is the installer's only when no line names an
    install step error (stopping_error counts one resolved once any install run ended 0, so the item said
    "not logged" beside the agent's own line), and only for a run that ended: a killed or unfinished run is
    no stop the person made."""
    log = fake_mac["setup"] / "install.log"
    friction = V7_HAPPY
    if shape == "a run with no end line":
        log.write_text(LIST_RUN + install_run("2026-09-29T10:00:00Z", None))
    else:
        write_install_log(fake_mac, start="2026-09-29T10:00:00Z", rc=0, run="20260929T100000Z-4242")
        write_install_log(
            fake_mac, start="2026-09-29T10:00:30Z", rc=1, run="20260929T100030Z-4343", append=True
        )
        log.write_text(LIST_RUN + log.read_text().replace("compat=5", "compat=6"))
        logged = (
            "2026-09-29T10:01:00Z | step 2 | error | install.sh exited 1: its NEXT says the [FAIL] lines are "
            "the person's to fix, not a step for the agent | -\n"
        )
        friction = _insert_before("2026-09-29T10:01:10Z | end", logged, V7_HAPPY)
    write_friction(fake_mac, friction)
    _text, summary = summary_of(fake_mac)
    assert "**outcome: failed at step 2**" in summary, summary
    items = summary.split("Items that were not one command (attempt 1):", 1)[1].split("\n\n### Run", 1)[0]
    assert "the install stopped" not in items, items


# ---- an install run that failed before the one that ended 0 (second bring-back, 2026-10-07) -------------


def install_run(start: str, rc: int | None, *, status_failed: bool = False) -> str:
    """One install run of step 2 as install.log has it. ``status_failed``: status printed a [FAIL] line, so
    the sync was skipped (the field's failed run, and a listing macOS holds for a click: the log lines are
    the same). ``rc`` None: no end line, the agent's tool stopped the run."""
    run = f"run={re.sub('[-:]', '', start)}-77"
    status = "seconds=6 rc=1 result=done note=fail-lines" if status_failed else "seconds=6 rc=0 result=done"
    sync = "seconds=34 rc=0 result=done note=converted-3-deferred-0"
    if status_failed:
        sync = "seconds=0 rc=0 result=skipped note=status-failed"
    lines = [
        f"{start} {run} start install.sh compat=8 commit=0123456789ab kind=checkout source=- "
        "args=--source-local x",
        f"{start} {run} step=config seconds=1 rc=0 result=done note=add-source",
        f"{start} {run} step=status {status}",
        f"{start} {run} step=first-sync {sync}",
        f"{start} {run} step=report seconds=2 rc=0 result=done note=agentsync",
    ]
    if rc is not None:
        lines.append(f"{start} {run} end rc={rc} seconds=19")
    return "\n".join(lines) + "\n"


def test_the_outcome_of_a_status_fail_stop_names_the_status_step(tmp_path: Path) -> None:
    """B13 round 13 O33: a run that exited 1 on doctor's [FAIL] lines read ``install.sh exited 1`` with no
    step: no step logged ``result=failed``. Its status step (``doctor`` in older logs) logged
    ``note=fail-lines`` with rc 1, and the outcome says so. A failed step keeps priority, and another status
    note (an Allow click macOS holds, exit 3) names no status step."""

    def outcome(text: str) -> str:
        log = tmp_path / "install.log"
        log.write_text(text, encoding="utf-8")
        return "; ".join(setup_report.compute_outcome(None, setup_report.read_install_runs(log)).why)

    failed = install_run("2026-09-29T10:00:00Z", 1, status_failed=True)
    assert outcome(failed) == "install.sh exited 1: its status step reported [FAIL] lines (rc 1)"
    assert outcome(failed.replace("step=status", "step=doctor")) == (
        "install.sh exited 1: its status step reported [FAIL] lines (rc 1)"
    )
    pending = failed.replace("rc=1 result=done note=fail-lines", "rc=3 result=done note=tcc-pending")
    assert outcome(pending.replace("end rc=1", "end rc=3")) == "install.sh exited 3"
    config = failed.replace(
        "step=config seconds=1 rc=0 result=done", "step=config seconds=1 rc=78 result=failed"
    )
    assert outcome(config) == "install.sh exited 1: its config step failed with rc 78"
    assert outcome(install_run("2026-09-29T10:00:00Z", 0)).startswith("install.sh exit 0")
    # round 14 Q50: status exited 1 with no [FAIL] line (a traceback), which install.sh logs as status-error
    crashed = failed.replace("note=fail-lines", "note=status-error")
    assert outcome(crashed) == "install.sh exited 1: its status step exited 1 with no [FAIL] line"


def test_the_installer_table_puts_the_status_steps_note_in_words() -> None:
    """B13 round 14 Q4, Q50: the status row read ``done, rc 1 (fail-lines)`` beside worded rows. Each of the
    status step's notes (``doctor`` in older logs) has words in the table; install.log keeps its tokens."""
    for step in ("status", "doctor"):
        for note, words in (
            ("fail-lines", "agentsync status reported [FAIL] lines"),
            ("tcc-pending", "its only [FAIL] lines were a macOS Allow still to be clicked"),
            ("status-error", "agentsync status exited with no [FAIL] line"),
        ):
            table = "\n".join(
                setup_report._step_table(
                    [(step, {"result": "done", "rc": "1", "note": note, "seconds": "1"})]
                )
            )
            assert f"| done, rc 1 ({words}" in table and note not in table, table


@pytest.mark.usefixtures("clean_doctor")
def test_an_install_run_after_the_attempts_end_line_is_named_apart(fake_mac: dict[str, Path]) -> None:
    """B13 round 5 F37: a probe install.sh ran after the attempt's end line, and the Summary said "2 install
    runs during this attempt" and summed its seconds into step 2's time beside a session time that does not
    cover it. The run is still the attempt's (runs_for_attempt: the outcome is judged on it), so only the
    words change: the install line says how many came after the end line, and the time clause sums only the
    runs inside it and names the others apart."""
    log = fake_mac["setup"] / "install.log"
    log.write_text(LIST_RUN + install_run("2026-09-29T09:59:00Z", 0), encoding="utf-8")
    write_friction(fake_mac, V6_HAPPY)
    _text, summary = summary_of(fake_mac)
    assert "- install.sh: 1 install run (+1 --list-folders) during this attempt; the last exit 0" in summary
    assert "after its end line" not in summary
    both = LIST_RUN + install_run("2026-09-29T09:59:00Z", 0) + install_run("2026-09-29T10:05:00Z", 0)
    log.write_text(both, encoding="utf-8")
    _text, summary = summary_of(fake_mac)
    assert (  # B13 round 6 G32: the late run is counted apart, not inside the first count too
        "- install.sh: 1 install run (+1 --list-folders) during this attempt and 1 install run after its end "
        "line (2026-09-29T10:01:10Z); the last exit 0 after 19s" in summary
    ), summary
    [time] = [ln for ln in summary.splitlines() if ln.startswith("- time: ")]
    assert time.endswith("install 19s; after its end line: install 19s"), time
    assert "install 19s + 19s" not in time and "for this attempt" not in summary
    # Each kind counted apart: a --list-folders run after the end line is not an install run's
    later_list = LIST_RUN.replace("2026-09-29T09:58:05Z", "2026-09-29T10:06:00Z").replace(
        "095805Z", "100600Z"
    )
    log.write_text(LIST_RUN + install_run("2026-09-29T09:59:00Z", 0) + later_list, encoding="utf-8")
    _text, summary = summary_of(fake_mac)
    assert (
        "- install.sh: 1 install run (+1 --list-folders) during this attempt and 1 --list-folders run after "
        "its end line (2026-09-29T10:01:10Z); the last exit 0" in summary
    ), summary
    log.write_text(install_run("2026-09-29T10:05:00Z", 0), encoding="utf-8")
    _text, summary = summary_of(fake_mac)
    assert (
        "- install.sh: no install run during this attempt and 1 install run after its end line "
        "(2026-09-29T10:01:10Z); the last exit 0" in summary
    ), summary


@pytest.mark.usefixtures("clean_doctor")
def test_the_report_install_sh_writes_says_its_run_time_is_before_its_report_step(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 5 F38: install.sh writes a provisional end line, runs the report, and only then logs the
    report step and the final end line, whose seconds include the report. The report written during the run
    said "exit 0 after 41s" where install.log ends 43s. Only that run, read then, is labelled: a later read
    has the final line, which includes the report step."""
    log = fake_mac["setup"] / "install.log"
    run = install_run("2026-09-29T09:59:00Z", 0)
    provisional = "".join(ln for ln in run.splitlines(keepends=True) if "step=report" not in ln)
    log.write_text(LIST_RUN + provisional, encoding="utf-8")
    write_friction(fake_mac, V6_HAPPY)
    monkeypatch.setenv(setup_report.REPORT_RUN_ENV, "20260929T095900Z-77")
    text, summary = summary_of(fake_mac)
    installer = section(text, "Installer")
    assert "run 20260929T095900Z: exit 0 after 19s, before its report step\n" in installer, installer
    assert "in total (runs with an end line; this run's report step not counted);" in installer
    assert "the last exit 0 after 19s, before its report step" in summary
    assert "| install | 3 |" in installer and "exit 0 after 19s, before its report step |" in installer
    final = provisional + (
        "2026-09-29T09:59:00Z run=20260929T095900Z-77 step=report seconds=2 rc=0 result=done note=agentsync\n"
        "2026-09-29T09:59:00Z run=20260929T095900Z-77 end rc=0 seconds=21\n"
    )
    log.write_text(LIST_RUN + final, encoding="utf-8")
    text, summary = summary_of(fake_mac)
    assert "before its report step" not in text and "report step not counted" not in text
    assert "the last exit 0 after 21s" in summary
    log.write_text(LIST_RUN + provisional, encoding="utf-8")
    monkeypatch.delenv(setup_report.REPORT_RUN_ENV)  # a report another command wrote: not this run's
    assert "before its report step" not in summary_of(fake_mac)[0]


RETRY_ERROR = (
    "2026-09-29T10:00:30Z | step 2 | error | install.sh exited 1: [FAIL] docs_repo.permissions | -\n"
)
RETRY_LATE = (
    "2026-09-29T10:21:00Z | step 2 | deviation | ran the chmod the FAIL line named, then step 2 again | -\n"
    "2026-09-29T10:22:00Z | step 3 | deviation | rewrote the fix request after the second run | -\n"
)
RETRY_WHY = "1 earlier install run of this attempt exited 1"


def retry_friction(happy: str | None = None) -> str:
    """The field's attempt: step 2's error, the closing line of the first report, then two lines logged
    after the same session ran step 2 again."""
    return _insert_before("2026-09-29T10:01:10Z | end", RETRY_ERROR, happy or V8_HAPPY) + RETRY_LATE


@pytest.mark.usefixtures("clean_doctor")
def test_an_install_run_that_failed_before_the_one_that_ended_0_is_worked_with_help(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field report 2026-10-07: step 2's install.sh exited 1 on a doctor FAIL, the person approved a fix by
    hand, and the second run ended 0. Only the last run was judged, so the Summary, the attempt's line and
    the issue link all read "fully one command". A retry and a manual step are not one command.

    The lines after ``end | finished`` are read as before: they stay in the attempt, they are agent
    friction, and none of them changes the outcome. The run that ended 0 started after that closing line
    (``install.sh --report-only`` ran twice and closes an attempt once), and it is still this attempt's."""
    log = fake_mac["setup"] / "install.log"
    failed = install_run("2026-09-29T10:00:00Z", 1, status_failed=True)
    log.write_text(LIST_RUN + failed + install_run("2026-09-29T10:20:00Z", 0), encoding="utf-8")
    write_friction(fake_mac, retry_friction())
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    summary = section(text, "Summary")
    assert (
        summary.strip().splitlines()[0]
        == f"- **outcome: worked with help** (computed: install.sh exit 0; {RETRY_WHY})"
    )
    assert (
        "- agent friction: 2 deviation, 0 prompt, 1 error (F4, F6, F7); none of these changes the outcome "
        "by itself" in summary
    )
    assert (  # B13 round 5 F37: the run that ended 0 began after the end line, and the line says so
        "- install.sh: 1 install run (+1 --list-folders) during this attempt and 1 install run after its end "
        "line (2026-09-29T10:01:10Z); the last exit 0" in summary
    )
    items = summary.split("Items that were not one command (attempt 1):", 1)[1].split("\n\n", 2)[1]
    assert items == (
        "- install.sh · step 2 · run 20260929T100000Z exited 1 at its status step; the same command was run "
        "again and ended 0"
    ), "the run is the item: the list said none beside worked with help"
    fr = section(text, "Agent friction log")
    assert "1 attempt:" in fr and "- attempt:" not in summary, "the late lines start no attempt"
    assert re.search(
        r"^- attempt 1 \(lines 1-7; .*; prompt v8; .*\): worked with help; 4 event line", fr, re.M
    )
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["outcome"] == ["Worked with help"]
    assert link["title"][0].startswith("Setup report: Worked with help · ") and link["title"][0].endswith(
        "v8"
    )

    # A later attempt is judged on its own runs: the failed run of the one before does not count against it,
    # and the earlier attempt keeps its outcome on the "earlier:" line.
    later = V11_HAPPY.replace("2026-09-29T09:58:00Z", "2026-10-03T09:00:00Z").replace(
        "2026-09-29T10:01:10Z", "2026-10-03T09:03:00Z"
    )
    log.write_text(log.read_text(encoding="utf-8") + install_run("2026-10-03T09:00:30Z", 0), encoding="utf-8")
    write_friction(fake_mac, retry_friction() + later)
    text, summary = summary_of(fake_mac)
    assert summary.strip().startswith("- **outcome: fully one command**")
    assert "- attempt: 2 of 2; earlier: attempt 1 worked with help" in summary
    assert "Items that were not one command (attempt 2):\n\n- none" in summary

    # The link is built from the same runs when the Summary could not be written. It was given every run
    # in install.log, so the earlier attempt's failed run would have counted against this one.
    def broken(*args: object, **kwargs: object) -> list[str]:
        raise RuntimeError("no summary")

    monkeypatch.setattr(setup_report, "_summary", broken)
    text, _red = setup_report.build_report(fake_mac["config"])
    assert "_This section failed: RuntimeError" in section(text, "Summary")
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["outcome"] == ["Fully one command"] and link["prompt_version"] == ["v11"]


@pytest.mark.usefixtures("clean_doctor")
def test_a_retried_run_says_how_it_was_run_again_and_which_checks_failed(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 14 Q12: Items said "the same command was run again" of a run whose re-run had other
    arguments (v22's step 1 leads to a re-run with other folders): the line never saw the run that ended
    0. It compares the two runs' ``args=`` now, never printing them. Q21: a status step's [FAIL] lines (a
    pandoc whose first start did not answer) were in no part of the report; the line names the checks,
    redacted, and ``timed out`` only on doctor's own words."""
    log = fake_mac["setup"] / "install.log"
    out = fake_mac["setup"] / "install.out"
    write_friction(fake_mac, retry_friction())
    folder = f"/Users/{LOGIN}/{ORG}/Project\\ Alpha"

    def run(start: str, rc: int, args: str, *, status: bool = False, sync: bool = False) -> str:
        text = install_run(start, rc, status_failed=status).replace("args=--source-local x", f"args={args}")
        if sync:
            text = text.replace(
                "step=first-sync seconds=34 rc=0 result=done", "step=first-sync seconds=34 rc=1 result=failed"
            )
        return text

    def items(*runs: str) -> str:
        log.write_text(LIST_RUN + "".join(runs), encoding="utf-8")
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        summary = section(text, "Summary")
        return summary.split("Items that were not one command (attempt 1):", 1)[1].split("\n\n", 2)[1]

    # the shape of the field's install.log: a folder named, exit 1 at the first sync; then no arguments
    got = items(
        run("2026-09-29T10:00:00Z", 1, f"--source-local {folder}", sync=True),
        run("2026-09-29T10:20:00Z", 0, ""),
    )
    assert got == (
        "- install.sh · step 2 · run 20260929T100000Z exited 1 at its first-sync step; install.sh was run "
        "again with no --source-local and ended 0"
    ), got
    assert "Alpha" not in got and ORG not in got, got
    # B13 round 16 T21: the reverse names folders added; other folders on both runs keep "other folders"
    got = items(
        run("2026-09-29T10:00:00Z", 1, "", sync=True),
        run("2026-09-29T10:20:00Z", 0, f"--source-local {folder}"),
    )
    assert got.endswith("; install.sh was run again with --source-local folders added and ended 0"), got
    got = items(
        run("2026-09-29T10:00:00Z", 1, "--source-local x", sync=True),
        run("2026-09-29T10:20:00Z", 0, f"--source-local {folder}"),
    )
    assert got.endswith("; install.sh was run again with other folders and ended 0"), got
    # two failed runs: the first's arguments differ from the run that worked, the second's are the same
    got = items(
        run("2026-09-29T10:00:00Z", 1, "--source-local x --confirm-install-agent", sync=True),
        run("2026-09-29T10:10:00Z", 1, "--source-local x", sync=True),
        run("2026-09-29T10:20:00Z", 0, "--source-local x"),
    )
    first, second = got.splitlines()
    assert first.endswith("first-sync step; install.sh was run again with other arguments and ended 0"), got
    assert second.endswith("first-sync step; the same command was run again and ended 0"), got
    # the same options in another order, or spaced apart, are the same command
    got = items(
        run("2026-09-29T10:00:00Z", 1, "--source-local x --confirm-install-agent", sync=True),
        run("2026-09-29T10:20:00Z", 0, "--confirm-install-agent  --source-local x"),
    )
    assert got.endswith("first-sync step; the same command was run again and ended 0"), got

    # Q21: the status step's [FAIL] lines in install.out, by the run's own header
    failed = run("2026-09-29T10:00:00Z", 1, "--source-local x", status=True)
    worked = run("2026-09-29T10:20:00Z", 0, "--source-local x")
    out.write_text(
        "# run=20260929T100000Z-77 2026-09-29T10:00:00Z install.sh --source-local x\n"
        "[ok  ] config — 2 sources\n"
        "[FAIL] pandoc — `pandoc --version` did not answer within 60s: the check ran out of time (fix: run "
        "~/.local/bin/agentsync status again)\n"
        f"[FAIL] source.client-alpha.listable — {fake_mac['one']} does not exist (fix: sync it)\n"
        "# run=20260929T102000Z-77 2026-09-29T10:20:00Z install.sh --source-local x\n"
        "[ok  ] pandoc — 3.6\n",
        encoding="utf-8",
    )
    got = items(failed, worked)
    assert got.startswith(
        "- install.sh · step 2 · run 20260929T100000Z exited 1 at its status step ([FAIL] pandoc, timed out; "
        "source."
    ) and got.endswith(".listable); the same command was run again and ended 0"), got
    assert "Client Alpha" not in got and "client-alpha" not in got, got
    # the run's header is not among the install.out lines read
    out.write_text(
        "# run=20260929T102000Z-77 2026-09-29T10:20:00Z install.sh --source-local x\n", encoding="utf-8"
    )
    assert items(failed, worked) == (
        "- install.sh · step 2 · run 20260929T100000Z exited 1 at its status step (its [FAIL] lines are not "
        "in the install.out lines read); the same command was run again and ended 0"
    )


def judged(tmp_path: Path, friction: str, *runs: str) -> tuple[setup_report.Outcome, list[int | None]]:
    """The latest attempt's outcome on its own runs, as the Summary computes it, and the exits of the runs
    that count as a retry."""
    log = tmp_path / "install.log"
    log.write_text("".join(runs), encoding="utf-8")
    fr = setup_report.parse_friction(friction)
    mine = setup_report.runs_for_attempt(fr, len(fr.attempts) - 1, setup_report.read_install_runs(log))
    return setup_report.compute_outcome(fr.latest, mine), [
        run.rc for run in setup_report.retried_runs(fr.latest, mine)
    ]


@pytest.mark.parametrize(
    ("exits", "kind", "why"),
    [
        ((0, 0), "fully one command", None),
        ((1, 0), "worked with help", RETRY_WHY),
        ((0, 3, 0), "fully one command", None),
        ((1, 0, 3, 0), "worked with help", RETRY_WHY),
        ((143, 0), "fully one command", None),
        ((130, 0), "fully one command", None),
        ((129, 0), "fully one command", None),
        ((None, 0), "fully one command", None),
        ((143, 1, None, 0), "worked with help", RETRY_WHY),
        ((1, 2, 0), "worked with help", "2 earlier install runs of this attempt exited 1, 2"),
        ((1, 1, 0), "worked with help", "2 earlier install runs of this attempt exited 1"),
        ((1,), "failed", None),
        ((1, 0, 1), "failed", None),
    ],
)
def test_only_a_run_the_installer_failed_before_the_first_success_counts_as_a_retry(
    tmp_path: Path, exits: tuple[int | None, ...], kind: str, why: str | None
) -> None:
    """The narrowed rule, by the exits of an attempt's install runs in order.

    - A run after the first one that ended 0 does not count: the latest attempt has no end time, so the
      operator's later ``--confirm-install-agent`` run (exit 3 while macOS waits for the launcher's click)
      and its re-run would count in every later report.
    - A run the agent's tool stopped does not count: it ends with a signal's exit (143, 130, 129) or has no
      end line, and the prompt calls running the command again safe.
    - When the last run failed the outcome is "failed at step 2", as before."""
    runs = [install_run(f"2026-09-29T10:{minute:02d}:00Z", rc) for minute, rc in enumerate(exits)]
    outcome, retried = judged(tmp_path, V8_HAPPY, *runs)
    assert outcome.kind == kind, outcome
    if kind == "fully one command":
        assert retried == [] and outcome.why == ("install.sh exit 0", "no turn beyond the unavoidable ones")
    elif kind == "worked with help":
        assert outcome.why == ("install.sh exit 0", why) and retried
        assert all(rc not in (None, 0, *setup_report.STOPPED_EXITS) for rc in retried)
    else:
        assert outcome.text == "failed at step 2" and not any("earlier" in reason for reason in outcome.why)


def test_a_retry_counts_since_v7_in_an_attempt_with_a_time_and_never_a_list_run(tmp_path: Path) -> None:
    """Who the rule is for. v7 to v10 share their steps, so the same log reads the same under each. v5
    and v6 announce the launcher's Allow click in the install step: a run that timed out waiting for it was
    theirs to run again, and their logs are judged as before. A ``--list-folders`` run is no install run,
    so step 1's own click and the list's re-run cost nothing. An attempt with no time gets every run in
    install.log, an earlier attempt's too, so none is counted for it."""
    pair = (
        install_run("2026-09-29T10:00:00Z", 1, status_failed=True),
        install_run("2026-09-29T10:02:00Z", 0),
    )
    for happy in (V7_HAPPY, V8_HAPPY, V9_HAPPY, V10_HAPPY, V11_HAPPY):
        outcome, retried = judged(tmp_path, happy, *pair)
        assert (outcome.kind, outcome.why[1:], retried) == ("worked with help", (RETRY_WHY,), [1]), happy
    outcome, retried = judged(tmp_path, V6_HAPPY, install_run("2026-09-29T10:00:00Z", 3), pair[1])
    assert (outcome.kind, retried) == ("fully one command", []), "v6 announces a click in its install step"
    outcome, retried = judged(tmp_path, V5_HAPPY, *pair)
    assert (outcome.kind, retried) == ("fully one command", []), "a v5 log is judged by its step lines"
    denied_list = (
        "2026-09-29T09:58:05Z run=20260929T095805Z-11 start install.sh compat=8 args=--list-folders\n"
        "2026-09-29T09:58:05Z run=20260929T095805Z-11 step=list-folders seconds=1 rc=4 result=failed "
        "note=denied\n"
        "2026-09-29T09:58:06Z run=20260929T095805Z-11 end rc=4 seconds=1\n"
    )
    outcome, retried = judged(tmp_path, V8_HAPPY, denied_list, LIST_RUN, pair[1])
    assert (outcome.kind, retried) == ("fully one command", []), "the announced click is step 1's"
    before = install_run("2026-09-29T09:00:00Z", 1)  # an hour before the Attempt: line
    outcome, retried = judged(tmp_path, V8_HAPPY, before, pair[1])
    assert (outcome.kind, retried) == ("fully one command", []), "another attempt's run"
    outcome, retried = judged(tmp_path, "Prompt: v8\nAgent: Cursor agent\n", *pair)
    assert retried == [] and outcome.why == (
        "install.sh exit 0",
        "human turns unknown: the attempt has no Attempt: line (install.sh --log-start)",
    )


def test_the_exits_of_a_stopped_run_are_the_installers_signal_traps() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    traps = tuple(int(code) for code in re.findall(r"^trap 'on_signal (\d+)' [A-Z]+$", script, re.M))
    assert traps == setup_report.STOPPED_EXITS and len(traps) == 3


def test_expected_doctor_warns_are_annotated_not_their_fix(fake_mac: dict[str, Path]) -> None:
    """L8, V3: an expected warn's fix is nothing for this setup to do: the Doctor section says why it is
    expected instead; an unexpected warn keeps its fix."""
    v6_install_log(fake_mac)
    write_friction(fake_mac, V6_HAPPY)

    def doctor(config: object) -> list[str]:
        return [
            "[ok  ] python — fine",
            "[warn] launcher.signature — valid; ad hoc; hardened runtime: every rebuild is a new TCC subject "
            "(fix: SIGN_IDENTITY='Developer ID Application: <Org> (<TEAMID>)' launcher/build.sh, then "
            "scripts/install.sh)",
            "[warn] launcher.requirement — designated => cdhash (ad hoc: a PPPC CodeRequirement cannot pin a "
            "cdhash) (for IT: Developer ID build (docs/deploy/mdm))",
            "[warn] launchd.poll — com.agentsync.poll is not installed (fix: agentsync install-agent)",
            "[warn] launchd.reconcile — com.agentsync.reconcile is not installed (installed by the agent "
            "step below)",
            "[warn] network.proxy — odd (fix: set [network] proxy)",
        ]

    text, summary = summary_of(fake_mac, doctor=doctor)
    doc = section(text, "Doctor")
    assert "4 expected warns end in (expected: <why>) instead of their fix" in doc
    lines = {ln.split(" — ", 1)[0].split("] ", 1)[1].strip(): ln for ln in doc.splitlines() if " — " in ln}
    assert lines["launcher.signature"].endswith("new TCC subject (expected: ad hoc launcher)")
    assert lines["launcher.requirement"].endswith("cannot pin a cdhash) (expected: ad hoc launcher)")
    for name in ("launchd.poll", "launchd.reconcile"):
        assert lines[name].endswith("is not installed (expected: LaunchAgents not installed yet)"), name
    assert lines["network.proxy"].endswith("(fix: set [network] proxy)"), "an unexpected warn keeps its fix"
    assert "SIGN_IDENTITY" not in text and "install-agent)" not in doc
    assert "unexpected 1: network.proxy warn" in summary


@pytest.mark.usefixtures("roomy_calls")
def test_ocr_not_built_is_an_info_line_and_an_ocr_warn_is_unexpected(fake_mac: dict[str, Path]) -> None:
    """Doctor's ``ocr`` line when the helper is not built is INFO: shown, never counted as a problem, so
    ``expected_warn`` needs no entry for it.  An ``ocr`` warn means OCR is broken: unexpected, with its fix,
    and (OCR being optional) with the same outcome."""
    v6_install_log(fake_mac)
    write_friction(fake_mac, V6_HAPPY)
    info = "[info] ocr — the OCR helper is not built; ~/src/agent-context-sync/scripts/install.sh builds it"
    warn = (
        "[warn] ocr — on-device OCR is not working: no Xcode or Command Line Tools (xcode-select -p names "
        "no folder) (fix: xcode-select --install, then run ~/src/agent-context-sync/scripts/install.sh again)"
    )

    def outcome(text: str) -> str:
        return next(ln for ln in section(text, "Summary").splitlines() if ln.startswith("- **outcome:"))

    text, summary = summary_of(fake_mac, doctor=lambda config: ["[ok  ] python — fine", info])
    assert info in section(text, "Doctor")
    assert "- doctor: 0 FAIL, 0 warn (2 checks) · unexpected 0" in summary
    assert setup_report.expected_warn("ocr", info.split(" — ", 1)[1], agents_installed=False) is None
    built = outcome(text)

    text, summary = summary_of(fake_mac, doctor=lambda config: ["[ok  ] python — fine", warn])
    assert warn in section(text, "Doctor"), "an unexpected warn keeps its fix"
    assert "- doctor: 0 FAIL, 1 warn (2 checks) · unexpected 1: ocr warn" in summary
    assert outcome(text) == built


@pytest.mark.usefixtures("roomy_calls")
def test_a_speech_build_that_ran_out_of_time_is_its_own_problem_never_expected(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 18 V5, V28: a speech helper build that ran out of time was "unexpected 1" with nothing to do.
    Speech is missing, so it is no expected warn; it gets a bucket of its own, keyed on the report's own read
    of the build's marker, never on doctor's words. Any other failed speech build stays unexpected."""
    v6_install_log(fake_mac)
    write_friction(fake_mac, V6_HAPPY)
    config = load_config(fake_mac["config"])
    marker = ocr._marker(ocr._helper_path(config.cache_dir, speech.SPEECH))
    marker.parent.mkdir(parents=True, exist_ok=True)
    warn = f"[warn] speech — speech: failed ({speech._TIMED_OUT}) (fix: run install.sh again)"
    marker.write_text(speech._TIMED_OUT, encoding="utf-8")
    _text, summary = summary_of(fake_mac, doctor=lambda config: ["[ok  ] python — fine", warn])
    assert (
        "- doctor: 0 FAIL, 1 warn (2 checks) · from the speech helper's build that ran out of time "
        "(1 check): speech warn (install.sh builds it again) · unexpected 0" in summary
    ), summary
    assert "expected 1" not in summary
    # V5's review: a marker that ran out of time beside a warn with another reason (an untrusted helper)
    other = "[warn] speech — speech: failed (the speech helper is not owned by this user)"
    _text, summary = summary_of(fake_mac, doctor=lambda config: ["[ok  ] python — fine", other])
    assert "- doctor: 0 FAIL, 1 warn (2 checks) · unexpected 1: speech warn" in summary, summary
    marker.write_text("swift package resolve failed (exit 1): could not reach the host", encoding="utf-8")
    _text, summary = summary_of(fake_mac, doctor=lambda config: ["[ok  ] python — fine", warn])
    assert "- doctor: 0 FAIL, 1 warn (2 checks) · unexpected 1: speech warn" in summary, summary


def test_residue_ignores_macos_path_components_and_the_temp_folder_is_redacted(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L6, L7: /Users/<user>/Library/Application Support/... is no residue (a SHADOW path in every sandbox
    run), and the per-account temporary folder id (/var/folders/<x>/<y>, $TMPDIR) is <tmp>."""
    tmp_id = "/private/var/folders/zz/abcdEFGH1234_ijkl0000gn"
    monkeypatch.setenv("TMPDIR", f"{tmp_id}/T/")
    v6_install_log(fake_mac)
    write_friction(
        fake_mac,
        _insert_before(
            "2026-09-29T10:01:10Z | end",
            f"2026-09-29T10:00:40Z | step 2 | deviation | used /Users/{LOGIN}/.local/bin/agentsync and "
            f"/Users/{LOGIN}/Library/Application Support/{FOLDERS[1]} and /Volumes/{FOLDERS[2]} | -\n"
            f"2026-09-29T10:00:41Z | step 2 | deviation | codesign wrote {tmp_id}/T/build.x and "
            "/var/folders/yy/OtherAccount99/C/cache | -\n",
            V6_HAPPY,
        ),
    )
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    fr = section(text, "Agent friction log")
    assert "/Users/<user>/.local/bin/agentsync and /Users/<user>/Library/Application Support/<folder-2>" in fr
    assert "/Volumes/<folder-3>" in fr
    assert "codesign wrote <tmp>/build.x and <tmp>/C/cache" in fr
    assert "abcdEFGH1234" not in text and "OtherAccount99" not in text
    red = section(text, "Redaction")
    assert "Residue check: no capitalized or joined word next to a placeholder in this report." in red
    assert "<tmp> this account's temporary folder" in red
    assert setup_report.residue("/Users/<user>/Library/Application Support/<folder-1> Board <org-1>") == [
        "Board"
    ]
    # B13 round 12 N8: a word glued to a placeholder with no separator, as M50's cut inside a longer word
    # leaves it; "2026" starts with a digit, and "My" is an ignored word
    assert setup_report.residue("<org-1>Drive, <org-1>2026, My<org-1>Team and Acme<org-1>") == [
        "Drive",
        "Team",
        "Acme",
    ]
    assert setup_report.residue("/Users/<user>/Development/x and /Volumes/<folder-1>/Roadmap") == [
        "Roadmap"
    ], "a folder in the login's home is a path component; a folder below a redacted one is still checked"
    assert setup_report.residue("~/Development/<folder-1> and /Users/<user>/Projects/Work/<folder-2>") == []
    assert setup_report.residue("fix: chmod -R go-rwx ~/'Development/<folder-1>/docs'") == [], (
        "a doctor fix writes a home path as ~/'<rest>' (§16.39 N11)"
    )
    assert setup_report.residue('fix: mkdir -p ~/"Development/<folder-1>/docs"') == [], (
        'a path with a \' is written ~/"<rest>" (§16.45 R78)'
    )
    assert setup_report.residue("~/Development/Acme/<folder-1> and <folder-2> Development") == [
        "Acme",
        "Development",
    ], "only the generic folders right below the home are a prefix (field report 2026-10-07)"
    assert setup_report.residue("abc-<folder-1> comms and <source-2>_xyz, alpha-beta-<org-1>") == [
        "abc",
        "xyz",
        "beta",
    ], "a word of any case joined by - or _ (field report 2026-10-07); of a compound, its last piece"
    assert setup_report.residue("OneDrive-<org-1> at https://<org-1>-my.sharepoint.com, see <folder-1>") == []


@pytest.mark.parametrize("origin", ["github", "mirror"])
def test_install_source_reads_the_configured_origin_and_says_a_rewrite_changes_it(
    tmp_path: Path, origin: str
) -> None:
    """B13 round 18 V70: ``remote get-url`` applies a URL rewrite (insteadOf; CONTRACTS §16.72 H21), so a
    checkout whose origin is GitHub read ``origin: other (redacted)`` under a rewrite alone, and the person
    could not tell a mirror remote from a rewrite. The label is the configured URL's; a rewrite that sends it
    elsewhere is said beside it, never its host. A mirror origin stays ``other``."""
    repo = tmp_path / "checkout"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "one")
    url = {
        "github": "https://github.com/renchris/agent-context-sync.git",
        "mirror": "https://git.contoso.example/mirror/agent-context-sync.git",
    }[origin]
    git("remote", "add", "origin", url)
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    run = setup_report._Run(tmp_path / "none.toml", setup_report.ReportHooks(), 12.0)
    label = "github.com/renchris/agent-context-sync" if origin == "github" else "other (redacted)"
    assert f" · origin: {label} · " in setup_report._checkout_facts(run, repo)
    git("config", "url.https://git.contoso.example/mirror/.insteadOf", "https://github.com/renchris/")
    facts = setup_report._checkout_facts(run, repo)
    if origin == "github":
        assert f" · origin: {label} (a URL rewrite sends it elsewhere) · " in facts, facts
    else:
        assert f" · origin: {label} · " in facts, facts
    assert "contoso" not in facts


def test_install_source_names_origin_main_and_the_tree(tmp_path: Path) -> None:
    """L10: which code ran. A checkout's origin (this repository, or "other" without its URL), whether its
    HEAD is on origin/main, and the dirty tree's fingerprint as install.sh computes it."""
    assert setup_report.origin_label("https://github.com/renchris/agent-context-sync.git") == (
        "github.com/renchris/agent-context-sync"
    )
    for same in (
        "git@github.com:renchris/agent-context-sync",
        "ssh://git@github.com/Renchris/agent-context-sync/",
    ):
        assert setup_report.origin_label(same) == "github.com/renchris/agent-context-sync", same
    for other in ("https://github.com/someone/agent-context-sync.git", "https://git.contoso.com/x/y.git"):
        assert setup_report.origin_label(other) == "other (redacted)", other
    assert setup_report.origin_label("") == "none"
    repo = tmp_path / "checkout"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "one")
    git("remote", "add", "origin", "https://github.com/someone-else/fork.git")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    run = setup_report._Run(tmp_path / "none.toml", setup_report.ReportHooks(), 12.0)
    sha = git("rev-parse", "--short=12", "HEAD")
    facts = setup_report._checkout_facts(run, repo)
    assert facts == f" @ {sha} · origin: other (redacted) · on origin/main: yes (no fetch since the clone)"
    assert "someone-else" not in facts
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    git("commit", "-q", "-am", "two")
    (repo / "a.txt").write_text("three\n", encoding="utf-8")
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-ext-diff", "--no-color", "HEAD", "--"],
        check=True,
        capture_output=True,
    ).stdout
    facts = setup_report._checkout_facts(run, repo)
    tree = setup_report.tree_fingerprint(diff)
    assert re.fullmatch(r"[0-9a-f]{12}", tree)
    assert f"(uncommitted changes, tree={tree})" in facts and "on origin/main: no, 1 ahead (" in facts

    # B13 round 7 H22: "yes" is any ancestor of origin/main, so it says how far behind it is
    git("checkout", "-q", "--", "a.txt")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("checkout", "-q", "--detach", "HEAD~1")

    def facts() -> str:  # a fresh run each time: its 12 s budget covers one call, not the whole test
        return setup_report._checkout_facts(
            setup_report._Run(tmp_path / "none.toml", setup_report.ReportHooks(), 12.0), repo
        )

    assert " · on origin/main: yes, 1 behind (no fetch since the clone)" in facts()
    (repo / "a.txt").write_text("four\n", encoding="utf-8")
    git("commit", "-q", "-am", "four")
    assert " · on origin/main: no, 1 ahead, 1 behind (" in facts()


def test_environment_says_whether_each_provider_folder_is_a_file_provider_domain(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L10: a real OneDrive domain versus a sandbox's plain folder, from the folder's own attributes."""
    if sys.platform == "darwin":
        assert setup_report.file_provider_marker(tmp_path) is None, "a plain folder has none"
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    env = section(text, "Environment")
    assert "OneDrive-<org-1> (not a File Provider domain: no File Provider attribute (a plain folder))" in env
    for module in (setup_report, arm_local):  # the walk reads it too
        monkeypatch.setattr(
            module,
            "file_provider_marker",
            lambda p: "com.apple.file-provider-domain-id" if p.name == f"OneDrive-{ORG}" else None,
        )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    env = section(text, "Environment")
    assert "OneDrive-<org-1> (File Provider domain (com.apple.file-provider-domain-id))" in env
    assert "OneDrive-SharedLibraries-<org-1> (not a File Provider domain" in env
    # B13 round 4 Q33: a symlink is read as the walk reads it, and is no provider folder of its own; since
    # round 6 G40 the walk reads it as the provider folder it points into (here a domain), not by its own
    # attributes
    root = setup_report.cloud_storage_root()
    (root / "OneDrive-Zeta").symlink_to(root / f"OneDrive-{ORG}")
    (root / "Icon\r").write_text("", encoding="utf-8")
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    env = section(text, "Environment")
    assert "listable, 2 provider folders and 1 symlink and 1 other entry: " in env, env
    assert (  # B13 round 16 T40: the link's own name is a <link-N>, and the folder it points into is named
        "<link-1> (a symlink into OneDrive-<org-1>: read as that folder, File Provider domain "
        "(com.apple.file-provider-domain-id))" in env
    ), env
    assert "(not a folder: read as a File Provider domain (the empty-folder rule stands))" in env
    assert "OneDrive-<org-1> (File Provider domain (com.apple.file-provider-domain-id))" in env
    assert "OneDrive-SharedLibraries-<org-1> (not a File Provider domain: no File Provider attribute" in env
    assert arm_local._is_domain(root / "OneDrive-Zeta"), "the walk's own rule"


# ---------------------------------------------------------------------------------------------------------
# the next round's evidence (CONTRACTS.md 16.28): the ### parts under Status. Counts and fixed words only.
# ---------------------------------------------------------------------------------------------------------

SECRET_NAMES = ("Wingtip", "Northwind", "Tailspin", "Fourth Coffee", "merger", "payroll", "ledger-2031")
"""Made-up folder and file names the seeded data holds: none may reach a report."""
OCR_VERSION = "2.1.0+pypdfium2-4.30.0+pdfminer.six-20231228+ocr-paper-vision-r2-h0.3.0-l1"
PLAIN_VERSION = "2.1.0+pypdfium2-4.30.0+pdfminer.six-20231228"


SEED_NUMBERS = itertools.count(1)


class Seed:
    """A manifest with made-up rows, written straight into its tables: what a few hundred cycles leave."""

    def __init__(self, cfg: Path) -> None:
        self.config = load_config(cfg)
        self.m = Manifest(self.config.state_paths.db)
        self.m.sync_sources(self.config.sources)
        self.n = 0  # the last number taken from SEED_NUMBERS: unique across the seeds of one test

    def ids(self) -> list[str]:
        return [s.id for s in self.config.sources]

    def source(self, source_id: str, *, complete: bool = True) -> None:
        """A ``sources`` row for an id the config does not have (a retired source keeps its rows)."""
        self.m._db.execute(
            "INSERT OR IGNORE INTO sources (source_id, kind, config_state, config_fingerprint) "
            "VALUES (?, 'local', 'live', 'f')",
            (source_id,),
        )
        self.m._db.execute(
            "UPDATE sources SET enumeration_complete = ? WHERE source_id = ?", (int(complete), source_id)
        )

    def item(
        self,
        source_id: str,
        name: str,
        *,
        state: str = "live",
        reason: str | None = None,
        dataless: bool = False,
        verdict: str = "unchanged",
        size: int = 1000,
        canonical: str | None = None,
        rel: str | None = None,
        page: str | None = "ok",
        version: str | None = PLAIN_VERSION,
        cached: str | None = None,
        built: int = 1,
    ) -> str:
        """One file row and, unless ``page`` is None, its one output row (``cached``: the version of the
        cache row behind its action key, when that differs from the output row's own)."""
        self.n = next(SEED_NUMBERS)
        stable = f"id-{self.n:06d}"
        where = rel or f"Wingtip merger/{name}"
        self.m._db.execute(
            "INSERT INTO items (source_id, stable_id, name, rel_path, is_dir, size, dataless, state, "
            "state_reason, last_verdict, first_seen_run, last_seen_run, canonical_sha256) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, 1, 1, ?)",
            (source_id, stable, name, where, size, int(dataless), state, reason, verdict, canonical),
        )
        if page is not None:
            key = f"key-{self.n:06d}"
            self.m._db.execute(
                "INSERT INTO outputs (output_path, source_id, stable_id, unit_id, action_key, converter_id, "
                "converter_version, status, built_run) VALUES (?, ?, ?, 'whole', ?, 'conv', ?, ?, ?)",
                (f"mirror/{source_id}/{stable}.md", source_id, stable, key, version, page, built),
            )
            if cached is not None:
                self.m._db.execute(
                    "INSERT INTO cache (action_key, converter_id, converter_version, options_hash, "
                    "canonical_sha256, status, unit_count, bytes, created_run, last_used_run) "
                    "VALUES (?, 'conv', ?, 'o', 'c', 'ok', 1, 1, ?, ?)",
                    (key, cached, built, built),
                )
        return stable

    def run(
        self, run_id: int, counts: dict[str, int], *, mode: str = "poll", day: str = "2026-10-06"
    ) -> None:
        self.m._db.execute(
            "INSERT INTO runs (run_id, mode, started_at, finished_at, status, host, pid, counts_json) "
            "VALUES (?, ?, ?, ?, 'ok', 'Tailspin-MacBook', 1, ?)",
            (run_id, mode, f"{day}T10:00:00Z", f"{day}T10:01:05Z", json.dumps(counts)),
        )

    def close(self) -> None:
        self.m.close()


def status_parts(fake_mac: dict[str, Path], **kwargs: Any) -> tuple[str, dict[str, str]]:
    """The report (no hooks) and each evidence part's text by its ``### `` title."""
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks(), **kwargs)
    status = section(text, "Status")
    parts = {}
    for title in setup_report.EVIDENCE_TITLES:
        parts[title] = status.split(f"\n### {title}\n", 1)[1].split("\n### ", 1)[0]
    for name in SECRET_NAMES:
        assert name not in text and name.lower() not in text.lower(), f"{name!r} reached the report"
    return text, parts


def test_the_evidence_parts_sit_under_status_and_say_so_when_nothing_has_synced(
    fake_mac: dict[str, Path],
) -> None:
    text, parts = status_parts(fake_mac)
    headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
    assert headings == list(setup_report.SECTION_TITLES), "sub-headings: the ## headings are as they were"
    status = section(text, "Status")
    assert re.findall(r"^### (.+)$", status, flags=re.MULTILINE) == list(setup_report.EVIDENCE_TITLES)
    assert "- helper: off (AGENTSYNC_OCR=0)" in parts["OCR"] and "- [policy] label rule: off" in parts["OCR"]
    assert "- helper: off (AGENTSYNC_OCR=0)\n- media helper: off (" in parts["OCR"], "by Doctor's media probe"
    assert re.search(r"^- media helper: off \(.*\)\n- speech: off \(", parts["OCR"], re.MULTILINE), (
        "Doctor's speech probe"
    )
    assert "- images: 0 files\n" in parts["OCR"] and "- OCR time: no run is recorded" in parts["OCR"]
    assert parts["Quarantine by reason"].strip() == "- no file is quarantined or refused"
    assert parts["Purge queue"].strip() == "- no purge is queued"
    assert parts["Overlapping sources"].strip() == (
        "- none: no source's folder is inside another's (3 sources with a folder)"
    )
    assert parts["Empty cloud folders"].strip() == (
        "- none: no source's last walk held a zero-child cloud folder as unknown"
    )
    assert parts["Repeat conversions"].strip() == "- no run is recorded"
    assert "- evidence: the 6 parts at the end of Status were measured\n" in section(text, "Summary")
    # No sync has run on this Mac: there is no manifest, and reading makes none.
    db = load_config(fake_mac["config"]).state_paths.db
    before = sorted(p.name for p in db.parent.iterdir())
    _text, parts = status_parts(fake_mac)
    assert sorted(p.name for p in db.parent.iterdir()) == before, "the manifest is opened read-only"
    for leftover in db.parent.glob(db.name + "*"):
        leftover.unlink()
    _text, parts = status_parts(fake_mac)
    for title in ("Quarantine by reason", "Empty cloud folders", "Repeat conversions"):
        assert parts[title].strip() == "- no manifest yet (no sync has run)", title
    assert "- helper: off (AGENTSYNC_OCR=0)" in parts["OCR"] and "- no manifest yet" in parts["OCR"]
    assert not db.exists(), "reading made no manifest"


NO_CONVERTER = "no converter for .png"
IMAGE_VERSION = "2.0.0+ocr-paper-vision-r2-h0.3.0-l1"
NOT_RUN = "no text layer (scanned or image-only PDF; OCR not run)"
NO_TEXT_FOUND = "no text layer (scanned or image-only PDF; on-device OCR found no text)"


def seed_ocr(fake_mac: dict[str, Path]) -> tuple[str, str]:
    """Images in every outcome, documents with and without an engine's identity, two scanned PDFs, one
    source's re-read record and three runs: one of an earlier build, one that used up its OCR time, one
    whose helper stopped. Returns the two cloud sources' ids."""
    seed = Seed(fake_mac["config"])
    one, _inbox, two = seed.ids()
    seed.item(one, "Wingtip plan.png", version=IMAGE_VERSION)
    seed.item(one, "Wingtip plan 2.PNG", version=IMAGE_VERSION)
    no_text = "no text found in the image by on-device OCR"
    seed.item(one, "Northwind logo.jpeg", state="quarantined", reason=no_text, page="quarantined")
    online = {"dataless": True, "size": 4_000_000}
    seed.item(one, "Northwind photo.heic", state="refused", reason=NO_CONVERTER, page="refused", **online)
    seed.item(two, "Tailspin scan.tiff", state="refused", reason=NO_CONVERTER, page="refused")
    seed.item(two, "Tailspin late.png", verdict="deferred", page=None)
    seed.item(two, "Tailspin cloud.png", verdict="deferred", state="dataless", page=None, **online)
    failed = "conversion failed: KeyError('Fourth Coffee payroll')"
    seed.item(two, "payroll.gif", state="quarantined", reason=failed, verdict="error", page="failed")
    seed.item(one, "ledger-2031.pdf", version=PLAIN_VERSION, cached=OCR_VERSION)  # the H2 cutoff's key
    seed.item(one, "ledger-2031 b.pdf", version=PLAIN_VERSION)
    seed.item(two, "merger deck.pptx", version="1.0.0+python-pptx-1.0.2+ocr-off")
    seed.item(two, "merger notes.docx", version="1.0.0+pandoc-3.1", dataless=True, state="dataless")
    seed.item(one, "Wingtip scan.pdf", state="quarantined", reason=NOT_RUN, page="quarantined", built=3)
    seed.item(
        one, "Wingtip blank.pdf", state="quarantined", reason=NO_TEXT_FOUND, page="quarantined", built=4
    )
    record = [
        {"done": False, "for": "a" * 64, "tried": ["id-9"], "failed": {"id-10": 1}, "reading": "id-1"},
        {"done": True, "for": "b" * 64, "tried": ["Wingtip x", "Wingtip y"]},
    ]
    seed.m.set_meta("reread:" + one, json.dumps(record))
    seed.run(3, {"A": 4}, day="2026-09-30")
    again = {"converted": 6, "converted_seen": 6, "converted_again": 6}
    over = {"ocr_ms": 181_500, "ocr_budget_s": 180, "ocr_over": 1, "ocr_deferred": 3, "ocr_without_budget": 2}
    seed.run(4, {**again, **over, "reread": 4, "reread_kept": 1})
    down = {"ocr_ms": 2_250, "ocr_budget_s": 180, "ocr_down": 1, "ocr_failed": 1, "ocr_without_down": 2}
    seed.run(
        5, {**again, **down, "converted_failed": 1, "ocr_page_cap": 1, "ocr_picture_cap": 1}, mode="reconcile"
    )
    seed.close()
    return one, two


def test_the_ocr_part_folds_the_document_kinds_with_no_page_into_one_line(fake_mac: dict[str, Path]) -> None:
    """B13 round 10 L5: the OCR part printed a row of zeros for each document kind this Mac has no page of
    (.potx, .odt) and "scanned PDFs with a stub: 0 ..., 0 ..., 0 ...". A kind with no page is folded into one
    line that says "has a page", never "no file": the query counts only files with a page, so a refused
    template is there and reads 0. The images-not-on-this-Mac line stays at 0: it is the cost the open
    decision O2 is measured by."""
    seed = Seed(fake_mac["config"])
    one = seed.ids()[0]
    seed.item(one, "ledger.pdf")
    seed.item(one, "Wingtip template.potx", state="refused", reason=NO_CONVERTER, page="refused")
    seed.close()
    _text, parts = status_parts(fake_mac)
    ocr = parts["OCR"].splitlines()
    assert "- PDF (.pdf) files with a page: 0 with an OCR identity, 1 without an OCR identity" in ocr, ocr
    assert [ln for ln in ocr if "has a page" in ln] == [
        "- no deck (.pptx), deck template (.potx), Word (.docx) or OpenDocument (.odt) file has a page"
    ], ocr
    assert not [ln for ln in ocr if "files with a page: 0 with an OCR identity, 0 without" in ln], ocr
    assert "- scanned PDFs with a stub: none" in ocr, ocr
    assert [ln for ln in ocr if ln.startswith("- images that are not on this Mac: 0 files")], ocr


def test_ocr_part_counts_images_documents_rereads_and_time(fake_mac: dict[str, Path]) -> None:
    seed_ocr(fake_mac)
    text, parts = status_parts(fake_mac)
    ocr_part = parts["OCR"]
    assert (
        "- images: 8 files: 2 with a page · 1 no-text stub · 1 not-on-this-Mac stub · 1 no-converter stub "
        "on this Mac · 1 deferred on this Mac · 1 deferred online-only · 1 failed\n" in ocr_part
    )
    assert (
        "- images that are not on this Mac: 2 files, 8.0 MB (2 online-only, 0 of a Graph source; none is "
        "downloaded for OCR;" in ocr_part
    )
    assert (
        "- PDF (.pdf) files with a page: 1 with an OCR identity, 1 without an OCR identity\n" in ocr_part
    ), "the version is the cache row's when the page's key has one"
    assert (
        "- deck (.pptx) files with a page: 0 with an OCR identity, 0 without an OCR identity, 1 from the "
        "early OCR build (an OCR build that was never released)\n" in ocr_part
    ), "the term's first mention says what it is (B13 round 7 H5)"
    assert ocr_part.count("(an OCR build that was never released)") == 1 and "field build" not in ocr_part

    assert (
        "- Word (.docx) files with a page: 0 with an OCR identity, 1 without an OCR identity (1 "
        "online-only)\n" in ocr_part
    )
    assert (
        "- scanned PDFs with a stub: 1 no text layer (OCR not run), 1 no text layer (OCR found no text), 0 "
        "no text layer (over the OCR page limit)\n" in ocr_part
    )
    assert "- engine identities on pages: ocr-paper-vision-r2-h0.3.0-l1 (3 pages)\n" in ocr_part
    # B13 round 10 L5: the kinds with no page are one line, not a row of zeros each
    assert "(.potx) files with a page" not in ocr_part and "(.odt) files with a page" not in ocr_part
    folded = [ln for ln in ocr_part.splitlines() if ln.startswith("- no ")]
    assert folded == ["- no deck template (.potx) or OpenDocument (.odt) file has a page"], folded
    rows = [ln for ln in ocr_part.splitlines() if ln.startswith("| <")]
    assert rows[:2] == [
        # one PDF without an identity and one scan OCR was not run on; the online-only image does not wait
        "| <folder-2> | no | 2 | 0 | 1 | 1 | 2 | yes |",
        # the image with the no-converter stub and the deck of the field build; the Word file is online-only
        "| <folder-3> | no record | 2 | 1 | 0 | 0 | 0 | no |",
    ]
    # OCR is off in this test (AGENTSYNC_OCR=0) and no run says what its re-read looked for: the table says
    # both, so its counts are not read as what a cycle is about to do.
    lead = next(ln for ln in ocr_part.splitlines() if ln.startswith("- re-read, per source."))
    assert (
        "None of the last 3 runs says what its re-read looked for (a run of this build that reaches a "
        "local source does), so the second column is as last recorded, by whichever build wrote the record."
        in lead
    )
    assert (
        "The helper is not ready (its line above), so a cycle has no engine. Without an engine a cycle reads "
        "again only what needs none, such as a page of the early OCR build. The third column is the "
        "report's own count of files on this Mac from before OCR, which a re-read looks at when there is an "
        "engine: "
        "images with the no-converter stub, scanned PDFs whose stub says OCR was not run and documents "
        "whose page has no OCR identity or comes from the early OCR build (fourth column)." in lead
    )
    assert (
        "- OCR time: of the last 3 runs, 2 recorded these counts (a run of an earlier build did not) and 2 "
        "had an engine; 1 used up the cycle's OCR time; 1 ended with the helper not working; the helper ran "
        "184s in all\n" in ocr_part
    )
    assert (
        "- waiting for OCR: the newest run that had an engine (run 5) left 0 files for a later cycle's "
        "OCR; the 3 runs add up to 3 waits, a file counted once in every run it waited in\n" in ocr_part
    )
    assert (
        "- in those runs, each count a sum over the runs (a file converted or read in two of them counts "
        "twice): 2 conversions without OCR because the cycle's OCR time was used up · 2 without OCR "
        "because the helper had stopped working · 1 the engine failed on (a helper failure, or the file's "
        "own time limit) · 1 that say pages past the OCR page limit were not read · 1 that say pictures past "
        "the picture limit were not read · 4 reads again (1 of them could not be converted and kept "
        "its page)\n" in ocr_part
    ), "a re-read keeps its page only when its conversion failed: 0 there is no count of unchanged pages"
    started = "2026-10-06T10:00:00Z | 1m05s"
    assert [ln for ln in ocr_part.splitlines() if re.match(r"\| \d", ln)] == [
        f"| 5 | reconcile | ok | {started} | 2.2s of 180s | no | yes | 0 | 0 + 2 | 1 | 1 + 1 | 0 + 0 | 0 (0) "
        "| - |",
        f"| 4 | poll | ok | {started} | 181.5s of 180s | yes | no | 3 | 2 + 0 | 0 | 0 + 0 | 0 + 0 | 4 (1) "
        "| - |",
    ], "the run of an earlier build had no engine: it is not in the OCR table"
    assert "| pages added + changed | read again (conversion failed) | left to read again |" in ocr_part, (
        "what each run's re-read left; these runs do not say what they looked for, so they have no count"
    )
    assert "\nThe last runs that had an engine, newest first. A run starts no more re-reads once" in ocr_part
    assert (
        "The count includes the files given up (two failed tries). A file that failed once is tried once "
        "more while its source's scan is not finished; under a finished scan it is no longer among the files "
        "left to read again (read since, changed, online-only or gone), and the record keeps its count, a "
        "deleted file's only until the next sync:" in lead
    )
    assert (
        "- <folder-2>, the 1 file that failed once, by its row in the manifest now: 1 with no row\n"
        in ocr_part
    ), "the record's id is no row of this manifest"
    for raw in ("id-9", "id-10", 'id-1"', "a" * 16, "KeyError"):
        assert raw not in text, raw


def test_the_early_ocr_builds_gloss_never_sits_beside_an_online_only_bracket(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 8 J41: the term's gloss went in right after its first mention, and a document cell of the
    early OCR build with online-only files has a bracket there: "1 from the early OCR build (an OCR build
    that was never released) (1 online-only)". The gloss goes at the end of that line instead, once."""
    seed = Seed(fake_mac["config"])
    _one, _inbox, two = seed.ids()
    seed.item(
        two, "merger deck.pptx", version="1.0.0+python-pptx-1.0.2+ocr-off", dataless=True, state="dataless"
    )
    seed.close()
    ocr_part = status_parts(fake_mac)[1]["OCR"]
    [line] = [ln for ln in ocr_part.splitlines() if ln.startswith("- deck (.pptx) files with a page: ")]
    assert line.endswith("1 from the early OCR build (1 online-only; that build was never released)"), line
    assert ") (" not in ocr_part and ocr_part.count("never released") == 1, ocr_part


def test_the_ocr_run_table_says_what_it_hides_and_what_a_file_that_failed_once_is_now(
    fake_mac: dict[str, Path],
) -> None:
    """Field report 2026-10-07, read wrongly three ways. The sums were over six runs that had an engine and
    the table showed five, with nothing to say one was hidden. "442 read again (0 kept the page they had)"
    read as 442 new pages, though a file read again whose text is the same changes no page. And "failed
    once: 1" stood beside "scan finished: yes" and 0 files left, under a lead that said such a file is
    tried once more."""
    seed = Seed(fake_mac["config"])
    one, _inbox, two = seed.ids()
    here = seed.item(one, "Wingtip plan.pdf", version=OCR_VERSION)
    cloud = seed.item(one, "Northwind notes.docx", state="dataless", dataless=True)
    gone = seed.item(one, "Tailspin deck.pptx", state="tombstone", page="tombstone")
    stub = seed.item(one, "payroll.pdf", state="quarantined", reason=NOT_RUN, page="quarantined")
    now = "c0ffee11" + "0" * 56
    failed = {here: 1, cloud: 1, gone: 1, stub: 1, "ledger-2031 id": 1}
    seed.m.set_meta("reread:" + one, json.dumps([{"done": True, "for": now, "tried": [], "failed": failed}]))
    seed.m.set_meta("reread:" + two, json.dumps([{"done": True, "for": now, "tried": []}]))
    engine = {"converted": 0, "ocr_budget_s": 180, "reread_for": int(now[:8], 16)}
    seed.run(1, {"A": 3})  # an earlier build's: no engine, and not in the table
    for run_id, read in enumerate((31, 35, 64, 65, 239, 8), 2):
        changed = {"A": 2, "M": 31} if run_id == 6 else {}
        seed.run(run_id, {**engine, "ocr_ms": 100_000, "reread": read, "reread_left": 8, **changed})
    seed.close()
    text, parts = status_parts(fake_mac)
    ocr_part = parts["OCR"]
    assert (
        "7 runs, 6 recorded these counts (a run of an earlier build did not) and 6 had an engine" in ocr_part
    )
    assert (
        "the helper ran 600s in all\n" in ocr_part and " · 442 reads again (0 of them could not" in ocr_part
    )
    title = next(ln for ln in ocr_part.splitlines() if ln.startswith("The last "))
    assert title == (
        "The last 5 of the 6 runs that had an engine, newest first (the sums above are over all 7 runs "
        "read, so these rows do not add up to them). A run starts no more re-reads once it has spent 120s "
        "on them, whatever OCR time is left: that usually ends its reading first, though one long read can "
        "still use up the OCR time. `pages added + changed` is every page the run added or changed, whatever "
        "the cause: a file read again whose text comes out the same changes no page, so `read again` is no "
        "count of new pages. `mode` is the kind of pass, not who started it: a sync typed in a terminal is "
        "recorded like the background job's:"
    )
    rows = [ln.strip("| ").split(" | ") for ln in ocr_part.splitlines() if re.match(r"\| \d", ln)]
    assert [(cells[0], *cells[-3:]) for cells in rows] == [
        ("7", "0 + 0", "8 (0)", "8"),
        ("6", "2 + 31", "239 (0)", "8"),
        ("5", "0 + 0", "65 (0)", "8"),
        ("4", "0 + 0", "64 (0)", "8"),
        ("3", "0 + 0", "35 (0)", "8"),
    ], "the run that read 31 is the one not shown: the five rows add up to 411 of the 442"
    assert reread_rows(ocr_part)["<folder-2>"][:5] == ["yes", "1", "0", "5", "0"], "finished, 5 failed once"
    assert (
        "- <folder-2>, the 5 files that failed once, by their row in the manifest now: 1 on this Mac · 1 "
        "online-only · 1 with a stub · 1 deleted · 1 with no row\n" in ocr_part
    )
    assert ocr_part.count("that failed once, by their row") == 1, "a source with none has no line"
    for raw in (here, cloud, gone, stub, "c0ffee"):
        assert raw not in text, raw

    # Five runs or fewer: all are shown, and the title says no more than it did.
    seed = Seed(fake_mac["config"])
    seed.m._db.execute("DELETE FROM runs WHERE run_id IN (1, 2)")
    seed.close()
    ocr_part = status_parts(fake_mac)[1]["OCR"]
    assert "\nThe last runs that had an engine, newest first. A run starts no more" in ocr_part
    assert " · 411 reads again (0 of them" in ocr_part
    # More runs than are shown and none had an engine: the same count of what is hidden.
    seed = Seed(fake_mac["config"])
    seed.m._db.execute("DELETE FROM runs")
    for run_id in range(1, 8):
        seed.run(run_id, {"converted": 1})
    seed.close()
    ocr_part = status_parts(fake_mac)[1]["OCR"]
    assert (
        "\nThe last 5 of the 7 runs (none had an engine), newest first (the sums above are over all 7 runs "
        "read, so these rows do not add up to them). A run starts" in ocr_part
    )


def reread_rows(part: str) -> dict[str, list[str]]:
    """The re-read table's rows by their source cell."""
    rows = [ln.strip("| ").split(" | ") for ln in part.splitlines() if ln.startswith(("| <", "| inbox"))]
    return {cells[0]: cells[1:] for cells in rows}


def test_the_re_read_table_goes_by_what_the_newest_run_looked_for(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record says what it was written for (the build, the converters, the engine) and a cycle goes by
    it only when that is what it looks for itself. The table printed ``done`` of whatever record was
    stored: a source no cycle of this build had reached (paused, or held at the macOS prompt) said "scan
    finished: yes" for a scan the build never started."""
    cfg = fake_mac["config"]
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("AGENTSYNC_OCR")
    write_fake(ocr._helper_path(load_config(cfg).cache_dir))
    seed = Seed(cfg)
    one, inbox, two = seed.ids()
    now, earlier = "c0ffee11" + "0" * 56, "0ddba11" + "0" * 57
    seed.m.set_meta("reread:" + one, json.dumps([{"done": True, "for": now, "tried": []}]))
    seed.m.set_meta("reread:" + inbox, json.dumps([{"done": False, "for": now, "tried": ["id-7"]}]))
    stale = [{"done": True, "for": earlier, "tried": ["id-8", "id-9"], "failed": {"id-5": 1}}]
    seed.m.set_meta("reread:" + two, json.dumps(stale))
    seed.item(two, "Tailspin scan.tiff", state="refused", reason=NO_CONVERTER, page="refused")
    seed.item(two, "merger deck.pptx", version="1.0.0+python-pptx-1.0.2+ocr-off")
    engine = {"ocr_ms": 900, "ocr_budget_s": 180}
    seed.run(6, {"converted": 1, **engine, "reread": 3, "reread_for": int(now[:8], 16)})
    seed.run(7, {"converted": 0, "reread_for": int(earlier[:8], 16)})
    seed.run(8, {"converted": 2, **engine, "reread": 5, "reread_left": 31, "reread_for": int(now[:8], 16)})
    seed.run(9, {"converted": 0, "ocr_ms": 0, "ocr_budget_s": 180})  # `materialise PATH`: no re-read ran
    seed.close()
    text, parts = status_parts(fake_mac)
    assert "- helper: ready (paper-vision revision 2, helper 0.3.0)\n" in parts["OCR"]
    # The per-run table's last column: the files each run's re-read left. Read down the runs it is how many
    # syncs the re-read takes. A run that did not look has no count; one that looked and left none says 0.
    runs = [ln.strip("| ").split(" | ") for ln in parts["OCR"].splitlines() if re.match(r"\| \d", ln)]
    assert [(cells[0], *cells[-2:]) for cells in runs] == [
        ("9", "0 (0)", "-"),
        ("8", "5 (0)", "31"),
        ("6", "3 (0)", "0"),
    ]
    assert reread_rows(parts["OCR"]) == {
        "<folder-2>": ["yes", "0", "0", "0", "0", "0", "no"],
        "inbox": ["no", "0", "0", "0", "1", "0", "no"],
        # its record is the earlier build's: no cycle goes by its "done", and what it gave up is the other
        # build's too
        "<folder-3>": ["not started", "2", "1", "0", "0", "2", "no"],
    }
    lead = next(ln for ln in parts["OCR"].splitlines() if ln.startswith("- re-read, per source."))
    assert (
        "The second column is for what run 8 looked for, the newest run that says so (it had an engine): "
        "`not started` is a record another build or engine left, which no cycle goes by. The third column "
        "is the report's own count of files a re-read would look at: images with the no-converter stub, "
        in lead
    )
    assert "c0ffee" not in text and "0ddba11" not in text and str(int(now[:8], 16)) not in text
    # The newest run that looked had no engine (OCR switched off for one run in a terminal): said as such.
    seed = Seed(cfg)
    seed.run(10, {"converted": 0, "reread_for": int(earlier[:8], 16)})
    seed.close()
    parts = status_parts(fake_mac)[1]
    assert (
        "what run 10 looked for, the newest run that says so (it had no engine, so a finished scan says "
        "nothing of OCR)" in parts["OCR"]
    )
    assert [cells[0] for cells in reread_rows(parts["OCR"]).values()] == ["not started", "not started", "yes"]
    # Under a label rule no image is read: the image with the no-converter stub is not a file to read again.
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + '\n[policy]\nexclude_label_ids = ["00000000-0000-4000-8000-00000000c0de"]\n',
        encoding="utf-8",
    )
    parts = status_parts(fake_mac)[1]
    assert reread_rows(parts["OCR"])["<folder-3>"][1:3] == ["1", "1"], "the deck of the field build only"
    lead = next(ln for ln in parts["OCR"].splitlines() if ln.startswith("- re-read, per source."))
    assert "would look at: scanned PDFs whose stub says OCR was not run and documents whose page" in lead
    assert "A label rule is on, so no image is read and none is counted." in lead


def test_an_image_of_a_graph_source_is_not_on_this_mac(fake_mac: dict[str, Path]) -> None:
    """A Graph item's ``dataless`` column is 0, and no image is ever downloaded for OCR: its ``no
    converter`` stub was counted as one "on this Mac", among the files a re-read would look at."""
    cfg = fake_mac["config"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + '\n[graph]\nclient_id = "00000000-0000-0000-0000-000000000000"\n'
        + '\n[[source]]\nid = "tailspin-drive"\nkind = "graph_drive"\ndrive_id = "me"\n',
        encoding="utf-8",
    )
    seed = Seed(cfg)
    one = seed.ids()[0]
    seed.item("tailspin-drive", "Northwind photo.png", state="refused", reason=NO_CONVERTER, page="refused")
    seed.item("tailspin-drive", "merger notes.pdf", version=PLAIN_VERSION)
    seed.item(one, "Wingtip plan.png", state="refused", reason=NO_CONVERTER, page="refused", size=2_000_000)
    seed.item(one, "Wingtip cloud.png", state="refused", reason=NO_CONVERTER, page="refused", dataless=True)
    seed.close()
    text, parts = status_parts(fake_mac)
    assert "- images: 3 files: 2 not-on-this-Mac stub · 1 no-converter stub on this Mac\n" in parts["OCR"]
    assert (
        "- images that are not on this Mac: 2 files, 0.0 MB (1 online-only, 1 of a Graph source; none is "
        "downloaded for OCR;" in parts["OCR"]
    )
    assert reread_rows(parts["OCR"]) == {"<folder-2>": ["no record", "1", "0", "0", "0", "0", "no"]}, (
        "a Graph source has no row: nothing of it is read again"
    )
    assert "tailspin-drive" not in text


@pytest.mark.usefixtures("roomy_calls")
def test_a_backlog_read_over_many_runs_is_reported_as_files_waiting_not_as_a_sum(
    fake_mac: dict[str, Path],
) -> None:
    """A file that waits for OCR is counted in every run it waits in. 1,000 screenshots read 30 a cycle:
    the sum over the runs was printed as "file(s) waited", some 16 times the files there are. The files
    waiting are the newest run's; the sum is worded as waits."""
    seed = Seed(fake_mac["config"])
    left = list(range(970, 0, -30))
    for run_id, waits in enumerate(left, 1):
        seed.run(run_id, {"converted": 30, "ocr_ms": 180_000, "ocr_budget_s": 180, "ocr_deferred": waits})
    seed.run(len(left) + 1, {"converted": 10, "ocr_ms": 60_000, "ocr_budget_s": 180})
    seed.run(len(left) + 2, {"converted": 0})  # a run without an engine: AGENTSYNC_OCR=0 in a terminal
    seed.close()
    ocr_part = status_parts(fake_mac)[1]["OCR"]
    assert sum(left) == 16_170 and len(left) == 33
    assert (
        "- waiting for OCR: the newest run that had an engine (run 34) left 0 files for a later cycle's "
        "OCR; the 35 runs add up to 16170 waits, a file counted once in every run it waited in\n" in ocr_part
    )
    assert "file(s) waited" not in ocr_part
    rows = [ln for ln in ocr_part.splitlines() if re.match(r"\| \d", ln)]
    assert [row.split(" | ")[8] for row in rows] == ["0", "10", "40", "70", "100"], "left waiting, per run"


def test_ocr_part_says_the_helper_is_ready_and_that_a_label_rule_is_on(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe looks and stamps nothing: the helper's modification time is its last use by a cycle, and a
    report is not one."""
    cfg = fake_mac["config"]
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("AGENTSYNC_OCR")
    helper = write_fake(ocr._helper_path(load_config(cfg).cache_dir))
    os.utime(helper, (1_700_000_000, 1_700_000_000))
    _text, parts = status_parts(fake_mac)
    assert "- helper: ready (paper-vision revision 2, helper 0.3.0)\n" in parts["OCR"]
    assert helper.stat().st_mtime == 1_700_000_000, "the report is no use of the helper"
    helper.unlink()
    assert "- helper: not built (the OCR helper is not built)\n" in status_parts(fake_mac)[1]["OCR"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + '\n[policy]\nexclude_label_ids = ["00000000-0000-4000-8000-00000000c0de"]\n',
        encoding="utf-8",
    )
    assert "- [policy] label rule: on (no image is read under one)\n" in status_parts(fake_mac)[1]["OCR"]


def test_an_image_a_label_rule_withholds_is_its_own_outcome_in_the_ocr_part(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 4 Q30: an image whose stub page says withheld by label rule (the wording of a run with the
    engine ready, B13 round 8 J52) is the rule's (Quarantine by reason says `withheld by label rule`), and
    the OCR part's images line names the same outcome, not "other stub". Its count comes first, as on the
    document lines (Q4)."""
    from agentsync import publish  # noqa: PLC0415

    cfg = fake_mac["config"]
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("AGENTSYNC_OCR")
    write_fake(ocr._helper_path(load_config(cfg).cache_dir))
    seed = Seed(cfg)
    one = seed.ids()[0]
    stable = seed.item(
        one, "Northwind logo.png", reason="no converter for .png", state="refused", page="refused"
    )
    seed.close()
    page = load_config(cfg).docs_repo / "mirror" / one / f"{stable}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    reason = publish._withheld_reason(".png")  # the report reads no constant of its own (B13 round 10 L53)
    page.write_text(f"---\nreason: '{reason}'\nstatus: refused\n---\nstub\n", encoding="utf-8")
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + '\n[policy]\nexclude_label_ids = ["00000000-0000-4000-8000-00000000c0de"]\n',
        encoding="utf-8",
    )
    parts = status_parts(fake_mac)[1]
    assert "- images: 1 file: 1 withheld-by-label-rule stub\n" in parts["OCR"], parts["OCR"]
    assert "could not be read" not in parts["Quarantine by reason"], "the page was read (B13 round 9 K62)"
    assert "other stub" not in parts["OCR"]
    assert any(
        ln.split(" | ")[2:4] == ["withheld by label rule", "1"]
        for ln in parts["Quarantine by reason"].splitlines()
    )


def test_an_images_class_follows_its_stub_page_not_this_reports_ocr_probe(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 8 J52, J54: since H52 an image stub's page and _sync/QUARANTINE.tsv keep the wording of the
    run that planned the stub, while the report classed the row by its own OCR probe and said "the page says
    so". A stub planned with no engine reads "no converter" here once an engine is ready, and one planned
    with an engine reads withheld once it is gone; a stub whose page cannot be read goes by the probe."""
    cfg = fake_mac["config"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + '\n[policy]\nexclude_label_ids = ["00000000-0000-4000-8000-00000000c0de"]\n',
        encoding="utf-8",
    )
    docs = load_config(cfg).docs_repo
    seed = Seed(cfg)
    one = seed.ids()[0]
    pages = {
        "no converter for .png": seed.item(
            one, "Northwind logo.png", reason="no converter for .png", state="refused", page="refused"
        ),
        "withheld by label rule: the label of a .jpeg file cannot be read": seed.item(
            one, "Northwind map.jpeg", reason="no converter for .jpeg", state="refused", page="refused"
        ),
    }
    unread = seed.item(one, "Northwind chart.gif", reason="no converter for .gif", state="refused")
    seed.close()
    for reason, stable in pages.items():
        page = docs / "mirror" / one / f"{stable}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(f"---\nreason: '{reason}'\nstatus: refused\n---\nstub\n", encoding="utf-8")

    def classes() -> tuple[list[list[str]], str, str]:
        parts = status_parts(fake_mac)[1]
        rows = [
            ln.split(" | ")[2:4]
            for ln in parts["Quarantine by reason"].splitlines()
            if ln.startswith("| ") and "---" not in ln
        ][1:]
        return rows, parts["OCR"], parts["Quarantine by reason"]

    # engine ready: the .png page says no converter (planned with no engine), the .jpeg page says withheld,
    # and the .gif, with no page, goes by the probe
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("AGENTSYNC_OCR")
    write_fake(ocr._helper_path(load_config(cfg).cache_dir))
    rows, ocr_part, part = classes()
    assert sorted(rows) == [["no converter", "1"], ["withheld by label rule", "2"]], rows
    assert "- images: 3 files: 1 no-converter stub on this Mac · 2 withheld-by-label-rule stub\n" in ocr_part
    assert "the page says so" not in part
    assert "an image is counted here as its stub page says" in part
    assert (
        "and 1 image stub whose page could not be read is counted as withheld because OCR is ready now"
        in " ".join(part.split())
    ), part
    assert [ln for ln in part.splitlines() if ", no converter by type: " in ln] == [
        "- <folder-2>, no converter by type: .png 1"
    ], part
    # engine gone: the .jpeg page still says withheld; the .png and the unread .gif read no converter
    ocr._helper_path(load_config(cfg).cache_dir).unlink()
    rows, _ocr_part, part = classes()
    assert sorted(rows) == [["no converter", "2"], ["withheld by label rule", "1"]], rows
    assert (  # said with OCR not ready too (B13 round 9 K58)
        "and 1 image stub whose page could not be read is counted under no converter because OCR is not "
        "ready now" in " ".join(part.split())
    ), part
    assert unread


LABEL_RULE_TOML = '\n[policy]\nexclude_label_ids = ["00000000-0000-4000-8000-00000000c0de"]\n'


def quarantine_rows(fake_mac: dict[str, Path]) -> tuple[list[list[str]], str]:
    """The Quarantine part's class and file-count cells, and the part."""
    part = status_parts(fake_mac)[1]["Quarantine by reason"]
    rows = [ln.split(" | ")[2:4] for ln in part.splitlines() if ln.startswith("| ") and "---" not in ln][1:]
    return rows, part


@pytest.mark.parametrize("ready", [True, False], ids=["ocr ready", "ocr not ready"])
def test_an_image_stub_page_from_before_its_reason_said_withheld_is_read_as_withheld(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch, ready: bool
) -> None:
    """B13 round 9 K48: before G29 a withheld image's page kept ``reason: no converter for .png`` under a
    withheld summary and body. The cycle's re-render reads it as withheld (B13 round 8 J47); the report read
    only its ``reason:`` and classed it `no converter`, whatever OCR's state. Both read it by one test now."""
    cfg = fake_mac["config"]
    cfg.write_text(cfg.read_text(encoding="utf-8") + LABEL_RULE_TOML, encoding="utf-8")
    docs = load_config(cfg).docs_repo
    seed = Seed(cfg)
    one = seed.ids()[0]
    by_summary = seed.item(
        one, "Northwind logo.png", reason="no converter for .png", state="refused", page="refused"
    )
    by_body = seed.item(
        one, "Northwind map.jpeg", reason="no converter for .jpeg", state="refused", page="refused"
    )
    plain = seed.item(
        one, "Northwind chart.gif", reason="no converter for .gif", state="refused", page="refused"
    )
    seed.close()
    for stable, suffix, summary, text in (
        (by_summary, ".png", "refused: withheld by label rule: the label cannot be read", "stub"),
        (by_body, ".jpeg", "Northwind map.jpeg", "its content is withheld (content policy). See the TSV."),
        (plain, ".gif", "Northwind chart.gif", "agentsync has no converter for this file."),
    ):
        page = docs / "mirror" / one / f"{stable}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            f'---\nreason: "no converter for {suffix}"\nsummary: "{summary}"\nstatus: refused\n---\n'
            f"> banner\n\n# Northwind file{suffix}\n\n{text}\n",
            encoding="utf-8",
        )
    if ready:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.delenv("AGENTSYNC_OCR")
        write_fake(ocr._helper_path(load_config(cfg).cache_dir))
    rows, part = quarantine_rows(fake_mac)
    assert sorted(rows) == [["no converter", "1"], ["withheld by label rule", "2"]], part
    assert "could not be read" not in part, part


def test_image_stub_pages_are_all_read_and_the_unread_are_said_with_ocr_not_ready(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 9 K58: past 400 image stubs the rest went by the OCR probe, so with OCR not ready a stub
    whose page says withheld was counted under `no converter`, and the legend, which named the unread
    stubs only with OCR ready, said every image was counted as its page says. Every page is read now (within
    the evidence time), and the unread are named in both probe states."""
    from agentsync import publish  # noqa: PLC0415

    cfg = fake_mac["config"]
    cfg.write_text(cfg.read_text(encoding="utf-8") + LABEL_RULE_TOML, encoding="utf-8")
    docs = load_config(cfg).docs_repo
    seed = Seed(cfg)
    one = seed.ids()[0]
    stables = [
        seed.item(one, f"Northwind {n}.png", reason="no converter for .png", state="refused", page="refused")
        for n in range(450)
    ]
    seed.item(one, "Northwind chart.gif", reason="no converter for .gif", state="refused")  # no page
    seed.close()
    folder = docs / "mirror" / one
    folder.mkdir(parents=True, exist_ok=True)
    reason = json.dumps(publish._withheld_reason(".png"))
    for stable in stables:
        (folder / f"{stable}.md").write_text(
            f"---\nreason: {reason}\nstatus: refused\n---\nstub\n", encoding="utf-8"
        )
    rows, part = quarantine_rows(fake_mac)
    assert sorted(rows) == [["no converter", "1"], ["withheld by label rule", "450"]], part
    flat = " ".join(part.split())
    assert (
        "and 1 image stub whose page could not be read is counted under no converter because OCR is not "
        "ready now" in flat
    ), part


def test_image_stubs_whose_pages_are_all_unread_still_get_the_legend(fake_mac: dict[str, Path]) -> None:
    """B13 round 9 K58: with every image page unread and OCR not ready, no row is `withheld by label rule`,
    and the legend, shown only beside such a row, left the unread count out entirely."""
    cfg = fake_mac["config"]
    cfg.write_text(cfg.read_text(encoding="utf-8") + LABEL_RULE_TOML, encoding="utf-8")
    seed = Seed(cfg)
    seed.item(seed.ids()[0], "Northwind chart.gif", reason="no converter for .gif", state="refused")
    seed.close()
    rows, part = quarantine_rows(fake_mac)
    assert rows == [["no converter", "1"]], part
    assert "and 1 image stub whose page could not be read is counted under no converter" in " ".join(
        part.split()
    ), part


def test_a_failed_image_stub_query_is_said_not_passed_over(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 9 K58: an error from the image stub query itself (a locked manifest, say) was swallowed
    before any page was counted, so every image went by the probe and nothing said so."""
    cfg = fake_mac["config"]
    cfg.write_text(cfg.read_text(encoding="utf-8") + LABEL_RULE_TOML, encoding="utf-8")
    seed = Seed(cfg)
    seed.item(
        seed.ids()[0], "Northwind logo.png", reason="no converter for .png", state="refused", page="refused"
    )
    seed.close()
    rows_of = setup_report._Mirror.rows

    def rows(self: setup_report._Mirror, sql: str, params: Sequence[object] = ()) -> list[tuple[Any, ...]]:
        if "ORDER BY i.source_id, i.stable_id" in sql:
            raise sqlite3.OperationalError("database is locked")
        return rows_of(self, sql, params)

    monkeypatch.setattr(setup_report._Mirror, "rows", rows)
    rows_seen, part = quarantine_rows(fake_mac)
    assert rows_seen == [["no converter", "1"]], part
    assert (
        "and every image stub is counted under no converter because OCR is not ready now: the manifest query "
        "for their pages failed" in " ".join(part.split())
    ), part


def test_quarantine_part_groups_by_source_state_and_reason_class(fake_mac: dict[str, Path]) -> None:
    one, two = seed_ocr(fake_mac)
    seed = Seed(fake_mac["config"])
    seed.source("northwind-archive")  # a source the config no longer has: its id is registered with no one
    dup = "duplicate-of tailspin-mail (mirror/tailspin-mail/Wingtip merger/terms.eml.md)"
    seed.item("northwind-archive", "terms.eml", state="refused", reason=dup, page="refused")
    label = 'refused: sensitivity label "Wingtip Secret" is excluded by [policy]'
    seed.item(one, "payroll.xlsx", state="refused", reason=label, page="refused", built=5)
    seed.item(one, "payroll 2.xlsx", state="refused", reason=label, page="refused", built=3)
    seed.item(
        two, "ledger-2031.docx", state="quarantined", reason="contains a credential", page="quarantined"
    )
    seed.item(two, "ledger-2031.zip", reason="hydration-refused", dataless=True, state="dataless", page=None)
    seed.item(two, "Fourth Coffee", state="quarantined", reason="Fourth Coffee said no", page="quarantined")
    seed.close()
    text, parts = status_parts(fake_mac)
    part = parts["Quarantine by reason"]
    assert (
        "- files whose row carries a reason: 1 dataless, 6 quarantined, 5 refused (a reason is shown as"
        in part
    )
    rows = [ln for ln in part.splitlines() if ln.startswith("| ") and "---" not in ln][1:]
    assert rows == [
        "| (<source-1>, not in the config) | refused | duplicate | 1 | 0 | - |",
        "| <folder-2> | quarantined | no text layer (OCR not run) | 1 | 0 | 2026-09-30 |",
        "| <folder-2> | quarantined | no text layer (OCR found no text) | 1 | 0 | 2026-10-06 |",
        "| <folder-2> | quarantined | no text in image | 1 | 0 | - |",
        "| <folder-2> | refused | no converter | 1 | 1 | - |",
        "| <folder-2> | refused | label policy | 2 | 0 | 2026-09-30 to 2026-10-06 |",
        "| <folder-3> | dataless | download refused by the OS | 1 | 1 | - |",
        "| <folder-3> | quarantined | credential | 1 | 0 | - |",
        "| <folder-3> | quarantined | conversion failed | 1 | 0 | - |",
        "| <folder-3> | quarantined | other | 1 | 0 | - |",
        "| <folder-3> | refused | no converter | 1 | 0 | - |",
    ]
    for raw in ("northwind-archive", "tailspin-mail", "terms.eml", "Secret", "said no"):
        assert raw not in text, raw
    assert [ln for ln in part.splitlines() if ", no converter by type: " in ln] == [
        "- <folder-2>, no converter by type: .png 1 (1 online-only)",
        "- <folder-3>, no converter by type: .png 1",
    ], "the two image stubs: a stub can be older than the converter that reads its type now"


def type_counts(part: str) -> dict[str, dict[str, int]]:
    """The "no converter by type" lines of a Quarantine part: source -> type -> files, with what a line
    left out as ``more``."""
    out: dict[str, dict[str, int]] = {}
    for line in part.splitlines():
        if ", no converter by type: " not in line:
            continue
        source, cells = line[2:].split(", no converter by type: ", 1)
        more = re.search(r" \(\+(\d+) files? of \d+ more types?\)$", cells)
        if more is not None:
            cells = cells[: more.start()]
        out[source] = {"more": int(more.group(1))} if more is not None else {}
        for cell in cells.split(" · "):
            found = re.fullmatch(r"(.+?) (\d+)(?: \(.*\))?", cell)
            assert found is not None, cell
            out[source][found.group(1)] = int(found.group(2))
    return out


def table_counts(part: str) -> dict[str, int]:
    """The files in the Quarantine table's ``no converter`` rows, by source."""
    out: dict[str, int] = {}
    for line in part.splitlines():
        cells = line.strip("| ").split(" | ")
        if line.startswith("| ") and len(cells) == 6 and cells[2] == "no converter":
            out[cells[0]] = out.get(cells[0], 0) + int(cells[3])
    return out


def test_quarantine_part_names_the_file_types_no_converter_reads(fake_mac: dict[str, Path]) -> None:
    """Field report 2026-10-07: 22 files refused "no converter" in three sources, none of them an image
    OCR reads, and nothing to say what they were. The class kept the reason and dropped the suffix it
    carries. The suffix is now counted, per source, when it is one of a fixed list of file types. A row
    stores its name's text from the last dot on, which for a name with a dot and no extension is a piece
    of the name: that is ``other``, counted and never printed."""
    seed = Seed(fake_mac["config"])
    one, _inbox, two = seed.ids()
    refused = {"state": "refused", "page": "refused"}
    seed.item(one, "Wingtip thread.msg", reason="no converter for .msg", **refused)
    seed.item(one, "Wingtip thread 2.MSG", reason="no converter for .msg", dataless=True, **refused)
    seed.item(one, "merger call.mp4", reason="no converter for .mp4", **refused)
    seed.item(one, "Makefile", reason="no converter for files without an extension", **refused)
    seed.item(one, "notes.wingtip", reason="no converter for .wingtip", **refused)
    seed.item(one, "notes 2.wingtip", reason="no converter for .wingtip", dataless=True, **refused)
    seed.item(one, "minutes.final payroll", reason="no converter for .final payroll", **refused)
    seed.item(one, "Northwind logo.png", reason=NO_CONVERTER, **refused)
    seed.item(two, "ledger-2031.zip", reason="no converter for .zip", **refused)
    seed.item(two, "Tailspin old.msg", state="tombstone", reason="no converter for .msg", page="tombstone")
    label = 'refused: sensitivity label "Wingtip Secret" is excluded by [policy]'
    seed.item(two, "payroll.xlsx", reason=label, **refused)
    seed.close()
    text, parts = status_parts(fake_mac)
    part = parts["Quarantine by reason"]
    assert (
        "- files whose row carries a reason: 10 refused (a reason is shown as its class, never as its text; "
        "a live or dataless row here is a download the OS refused; under the table, a `no converter` file is "
        "counted by its type when that is one of a fixed list, else as `other`)\n" in part
    )
    assert [ln for ln in part.splitlines() if ln.startswith("- <")] == [
        "- <folder-2>, no converter by type: .msg 2 (1 online-only) · .mp4 1 · .png 1 · no extension 1 · "
        "other 3 (1 online-only, 2 distinct: 1 like a type, 1 with a space)",
        "- <folder-3>, no converter by type: .zip 1",
    ], "most first, then by name; a deleted file and a file refused for its label are not counted"
    assert {src: sum(found.values()) for src, found in type_counts(part).items()} == table_counts(part)
    assert table_counts(part) == {"<folder-2>": 8, "<folder-3>": 1}
    assert ".final" not in text and "Secret" not in text, "SECRET_NAMES holds the other tail"
    assert residue_free(text), "no word beside the source's placeholder reads as a name the Redactor missed"


def test_a_msg_or_image_under_a_label_rule_is_withheld_not_missing_a_converter(
    fake_mac: dict[str, Path],
) -> None:
    """Under a label rule a binary .msg or an image gets no converter, because their label cannot be read (a
    .msg that holds MIME text is read as an .eml, B13 round 4 Q78): the report counted them as missing a
    converter. While the rule is on they are `withheld by label rule`, with a line naming the .eml and PDF
    routes, and are not in the by-type list; any other file with no converter keeps its class (B13 round 2
    R33)."""
    seed = Seed(fake_mac["config"])
    one = seed.ids()[0]
    refused = {"state": "refused", "page": "refused"}
    seed.item(one, "Wingtip thread.msg", reason="no converter for .msg", **refused)
    seed.item(one, "Northwind logo.png", reason="no converter for .png", **refused)
    seed.item(one, "merger call.mp4", reason="no converter for .mp4", **refused)
    seed.close()
    part = status_parts(fake_mac)[1]["Quarantine by reason"]
    assert "withheld by label rule" not in part and ".msg 1" in part, "no rule: a missing converter"
    cfg = fake_mac["config"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + '\n[policy]\nexclude_label_ids = ["00000000-0000-4000-8000-00000000c0de"]\n',
        encoding="utf-8",
    )
    part = status_parts(fake_mac)[1]["Quarantine by reason"]
    # This Mac has no OCR engine: no image converter exists without one, so the image is no converter's.
    assert table_counts(part) == {"<folder-2>": 2}, part
    assert any(ln.split(" | ")[2:4] == ["withheld by label rule", "1"] for ln in part.splitlines()), part
    assert (
        "- `withheld by label rule`: a [policy] label rule is set and the label of a binary .msg or an image"
        " cannot be read, or a .msg is not on this Mac and is not downloaded to tell, so no converter"
        in " ".join(part.split())
    ), "B13 round 6 G62: a .msg not on this Mac is withheld by its name (B13 round 5 F73)"
    assert [ln for ln in part.splitlines() if ln.startswith("- <")] == [
        "- <folder-2>, no converter by type: .mp4 1 · .png 1"
    ]
    assert registry_mod.label_withheld_suffixes() == setup_report._LABEL_WITHHELD
    # The report withholds the part that is not an image for every row (B13 round 9 K61: a registry suffix
    # _Mirror ignored passed the line above); an image row goes by its stub page, or by the OCR probe when
    # the page could not be read (B13 round 8 J52).
    mirror = setup_report._Mirror(Path("manifest.db"), 1.0, labels_active=True)
    assert mirror.withheld == set(registry_mod.label_withheld_suffixes()).difference(
        setup_report._IMAGE_SUFFIXES
    )
    assert setup_report._Mirror(Path("manifest.db"), 1.0).withheld == set()
    with pytest.MonkeyPatch.context() as later:  # a suffix the registry and the constant gain is withheld
        later.setattr(setup_report, "_LABEL_WITHHELD", setup_report._LABEL_WITHHELD | {".one"})
        assert setup_report._Mirror(Path("manifest.db"), 1.0, labels_active=True).withheld == {".msg", ".one"}
    withheld = setup_report._LABEL_WITHHELD
    assert (
        setup_report.quarantine_class("no converter for .png", withheld=withheld) == "withheld by label rule"
    )
    assert setup_report._refused_type("no converter for .png", withheld=withheld) is None
    assert setup_report.quarantine_class("no converter for .png") == "no converter"


def residue_free(text: str) -> bool:
    """Whether the Status section adds no hit to the report's own residue check."""
    return not any(title == "Status" for title, _words in setup_report.residue_by_section(text))


def test_a_source_with_many_refused_types_shows_the_most_and_counts_the_rest(
    fake_mac: dict[str, Path],
) -> None:
    """Bounded: at most 12 listed types a source. The rest are counted as files and types, apart from
    ``other``, so "other N (D distinct)" always means suffixes that are not in the list."""
    seed = Seed(fake_mac["config"])
    one = seed.ids()[0]
    types = sorted(setup_report._REFUSED_TYPES)[:15]
    for n, suffix in enumerate(types):
        for copy in range(1 + (n < 3)):  # the first three types twice: they sort first
            seed.item(
                one, f"ledger-2031 {n} {copy}{suffix}", state="refused", reason=f"no converter for {suffix}"
            )
    seed.item(one, "minutes.Tailspin", state="refused", reason="no converter for .tailspin")
    seed.item(one, "LICENSE", state="refused", reason="no converter for files without an extension")
    seed.close()
    part = status_parts(fake_mac)[1]["Quarantine by reason"]
    [line] = [ln for ln in part.splitlines() if ln.startswith("- <folder-2>, no converter by type: ")]
    shown = " · ".join(f"{suffix} {2 if n < 3 else 1}" for n, suffix in enumerate(types[:12]))
    assert line == (
        f"- <folder-2>, no converter by type: {shown} · no extension 1 · other 1 (1 distinct: 1 like a type) "
        "(+3 files of 3 more types)"
    )
    counts = type_counts(part)["<folder-2>"]
    assert counts["more"] == 3 and sum(counts.values()) == table_counts(part)["<folder-2>"] == 20


@pytest.mark.parametrize(
    ("reason", "shape"),
    [
        ("no converter for .wingtip", "like a type"),
        ("no converter for .WINGTIP2", "like a type"),
        ("no converter for .doe", "like a type"),
        ("no converter for .2", "digits only"),
        ("no converter for .2031", "digits only"),
        ("no converter for .final draft", "with a space"),
        ("no converter for .msg and more", "with a space"),
        ("no converter for .docx~", "with a symbol"),
        ("no converter for .zürich", "with a symbol"),
        ("no converter for .tar_old", "with a symbol"),
        ("no converter for .strauß", "with a symbol"),  # casefold reads it as ss, an ASCII "like a type"
        ("no converter for .\ufb01le", "with a symbol"),  # the fi ligature, which casefold reads as fi
        ("no converter for .\uff12\uff10\uff13\uff11", "with a symbol"),  # full-width digits are isdigit
        ("no converter for ." + "x" * 11, "too long for a type"),
        ("no converter for ." + "x" * 200, "too long for a type"),
        ("no converter for .", "nothing after the dot"),
        ("no converter for .msg", None),
        ("no converter for files without an extension", None),
        ("contains a credential", None),
        (None, None),
    ],
)
def test_an_unlisted_suffix_is_counted_by_its_shape(reason: str | None, shape: str | None) -> None:
    """Field report 2026-10-08 (D9.f): "other 2 (1 distinct)" could be a type the list lacks or a dotted
    name, and three rounds did not say which. A fixed shape word tells them apart without printing either."""
    assert setup_report._refused_shape(reason) == shape
    assert (shape is not None) is (setup_report._refused_type(reason) == "other"), "every `other` has a shape"
    assert shape is None or shape in setup_report._OTHER_SHAPES


def test_like_a_type_is_as_long_as_the_longest_listed_type() -> None:
    longest = max(len(t) - 1 for t in setup_report._REFUSED_TYPES if t.count(".") == 1)
    assert longest == setup_report._SHAPE_LONG, "no listed type would read `too long for a type`"


@pytest.mark.usefixtures("roomy_calls")
def test_a_source_with_unlisted_suffixes_counts_them_by_shape(fake_mac: dict[str, Path]) -> None:
    """The shapes count distinct suffixes and add up to D; the files add up to N; no tail is printed."""
    seed = Seed(fake_mac["config"])
    one = seed.ids()[0]
    refused = {"state": "refused", "page": "refused"}
    seed.item(one, "notes.wingtip", reason="no converter for .wingtip", **refused)
    seed.item(one, "notes 2.WINGTIP", reason="no converter for .WINGTIP", dataless=True, **refused)
    seed.item(one, "minutes.final payroll", reason="no converter for .final payroll", **refused)
    seed.item(one, "budget v1.2", reason="no converter for .2", **refused)
    seed.item(one, "budget v1.3", reason="no converter for .3", **refused)
    seed.item(one, "ledger.xlsx~", reason="no converter for .xlsx~", **refused)
    seed.close()
    text, parts = status_parts(fake_mac)
    part = parts["Quarantine by reason"]
    [line] = [ln for ln in part.splitlines() if ln.startswith("- <folder-2>, no converter by type: ")]
    assert line == (
        "- <folder-2>, no converter by type: other 6 (1 online-only, 5 distinct: 1 like a type, 2 digits "
        "only, 1 with a space, 1 with a symbol)"
    )
    assert sum(type_counts(part)["<folder-2>"].values()) == table_counts(part)["<folder-2>"] == 6
    assert ".final" not in text and "wingtip" not in text.lower() and "xlsx~" not in text
    assert residue_free(text)


def test_the_guides_print_the_shapes_in_the_order_the_report_does() -> None:
    """Review of B13 N35: setup-feedback.md is the reader's guide to the line, so its examples list the
    shapes in :data:`setup_report._OTHER_SHAPES` order, the only order the report prints them in."""
    root = Path(__file__).parents[1]
    spans = [
        (page.name, span)
        for page in (
            root / "docs" / "deploy" / "setup-feedback.md",
            root / "docs" / "design" / "CONTRACTS.md",
        )
        for span in re.findall(r"distinct: ([^)`]*)\)", " ".join(page.read_text(encoding="utf-8").split()))
    ]
    assert len(spans) >= 3, spans
    for name, span in spans:
        words = [re.sub(r"^\S+ ", "", piece) for piece in span.split(", ") if piece != "..."]
        assert set(words) <= set(setup_report._OTHER_SHAPES), (name, span)
        assert words == sorted(words, key=setup_report._OTHER_SHAPES.index), (name, span)


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        ("no converter for .msg", ".msg"),
        ("  No  Converter For   .MSG ", ".msg"),
        ("no converter for .teams.json", ".teams.json"),
        ("no converter for files without an extension", "no extension"),
        ("no converter for .wingtip", "other"),
        ("no converter for .final draft", "other"),
        ("no converter for .msg and more", "other"),
        ("no converter for ." + "x" * 200, "other"),
        ("no converter for .zürich", "other"),
        ("no converter for ", None),
        ("contains a credential", None),
        ("duplicate-of mail (no converter for .msg)", None),
        (None, None),
    ],
)
def test_a_no_converter_reason_is_a_listed_type_no_extension_or_other(
    reason: str | None, kind: str | None
) -> None:
    assert setup_report._refused_type(reason) == kind
    assert (setup_report.quarantine_class(reason) == "no converter") is (kind is not None), (
        "the two read a reason the same way, so the type line counts the table's rows"
    )


REASONS = {
    "no converter for .xyz": "no converter",
    "no converter for files without an extension": "no converter",
    pdf._NO_TEXT: "no text layer (OCR not run)",
    pdf._NO_TEXT_FOUND: "no text layer (OCR found no text)",
    pdf._NO_TEXT_PAST_LIMIT.format(40): "no text layer (over the OCR page limit)",
    image._NO_TEXT: "no text in image",
    image._TOO_SMALL: "no text in image",
    image._NOT_RASTER: "image not readable",
    f"{image._NOT_READABLE} (not an image)": "image not readable",
    f"{image._NOT_READABLE} (too large)": "too large",
    "encrypted": "encrypted",
    "password-protected": "encrypted",
    "IRM-protected": "encrypted",
    "encrypted or legacy binary Office file (OLE container, not OOXML)": "encrypted",
    policy.ENCRYPTED_OFFICE_REASON: "encrypted",
    policy.ENCRYPTED_PDF_REASON: "encrypted",
    f"{pdf._ENCRYPTED_PDF} (password-protected)": "encrypted",
    cycle._CREDENTIAL: "credential",
    f'{policy.REFUSED_PREFIX}sensitivity label "Encrypted, too large" is excluded': "label policy",
    f"{policy.REFUSED_PREFIX}no sensitivity label; refuse_unlabelled = true": "label policy",
    "duplicate-of contoso-mail (mirror/contoso-mail/too large to mail.eml.md)": "duplicate",
    "conversion failed: the encrypted stream is too large": "conversion failed",
    cycle._SIDECAR_PATH: "path too long",
    policy.EMPTY_OUTPUT_REASON: "empty",
    "empty PDF: no pages": "empty",
    policy.NOT_OOXML_REASON: "not the type its name says",
    msg.NOT_MSG_REASON: "not the type its name says",
    f"{msg.NOT_MSG_REASON}: OleFileError": "not the type its name says",
    msg.DAMAGED_MSG_REASON: "damaged",
    cycle.HYDRATION_REFUSED: "download refused by the OS",
    None: "no reason recorded",
    "  ": "no reason recorded",
    "Contoso Roadmap said no": "other",
}


@pytest.mark.parametrize("reason", list(REASONS), ids=[str(n) for n in range(len(REASONS))])
def test_a_reason_is_reported_as_one_of_the_fixed_classes(reason: str | None) -> None:
    """A reason is free text in places, so only its class is printed. A prefix decides before a word inside
    the text does: a duplicate's mirror path or a label's name that says "too large" is still what it is."""
    assert setup_report.quarantine_class(reason) == REASONS[reason]
    assert REASONS[reason] in setup_report.QUARANTINE_CLASSES
    # "withheld by label rule" is given only while a label rule is on (B13 round 2 R33): its own test.
    rule_only = {"withheld by label rule"}
    assert set(REASONS.values()) | rule_only == set(setup_report.QUARANTINE_CLASSES), "every class has a case"


def test_every_unreadable_reason_is_a_class_the_repeat_conversions_note_names() -> None:
    """B13 round 4 Q3, Q66: the note on unreadable files names the classes their stubs get. Every reason a
    converter raises ``UnreadableSourceError`` with is one of :data:`setup_report._UNREADABLE_CLASSES`, and
    never "conversion failed", the class the note says it is not."""
    reasons = [
        policy.ENCRYPTED_OFFICE_REASON,
        policy.ENCRYPTED_PDF_REASON,
        policy.EMPTY_OUTPUT_REASON,
        policy.NOT_OOXML_REASON,
        msg.NOT_MSG_REASON,
        msg.DAMAGED_MSG_REASON,
        "encrypted",
        "password-protected",
        "IRM-protected",
        "encrypted or legacy binary Office file (OLE container, not OOXML)",
        f"{pdf._ENCRYPTED_PDF} (password-protected)",
        f"{pdf._ENCRYPTED_PDF} (/Encrypt in the trailer)",
        "empty PDF: no pages",
        pdf._NO_TEXT,
        pdf._NO_TEXT_FOUND,
        pdf._NO_TEXT_PAST_LIMIT.format(pdf.MAX_PAGES),
        image._NO_TEXT,
        image._TOO_SMALL,
        image._NOT_RASTER,
        f"{image._NOT_READABLE} (not an image)",
        f"{image._NOT_READABLE} (too large)",
        vtt._NOT_VTT,
        vtt._NO_CUE,
        recording._NOT_RECORDING,
        recording._NOTHING,
        recording._UNDECODABLE,
    ]
    classes = {setup_report.quarantine_class(r) for r in reasons}
    assert classes <= set(setup_report._UNREADABLE_CLASSES), classes - set(setup_report._UNREADABLE_CLASSES)
    assert {"damaged", "image not readable", "too large", "other"} <= classes
    assert "conversion failed" not in setup_report._UNREADABLE_CLASSES
    assert set(setup_report._UNREADABLE_CLASSES) <= set(setup_report.QUARANTINE_CLASSES)


def test_the_earlier_build_brackets_are_said_only_where_an_earlier_builds_run_is(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 9 K6: on a Mac whose runs are all this build's, the OCR time line said "(a run of an earlier
    build did not)" beside "4 recorded these counts" of 4, and the converter cache line "(a count that also
    holds for runs of an earlier build)" though no such run exists."""
    seed = Seed(fake_mac["config"])
    for run in (1, 2, 3, 4):
        seed.run(run, {"converted": 1, "ocr_ms": 1000, "ocr_budget_s": 180})
    seed.close()
    parts = status_parts(fake_mac)[1]
    assert "- OCR time: of the last 4 runs, 4 recorded these counts and 4 had an engine;" in parts["OCR"], (
        parts["OCR"]
    )
    assert "earlier build" not in parts["Repeat conversions"], parts["Repeat conversions"]
    assert "by the run before it\n" in parts["Repeat conversions"] + "\n"
    # the run before the newest is an earlier build's: the cache line's bracket comes back
    seed = Seed(fake_mac["config"])
    seed.m._db.execute("UPDATE runs SET counts_json = '{}' WHERE run_id = 3")
    seed.close()
    parts = status_parts(fake_mac)[1]
    assert (
        "by the run before it (a count that also holds for runs of an earlier build)"
        in parts["Repeat conversions"]
    )
    assert "3 recorded these counts (a run of an earlier build did not) and 3 had an engine" in parts["OCR"]


def test_a_running_or_crash_closed_run_is_no_earlier_builds(fake_mac: dict[str, Path]) -> None:
    """B13 round 10 L42: a run still running has the column's ``{}`` and crash recovery closes a run with
    ``{}``, so a report taken during a cycle said "(a run of an earlier build did not)" and "(a count that
    also holds for runs of an earlier build)" on a Mac that only ran this build.  Each now says what the run
    is; a finished run without the counts is still an earlier build's."""
    seed = Seed(fake_mac["config"])
    seed.run(10, {})
    seed.run(11, {"converted": 1, "ocr_ms": 1000, "ocr_budget_s": 180})
    seed.run(12, {})
    seed.m._db.execute("UPDATE runs SET status = 'aborted', finished_at = NULL WHERE run_id = 10")
    seed.m._db.execute("UPDATE runs SET status = 'running', finished_at = NULL WHERE run_id = 12")
    seed.close()
    parts = status_parts(fake_mac)[1]
    assert "earlier build" not in parts["OCR"] + parts["Repeat conversions"]
    assert (
        "of the last 3 runs, 1 recorded these counts (a run still running has none yet; a run crash "
        "recovery closed has none) and 1 had an engine" in parts["OCR"]
    ), parts["OCR"]
    assert (
        "by the run before it; the newest run is still running, so its count can still grow"
        in parts["Repeat conversions"]
    ), parts["Repeat conversions"]
    seed = Seed(fake_mac["config"])
    seed.m._db.execute("UPDATE runs SET status = 'ok' WHERE run_id IN (10, 12)")
    seed.close()
    parts = status_parts(fake_mac)[1]
    assert "1 recorded these counts (a run of an earlier build did not) and 1 had an engine" in parts["OCR"]
    assert (
        "by the run before it (a count that also holds for runs of an earlier build)\n"
        in parts["Repeat conversions"] + "\n"
    )


@pytest.mark.usefixtures("clean_doctor")
def test_an_install_out_of_one_line_is_the_last_line_never_the_last_1_line(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 16 T49: the Installer section's embed read "the last 1 line of", the wording
    ``_the_last`` exists to avoid (S31); install.sh's stopped-copy reader takes both forms."""
    write_install_log(fake_mac)
    (fake_mac["setup"] / "install.out").write_text("one line of output\n", encoding="utf-8")
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    inst = section(text, "Installer")
    assert "<details><summary>the last line of ~/agent-context/setup/install.out (" in inst, inst
    assert "the last 1 line" not in text


def test_repeat_conversions_names_the_upgrades_retry_of_an_older_builds_failures(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 19 W18: the sync that gives an older build's settled failures the bounded retries once (V65)
    may be a LaunchAgent's, which nobody reads, and an old conversion error then shows again. The run record
    counts them (``legacy_retried``) and the Repeat conversions part names that run; a run without them adds
    nothing."""
    seed_ocr(fake_mac)
    seed = Seed(fake_mac["config"])
    four = {"converted": 1, "converted_failed": 1, "legacy_retried": 3}
    seed.m._db.execute("UPDATE runs SET counts_json = ? WHERE run_id = 4", (json.dumps(four),))
    seed.close()
    part = status_parts(fake_mac)[1]["Repeat conversions"]
    assert (
        "- tried again after an upgrade: run 4, 3 files. An older build had given up on these conversions; "
        f"this build tries each again from the same bytes in up to {cycle._CONVERT_RETRIES} runs from that "
        "one on, so an old conversion error can show in those runs" in " ".join(part.split())
    ), part
    assert part.count("tried again after an upgrade") == 1


@pytest.mark.parametrize(("last_used", "said"), [(5, "the newest run (5)"), (4, "the run before it")])
def test_one_repeat_conversion_is_it_never_them(fake_mac: dict[str, Path], last_used: int, said: str) -> None:
    """B13 round 16 T49, T63: the converter-cache line agreed its verb with a count of 1, then said "made
    them" and "1 of them last by the newest run". One conversion is "it", and the line says which run used
    it last."""
    seed_ocr(fake_mac)
    seed = Seed(fake_mac["config"])
    seed.n = next(SEED_NUMBERS)
    seed.m._db.execute(
        "INSERT INTO cache (action_key, converter_id, converter_version, options_hash, canonical_sha256, "
        "status, unit_count, bytes, created_run, last_used_run) "
        "VALUES (?, 'c', '1', 'o', 'c', 'ok', 1, 1, 1, ?)",
        (f"loop-{seed.n}", last_used),
    )
    seed.close()
    part = status_parts(fake_mac)[1]["Repeat conversions"]
    line = next(ln for ln in part.splitlines() if ln.startswith("- converter cache: "))
    assert line.startswith(
        "- converter cache: 1 conversion was used again by a later run than the one that made it (the same "
        f"bytes converted again, by the same file or by a copy of it); it was used last by {said}"
    ), line
    assert "made them" not in line and "1 of them" not in line, line


def test_repeat_conversions_part_reads_the_run_records_and_the_cache(fake_mac: dict[str, Path]) -> None:
    seed_ocr(fake_mac)
    seed = Seed(fake_mac["config"])
    # run 4 read two files whose stub says unreadable: their own column, not "of them failed" (§16.45 R2)
    four = {"converted": 6, "converted_seen": 6, "converted_again": 6, "converted_unreadable": 2}
    seed.m._db.execute("UPDATE runs SET counts_json = ? WHERE run_id = 4", (json.dumps(four),))
    for created, last_used in ((1, 5), (2, 5), (1, 4), (3, 3), (1, 2)):
        seed.n = next(SEED_NUMBERS)
        seed.m._db.execute(
            "INSERT INTO cache (action_key, converter_id, converter_version, options_hash, canonical_sha256, "
            "status, unit_count, bytes, created_run, last_used_run) "
            "VALUES (?, 'c', '1', 'o', 'c', 'ok', 1, 1, ?, ?)",
            (f"loop-{seed.n}", created, last_used),
        )
    seed.close()
    _text, parts = status_parts(fake_mac)
    part = parts["Repeat conversions"]
    # a paragraph of its own starts in capitals, as the OCR part's run table title (B13 round 11 M19)
    assert "\n\nThe last runs, newest first:\n\n" in part and "the last runs" not in part
    assert (
        "- of the last 3 runs, 2 recorded what they converted; 2 of those converted at least one file "
        "again from the same bytes and converter options its own page was made from, which the run just "
        "before had converted too (the sign of a loop; a copy of a file is a new file and is not counted, "
        "nor is a file re-screened for a label-rule change)\n" in part
    )
    # a re-screen under a label-rule change gets a new action key, so the bullet no longer covers it in words
    # it does not count it by (B13 round 10 L71)
    assert "again from the bytes its page was made from" not in part
    assert (
        "| files converted | of them failed | of them unreadable (stub, kept, not retried) | the same file "
        "from the same bytes and converter options as an earlier run | of those, as the run just before |\n"
        in part
    ), "B13 round 8 J30: a label-rule re-screen changes the options, so it is no 'same bytes' file"
    assert "re-screened for a rule change" not in part, "no run re-screened: no note"
    assert [ln for ln in part.splitlines() if re.match(r"\| \d", ln)] == [
        "| 5 | reconcile | ok | 2026-10-06T10:00:00Z | 6 | 1 | 0 | 6 | 6 |",
        "| 4 | poll | ok | 2026-10-06T10:00:00Z | 6 | 0 | 2 | 6 | 6 |",
        "| 3 | poll | ok | 2026-09-30T10:00:00Z | not recorded | - | - | - | - |",
    ]
    unreadable = "- an unreadable file is not a failed conversion: its stub is kept and not tried again until"
    assert unreadable in part and 'class its stub names (such as "encrypted", "damaged", ' in part
    assert (
        '"image not readable", "too large", a "no text" class or "other"), never "conversion failed"' in part
    )
    assert (
        "- converter cache: 4 conversions were used again by a later run than the one that made them (the "
        "same bytes converted again, by the same file or by a copy of it); 2 of them last by the newest run "
        "(5), 1 by the run before it" in part
    )
    # Run 5 has a failed conversion: the part says what a later run without one does not mean.
    # B13 round 17 U3: a failed conversion is tried again, as many times as the cycle does
    note = "- a conversion that failed is counted in each run it failed in and is tried again from the same"
    assert note in part and 'Quarantine by reason, class "conversion failed"\n' in part
    assert f" bytes in up to {cycle._CONVERT_RETRIES} later runs, or, when its converter could not " in part
    # B13 round 18 V75: while its converter still cannot start, no run reads or counts the file
    assert (
        "converted it, gave it up, or (its converter still not starting) did not read it at all, which "
        in (part)
    )
    guide = " ".join(
        (Path(__file__).parents[1] / "docs" / "deploy" / "setup-feedback.md").read_text().split()
    )
    assert "converted it, gave it up, or, while its converter still cannot start, left it unread and" in guide
    assert "converted it or gave it up" not in part + guide
    seed = Seed(fake_mac["config"])
    seed.m._db.execute(
        "UPDATE runs SET counts_json = ? WHERE run_id IN (4, 5)", (json.dumps({"converted": 2}),)
    )
    seed.close()
    plain = status_parts(fake_mac)[1]["Repeat conversions"]
    assert note not in plain and unreadable not in plain, "no run shown has a failure or an unreadable file"
    # a run that re-screened files for a label-rule change: its 'same bytes' cell reads 0, and a note says why
    seed = Seed(fake_mac["config"])
    five = {"converted": 14, "rescreened": 11, "rescreened_unchanged": 10}
    seed.m._db.execute("UPDATE runs SET counts_json = ? WHERE run_id = 5", (json.dumps(five),))
    seed.close()
    part = status_parts(fake_mac)[1]["Repeat conversions"]
    assert "| 5 | reconcile | ok | 2026-10-06T10:00:00Z | 14 | 0 | 0 | 0 | 0 |" in part, part
    assert (
        "- re-screened for a rule change: run 5, 11 files (10 came out the same). A label rule is one of "
        "the converter's options, so a file re-screened for a label-rule change is a new conversion and is "
        "not in the 'same bytes' column; one re-screened for a secret rule is\n" in part + "\n"
    ), part


def test_purge_queue_part_counts_by_source_reason_day_and_what_took_the_files_place(
    fake_mac: dict[str, Path],
) -> None:
    """The first bring-back file could not tell fourteen queued purges from renamed exports. For a queued
    stable id the manifest can: a live file with the same bytes, or one at the same path under a new id."""
    seed = Seed(fake_mac["config"])
    one, inbox, _two = seed.ids()
    root = seed.config.state_paths.root
    seed.item(inbox, "Wingtip terms v2.eml", canonical="c" * 64)  # the re-export of the first purge's file
    seed.item(inbox, "Northwind memo.eml", rel="Tailspin/Northwind memo.eml")  # a new id at an old path
    gone = [
        seed.item(inbox, "Wingtip terms.eml", state="tombstone", canonical="c" * 64),
        seed.item(inbox, "Northwind memo.eml", state="tombstone", rel="Tailspin/Northwind memo.eml"),
        seed.item(inbox, "payroll.eml", state="tombstone", canonical="d" * 64),
        seed.item(inbox, "merger.eml"),  # queued, and listed again since
        # Queued, and back since as a quarantined file: a run of the queue still erases its earlier text.
        seed.item(inbox, "Contoso offsite.eml", state="quarantined", reason="contains a secret"),
        "id-that-was-never-a-row",
        # Queued, then back and saved again: the row moved to a new id and the queued one is its alias.
        seed.item(inbox, "Fourth Coffee notes.eml"),
        # The same, and then deleted again: the alias points at a tombstone, which is a real deletion.
        seed.item(inbox, "ledger-2031.eml", state="tombstone", canonical="e" * 64, page="tombstone"),
    ]
    seed.m.rekey(inbox, gone[-2], "id-after-the-save")
    seed.m.rekey(inbox, gone[-1], "id-after-the-second-delete")
    seed.close()
    day = datetime(2026, 10, 2, 9, 30, tzinfo=UTC)
    upstream = governance.PurgeReason.UPSTREAM_DELETED
    for stable in gone:
        governance.enqueue_purge(
            root, governance.PurgeSelector(source_id=inbox, stable_id=stable), upstream, now=day
        )
    later = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
    governance.enqueue_purge(
        root,
        governance.PurgeSelector(source_id=one, path_glob="Wingtip merger/**"),
        governance.PurgeReason.ERASURE_REQUEST,
        now=later,
    )
    governance.enqueue_purge(
        root,
        governance.PurgeSelector(source_id="tailspin-bridge", stable_id="ledger-2031"),
        governance.PurgeReason.LABEL_ESCALATION,
        now=later,
    )
    # A purge that ran: one line per item and one summary. The one from before the oldest queued day is
    # not counted, and neither is a line that is not JSON.
    for at, items in (("2026-10-01T08:00:00Z", 3), ("2026-10-03T08:00:00Z", 2)):
        for _n in range(items):
            governance.append_audit(root, {"action": "purge", "at": at, "source_id": "tailspin-bridge"})
        governance.append_audit(root, {"action": "purge-summary", "at": at, "verified": True})
    with governance.audit_path(root).open("a", encoding="utf-8") as fh:
        fh.write("cut sho\n")
    text, parts = status_parts(fake_mac)
    part = parts["Purge queue"]
    assert (
        "- 10 purges queued. For a queued stable id the last six columns say what the manifest holds" in part
    )
    header = "| source | reason | selector | queued (UTC day) | purges | same bytes live | same path live | "
    assert header + "still listed | no live twin | no row | not looked up |" in part

    def rows(part: str) -> list[str]:
        return [ln for ln in part.splitlines() if ln.startswith("| ") and "---" not in ln][1:]

    assert rows(part) == [
        "| (<source-1>, not in the config) | label-escalation | stable-id | 2026-10-05 | 1 | 0 | 0 | 0 | 0 "
        "| 1 | 0 |",
        "| <folder-2> | erasure-request | path-glob | 2026-10-05 | 1 | 0 | 0 | 0 | 0 | 0 | 1 |",
        # an id that is now an alias is judged by the row it points at: listed again, and deleted again
        "| inbox | upstream-deleted | stable-id | 2026-10-02 | 8 | 1 | 1 | 3 | 2 | 1 | 0 |",
    ]
    assert [ln for ln in part.splitlines() if ln.startswith("- ")][1:] == [
        "- renamed or re-keyed: 2 queued ids are an earlier id of a file the manifest now holds under a "
        "later one (it was saved again, or its volume's id changed). Each is counted by that file's row, "
        "since a purge follows the alias to it",
        "- still listed: 3 queued purges name a file the manifest lists now (1 of them under a later id). "
        "A run of the queue keeps 2 of them queued (an upstream deletion or a label escalation of a file "
        "that is live or online-only) and erases the page and history of the other 1",
        "- no trace: 2 queued ids have no row and are no alias. A re-key leaves an alias, so what took "
        "such a row away is a purge that already ran, or an erasure. A run of the queue erases only what "
        "history still names for them and takes them off the queue",
        "- audit trail since 2026-10-02 (the oldest queued day): 10 purge-enqueued, 2 purge (one per item "
        "erased) and 1 purge-summary (one per purge that ran) lines",
    ], "what the columns mean for a run of the queue, in counts and fixed words"
    for raw in ("tailspin-bridge", "id-that-was", "id-after", "terms", "memo", "c" * 16, "merger/"):
        assert raw not in text, raw
    # A manifest from before the alias table: read as it is (the report never migrates one), so an id with
    # no row is `no row`. No alias was looked up, and a re-key by that build left none, so the two re-keyed
    # ids read the same as the purged ones. The line says so, and no line says what it cannot know: not
    # that they are no alias, and not that a purge or an erasure took the rows.
    seed = Seed(fake_mac["config"])
    seed.m._db.execute("DROP TABLE item_aliases")
    seed.close()
    text, parts = status_parts(fake_mac)
    part = parts["Purge queue"]
    assert (
        rows(part)[2] == "| inbox | upstream-deleted | stable-id | 2026-10-02 | 8 | 1 | 1 | 2 | 1 | 3 | 0 |"
    )
    assert [ln for ln in part.splitlines() if ln.startswith("- ")][1:] == [
        "- still listed: 2 queued purges name a file the manifest lists now. A run of the queue keeps 1 of "
        "them queued (an upstream deletion or a label escalation of a file that is live or online-only) and "
        "erases the page and history of the other 1",
        "- no row: 4 queued ids have no row. This manifest has no alias table, so none was looked up as an "
        "alias: an earlier id of a file listed now cannot be told from one a purge took away",
        "- audit trail since 2026-10-02 (the oldest queued day): 10 purge-enqueued, 2 purge (one per item "
        "erased) and 1 purge-summary (one per purge that ran) lines",
    ]
    governance.audit_path(root).unlink()
    assert "\n- audit trail: no file" in status_parts(fake_mac)[1]["Purge queue"]
    for claim in ("no trace", "are no alias", "A re-key leaves an alias", "a purge that already ran"):
        assert claim not in part, claim
    for raw in ("tailspin-bridge", "id-that-was", "id-after", "terms", "memo", "c" * 16, "merger/"):
        assert raw not in text, raw


def test_overlapping_sources_part_names_the_pair_and_each_ones_counts(fake_mac: dict[str, Path]) -> None:
    """One source's folder inside another's: the pair as placeholders, how deep, whether the outer source's
    exclude list prunes the inner folder, and each one's files by state."""
    inner = fake_mac["one"] / "Wingtip merger" / "Northwind"
    inner.mkdir(parents=True)
    cfg = fake_mac["config"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            'id = "client-alpha"', 'id = "client-alpha"\nexclude = ["Wingtip merger/"]', 1
        )
        + f'\n[[source]]\nid = "tailspin-deal"\nkind = "local"\npath = "{inner}"\n',
        encoding="utf-8",
    )
    seed = Seed(cfg)
    seed.source("client-alpha", complete=False)
    for n in range(3):
        seed.item("tailspin-deal", f"payroll {n}.docx")
    seed.item("tailspin-deal", "payroll.heic", state="dataless", dataless=True)
    seed.item("tailspin-deal", "payroll.zip", state="refused", reason="no converter for .zip", page="refused")
    seed.item("client-alpha", "ledger-2031.docx", state="tombstone")
    seed.close()
    text, parts = status_parts(fake_mac)
    part = parts["Overlapping sources"].strip()
    outer, inner_label = re.findall(r"<(?:folder|source)-\d+>", part)[:2]
    assert part == (
        f"- {outer} (local, live) contains, 2 folder levels down, {inner_label} (local, live); the outer "
        f"source's exclude list prunes the inner folder: yes; {outer}: live 0 (online-only 0), stubs 0, "
        f"tombstones 1, listing complete: no; {inner_label}: live 4 (online-only 1), stubs 1, tombstones 0, "
        "listing complete: no"
    )
    assert "tailspin-deal" not in text and "client-alpha" not in text
    # Without the exclude line the outer walk reaches the inner folder too.
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('exclude = ["Wingtip merger/"]\n', ""), "utf-8")
    assert "exclude list prunes the inner folder: no;" in status_parts(fake_mac)[1]["Overlapping sources"]


def test_overlapping_sources_part_finds_a_folder_stored_through_a_link(fake_mac: dict[str, Path]) -> None:
    """B13 round 6 G39: two sources on one folder, one stored through a provider entry in CloudStorage that
    links to the other's provider folder, were compared as text and the part said none. The entry is read
    through (one readlink, nothing inside the domain), and the stored paths are kept."""
    provider = fake_mac["one"].parents[1]
    (provider.parent / "OneDrive-Link").symlink_to(provider, target_is_directory=True)
    link = provider.parent / "OneDrive-Link" / fake_mac["one"].relative_to(provider)
    cfg = fake_mac["config"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + f'\n[[source]]\nid = "tailspin-deal"\nkind = "local"\npath = "{link}"\n',
        encoding="utf-8",
    )
    part = status_parts(fake_mac)[1]["Overlapping sources"].strip()
    assert " has the same folder as " in part and not part.startswith("- none"), part
    assert "tailspin-deal" not in part


def test_empty_cloud_folders_part_says_its_zeros_in_words(fake_mac: dict[str, Path]) -> None:
    """B13 round 4 Q17: one empty folder that is still there and holds nothing. The line keeps its head (the
    dataless count is the measured answer to the open question) and "1 of 1 checked", drops the kinds with
    no folder, and says "none excluded" and "no file below them" rather than printing zeros."""
    (fake_mac["one"] / "Wingtip merger").mkdir()
    seed = Seed(fake_mac["config"])
    seed.m.set_meta("empty_cloud_dirs:" + seed.ids()[0], json.dumps(["Wingtip merger"]))
    seed.close()
    part = status_parts(fake_mac)[1]["Empty cloud folders"]
    line = next(ln for ln in part.splitlines() if ln.startswith("- <"))
    # B13 round 13 O38: each pronoun by the count it refers to, never "them" for one folder
    assert line.split(": ", 1)[1] == (
        "1 unknown: 0 dataless, 1 materialised-and-empty (still empty on this Mac, by its link count of "
        "2); 1 of 1 checked; it is not excluded in sources.toml now; the mirror holds no file below it; no "
        "walk of it is recorded"
    ), line
    assert "0 gone" not in line and "0 of the checked" not in line and "0 files" not in line
    assert not re.search(r"\b(of them|below them|their)\b", line), line
    # the same folder with an entry since: no longer empty, by its link count
    (fake_mac["one"] / "Wingtip merger" / "arrived.txt").write_text("x", encoding="utf-8")
    line = next(
        ln for ln in status_parts(fake_mac)[1]["Empty cloud folders"].splitlines() if ln.startswith("- <")
    )
    assert "1 materialised-and-empty (no longer empty on this Mac, by its link count); " in line, line
    # B13 round 16 T49: one checked folder that holds a mirrored file and is excluded now: never "1 of the
    # checked folder"
    seed = Seed(fake_mac["config"])
    seed.item(seed.ids()[0], "old.docx", rel="Wingtip merger/old.docx")
    seed.close()
    cfg = fake_mac["config"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            'kind = "local"\n', 'kind = "local"\nexclude = ["Wingtip*"]\n', 1
        ),
        encoding="utf-8",
    )
    line = next(
        ln for ln in status_parts(fake_mac)[1]["Empty cloud folders"].splitlines() if ln.startswith("- <")
    )
    assert (
        "; the checked folder is excluded in sources.toml now; the mirror still holds 1 file below the "
        in (line)
    ), line
    assert "of the checked folder" not in line, line


def test_empty_cloud_folders_part_says_which_are_dataless_without_listing_one(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open question about empty cloud folders: is a folder that was never listed told from an empty
    one by its own dataless flag? One lstat per folder; no folder is listed, so nothing is fetched."""
    root = fake_mac["one"]
    names = ["Wingtip merger", "Northwind/Tailspin", "Fourth Coffee", "payroll", "ledger-2031", "../escape"]
    for rel in names[:3]:
        (root / rel).mkdir(parents=True)
    (root / names[2] / "arrived since.txt").write_text("x", encoding="utf-8")  # no longer empty: link count 3
    (root / names[4]).write_text("a file now", encoding="utf-8")
    seed = Seed(fake_mac["config"])
    one, _inbox, two = seed.ids()
    seed.m.set_meta("empty_cloud_dirs:" + one, json.dumps(names))
    seed.m.set_meta("empty_cloud_dirs:" + two, "")
    for n in range(4):
        seed.item(one, f"old {n}.docx", rel=f"Northwind/Tailspin/old {n}.docx")
    seed.item(one, "gone.docx", rel="Northwind/Tailspin/gone.docx", state="tombstone")
    seed.item(one, "beside.docx", rel="Northwind/Tailspin beside.docx")
    seed.close()
    dataless = (root / names[1]).lstat().st_ino
    monkeypatch.setattr(materialise, "is_dataless", lambda st: st.st_ino == dataless)
    listed: list[str] = []
    real_scandir = os.scandir

    def scandir(path: Any = ".") -> Any:
        listed.append(str(path))
        return real_scandir(path)

    with monkeypatch.context() as watched:
        watched.setattr(os, "scandir", scandir)
        watched.setattr(setup_report, "cloud_folder_names", lambda *a, **k: [])  # the redactor's listing
        _text, parts = status_parts(fake_mac)
    part = parts["Empty cloud folders"]
    assert (
        "Read from each folder's own metadata (one lstat; no folder is listed, so nothing is fetched)."
        in part
    )
    line = next(ln for ln in part.splitlines() if ln.startswith("- <"))
    assert line.split(": ", 1)[1] == (
        "6 unknown: 1 dataless, 2 materialised-and-empty (1 of them still empty on this Mac, by its link "
        "count of 2); 1 gone, 1 not a folder, 1 not checked; 6 of 6 checked; none of them "
        "excluded in sources.toml now; the mirror still holds 4 files below 1 of the checked folders; no "
        "walk of it is recorded"
    )
    assert part.count("\n- ") == 1, "a source whose walk held none has no line"
    # N26: the files the mirror holds that the last walk (the newest FULL pass) did not list; no path.
    seed = Seed(fake_mac["config"])
    seed.run(2, {})
    seed.m._db.execute(
        "INSERT INTO run_sources (run_id, source_id, pass_kind, enumeration_complete, cursor_reset, "
        "counts_json) VALUES (2, ?, 'full_enumeration', 0, 0, '{}')",
        (one,),
    )
    seed.m._db.execute(
        "UPDATE items SET last_seen_run = 2 WHERE source_id = ? AND rel_path = ?",
        (one, "Northwind/Tailspin beside.docx"),
    )
    seed.close()
    walked = status_parts(fake_mac)[1]["Empty cloud folders"]
    assert walked.rstrip().endswith(
        "below 1 of the checked folders; its last walk did not list 4 of the 5 files the mirror holds for it"
    ), walked
    # A walk that listed every mirrored file says so, not "did not list 0" (B13 round 2 R14).
    seed = Seed(fake_mac["config"])
    seed.m._db.execute("UPDATE items SET last_seen_run = 2 WHERE source_id = ?", (one,))
    seed.close()
    walked = status_parts(fake_mac)[1]["Empty cloud folders"]
    assert walked.rstrip().endswith(
        "; its last walk listed every one of the 5 files the mirror holds for it"
    ), walked
    # A newer walk that ended on an error may have stamped nothing: not counted against it.
    seed = Seed(fake_mac["config"])
    seed.run(3, {})
    seed.m._db.execute(
        "INSERT INTO run_sources (run_id, source_id, pass_kind, enumeration_complete, cursor_reset, "
        "counts_json, error) VALUES (3, ?, 'full_enumeration', 0, 0, '{}', 'RuntimeError: failed')",
        (one,),
    )
    seed.close()
    failed = status_parts(fake_mac)[1]["Empty cloud folders"]
    assert failed.rstrip().endswith(
        "its last walk ended on an error, so the files it did not list are not counted"
    ), failed
    assert not [p for p in listed if str(root) in p], "no folder of the source was listed"
    cfg = fake_mac["config"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            'id = "client-alpha"', 'id = "client-alpha"\nexclude = ["Northwind/", "payroll"]', 1
        ),
        encoding="utf-8",
    )
    part = status_parts(fake_mac)[1]["Empty cloud folders"]
    assert "; 2 of the checked folders excluded in sources.toml now;" in part
    seed = Seed(cfg)
    seed.m.set_meta("empty_cloud_dirs:" + one, json.dumps([f"Wingtip {n}" for n in range(60)]))
    seed.close()
    capped = status_parts(fake_mac)[1]["Empty cloud folders"]
    assert "60 unknown: 0 dataless, 0 materialised-and-empty" in capped and "50 gone" in capped
    assert "; 50 of 60 checked;" in capped


@pytest.mark.timing
def test_the_exclude_rule_is_asked_of_the_checked_folders_only_and_inside_the_time(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exclude rule costs names x folder levels x globs, and the advice a sync gives for an empty cloud
    folder is one more glob: asked of every stored name on the report's own thread, it ran for as long as
    it liked (44 s for 5,000 names against 506 globs). It is asked of the 50 checked folders, in a call that
    is stopped; the folders' own facts are printed either way."""
    cfg = fake_mac["config"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            'id = "client-alpha"', 'id = "client-alpha"\nexclude = ["Wingtip 5*"]', 1
        ),
        encoding="utf-8",
    )
    seed = Seed(cfg)
    one = seed.ids()[0]
    seed.m.set_meta("empty_cloud_dirs:" + one, json.dumps([f"Wingtip {n}" for n in range(60)]))
    seed.close()
    asked: list[int] = []
    real = arm_local._unexcluded

    def counting(source: Any, dirs: Any) -> list[str]:
        asked.append(len(dirs))
        return real(source, dirs)

    monkeypatch.setattr(arm_local, "_unexcluded", counting)
    part = status_parts(fake_mac)[1]["Empty cloud folders"]
    # "Wingtip 5" is one of the first 50; "Wingtip 50" to "Wingtip 59" are stored and not checked
    assert "; 50 of 60 checked; 1 of the checked folders excluded in sources.toml now;" in part
    assert asked == [setup_report._EMPTY_DIRS_CHECKED]
    release = threading.Event()

    def hung(_source: Any, _dirs: Any) -> list[str]:
        release.wait(60)
        return []

    monkeypatch.setattr(arm_local, "_unexcluded", hung)
    monkeypatch.setattr(setup_report, "_FOLDERS_S", 0.3)
    try:
        text, parts = status_parts(fake_mac)
    finally:
        release.set()
    part = parts["Empty cloud folders"]
    assert "60 unknown: 0 dataless, 0 materialised-and-empty" in part and "; 50 of 60 checked;" in part
    assert (
        f"; excluded in sources.toml now: {setup_report.NOT_MEASURED}; the mirror holds no file below" in part
    )
    assert '- evidence: 1 line at the end of Status says "not measured"' in section(text, "Summary")
    assert "- no run is recorded" in parts["Repeat conversions"], "the part after it is measured"


def test_a_part_with_no_time_left_says_so_and_the_report_is_still_whole(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_ocr(fake_mac)
    monkeypatch.setattr(setup_report, "EVIDENCE_BUDGET_S", 0.0)
    text, parts = status_parts(fake_mac)
    assert re.findall(r"^## (.+)$", text, flags=re.MULTILINE) == list(setup_report.SECTION_TITLES)
    assert "- helper: off (AGENTSYNC_OCR=0)" in parts["OCR"], "the probe has a floor of its own"
    assert parts["OCR"].count(f"- {setup_report.NOT_MEASURED}") == 2, "the files, then the time"
    for title in ("Quarantine by reason", "Empty cloud folders", "Repeat conversions"):
        assert parts[title].strip() == f"- {setup_report.NOT_MEASURED}", title
    assert (
        '- evidence: 5 lines at the end of Status say "not measured": write the report again when this Mac '
        "is idle (`~/src/agent-context-sync/scripts/install.sh --report-only`) before sending it\n"
        in section(text, "Summary")
    )


def test_the_evidence_line_says_where_a_cause_waiting_cannot_fix_is_fixed(fake_mac: dict[str, Path]) -> None:
    """B13 round 18 V15, V35: the Summary said ``5 lines ... say "not measured": write the report again when
    this Mac is idle`` while each of the 5 said the state file cannot be read, which waiting never fixes. Each
    cause says where it is fixed; a time-out or a busy state file keeps the idle advice, a clause each when
    the causes are mixed."""
    r = setup_report._Run(fake_mac["config"], setup_report.ReportHooks(), 5.0)
    state = "the state file cannot be read: see Doctor's state_dir.manifest line"
    busy = "the state file is busy: a sync holds it"

    r.facts.evidence_why = Counter({state: 5})
    r.facts.evidence = 5
    assert setup_report._evidence_line(r) == (
        '- evidence: 5 lines at the end of Status say "not measured" because the state file cannot be read: '
        "a report written after it is fixed measures them (see Doctor's state_dir.manifest line)"
    )
    r.facts.evidence_why = Counter({state: 5, "time limit": 2})
    r.facts.evidence = 7
    mixed = setup_report._evidence_line(r)
    assert mixed == (
        '- evidence: 7 lines at the end of Status say "not measured": 5 because the state file cannot be '
        "read: a report written after it is fixed measures them (see Doctor's state_dir.manifest line); 2 "
        f"ran out of time: {setup_report._IDLE_AGAIN}"
    ), mixed
    r.facts.evidence_why = Counter({busy: 1, "time limit": 2})
    r.facts.evidence = 3
    assert setup_report._evidence_line(r).endswith(f'say "not measured": {setup_report._IDLE_AGAIN}')
    r.facts.evidence_why = Counter(
        {"the configuration is refused: see Configuration": 1, "an unexpected error: KeyError": 1}
    )
    r.facts.evidence = 2
    assert setup_report._evidence_line(r) == (
        '- evidence: 2 lines at the end of Status say "not measured": 1 because the configuration is '
        "refused: a report written after it loads measures them (see Configuration); 1 because of another "
        "cause: see those lines"
    )
    assert "idle" not in setup_report._evidence_line(r)


class Clock:
    """``setup_report``'s ``time``: the real monotonic clock, which a test can move on."""

    def __init__(self) -> None:
        self.ahead = 0.0

    def monotonic(self) -> float:
        return time.monotonic() + self.ahead


@pytest.mark.timing
def test_a_helper_that_hangs_costs_the_manifest_parts_none_of_their_time(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe starts a built helper, and it ran inside the time every part shares: a helper that did not
    answer left its own line and every manifest block unmeasured, on the Mac the OCR evidence is wanted
    from, and writing the report again gave the same. The probe has a wait of its own, and what it takes is
    not counted."""
    seed_ocr(fake_mac)
    release = threading.Event()

    def hung(_convert: ConvertConfig, _cache_dir: Path) -> tuple[str, str]:
        release.wait(60)
        return "ready", "too late"

    monkeypatch.setattr(ocr, "probe", hung)
    monkeypatch.setattr(media, "probe", hung)
    monkeypatch.setattr(speech, "probe", hung)
    monkeypatch.setattr(setup_report, "_PROBE_S", 0.3)
    try:
        text, parts = status_parts(fake_mac)
    finally:
        release.set()
    assert "- helper: did not answer within 0.3s (a built helper's `--version`;" in parts["OCR"]
    assert "- media helper: did not answer within 0.3s\n" in parts["OCR"], "the media helper waits the same"
    assert "- speech: did not answer within 0.3s\n" in parts["OCR"], "the speech helper waits the same"
    assert (
        "The helper did not say whether it is ready (its line above; Doctor's ocr line does)." in parts["OCR"]
    )
    assert "The helper is not ready" not in parts["OCR"]
    assert "- [policy] label rule: off\n" in parts["OCR"] and "too late" not in text
    assert "not measured" not in section(text, "Status"), "fixed words: a second report would say the same"
    assert (
        "- images: 8 files: 2 with a page" in parts["OCR"]
        and "- OCR time: of the last 3 runs" in parts["OCR"]
    )
    assert "| <folder-2> | quarantined | no text in image | 1 | 0 | - |" in parts["Quarantine by reason"]
    assert "of the last 3 runs, 2 recorded what they converted" in parts["Repeat conversions"]
    assert "- evidence: the 6 parts at the end of Status were measured\n" in section(text, "Summary")
    # On the report's own clock: a probe that answers after more time than the six parts have between them.
    clock = Clock()
    monkeypatch.setattr(setup_report, "time", clock)

    def slow(_convert: ConvertConfig, _cache_dir: Path) -> tuple[str, str]:
        clock.ahead += setup_report.EVIDENCE_BUDGET_S + 1.0
        return "ready", "paper-vision revision 2, helper 0.3.0"

    monkeypatch.setattr(ocr, "probe", slow)
    monkeypatch.setattr(media, "probe", lambda _convert, _cache_dir: ("ready", "media helper 0.1.0"))
    monkeypatch.setattr(speech, "probe", lambda _convert, _cache_dir: ("ready", "fluidaudio, helper 0.1.0"))
    text, parts = status_parts(fake_mac)
    assert "- helper: ready (paper-vision revision 2, helper 0.3.0)\n" in parts["OCR"]
    assert (
        "- media helper: ready (media helper 0.1.0)\n- speech: ready (fluidaudio, helper 0.1.0)\n"
        "- [policy] label rule: off\n"
    ) in parts["OCR"]
    assert "not measured" not in section(text, "Status"), "the probe's time is not the manifest's"
    assert "- images: 8 files: 2 with a page" in parts["OCR"]


def test_the_ocr_part_has_a_speech_line_of_its_own(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor lists an ok speech check by name only and the installer's line is past the install.out tail, so
    the 2026-10-10 report showed speech nowhere but a friction line (B12 E3.e)."""
    off = "the speech models are not placed: model-a/ and model-b/"
    monkeypatch.setattr(media, "probe", lambda _convert, _cache_dir: ("ready", "media helper 0.1.0"))
    monkeypatch.setattr(speech, "probe", lambda _convert, _cache_dir: ("off", off))
    _text, parts = status_parts(fake_mac)
    head = f"- media helper: ready (media helper 0.1.0)\n- speech: off ({off})\n- [policy] label rule"
    assert head in parts["OCR"]
    monkeypatch.setattr(
        speech, "probe", lambda _convert, _cache_dir: ("not-built", "the speech helper is not built")
    )
    _text, parts = status_parts(fake_mac)
    assert "- speech: not built (the speech helper is not built)\n" in parts["OCR"]

    def broken(_convert: ConvertConfig, _cache_dir: Path) -> tuple[str, str]:
        raise RuntimeError("Contoso")

    monkeypatch.setattr(speech, "probe", broken)
    text, parts = status_parts(fake_mac)
    assert "- speech: not measured (an unexpected error: RuntimeError)\n- [policy] label rule" in parts["OCR"]
    assert "Contoso" not in text


def test_a_statement_that_runs_past_the_time_is_stopped(fake_mac: dict[str, Path]) -> None:
    """SQLite is asked to stop from inside: a statement over a manifest far larger than any seen cannot
    hold the report. This one would count to a hundred million."""
    mirror = setup_report._Mirror(load_config(fake_mac["config"]).state_paths.db, 0.05)
    endless = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 100000000) "
    try:
        with pytest.raises(setup_report._OutOfTimeError):
            mirror.rows(endless + "SELECT COUNT(*) FROM c")
        assert mirror.steps > 0
        with pytest.raises(setup_report._OutOfTimeError):
            mirror.rows("SELECT 1")
    finally:
        mirror.close()


def test_the_suffixes_and_keys_the_evidence_goes_by_are_the_codes_own(tmp_path: Path) -> None:
    assert ImageConverter.extensions == setup_report._IMAGE_SUFFIXES
    registry = Registry.default(ConvertConfig(), ocr=fake_engine(tmp_path / "bin"))
    reads_pictures = {
        ext
        for conv in registry.converters()
        if "ocr_languages" in conv.options() and conv.converter_id != ImageConverter.converter_id
        for ext in conv.extensions
    }
    assert set(setup_report._OCR_DOCUMENTS) == reads_pictures
    assert setup_report._OCR_MARK == image._IDENTITY_MARK and setup_report._FIELD_MARKS == image._FIELD_MARKS
    listed = setup_report._REFUSED_TYPE_LIST.split()
    assert len(listed) == len(setup_report._REFUSED_TYPES) > 200, "no type twice, and a broad list"
    assert all(re.fullmatch(r"\.[a-z0-9]+(\.json)?", suffix) for suffix in listed), "type words only"
    reads = {*registry.extensions(), *ImageConverter.extensions}
    assert reads <= setup_report._REFUSED_TYPES, "a stub can be older than the converter of its type"
    assert setup_report._NO_CONVERTER == convert.NO_CONVERTER_PREFIX
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"bytes")
    for name, kind in (("Wingtip thread.OFT", ".oft"), ("Makefile", "no extension"), ("a.b c", "other")):
        refused = convert.convert_file(
            staged,
            name=name,
            content_sha256="0" * 64,
            canonical_sha256="0" * 64,
            registry=registry,
            cache=ConverterCache(tmp_path / "cache"),
        )
        assert setup_report._refused_type(refused.reason) == kind, refused.reason
    assert setup_report._REREAD_META == cycle._REREAD_META
    assert setup_report._REREAD_BUDGET_S == cycle._REREAD_BUDGET_S, "the seconds the per-run table names"
    assert setup_report._REREAD_FOR_DIGITS == cycle._REREAD_FOR_DIGITS
    digest = "c0ffee11" + "0" * 56
    assert setup_report._reread_number(digest) == cycle._reread_number(digest) == 0xC0FFEE11
    assert setup_report._reread_number("not a digest") is None
    assert setup_report._EMPTY_DIRS_META == cycle._EMPTY_DIRS_META
    assert [
        setup_report._version_class(v) for v in (PLAIN_VERSION, PLAIN_VERSION + "+ocr-off", OCR_VERSION)
    ] == [
        0,
        1,
        2,
    ]
    assert setup_report._version_class("2.0.0+ocr-apple-vision-r3-h2.0.0-l1+helper-9+macos-15") == 1


BIG = 50_000


def seed_big(fake_mac: dict[str, Path]) -> None:
    """A manifest of 50,000 files, each with an output row and a cache row, 300 runs and a re-read record."""
    seed = Seed(fake_mac["config"])
    sources = seed.ids()
    suffixes = (".png", ".pdf", ".docx", ".xlsx", ".jpeg", ".pptx", ".txt", ".heic")
    states = (
        ("live", None, 0, "ok"),
        ("live", None, 0, "ok"),
        ("quarantined", NOT_RUN, 0, "quarantined"),
        ("refused", NO_CONVERTER, 1, "refused"),
        ("live", None, 0, "ok"),
        ("quarantined", "conversion failed: Wingtip merger said no", 0, "failed"),
        ("dataless", None, 1, "ok"),
        ("tombstone", None, 0, "tombstone"),
        ("live", None, 0, "ok"),
        ("live", None, 0, "ok"),
        ("live", None, 0, "ok"),
    )
    items, outputs, cache = [], [], []
    for n in range(BIG):
        sid = sources[n % len(sources)]
        state, reason, dataless, status = states[n % len(states)]
        stable, key = f"big-{n:06d}", f"bigkey-{n:06d}"
        name = f"Wingtip payroll {n}{suffixes[n % len(suffixes)]}"
        rel = f"Northwind {n % 500}/{name}"
        items.append((sid, stable, name, rel, n, dataless, state, reason, "unchanged", f"{n:064d}"))
        version = OCR_VERSION if n % 3 else PLAIN_VERSION
        outputs.append((f"mirror/{sid}/{stable}.md", sid, stable, key, version, status, 1 + n % 300))
        cache.append((key, version, 1 + n % 300, 1 + (n * 7) % 300))
    db = seed.m._db
    db.execute("BEGIN")
    db.executemany(
        "INSERT INTO items (source_id, stable_id, name, rel_path, is_dir, size, dataless, state, "
        "state_reason, last_verdict, first_seen_run, last_seen_run, canonical_sha256) "
        "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, 1, 1, ?)",
        items,
    )
    db.executemany(
        "INSERT INTO outputs (output_path, source_id, stable_id, unit_id, action_key, converter_id, "
        "converter_version, status, built_run) VALUES (?, ?, ?, 'whole', ?, 'conv', ?, ?, ?)",
        outputs,
    )
    db.executemany(
        "INSERT INTO cache (action_key, converter_id, converter_version, options_hash, canonical_sha256, "
        "status, unit_count, bytes, created_run, last_used_run) "
        "VALUES (?, 'conv', ?, 'o', 'c', 'ok', 1, 1, ?, ?)",
        cache,
    )
    db.execute("COMMIT")
    for run_id in range(1, 301):
        seed.run(run_id, {"converted": 6, "converted_again": 6, "ocr_ms": 1000, "ocr_budget_s": 180})
    once = {"big-000000": 1, "big-000003": 1}  # two of this source's files: one live, one refused
    record = {"done": False, "for": "a" * 64, "tried": [], "failed": once}
    seed.m.set_meta("reread:" + sources[0], json.dumps([record]))
    seed.m.set_meta("empty_cloud_dirs:" + sources[0], json.dumps([f"Northwind {n}" for n in range(60)]))
    seed.close()


def test_the_evidence_stays_bounded_on_a_manifest_of_50_000_files(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report's time limit is kept by the work done, which is what is asserted, not a wall clock: the
    SQLite VM instructions spent grow with the rows (no statement looks at a table once per row of
    another), each statement reads a table from end to end at most once, and nothing is left unmeasured.
    On the Mac this was written on the 50,000 files take 0.7 s of the evidence's 3."""
    seed_big(fake_mac)
    monkeypatch.setattr(setup_report, "EVIDENCE_BUDGET_S", 3600.0)  # the bound is the work, not the clock
    r = setup_report._Run(fake_mac["config"], setup_report.ReportHooks(), 3600.0)
    text = "\n".join(setup_report._evidence(r))
    assert setup_report.NOT_MEASURED not in text and "not measured" not in text
    assert (
        "- images: 17045 files: 11933 with a page · 1704 not-on-this-Mac stub · 1704 failed · 1704 other "
        "stub" in text
    )
    assert "- files whose row carries a reason: 9091 quarantined, 4546 refused" in text
    by_type = re.findall(
        r"^- (?:\(source \d\)|inbox), no converter by type: \.png (\d+) \((\d+) online-only\)$", text, re.M
    )
    assert len(by_type) == 3 and sum(int(files) for files, _online in by_type) == 4546
    assert "; 50 of 60 checked;" in text and "of the last 200 runs, 200 recorded" in text
    assert (
        "- (source 1), the 2 files that failed once, by their row in the manifest now: 1 on this Mac · 1 "
        "with a stub\n" in text
    )
    assert (
        "The last 5 of the 200 runs that had an engine, newest first (the sums above are over all 200" in text
    )
    for name in SECRET_NAMES:
        assert name not in text, name
    instructions = r.evidence_steps * setup_report._STEP_TICK
    assert 0 < instructions <= 1000 * BIG, f"{instructions} VM instructions for {BIG} files"
    assert len(r.evidence_statements) < 200, "the statements do not grow with the files either"
    mirror = setup_report._Mirror(load_config(fake_mac["config"]).state_paths.db, 3600.0)
    try:
        conn = mirror._open()
        for sql in dict.fromkeys(r.evidence_statements):
            plan = conn.execute("EXPLAIN QUERY PLAN " + sql, [None] * sql.count("?")).fetchall()
            parent = {row[0]: row[1] for row in plan}
            detail = {row[0]: str(row[3]) for row in plan}
            scans = [node for node, what in detail.items() if re.match(r"SCAN (?!\()", what)]
            assert len(scans) <= 1, f"more than one table read from end to end: {sql}"
            for node in scans:
                above = parent[node]
                while above:
                    assert "CORRELATED" not in detail[above], f"a table read once per row: {sql}"
                    above = parent[above]
    finally:
        mirror.close()


def test_background_runs_say_which_arguments_of_an_installed_plist_differ(fake_mac: dict[str, Path]) -> None:
    """Doctor says an installed plist differs in ProgramArguments and no more. The report says which
    positions, and of what class each is: never an argument, a path or a variable's name."""
    home = fake_mac["home"]
    launcher = home / "Applications" / "AgentSyncLauncher.app" / "Contents" / "MacOS" / "agentsync-launcher"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    config = load_config(fake_mac["config"])
    spec = launchd.poll_spec(config)
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.agentsync.reconcile.plist").write_bytes(
        launchd.render_plist(launchd.reconcile_spec(config))
    )
    args = list(spec.program_arguments)
    assert args.count("--canary") == 2, "one per cloud source"
    # An older install: another launcher and interpreter, one canary (a folder no longer configured), a
    # longer interval, another PATH and a key nobody writes any more.
    old = plistlib.loads(launchd.render_plist(spec))
    stale = [str(home / "Wingtip" / "agentsync-launcher"), *args[1 : args.index("--canary")]]
    stale += ["--canary", str(home / "Northwind merger"), "--", str(home / "Tailspin" / "bin" / "python3")]
    old["ProgramArguments"] = [*stale, *args[args.index("--") + 2 :]]
    old["StartInterval"] = 900
    old["EnvironmentVariables"] = {"PATH": "/Wingtip/bin", "FOURTH": "Coffee"}
    old["Payroll"] = "ledger-2031"
    (agents / "com.agentsync.poll.plist").write_bytes(plistlib.dumps(old))
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    bg = section(text, "Background runs")
    assert "Installed plist against what this build would write (classes and counts, never a value):" in bg
    assert (
        "- com.agentsync.poll: differs in ProgramArguments, EnvironmentVariables, StartInterval (installed "
        "900, this build 300); 1 key this build does not write\n" in bg
    )
    assert (
        "  - ProgramArguments: 21 installed, 23 in this build; positions that differ (from 0): 0 (launcher), "
        "8 (canary path), 9 (separator installed, launcher option in this build), 10 (interpreter installed, "
        "canary path in this build), 11 (fixed argument installed, separator in this build), 12 (fixed "
        "argument installed, interpreter in this build), 13 (fixed argument), " in bg
    )
    assert "18 (mode installed, fixed argument in this build) (+4 more)\n" in bg
    assert (
        "  - by class: launcher differs (the same file: no; the installed one exists: no) · interpreter "
        "differs (the same file: no; the installed one exists: no) · config path same · mode same · watchdog "
        "seconds same · grace seconds same · canary timeout same · canary paths: 1 installed, 2 in this "
        "build, 0 in both · fixed arguments same · other arguments: 0 installed, 0 in this build\n" in bg
    )
    assert (
        "  - EnvironmentVariables: 2 installed, 2 in this build, 1 in both, 1 of those with another value\n"
        in bg
    )
    assert "- com.agentsync.reconcile: the installed plist is what this build would write\n" in bg
    # the fix commands name the binary by its full path (B13 round 5 F95), which holds no installed value
    for raw in (*SECRET_NAMES, "FOURTH", "Coffee", "Payroll", "python3", "/bin"):
        assert raw not in bg.replace(setup_report.AGENTSYNC_BIN, ""), raw
    # An interpreter that is a link to this build's is the same file under another name.
    link = home / "Tailspin" / "bin" / "python3"
    link.parent.mkdir(parents=True)
    link.symlink_to(sys.executable)
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    assert "interpreter differs (the same file: yes; the installed one exists: yes)" in text
    (agents / "com.agentsync.poll.plist").write_text("not a plist", encoding="utf-8")
    launcher.unlink()
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    bg = section(text, "Background runs")
    assert "- com.agentsync.poll: not compared (the plist cannot be parsed)\n" in bg
    assert (
        "- com.agentsync.reconcile: not compared (the configuration is refused: see Configuration)\n" in bg
    ), "no launcher: this build writes none"


def test_an_installed_job_that_names_the_launchers_pin_has_the_same_interpreter(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second bring-back (2026-10-07): both installed jobs read "interpreter differs (the same file: yes)"
    beside two canary paths the config had gained. The jobs ran: they named ``<tool>/bin/python``, the path
    the launcher is built for, and the updated tool ran as ``<tool>/bin/python3``. This build writes the
    launcher's pin, so the report compares against it: the interpreter is the same, and what is left is
    the real difference, the canary paths. With no pin to read the interpreter differs, as before."""
    home = fake_mac["home"]
    app = home / "Applications" / "AgentSyncLauncher.app"
    launcher = app / "Contents" / "MacOS" / "agentsync-launcher"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    tool = home / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin"
    tool.mkdir(parents=True)
    (tool / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (tool / "python").chmod(0o755)
    (tool / "python3").symlink_to("python")
    info = app / "Contents" / "Info.plist"
    info.write_bytes(plistlib.dumps({"AgentSyncAllowedProgram": str(tool / "python")}))
    config = load_config(fake_mac["config"])
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)

    # The jobs as install.sh's agent step wrote them, when the tool ran as bin/python and the config had
    # one cloud folder: one canary path.
    monkeypatch.setattr(sys, "executable", str(tool / "python"))
    for build in (launchd.poll_spec, launchd.reconcile_spec):
        spec = build(config)
        args = list(spec.program_arguments)
        assert args.count("--canary") == 2, "one per cloud source"
        last = len(args) - 1 - args[::-1].index("--canary")
        installed = plistlib.loads(launchd.render_plist(spec))
        installed["ProgramArguments"] = [*args[:last], *args[last + 2 :]]
        (agents / f"{spec.label}.plist").write_bytes(plistlib.dumps(installed))

    monkeypatch.setattr(sys, "executable", str(tool / "python3"))  # the updated tool, as on the field Mac
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    bg = section(text, "Background runs")
    for label in ("com.agentsync.poll", "com.agentsync.reconcile"):
        assert f"- {label}: differs in ProgramArguments\n" in bg
    assert bg.count("  - ProgramArguments: 21 installed, 23 in this build; positions that differ") == 2
    by_class = (
        "  - by class: launcher same · interpreter same · config path same · mode same · watchdog seconds "
        "same · grace seconds same · canary timeout same · canary paths: 1 installed, 2 in this build, 1 in "
        "both · fixed arguments same · other arguments: 0 installed, 0 in this build\n"
    )
    assert bg.count(by_class) == 2
    assert "interpreter differs" not in bg

    info.unlink()  # a launcher whose pin cannot be read: this build writes its own interpreter's path
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    bg = section(text, "Background runs")
    assert bg.count("interpreter differs (the same file: yes; the installed one exists: yes)") == 2


def test_comparing_a_plist_adds_no_log_line(
    fake_mac: dict[str, Path], caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """Without a launcher, building a job's spec warns that the job would run the interpreter directly.
    Doctor says so once; the comparison must not say it again in what the installer prints."""
    project = tmp_path / "plain"
    project.mkdir()
    cfg = fake_mac["config"]  # no source under a protected folder: no launcher is needed
    cfg.write_text(f'[[source]]\nid = "plain"\nkind = "local"\npath = "{project}"\n', "utf-8")
    config = load_config(cfg)
    agents = fake_mac["home"] / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    with caplog.at_level("WARNING", logger="agentsync.ops.launchd"):
        (agents / "com.agentsync.poll.plist").write_bytes(launchd.render_plist(launchd.poll_spec(config)))
        assert len(caplog.records) == 1, "the build itself warns"
        caplog.clear()
        report_text, _red = setup_report.build_report(cfg, hooks=setup_report.ReportHooks())
        assert caplog.records == []
    bg = section(report_text, "Background runs")
    assert "- com.agentsync.poll: the installed plist is what this build would write\n" in bg
    assert "- com.agentsync.reconcile: no plist\n" in bg
    assert not logging.getLogger("agentsync.ops.launchd").disabled, "the logger is as it was"


def test_argument_roles_follow_the_shape_launchd_writes() -> None:
    roles = functools.partial(
        setup_report.argument_roles, launcher=launchd.LAUNCHER_EXECUTABLE, fixed=launchd.CHILD_PREFIX
    )
    child = [
        "/venv/bin/python",
        "-I",
        "-X",
        "utf8",
        "-m",
        "agentsync",
        "sync",
        "--mode",
        "poll",
        "--config",
        "/c",
    ]
    child_roles = ["interpreter", *["fixed argument"] * 7, "mode", "fixed argument", "config path"]
    assert roles(child) == child_roles
    job = ["/apps/agentsync-launcher", "--timeout", "1800", "--canary", "/a", "--canary", "/b", "--", *child]
    assert roles(job) == [
        "launcher",
        "launcher option",
        "watchdog seconds",
        "launcher option",
        "canary path",
        "launcher option",
        "canary path",
        "separator",
        *child_roles,
    ]
    assert roles(["/apps/agentsync-launcher", "stray", "--canary"]) == [
        "launcher",
        "other",
        "launcher option",
    ]
    assert roles([]) == [] and roles([7, "--mode"]) == ["interpreter", "other"]


def test_a_run_with_one_step_line_and_no_end_line_says_its_only_step_line(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One step line reads "its only step line is", not "the last of its 1 step line(s)" (B13 round 2
    R107)."""
    monkeypatch.setattr(setup_report, "_pid_alive", lambda pid: False)
    run = "20261011T100000Z-777"
    (fake_mac["setup"] / "install.log").write_text(
        f"2026-10-11T10:00:00Z run={run} start install.sh compat=11 commit=0123456789ab kind=checkout "
        "source=- args=\n"
        f"2026-10-11T10:00:00Z run={run} step=uv seconds=0 rc=0 result=skipped note=present\n",
        encoding="utf-8",
    )
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    inst = section(text, "Installer").split("<details>", 1)[0]
    assert "; its only step line is uv (skipped), and nothing after it is logged (a step's line" in inst, inst
    assert "step line(s)" not in inst


def test_installer_lists_every_run_and_the_ones_with_no_end_line(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first bring-back file counted four runs and showed three. Every run is one line now, and a run
    with no end line says when it started, whether its process is gone, and the last step it logged."""
    monkeypatch.setattr(setup_report, "_pid_alive", lambda pid: False)  # pid 777 may exist on this Mac
    log = fake_mac["setup"] / "install.log"
    stopped = "20261006T021601Z-777"
    log.write_text(
        log.read_text(encoding="utf-8")
        + "2026-10-06T02:10:00Z run=20261006T021000Z-600 start install.sh compat=7 commit=0123456789ab "
        "kind=checkout source=- args=--list-folders\n"
        "2026-10-06T02:10:00Z run=20261006T021000Z-600 step=list-folders seconds=1 rc=0 result=done "
        "note=listed-24\n"
        "2026-10-06T02:10:01Z run=20261006T021000Z-600 end rc=0 seconds=1\n"
        f"2026-10-06T02:16:01Z run={stopped} start install.sh compat=7 commit=0123456789ab kind=checkout "
        "source=- args=--source-local Wingtip\\ merger\n"
        f"2026-10-06T02:16:01Z run={stopped} step=uv seconds=0 rc=0 result=skipped note=present\n"
        f"2026-10-06T02:16:02Z run={stopped} step=agentsync seconds=9 rc=1 result=failed\n",
        encoding="utf-8",
    )
    write_install_log(fake_mac, start="2026-10-06T16:42:00Z", run="20261006T164200Z-900", append=True)
    write_install_log(fake_mac, start="2026-10-06T16:50:00Z", run="20261006T165000Z-901", append=True)
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    inst = section(text, "Installer").split("<details>", 1)[0]
    assert "5 install.sh runs in" in inst and "the last 3 (run ids without their process id):" in inst
    assert "Every run, oldest first (the last 5 of 5; run ids without their process id):" in inst
    assert [ln for ln in inst.splitlines() if ln.startswith("| 2026")] == [
        "| 20260929T100000Z | 2026-09-29T10:00:00Z | install | 6 | agent (skipped) | exit 0 after 57s |",
        "| 20261006T021000Z | 2026-10-06T02:10:00Z | list-folders | 1 | list-folders (done) "
        "| exit 0 after 1s |",
        "| 20261006T021601Z | 2026-10-06T02:16:01Z | install | 2 | agentsync (failed, rc 1) | no end line |",
        "| 20261006T164200Z | 2026-10-06T16:42:00Z | install | 6 | report (done) | exit 0 after 57s |",
        "| 20261006T165000Z | 2026-10-06T16:50:00Z | install | 6 | report (done) | exit 0 after 57s |",
    ]
    assert "Runs with no end line (stopped early, or still running): 1\n" in inst
    assert (
        "- run 20261006T021601Z: started 2026-10-06T02:16:01Z, its process is gone: killed, the Mac "
        "restarted, or its end line could not be written; the last of its 2 step lines is agentsync "
        "(failed, rc 1), and nothing after it is logged (a step's line is written when the step ends, so a "
        "step under way has none)\n" in inst
    )
    assert " reached " not in inst
    assert "Wingtip" not in inst and "-777" not in inst


_KILLED_RUN = "20261010T043017Z-4243"


def _append_killed_run(fake_mac: dict[str, Path], *, failed: bool = False) -> None:
    """v10 E1 run B1's shape: a SIGKILL during the helper builds left uv, agentsync and launcher lines (a
    step's line is written when the step ends) and no end line."""
    log = fake_mac["setup"] / "install.log"
    run = _KILLED_RUN
    launcher = "seconds=0 rc=1 result=failed" if failed else "seconds=0 rc=0 result=skipped"
    log.write_text(
        log.read_text(encoding="utf-8")
        + f"2026-10-10T04:30:17Z run={run} start install.sh compat=10 commit=0123456789ab kind=checkout "
        "source=- args=\n"
        f"2026-10-10T04:30:17Z run={run} step=uv seconds=0 rc=0 result=skipped note=present\n"
        f"2026-10-10T04:30:17Z run={run} step=agentsync seconds=9 rc=0 result=done\n"
        f"2026-10-10T04:30:26Z run={run} step=launcher {launcher}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("alive", "state"),
    [
        (False, "its process is gone: killed, the Mac restarted, or its end line could not be written"),
        (True, "its process id is in use: still running, or the id was reused"),
        (None, "interrupted, or still running"),
    ],
)
def test_a_run_with_no_end_line_says_whether_its_process_is_gone(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch, alive: bool | None, state: str
) -> None:
    """H14: SIGKILL is never trapped, so a killed run has no end line, and the report said "interrupted, or
    still running" for a run whose process was long gone. The run id ends in install.sh's pid, and the
    report runs on the same Mac: signal 0 says which. The pid is read, never printed."""
    seen: list[int] = []
    monkeypatch.setattr(setup_report, "_pid_alive", lambda pid: seen.append(pid) or alive)
    _append_killed_run(fake_mac)
    text, summary = summary_of(fake_mac)
    assert 4243 in seen
    inst = section(text, "Installer").split("<details>", 1)[0]
    assert f"run 20261010T043017Z: no end line ({state})\n" in inst
    assert (
        f"- run 20261010T043017Z (the newest run): started 2026-10-10T04:30:17Z, {state}; the last of its 3 "
        "step lines is launcher (skipped), and nothing after it is logged (a step's line is written when "
        "the step ends, so a step under way has none)\n" in inst
    )
    assert f"- install.sh: 2 install runs; the last no end line ({state}) (uv 0s" in summary
    assert f"(computed: install.sh has no end line, {state})" in summary
    assert "reached launcher" not in text and "-4243" not in text


def test_a_run_with_no_end_line_and_a_failed_step_keeps_the_two_apart(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """H14 C2: "killed, or the Mac restarted, last failed step x" read as a third alternative after "or". The
    third cause (B13 round 2 R131) kept a semicolon before the step; with no bracket inside the outcome's own
    (B13 round 11 M41) the failed step comes before the causes instead."""
    monkeypatch.setattr(setup_report, "_pid_alive", lambda pid: False)
    _append_killed_run(fake_mac, failed=True)
    _text, summary = summary_of(fake_mac)
    assert (
        "(computed: install.sh has no end line after its failed launcher step, its process is gone: killed, "
        "the Mac restarted, or its end line could not be written)\n" in summary
    )


def test_a_run_cut_off_in_a_step_names_the_last_step_logged(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """N33, v10 E1 run B1: a SIGKILL during the helper builds left uv, agentsync and launcher lines and no
    end line, and the report said the run "reached launcher". A step's line is written when the step ends, so
    the bullet names the last step line and says nothing after it is logged; a start line alone has none."""
    monkeypatch.setattr(setup_report, "_pid_alive", lambda pid: False)  # pids 4243 and 4244 may exist
    _append_killed_run(fake_mac)
    log = fake_mac["setup"] / "install.log"
    bare = "20261010T050000Z-4244"
    log.write_text(
        log.read_text(encoding="utf-8")
        + f"2026-10-10T05:00:00Z run={bare} start install.sh compat=10 commit=0123456789ab kind=checkout "
        "source=- args=\n",
        encoding="utf-8",
    )
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    inst = section(text, "Installer").split("<details>", 1)[0]
    assert "reached launcher" not in inst and "-4243" not in inst and "-4244" not in inst
    assert (
        "- run 20261010T043017Z: started 2026-10-10T04:30:17Z, its process is gone: killed, the Mac "
        "restarted, or its end line could not be written; the last of its 3 step lines is launcher "
        "(skipped), and nothing after it is logged (a step's line is written when the step ends, so a step "
        "under way has none)\n" in inst
    )
    assert (
        "- run 20261010T050000Z (the newest run): started 2026-10-10T05:00:00Z, its process is gone: killed, "
        "the Mac restarted, or its end line could not be written; it has no step line (a step's line is "
        "written when the step ends, so a step under way has none)\n" in inst
    )


def test_pid_alive_reads_this_process_and_a_reaped_one() -> None:
    """Signal 0 sends nothing: this process exists, a child that was waited on does not, and 0 and 1 (a
    process group, init) are never probed."""
    assert setup_report._pid_alive(os.getpid()) is True
    child = subprocess.Popen(["/usr/bin/true"])
    child.wait()  # reaped: an unreaped zombie still exists
    assert setup_report._pid_alive(child.pid) is False
    assert setup_report._pid_alive(0) is None and setup_report._pid_alive(1) is None


@pytest.mark.parametrize("case", ["none", "both", "shell only", "version fails"])
def test_environment_says_what_the_secret_scan_can_use(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """B13 round 4 Q81: whether this Mac has gitleaks changes what every install's re-screen does, yet no
    report said. The Environment line says what the scan can use, by the lookups doctor makes (this shell's
    and launchd's), with the builtin patterns always; and the manifest's record of what the last re-screen
    used. A path under the home folder reads as ~."""
    exe = Path.home() / ".local" / "bin" / "gitleaks"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(
        "#!/bin/sh\n" + ("exit 3\n" if case == "version fails" else "echo 8.30.1\n"), encoding="utf-8"
    )
    exe.chmod(0o755)
    found = {"none": (None, None), "shell only": (exe, None)}.get(case, (exe, exe))
    monkeypatch.setattr(lints, "gitleaks_lookup", lambda path=None: found[path is not None])
    seed = Seed(fake_mac["config"])
    seed.m.set_meta(cycle._SECRET_RULES_META, json.dumps({"gitleaks": None, "builtin": 2}))
    seed.close()
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    [line] = [ln for ln in section(text, "Environment").splitlines() if ln.startswith("- secret scan: ")]
    want = {
        "none": "builtin patterns only (no gitleaks found)",
        "both": "builtin patterns + gitleaks 8.30.1 (~/.local/bin/gitleaks; shell and background runs)",
        "shell only": "builtin patterns + gitleaks 8.30.1 (~/.local/bin/gitleaks) in this shell only; "
        "background runs use the builtin patterns alone (fix: ~/.local/bin/agentsync sync from this shell)",
        "version fails": "builtin patterns + gitleaks (version unknown) (~/.local/bin/gitleaks; shell and "
        "background runs)",
    }[case]
    assert line == f"- secret scan: {want}; last re-screen: builtin patterns only", line
    assert cycle._SECRET_RULES_META == "secret_rules", "the key _last_rescreen reads"


def test_configuration_says_a_label_rule_is_on_in_counts_only(fake_mac: dict[str, Path]) -> None:
    """B13 round 4 Q31: a [policy] label rule changes what is mirrored, yet Configuration did not say one was
    set. It says so in counts: never a label's name, its id or the policy's fingerprint."""
    cfg = fake_mac["config"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + '\n[policy]\nexclude_label_names = ["Contoso Highly Confidential"]\n',
        encoding="utf-8",
    )
    text, _red = setup_report.build_report(cfg, hooks=setup_report.ReportHooks())
    conf = section(text, "Configuration")
    assert "- [policy] label rule: on (0 excluded ids, 1 name, refuse_unlabelled=false)\n" in conf, conf
    assert "Highly Confidential" not in text and "sha256:" not in conf
    (cfg.parent / "policy.toml").write_text("[policy]\nrefuse_unlabelled = true\n", encoding="utf-8")
    conf = section(setup_report.build_report(cfg, hooks=setup_report.ReportHooks())[0], "Configuration")
    assert (
        "- [policy] label rule: on (0 excluded ids, 1 name, refuse_unlabelled=true); policy.toml beside "
        "sources.toml: yes\n" in conf
    ), conf
    (cfg.parent / "policy.toml").write_text("[policy]\nexclude_label_ids = [1]\n", encoding="utf-8")
    conf = section(setup_report.build_report(cfg, hooks=setup_report.ReportHooks())[0], "Configuration")
    refused = "not read (the configuration is refused: see Configuration)"
    assert f"- [policy] label rule: {refused}; policy.toml beside sources.toml: yes\n" in conf, conf


def test_configuration_counts_the_source_excludes_beyond_agentsyncs_own(fake_mac: dict[str, Path]) -> None:
    """B13 round 5 F40: exclude-empty wrote a folder glob, the next walk pruned the folder, and the report
    said "Empty cloud folders: none" with nothing about a folder that will not be mirrored if it gains
    files. The Configuration counts each local or inbox source's excludes beyond agentsync's own, with no
    glob text and no folder name; a zero is said."""
    cfg = fake_mac["config"]
    conf = section(setup_report.build_report(cfg, hooks=setup_report.ReportHooks())[0], "Configuration")
    assert "- source excludes beyond agentsync's own: none\n" in conf, conf
    loaded = load_config(cfg)
    [src, *rest] = [s for s in loaded.sources if s.kind.value in ("local", "inbox")]
    set_source_exclude(
        cfg, src.id, [*DEFAULT_EXCLUDES, "/Empty Subfolder/", "*.bak"]
    )  # as exclude-empty does
    text = setup_report.build_report(cfg, hooks=setup_report.ReportHooks())[0]
    [line] = [ln for ln in section(text, "Configuration").splitlines() if ln.startswith("- source excludes")]
    label = line.split(": ", 1)[1].split(" ", 1)[0]
    assert re.fullmatch(r"<(?:source|folder)-\d+>|\(source", label), line
    assert line.endswith(
        f" 2 (1 folder glob); 0 on the other {setup_report._plural(len(rest), 'source')} (globs not shown; "
        "an excluded folder is not mirrored if it later gains files)"
    ), line
    assert "Empty Subfolder" not in text and "*.bak" not in text


def test_configuration_counts_the_folders_named_like_a_coding_agent(fake_mac: dict[str, Path]) -> None:
    """Whether the folder named like the agent's product is only listed or also a source's folder decides
    whether the Agent: line is readable. The count says which, with no name."""
    line = "- folders whose name is made only of coding-agent product words: "
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    conf = section(text, "Configuration")
    # no such folder: nothing follows from it (B13 round 11 M2)
    assert (
        line + "0 under ~/Library/CloudStorage, which this report does not redact; 0 of the configured "
        "source folders, which are redacted to <folder-N> like any other\n"
    ) in conf, conf
    assert "keeps its name" not in conf and "a placeholder like any other" not in conf
    # The line gives no example: a product word is a folder name a source can have, and a redacted example
    # in fixed text gives that name back.
    cloud = fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{ORG}"
    (cloud / "Copilot").mkdir()
    so = ", so the product name in the friction log's Agent: line stays as written"

    def conf_for(agent: str) -> str:
        write_friction(
            fake_mac, V11_HAPPY.replace("Agent: Claude Code, claude-opus-5-5\n", f"Agent: {agent}\n")
        )
        text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
        return section(text, "Configuration")

    # a listed folder "Copilot": the clause only when the Agent: line has its word
    assert line + "1 under ~/Library/CloudStorage, which this report does not redact; " in conf_for(
        "Claude Code claude-opus-5-5"
    )
    assert line + f"1 under ~/Library/CloudStorage, which this report does not redact{so}; " in conf_for(
        "GitHub Copilot model-x1"
    )
    # an earlier attempt's Agent: line is in the report too
    write_friction(
        fake_mac,
        V11_HAPPY.replace("Agent: Claude Code, claude-opus-5-5\n", "Agent: GitHub Copilot model-x1\n")
        + V11_HAPPY.replace("2026-09-29T", "2026-09-30T"),
    )
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    assert line + f"1 under ~/Library/CloudStorage, which this report does not redact{so}; " in text
    # A configured "GitHub Copilot" is redacted: it is not counted as unredacted, and its words never decide
    # the clause, which would say a redacted folder is named like the agent.
    (cloud / "GitHub Copilot").mkdir()
    assert cli.main(["add-source", str(cloud / "GitHub Copilot"), "--config", str(fake_mac["config"])]) == 0
    conf = conf_for("GitHub model-x1")
    assert (
        line + "1 under ~/Library/CloudStorage, which this report does not redact; 1 of the configured"
        in conf
    )
    conf = conf_for("Copilot model-x1")
    assert line + f"1 under ~/Library/CloudStorage, which this report does not redact{so}; 1 of the" in conf


def test_a_source_is_never_named_by_an_id_the_redactor_does_not_know(fake_mac: dict[str, Path]) -> None:
    """The evidence parts print a source as the Redactor shows it. With a Redactor that knows nothing (its
    facts could not be gathered), a configured id is its place in sources.toml, but for agentsync's own
    words; an id the config does not have is a number of its own."""
    config = load_config(fake_mac["config"])
    labels = setup_report._Labels(config, setup_report.Redactor())
    assert [labels.of(src.id) for src in config.sources] == ["(source 1)", "inbox", "(source 3)"]
    assert labels.of("northwind-archive") == "(not in the config, 1)"
    assert labels.of("tailspin-bridge") == "(not in the config, 2)"
    assert labels.of("northwind-archive") == "(not in the config, 1)" and labels.of(None) == "(any source)"
    known = setup_report.Redactor()
    known.add("source", config.sources[0].id)
    assert setup_report._Labels(config, known).of(config.sources[0].id) == "<source-1>"
    # A Redactor that knows one word of the id replaces that word only: the rest of the id is still an id.
    half = setup_report.Redactor()
    half.add("folder", "Client", ignore_case=True)
    assert half.redact(config.sources[0].id) == "<folder-1>-alpha"
    assert setup_report._Labels(config, half).of(config.sources[0].id) == "(source 1)"


def test_an_id_that_starts_as_a_folder_name_does_is_one_placeholder(fake_mac: dict[str, Path]) -> None:
    """A folder written with wide separators and a hand-set id that runs them together and adds a word: the
    Redactor's folder value matched the id's front, and every part named the source ``<folder-N>-hr``."""
    cfg = fake_mac["config"]
    folder = fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{ORG}" / "Wingtip - Merger - Docs"
    folder.mkdir()
    assert cli.main(["add-source", str(folder), "--config", str(cfg)]) == 0
    made = load_config(cfg).sources[-1].id  # add-source's own id keeps every separator: it comes out whole
    hand_set = "wingtip-merger-docs-hr"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(f'id = "{made}"', f'id = "{hand_set}"'), "utf-8")
    seed = Seed(cfg)
    assert seed.ids()[-1] == hand_set
    seed.item(hand_set, "payroll.png", state="refused", reason=NO_CONVERTER, page="refused")
    seed.close()
    text, parts = status_parts(fake_mac)
    rows = [ln for ln in parts["Quarantine by reason"].splitlines() if ln.startswith("| <")]
    assert len(rows) == 1 and re.fullmatch(
        r"\| <source-\d+> \| refused \| no converter \| 1 \| 0 \| - \|", rows[0]
    )
    assert re.search(r"^\| <source-\d+> \| no record \| 1 \|", parts["OCR"], flags=re.MULTILINE)
    assert not re.search(r">-?hr\b", text), "no part of the id is left beside a placeholder"


@pytest.mark.parametrize(("on_disk", "typed"), [("NFD", "NFC"), ("NFC", "NFD")])
def test_a_folder_name_is_redacted_in_either_unicode_form(
    fake_mac: dict[str, Path], tmp_path: Path, on_disk: str, typed: str
) -> None:
    """A Finder-made name is NFD on disk and an agent types it NFC, or the reverse (rehearsal 2026-10-10:
    "Café Plans" in clear). Below the listing's depth the folder is registered from sources.toml alone, in
    one form; the other form is the same placeholder."""
    deep = fake_mac["one"] / "Archive" / unicodedata.normalize(on_disk, "Café Plans")
    deep.mkdir(parents=True)
    assert cli.main(["add-source", str(deep), "--config", str(fake_mac["config"])]) == 0
    said = unicodedata.normalize(typed, "Café Plans")
    write_friction(fake_mac, f"Prompt: v10\nF1 | step 1 | clean | 1 | picked {said} | -\n")
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert re.search(r"picked <folder-\d+>", section(text, "Agent friction log"))
    for form in ("NFC", "NFD"):
        assert unicodedata.normalize(form, "Café") not in text, form
    red = setup_report.Redactor()
    red.add("folder", unicodedata.normalize("NFD", "Plans Café"), fuzzy=True)
    red.add("folder", "Plans Café", fuzzy=True)  # the same folder: no second placeholder
    # An accented last letter is one letter in either form, so a longer word stays whole ("Cafés" written NFD
    # was cut to "<folder-1>s" when the edge was judged on the combining mark), and every spelling the pattern
    # takes maps back to the placeholder, an escape between a letter and its accent included.
    line = unicodedata.normalize(typed, "Plans Café, plans-café and Plans Cafés")
    assert red.redact(line) == unicodedata.normalize(typed, "<folder-1>, <folder-1> and Plans Cafés")
    escaped = unicodedata.normalize("NFD", "Plans\\ Café").replace("\u0301", "\\\u0301")
    assert red.redact(escaped) == "<folder-1>"


@pytest.mark.parametrize(
    ("gecos", "said"),
    [
        ("Doe, Jane (Northwind)", "Jane Doe, jane-doe, JaneDoe, doe-jane, DOE_JANE and Northwind"),
        ("Jane Q. Doe", "Jane Doe, jane-doe, JaneDoe, doe-jane, DOE_JANE and jane-q-doe"),
        ("Doe, Jane Q. (Northwind)", "Jane Doe, jane-doe, JaneDoe, doe-jane, DOE_JANE and JaneQDoe"),
        ("Jane Doe, Jr.", "Jane Doe, jane-doe, JaneDoe, doe-jane, DOE_JANE and Doe-Jane"),
        ("Dr. Jane Doe", "Jane Doe, jane-doe, JaneDoe, doe-jane, DOE_JANE and Doe-Jane"),
    ],
    ids=["bracketed", "middle", "comma-middle-bracketed", "suffix", "title"],
)
def test_a_bracketed_tail_or_a_middle_part_does_not_hide_the_first_and_last_name(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch, gecos: str, said: str
) -> None:
    """A directory display name ("Doe, Jane (Northwind)", "Jane Q. Doe"): the names are formed without the
    bracketed part and the middle part, so "jane-doe" and "JaneDoe" are <name>, and a bracketed word is a lone
    name without its brackets (rehearsal review, B13 item N8)."""

    class Entry:
        pw_gecos = gecos

    monkeypatch.setattr(setup_report, "_full_name", _REAL_FULL_NAME)
    monkeypatch.setattr(setup_report.pwd, "getpwuid", lambda _uid: Entry())
    write_install_log(fake_mac)
    write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", f"| {said} agreed |"))
    text, _summary = summary_of(fake_mac)
    friction = section(text, "Agent friction log")
    assert "| <name>, <name>, <name>, <name>, <name> and <name> agreed |" in friction, friction
    assert "jane" not in text.lower() and "Northwind" not in text


def test_a_bracketed_tail_that_is_the_organisation_stays_org(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Doe, Jane (Contoso)" on a Mac whose OneDrive is Contoso's: the bracketed word is registered after the
    organisation, so "contoso-sales" stays <org-N> in all its forms rather than half a lone <name>."""

    class Entry:
        pw_gecos = f"Doe, Jane ({ORG})"

    monkeypatch.setattr(setup_report, "_full_name", _REAL_FULL_NAME)
    monkeypatch.setattr(setup_report.pwd, "getpwuid", lambda _uid: Entry())
    write_install_log(fake_mac)
    write_friction(
        fake_mac, V5_HAPPY.replace("| 1 folder chosen |", f"| {ORG} and {ORG.lower()}-sales agreed |")
    )
    text, _summary = summary_of(fake_mac)
    assert re.search(r"\| <org-\d+> and <org-\d+>-sales agreed \|", section(text, "Agent friction log"))
    assert ORG.lower() not in text.lower()


def test_a_bracketed_part_of_two_letters_stays_a_name(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Jane (JJ) Doe": the bracketed part as written stays a lone name, as before B13 item N8 (which only
    adds values), and the name without it is fuzzy ("jane-doe")."""

    class Entry:
        pw_gecos = "Jane (JJ) Doe"

    monkeypatch.setattr(setup_report, "_full_name", _REAL_FULL_NAME)
    monkeypatch.setattr(setup_report.pwd, "getpwuid", lambda _uid: Entry())
    write_install_log(fake_mac)
    write_friction(fake_mac, V5_HAPPY.replace("| 1 folder chosen |", "| (JJ) and jane-doe agreed |"))
    text, _summary = summary_of(fake_mac)
    assert "| <name> and <name> agreed |" in section(text, "Agent friction log")


@pytest.mark.parametrize(
    ("provider", "org"),
    [
        ("OneDrive-Contoso", "Contoso"),
        ("OneDrive-SharedLibraries-Contoso", "Contoso"),
        ("OneDrive-Personal", None),
        ("Dropbox-Wingtip Partners", "Wingtip Partners"),
        ("Dropbox-Personal", None),
        ("Dropbox-business", None),
        ("Box-Box", None),
        ("Dropbox", None),
        ("GoogleDrive-jane.doe@contoso.com", None),
        ("SharedLibraries-Contoso", "Contoso"),
        ("Nextcloud-mlee@192.168.1.10", "mlee@192.168.1.10"),
    ],
)
def test_org_of_takes_the_part_after_any_providers_app_name(provider: str, org: str | None) -> None:
    """macOS names a CloudStorage folder ``<app>-<domain>``: the domain is the organisation for every
    provider, not only OneDrive, unless it is generic, repeats the app's name or is an email address the email
    pattern takes whole; an ``@`` value that pattern misses is an organisation (B13 N9)."""
    assert setup_report._org_of(provider) == org


def test_a_dropbox_team_folder_is_an_org_and_its_app_name_no_residue(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """A ``Dropbox-<team>`` folder's team is ``<org-N>`` in the Environment line and in install.log's
    ``--source-local`` argument; ``Dropbox`` before it is no residue hit; OneDrive's organisation stays
    ``<org-1>``; a hidden entry registers nothing (B13 N9)."""
    cloud = fake_mac["home"] / "Library" / "CloudStorage"
    launch = cloud / "Dropbox-Wingtip Partners" / "Q3 Launch"
    launch.mkdir(parents=True)
    for name in ("Dropbox-Personal", "Box-Box", ".cache-folder"):
        (cloud / name).mkdir()
    log = fake_mac["setup"] / "install.log"
    quoted = str(launch).replace(" ", "\\ ")
    log.write_text(
        log.read_text(encoding="utf-8")
        + "2026-09-29T11:00:00Z run=20260929T110000Z-4343 start install.sh commit=0123456789ab "
        f"kind=checkout source=- args=--source-local {quoted}\n",
        encoding="utf-8",
    )
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    assert "Wingtip" not in text
    environment = section(text, "Environment")
    assert "Dropbox-<org-" in environment and "OneDrive-<org-1>" in environment
    assert "Box-Box" in environment and "Dropbox-Personal" in environment
    assert re.search(r"Dropbox-<org-\d+>/<folder-\d+>", section(text, "Installer"))
    check = next(ln for ln in section(text, "Redaction").splitlines() if "Residue check:" in ln)
    assert "Dropbox" not in check and "Box" not in check, check
    assert set(re.findall(r"<org-\d+>", text)) == {"<org-1>", "<org-2>"}


@pytest.mark.parametrize(
    ("text", "hits"),
    [
        ("~/Library/CloudStorage/Dropbox/<folder-3>", []),
        ("/Users/<user>/Library/CloudStorage/Box/<folder-1>", []),
        ("~/Library/CloudStorage/GoogleDrive-<name>/<folder-2>", []),
        ("~/Library/CloudStorage/Box-Box/<folder-2>", []),
        ("~/Library/CloudStorage/Egnyte-<org-1>/Shared/<folder-1>", []),
        ("~/Library/CloudStorage/Dropbox-Acme-<org-1>/<folder-1>", ["Acme"]),
        ("Contoso<org-1>", ["Contoso"]),
        ("Dropbox <folder-1>", ["Dropbox"]),
        ("<folder-1> Box", ["Box"]),
    ],
)
def test_an_app_name_is_no_residue_only_as_the_provider_folder(text: str, hits: list[str]) -> None:
    """B13 round 14 Q14, Q37: ``~/Library/CloudStorage/Dropbox/<folder-3>`` flagged ``Dropbox``, the app's own
    folder name, since only a provider folder that names an organisation was skipped. An app name inside the
    provider folder right after ``CloudStorage/`` is no hit; any other word there is, and so is an app name
    anywhere else (a project's leftover second word)."""
    assert setup_report.residue(text) == hits


def test_a_personal_dropbox_folder_leaves_no_residue_in_the_report(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 14 Q14, Q37: a report from a Mac with a personal Dropbox folder said "Residue check: 2
    capitalized or joined word(s) ... (Installer: Dropbox; Doctor: Dropbox); check them"."""
    gamma = fake_mac["home"] / "Library" / "CloudStorage" / "Dropbox" / "Project Gamma"
    gamma.mkdir(parents=True)
    assert cli.main(["add-source", str(gamma), "--config", str(fake_mac["config"])]) == 0
    gamma.rmdir()  # its doctor FAIL prints the path
    log = fake_mac["setup"] / "install.log"
    quoted = str(gamma).replace(" ", "\\ ")
    log.write_text(
        log.read_text(encoding="utf-8")
        + "2026-09-29T11:00:00Z run=20260929T110000Z-4343 start install.sh commit=0123456789ab "
        f"kind=checkout source=- args=--source-local {quoted}\n",
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "Gamma" not in text and re.search(r"CloudStorage/Dropbox/<folder-\d+>", text), text
    check = next(ln for ln in section(text, "Redaction").splitlines() if "Residue check:" in ln)
    assert check.startswith("Residue check: no capitalized or joined word"), check


def test_the_reports_git_lookup_is_asked_once_within_its_time_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 14 Q53: ``_git_ro_err`` looked git up (``xcrun --find git``, up to 10 s) before it read the
    report's budget, and a failed lookup was not kept, so each call asked again: four calls on a 2 s budget
    took 12 s with an xcrun that fails after 3 s. The budget is read first, the lookup is bounded by it and
    kept for the report, its ``/usr/bin/git`` fallback too."""
    asked = tmp_path / "asked"
    xcrun = tmp_path / "xcrun"
    xcrun.write_text(f"#!/bin/sh\necho x >> '{asked}'\nsleep 3\nexit 1\n", encoding="utf-8")
    xcrun.chmod(0o755)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(gitops, "_usr_bin_git_usable", lambda: True)
    monkeypatch.setattr(gitops, "_XCRUN", str(xcrun))
    monkeypatch.setattr(gitops, "_shim_git", None)
    r = setup_report._Run(None, setup_report.ReportHooks(), 2.0)
    t0 = time.monotonic()
    codes = [setup_report._git_ro_err(r, tmp_path, "rev-parse", "HEAD")[0] for _ in range(4)]
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0 + setup_report._RESERVE_S, (elapsed, codes)
    assert asked.read_text(encoding="utf-8").count("x") == 1, "xcrun was asked again"
    assert (
        r.git == Path("/usr/bin/git") and gitops._shim_git is None
    )  # the daemon's memo keeps successes only


def test_recount_counts_numbered_placeholders_in_the_text_and_leaves_the_rest() -> None:
    """A numbered kind counts as often as the text shows its placeholders, and one the text never shows drops
    out; an unnumbered kind keeps its replacement count; a disabled Redactor is unchanged (B13 N31)."""
    red = setup_report.Redactor()
    red.add("source", "northwind-ledger")
    red.add("folder", "Client Alpha", fuzzy=True)
    red.add("home", "/Users/jdoe")
    assert red.redact("/Users/jdoe northwind-ledger Client Alpha") == "~ <source-1> <folder-1>"
    for _ in range(2):  # the same text gives the same count
        red.recount("~ <source-1> <source-1> <source-10> agent text")
        assert red.counts["source"] == 2 and "folder" not in red.counts and red.counts["home"] == 1
        assert red.values == 2 and red.kinds_used() == ["home", "source"]
    off = setup_report.Redactor(enabled=False)
    off.recount("<source-1>")
    assert off.total == 0 and off.values == 0


def test_the_redaction_count_is_the_placeholders_the_report_shows(fake_mac: dict[str, Path]) -> None:
    """A source's label is replaced once and shown in every row for it, and a friction item is redacted
    before it is cut short: the count of a numbered kind is what the report shows, in the Summary's line and
    the Redaction section alike (B13 N31)."""
    cfg = fake_mac["config"]
    made = load_config(cfg).sources[0].id
    hand_set = "northwind-ledger"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(f'id = "{made}"', f'id = "{hand_set}"'), "utf-8")
    seed = Seed(cfg)
    for name in ("one.png", "two.png", "three.png"):
        seed.item(hand_set, name, state="refused", reason=NO_CONVERTER, page="refused")
    seed.close()
    what = "the folder listing took a while " * 8 + " ".join([str(fake_mac["one"])] * 3)
    friction = write_friction(
        fake_mac,
        V5_HAPPY.replace("| step 3 | end | install.sh exited 0 | -", f"| step 3 | error | {what} | -"),
    )
    text, red = setup_report.build_report(cfg, hooks=setup_report.ReportHooks(), friction_path=friction)
    main = text.split("\n## Redaction\n", 1)[0]
    numbered = [k for k in red.kinds_used() if k not in setup_report._UNNUMBERED]
    assert "source" in numbered and red.counts["source"] >= 3
    for kind in numbered:
        assert red.counts[kind] == len(re.findall(rf"<{kind}-\d+>", main)), kind
    said = f"{setup_report._plural(red.total, 'replacement')} of {setup_report._plural(red.values, 'value')}"
    assert f"- redaction: {said}" in section(text, "Summary")
    assert f"\n{said} (" in section(text, "Redaction")
    assert re.search(  # B13 round 6 G3: two sentences, each with its verb
        r"\. Numbered placeholders are counted each time this report shows one\. "
        r"The unnumbered kinds \([^)]*\) are counted each time a value was replaced, even in a line that "
        r"was cut short later, because the "
        r"report also writes ~/ paths and template fields of its own, and those are not replacements\.",
        section(text, "Redaction"),
    )


N32_INSTALL = (
    "2026-10-10T01:56:00Z run=20261010T015600Z-77 start install.sh compat=6 commit=0123456789ab "
    "kind=checkout source=- launchd=simulated args=\n"
    "2026-10-10T01:56:00Z run=20261010T015600Z-77 step=agentsync seconds=48 rc=0 result=done\n"
    "2026-10-10T01:56:49Z run=20261010T015600Z-77 step=helpers seconds=702 rc=0 result=done\n"
    "2026-10-10T02:08:42Z run=20261010T015600Z-77 step=first-sync seconds=2 rc=0 result=done "
    "note=converted-0-deferred-0{reread}\n"
    "2026-10-10T02:08:44Z run=20261010T015600Z-77 end rc=0 seconds=764\n"
)


def _n32_runs(seed: Seed, rows: list[tuple[int, str, int]]) -> None:
    for run_id, at, again in rows:
        seed.m._db.execute(
            "INSERT INTO runs (run_id, mode, started_at, finished_at, status, host, pid, counts_json) "
            "VALUES (?, 'poll', ?, ?, 'ok', 'Contoso-Mac', 1, ?)",
            (
                run_id,
                f"2026-10-10T{at}Z",
                f"2026-10-10T{at}Z",
                json.dumps({"reread": again, "reread_for": 1}),
            ),
        )


def _n32_line(fake_mac: dict[str, Path]) -> str:
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- first sync: ")]
    return line


def test_the_first_sync_line_counts_what_other_runs_read_again_since_the_install(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The v10 report's clause read ``converted 0, deferred 0 online-only`` while the OCR part showed a run
    that read 17 files again: a background poll during the helpers step, not the installer's sync. The
    first-sync line says what runs other than the installer's sync read again since the install run began
    (a run before it is not counted), with the newest run's id, and that who started them is not recorded."""
    (fake_mac["setup"] / "install.log").write_text(N32_INSTALL.format(reread=""), encoding="utf-8")
    seed = Seed(fake_mac["config"])
    _n32_runs(
        seed,
        [
            (1732, "01:50:00", 5),  # before the install run: not counted
            (1733, "02:01:51", 17),  # the background poll during the helpers step
            (1734, "02:07:02", 0),
            (1735, "02:08:43", 4),  # the installer's own sync (inside its first-sync step): not counted here
            (1736, "02:15:00", 3),  # the setup prompt's sync before the report
        ],
    )
    seed.close()
    line = _n32_line(fake_mac)
    assert line.startswith("- first sync: done in 2s (install.log) · converted 0, deferred 0 online-"), line
    assert line.endswith(
        " · since this install run began, 20 reads again by 2 other runs (the newest is run 1736; the "
        "background job's runs and syncs typed in a terminal are recorded alike: see the OCR part)"
    ), line

    # Only the installer's sync and runs before the install read again: the line is as before.
    seed = Seed(fake_mac["config"])
    seed.m._db.execute("DELETE FROM runs WHERE run_id IN (1733, 1736)")
    seed.close()
    assert "since this install run began" not in _n32_line(fake_mac)

    # A skipped first-sync step ran no sync, so a run that started inside it is another's.
    skipped = N32_INSTALL.replace(
        "seconds=2 rc=0 result=done note=converted-0-deferred-0{reread}",
        "seconds=0 rc=0 result=skipped note=status-failed",
    )
    (fake_mac["setup"] / "install.log").write_text(skipped, encoding="utf-8")
    assert "began, 4 reads again by 1 other run (the newest is run 1735;" in _n32_line(fake_mac)

    # More runs than the report reads, all since the install began: the count is a floor.
    monkeypatch.setattr(setup_report, "_RUNS_READ", 1)
    seed = Seed(fake_mac["config"])
    _n32_runs(seed, [(1740, "02:30:00", 6)])
    seed.close()
    assert "began, at least 6 reads again by 1 other run (the newest is run 1740;" in _n32_line(fake_mac)


def test_an_all_zero_re_read_table_is_one_line(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finished re-read printed one row per source of ``yes`` and six zeros (field reports 2026-10-08 and
    2026-10-10: 16 such rows). The table is one line then, which names the sources its rows named, and comes
    back the moment any row says something."""
    cfg = fake_mac["config"]
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("AGENTSYNC_OCR")
    write_fake(ocr._helper_path(load_config(cfg).cache_dir))
    seed = Seed(cfg)
    ids = seed.ids()
    now = "c0ffee11" + "0" * 56
    for source in ids:
        seed.m.set_meta("reread:" + source, json.dumps([{"done": True, "for": now, "tried": []}]))
    seed.run(6, {"converted": 0, "ocr_ms": 900, "ocr_budget_s": 180, "reread_for": int(now[:8], 16)})
    seed.close()
    finished = status_parts(fake_mac)[1]["OCR"]
    assert reread_rows(finished) == {} and "| source | scan finished |" not in finished
    # One count above 0 brings every row back, so the row says which source.
    seed = Seed(cfg)
    seed.m.set_meta(
        "reread:" + ids[1], json.dumps([{"done": True, "for": now, "tried": [], "failed": {"x": 1}}])
    )
    seed.close()
    part = status_parts(fake_mac)[1]["OCR"]
    names = list(reread_rows(part))
    assert len(names) == len(ids) and "Every count is 0, so the table is left out" not in part
    # one sentence, the same facts, every branch kept (B13 round 7 H5, round 8 J3, J15); the kinds of file a
    # re-read looks at are the table's lead only, so the line names no early OCR build (B13 round 9 K5, K16)
    line = (
        f"- re-read, per source: finished in all {len(ids)} local or inbox sources with a record "
        f"({', '.join(names)}); nothing is left to read again, none failed or was given up, and no read was "
        "cut off when a cycle died (measured against run 6, the newest run that records what its re-read "
        "looked for; it had an engine).\n"
    )
    assert line in finished, finished
    assert "early OCR build" not in next(ln for ln in finished.splitlines() if "re-read, per source" in ln)
    assert "or not, none failed once" not in finished and "or not," not in finished
    # The newest run that says what its re-read looked for had no engine: the line never claims one.
    seed = Seed(cfg)
    seed.m.set_meta("reread:" + ids[1], json.dumps([{"done": True, "for": now, "tried": []}]))
    seed.run(7, {"converted": 0, "reread_for": int(now[:8], 16)})
    seed.close()
    noengine = status_parts(fake_mac)[1]["OCR"]
    assert (
        "when a cycle died (measured against run 7, the newest run that records what its re-read looked for; "
        "it had no engine, so a finished scan says nothing of OCR).\n" in noengine
    ), noengine
    assert "it had an engine" not in noengine
    # A scan that is not finished keeps the table too, with every count 0.
    seed = Seed(cfg)
    seed.m.set_meta("reread:" + ids[1], json.dumps([{"done": False, "for": now, "tried": []}]))
    seed.close()
    part = status_parts(fake_mac)[1]["OCR"]
    assert reread_rows(part)[names[1]] == ["no", "0", "0", "0", "0", "0", "no"]
    assert "Every count is 0, so the table is left out" not in part


def test_an_all_zero_re_read_line_keeps_each_caveat_in_a_sentence_of_its_own(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 9 K5, K16: the all-zero line is one sentence, and what makes `finished` say less is a short
    sentence after it: no run that says what its re-read looked for, a helper that is not ready, a label
    rule. None of them lists the kinds of file a re-read looks at."""
    cfg = fake_mac["config"]
    seed = Seed(cfg)
    ids = seed.ids()
    now = "c0ffee11" + "0" * 56
    for source in ids:
        seed.m.set_meta("reread:" + source, json.dumps([{"done": True, "for": now, "tried": []}]))
    seed.run(6, {"converted": 0})  # no run says what its re-read looked for
    seed.close()
    cfg.write_text(cfg.read_text(encoding="utf-8") + LABEL_RULE_TOML, encoding="utf-8")
    part = status_parts(fake_mac)[1]["OCR"]
    line = next(ln for ln in part.splitlines() if ln.startswith("- re-read, per source"))
    assert line.startswith(
        f"- re-read, per source: finished in all {len(ids)} local or inbox sources with a record ("
    ), line
    assert line.count(".") == 4, line  # the sentence, then three caveats
    assert (
        "and no read was cut off when a cycle died. The last run does not say what its re-read looked "
        "for (a run of this build that reaches a local source does), so `finished` is as last recorded, by "
        "whichever build wrote the record. The helper" in line
    ), line
    assert " The helper is not ready (its line above), so a cycle has no engine. A label rule" in line, line
    assert line.endswith(" A label rule is on, so no image is read or counted."), line
    assert "measured against" not in line and "would look at" not in line and "early OCR build" not in line


# ---- §16.39 N16: the Summary's doctor line names the person's own waits -----------------------------------

_INCOMPLETE = "[warn] heartbeat.project-x — enumeration incomplete for 4 consecutive passes; deletions held"


def _yours_step() -> loop.NextStep:
    """Built per test, so a build without ``NextStep.checks`` fails these tests and not the file."""
    return loop.NextStep(
        "nothing to do: session done",
        waits=("1 empty cloud folder(s) keep the listing of project-x incomplete (deletions held; ...)",),
        checks=(
            "governance.purge_queue",
            "heartbeat.project-x",
            "heartbeat.project-y",
            "heartbeat.project-z",
            "heartbeat.project-w",
        ),
    )


@pytest.mark.parametrize(
    "tail",
    [
        " (fix: each WAITING ON YOU line about project-x says what stops the listing and what to do: "
        "another sync does not clear it)",
        f" ({doctor._LISTING_YOURS_NOTE})",
        f" ({doctor._LISTING_SKIPPED_NOTE})",
        " (as of its last walk: the Summary's Loop line says whether a WAITING ON YOU line still names it)",
        # an installed agentsync from before B13 round 15 S11, S28
        " (as of its last walk: if this run's sync runs, the end of this run says whether it cleared it)",
        " (as of its last walk: a [FAIL] line stops this run's sync, so it stays held until a sync after "
        "that fix)",
        # an installed agentsync from before B13 round 14 Q35
        " (as of its last walk: the end of this run says whether this run's sync cleared it)",
        # an installed agentsync from before B13 round 13 O36
        " (held at its last walk: the end of this run says whether this run's sync cleared it)",
        " (held at its last walk: the Summary's Loop line says whether a WAITING ON YOU line still holds it)",
    ],
    ids=[
        "by-hand",
        "install.sh",
        "install.sh-fail",
        "report",
        "install.sh-before-S11",
        "install.sh-fail-before-S28",
        "install.sh-before-Q35",
        "install.sh-before-O36",
        "report-before-O36",
    ],
)
def test_a_wait_the_loop_names_is_yours_and_a_stopped_sync_stays_unexpected(
    fake_mac: dict[str, Path], tail: str
) -> None:
    """A warn whose cause a WAITING ON YOU line names is the person's ("yours"), whichever tail doctor gave
    it. The same heartbeat check warns for a last success past 3 x cadence and for no successful pass, which a
    stopped background sync also gives: those stay unexpected though the id is in ``checks``, as do a FAIL
    and every launchd.* warn (C11)."""
    lines = [
        "[ok  ] python — fine",
        _INCOMPLETE + tail,
        "[warn] governance.purge_queue — 1 purge request(s) queued (confirmed upstream deletions / label "
        "escalations) (fix: agentsync purge --queue)",
        "[warn] heartbeat.project-y — last success 2026-10-05T00:00:00Z (327654s ago) > 3 x cadence (2700s) "
        "(fix: agentsync sync -v)",
        "[warn] heartbeat.project-z — no successful pass yet (fix: agentsync sync -v)",
        "[FAIL] heartbeat.project-w — auth: REAUTH_REQUIRED; no Graph cursor advances until you sign in "
        "again (fix: agentsync login)",
        "[warn] launchd.poll — every run exits 64 (PROGRAM_REFUSED) (background sync is yours to refresh, "
        "not a setup step)",
    ]
    hooks = setup_report.ReportHooks(doctor=lambda config: lines, loop_next=lambda config: _yours_step())
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    assert (
        "- doctor: 1 FAIL, 5 warn (7 checks) · yours 2: heartbeat.project-x, governance.purge_queue (see "
        "WAITING ON YOU) · unexpected 4: heartbeat.project-y warn, heartbeat.project-z warn, "
        "heartbeat.project-w FAIL, launchd.poll warn"
    ) in section(text, "Summary")


def test_the_report_doctor_never_points_at_the_end_of_its_run(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 5 F12, F45: install.sh's doctor note points at the WAITING ON YOU lines at the end of its
    run; the report has no such end (``--report-only`` prints no wait), so its Doctor section gets its own
    note, which points at the Summary's Loop line and promises no line. Other notes pass through."""
    held = doctor.CheckResult(
        "heartbeat.project-x",
        False,
        "enumeration incomplete for 4 consecutive passes; deletions held",
        doctor.Severity.WARN,
        note=doctor._LISTING_YOURS_NOTE,
    )
    agent = doctor.CheckResult(
        "launchd.poll", False, "every run exits 64", doctor.Severity.WARN, note=doctor._AGENT_YOURS_NOTE
    )
    monkeypatch.setattr(cli, "_status_checks", lambda config, offline: [held, agent])
    lines = [" ".join(ln.split()) for ln in cli._report_hooks().doctor(load_config(fake_mac["config"]))]
    assert lines == [
        "[warn] heartbeat.project-x — enumeration incomplete for 4 consecutive passes; deletions held "
        f"({doctor.LISTING_YOURS_REPORT_NOTE})",
        f"[warn] launchd.poll — every run exits 64 ({doctor._AGENT_YOURS_NOTE})",
    ]
    assert not any("end of this run" in line for line in lines)
    assert "(" not in doctor.LISTING_YOURS_REPORT_NOTE, "the report's fix parser strips one level"


def test_the_doctor_line_names_the_checks_install_shs_doctor_printed_that_this_one_does_not(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 5 F39: step 2's doctor warned on a heartbeat, the report's own doctor (run later) did not,
    and nothing tied the two. The line names install.sh's pre-sync warns of the attempt's last install run
    that the report's doctor does not report, says when each doctor ran, and claims no cause. An older
    run's warn, a warn after the sync line and a warn the report's doctor still gives are not named."""
    old, new = "20261001T100000Z-11", "20261009T100100Z-22"
    write_install_log(fake_mac, start="2026-10-01T10:00:00Z", run=old)
    write_install_log(fake_mac, start="2026-10-09T10:01:00Z", run=new, append=True)
    first = V6_HAPPY.replace("2026-09-29T", "2026-10-01T")
    second = V10_HAPPY.replace("2026-09-29T09:58:00Z", "2026-10-09T09:59:00Z").replace(
        "2026-09-29T10:01:10Z", "2026-10-09T10:02:00Z"
    )
    write_friction(fake_mac, first + second)
    out = [
        f"# run={old} 2026-10-01T10:00:00Z install.sh --source-local x",
        "[warn] heartbeat.project-q — no successful pass yet (fix: ~/.local/bin/agentsync sync -v)",
        "NEXT: done",
        f"# run={new} 2026-10-09T10:01:00Z install.sh --source-local x",
        _INCOMPLETE + " (held at its last walk: the end of this run says whether this run's sync cleared it)",
        "[warn] heartbeat.project-z — no successful pass yet (fix: ~/.local/bin/agentsync sync -v)",
        "[warn] launchd.poll — com.agentsync.poll is not installed (installed by the agent step below)",
        "first sync: converted 1, deferred 0 online-only",
        "[warn] heartbeat.project-y — no successful pass yet (fix: ~/.local/bin/agentsync sync -v)",
        "NEXT: done",
    ]
    (fake_mac["setup"] / "install.out").write_text("\n".join(out) + "\n", encoding="utf-8")
    still = ["[ok  ] python — fine", "[warn] heartbeat.project-z — no successful pass yet (fix: x)"]
    hooks = setup_report.ReportHooks(doctor=lambda config: still)
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- doctor: ")]
    assert line.startswith("- doctor: 0 FAIL, 1 warn (2 checks, run when this report was written) · "), line
    assert line.endswith(
        "· install.sh's doctor before its first sync (run 20261009T100100Z): 1 warn not in this one: "
        "heartbeat.project-x"
    ), line
    assert "project-q" not in line and "project-y" not in line and line.count("project-z") == 1
    assert "cleared" not in line and "launchd.poll" not in line, "the run's agent step installs it later"
    # No first sync in that run: "before its first sync" would mean nothing, so nothing is named
    write_install_log(fake_mac, start="2026-10-01T10:00:00Z", run=old)
    write_install_log(
        fake_mac,
        start="2026-10-09T10:01:00Z",
        run=new,
        append=True,
        first_sync="seconds=0 rc=0 result=skipped",
    )
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    assert "install.sh's doctor" not in section(text, "Summary")
    write_install_log(fake_mac, start="2026-10-01T10:00:00Z", run=old)
    write_install_log(fake_mac, start="2026-10-09T10:01:00Z", run=new, append=True)
    still.append(_INCOMPLETE)  # the report's doctor gives it too: nothing more to say
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- doctor: ")]
    assert "install.sh's doctor" not in line and "run when this report" not in line, line


def test_no_yours_group_without_the_loops_checks(fake_mac: dict[str, Path]) -> None:
    """A hook that gives lines (an older CLI), fails or times out names no check: the warn is unexpected."""

    def slow(config: Config) -> list[str]:
        time.sleep(setup_report._LOOP_NEXT_S + 1)
        return []

    def crash(config: Config) -> list[str]:
        raise OSError("disk gone")

    for hook in (lambda config: _yours_step().lines(), crash, slow):
        hooks = setup_report.ReportHooks(doctor=lambda config: [_INCOMPLETE], loop_next=hook)
        text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
        summary = section(text, "Summary")
        assert "yours" not in summary
        assert "unexpected 1: heartbeat.project-x warn" in summary


def test_no_yours_group_when_the_loop_line_is_not_read(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The checks are kept only once the waits are on the Loop line: a Loop line that fails after the hook
    answered shows no wait, so the doctor line must not point at one."""

    def boom(line: str) -> str:
        raise ValueError("no text")

    monkeypatch.setattr(setup_report, "loop_next_text", boom)
    hooks = setup_report.ReportHooks(
        doctor=lambda config: [_INCOMPLETE], loop_next=lambda config: _yours_step()
    )
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    summary = section(text, "Summary")
    assert "- Loop: not read (an unexpected error: ValueError)" in summary
    assert "yours" not in summary
    assert "unexpected 1: heartbeat.project-x warn" in summary


@pytest.mark.usefixtures("roomy_budget")
@pytest.mark.parametrize("hint", ["", "1"], ids=["by-hand", "install.sh"])
def test_an_empty_cloud_folders_heartbeat_is_yours_through_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hint: str
) -> None:
    """The real doctor text, the real loop and the CLI's hook agree (a drift guard for the detail the report
    keys on): three incomplete walks of a folder with an empty cloud folder give doctor's incomplete-listing
    warn, the loop's wait, and a Summary that calls it the person's, by hand and under install.sh alike."""
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Project Alpha"
    (root / "Archive 2019").mkdir(parents=True)
    (root / "note.txt").write_text("The purchase order is approved.\n", encoding="utf-8")
    cfg = Path.home() / "agent-context" / "sources.toml"
    assert cli.main(["add-source", str(root), "--config", str(cfg)]) == 0
    for _ in range(3):
        cli.main(["sync", "--config", str(cfg)])
    monkeypatch.setenv(loop.NO_NEXT_HINT_ENV, hint)
    _rc, text, _ = report(tmp_path, cfg)
    summary = section(text, "Summary")
    [line] = [ln for ln in summary.splitlines() if ln.startswith("- doctor: ")]
    assert "· yours 1: heartbeat.<folder-1> (see WAITING ON YOU) ·" in line
    assert "heartbeat.<folder-1> warn" not in line
    assert "WAITING ON YOU: 1 empty cloud folder keeps the listing of <folder-1> incomplete" in summary


# ---- B13 round 6 lane RA -----------------------------------------------------------------------------------


def _doctor_head(text: str) -> str:
    return section(text, "Doctor").strip().splitlines()[0]


@pytest.mark.usefixtures("roomy_budget")
def test_the_doctor_line_names_only_the_checks_status_would_run(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 6 G1: the Doctor line said `status` runs the Graph probe and the TCC canary on a local-only
    Mac with no background sync, where status runs neither. It names only what status's own gates run."""
    monkeypatch.delenv(doctor.AGENT_STEP_PENDING_ENV, raising=False)
    cfg = fake_mac["config"]
    hooks = setup_report.ReportHooks(doctor=lambda config: [])
    head = _doctor_head(setup_report.build_report(cfg, hooks=hooks)[0])
    assert "runs them" not in head and "runs it" not in head and "TCC canary" not in head, head
    assert head == "0 checks: 0 ok, 0 info, 0 warn, 0 FAIL. Only the lines that are not ok:", head

    monkeypatch.setenv(doctor.AGENT_STEP_PENDING_ENV, "1")  # background sync wanted, a protected source
    assert launchd.canary_paths(load_config(cfg))
    head = _doctor_head(setup_report.build_report(cfg, hooks=hooks)[0])
    assert "(run without the TCC canary (when it is due): `~/.local/bin/agentsync status` runs it)" in head
    assert "Graph" not in head

    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + '\n[graph]\nclient_id = "00000000-0000-0000-0000-000000000000"\n'
        + '\n[[source]]\nid = "tailspin-drive"\nkind = "graph_drive"\ndrive_id = "me"\n',
        encoding="utf-8",
    )
    head = _doctor_head(setup_report.build_report(cfg, hooks=hooks)[0])
    assert (
        "(run without the Graph network probe and the TCC canary (when it is due): "
        "`~/.local/bin/agentsync status` runs them)" in head
    ), head
    monkeypatch.delenv(doctor.AGENT_STEP_PENDING_ENV)
    head = _doctor_head(setup_report.build_report(cfg, hooks=hooks)[0])
    assert "(run without the Graph network probe: `~/.local/bin/agentsync status` runs it)" in head, head


def test_the_friction_section_names_the_summarys_f_ids_only_when_it_cites_one(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 6 G10: "the F<n> ids in the Summary are these numbers" was printed under a Summary that cites
    none. The line numbers stay (the attempt lines cite them); the Summary clause only when it has an F id."""
    v6_install_log(fake_mac)
    write_friction(fake_mac, V7_HAPPY)
    text, summary = summary_of(fake_mac)
    assert re.search(r"\bF\d+\b", summary) is None, summary
    fr = section(text, "Agent friction log")
    assert "\nEach line is shown with its line number.\n" in fr, fr
    assert "F<n> ids" not in text and "\x00" not in text
    late = "2026-09-29T10:00:30Z | step 2 | deviation | ran the install twice | -\n"
    write_friction(
        fake_mac,
        V7_HAPPY.replace(
            "Agent: Claude Code, claude-opus-5-5\n", f"Agent: Claude Code, claude-opus-5-5\n{late}"
        ),
    )
    text, summary = summary_of(fake_mac)
    assert re.search(r"\bF\d+\b", summary) is not None, summary
    assert "Each line is shown with its line number: the F<n> ids in the Summary are these numbers." in text


def test_a_stored_as_note_whose_two_forms_redact_alike_is_shown_once() -> None:
    """B13 round 6 G41: add-source notes `(<typed>, stored as <path>)` when the two differ, as /tmp and
    /private/tmp do; both are the home folder, so the report read `(~/agent-context/inbox, stored as
    ~/agent-context/inbox)`. A note whose forms differ after redaction keeps its parenthesis."""
    red = setup_report.Redactor()
    red.add("home", "/tmp/contoso-rehearsal/home3")
    red.add("home", "/private/tmp/contoso-rehearsal/home3")
    red.add("org", "Contoso", fuzzy=True)
    raw = "/tmp/contoso-rehearsal/home3"
    stored = "/private/tmp/contoso-rehearsal/home3"
    lines = [
        f"added inbox source 'inbox' ({raw}/agent-context/inbox, stored as {stored}/agent-context/inbox): "
        "drop files you save by hand here",
        f"added source 'plans' to {stored}/agent-context/sources.toml ({raw}/Library/CloudStorage/"
        f"OneDrive-Contoso/Plans, stored as {stored}/Library/CloudStorage/OneDrive-Contoso/Plans):",
        f"added source 'plans' to {stored}/agent-context/sources.toml ({raw}/OneDrive/Plans, stored as "
        f"{stored}/Library/CloudStorage/OneDrive-Contoso/Plans):",
    ]
    out = setup_report._redact_lines(red, lines).splitlines()
    assert out[0] == "added inbox source 'inbox' (~/agent-context/inbox): drop files you save by hand here"
    assert out[1] == (
        "added source 'plans' to ~/agent-context/sources.toml "
        "(~/Library/CloudStorage/OneDrive-<org-1>/Plans):"
    )
    assert out[2] == (
        "added source 'plans' to ~/agent-context/sources.toml (~/OneDrive/Plans, stored as "
        "~/Library/CloudStorage/OneDrive-<org-1>/Plans):"
    ), "two forms that still differ keep the note"


def test_an_attempt_line_alone_keeps_a_stated_step_1_error_in_its_attempt() -> None:
    """B13 round 6 G51: an ``Attempt:`` line alone is no header, so a step 1 error that states its words right
    after it stays in that attempt (install.sh's awk reads it so too); a Prompt: line makes it another
    session's. The clause that tested the Attempt: line was dead and is gone; this pins the behaviour.  B13
    round 10 L26: an attempt with no header reads a stated step 1 error's words wherever the error sits, so
    this one states them too (it stays in its attempt as before)."""
    err = "2026-10-06T10:01:00Z | step 1 | error | prompt v12, Contoso Bot: git pull failed | -\n"
    [only] = setup_report.parse_friction(f"Attempt: 2026-10-06T10:00:00Z\n{err}").attempts
    assert only.stated == {"Prompt": "v12", "Agent": "Contoso Bot"}
    assert only.events[0].what == "git pull failed"
    headed = f"Attempt: 2026-10-06T10:00:00Z\nPrompt: v12\n{err}"
    assert len(setup_report.parse_friction(headed).attempts) == 2


def keep_local_work(home: Path, setup: Path) -> Path:
    """Two keeps of step 1 (two local-work branches, one loose and one packed) whose second patch overwrote
    the first's, as two keeps of a commit with one subject do (B13 round 9 rehearsal D4)."""
    git = home / "src" / "agent-context-sync" / ".git"
    (git / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (git / "refs" / "heads" / "local-work-20261014-005444").write_text("a" * 40 + "\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{'b' * 40} refs/heads/local-work-20261014-010203\n{'c' * 40} refs/heads/main\n",
        encoding="utf-8",
    )
    patch = setup / "local-work" / "0001-Northwind-pricing-notes.patch"
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(
        "From aaaa Mon Sep 17 00:00:00 2001\nFrom: Jane Fabrikam <jane@fabrikam.example>\n"
        "Subject: [PATCH] Northwind pricing notes\n\n---\n",
        encoding="utf-8",
    )
    os.utime(patch, (1791950084, 1791950084))
    return patch


def test_the_run_metadata_says_step_1_kept_local_work_in_counts_only(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 9 K30: step 1's keep command moved local work aside (a local-work branch and its patches),
    and only the private bring-back.md said so: the public report read "fully one command". Run metadata has
    one count-only line; no patch name, subject, author or content. A Mac with no keep has none.  B13 round
    10 L8, L25, L40, L58 (one defect, built once): the line said "the patches are in bring-back.md section 3
    only" when section 3 listed each by hash (an earlier report carried it) or held none; it counted a nested
    patch section 3 never reads; and a keep with nothing to keep (a branch at main's own commit, no patch)
    read as "local work kept"."""
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "local work kept" not in text
    patch = keep_local_work(fake_mac["home"], fake_mac["setup"])
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    metadata = section(text, "Summary").split(setup_report.RUN_METADATA_HEADING, 1)[1]
    assert (
        "- local work kept by setup prompt step 1: 1 patch file in the setup folder's local-work, newest "
        f"written 2026-10-14T03:54:44Z ({setup_report.LOCAL_WORK_WHERE}); 2 local-work branches in the "
        "checkout\n" in metadata
    ), metadata
    assert "the patches are in" not in text
    for word in ("Northwind", "Fabrikam", "pricing", "0001"):
        assert word not in text, word
    git = fake_mac["home"] / "src" / "agent-context-sync" / ".git"
    # a patch in a subfolder: section 3 reads only the top of local-work, so it is not counted (L8, L25)
    nested = patch.parent / "older" / patch.name
    nested.parent.mkdir()
    patch.rename(nested)
    # B13 round 17 U38: a known count takes no "(es)", and one branch has commits of its own
    neutral = "- local-work branches in the checkout: {0} with commits of {1}, no patch file in the "
    neutral += "setup folder's local-work "
    neutral += "(moved, deleted or never written; those commits are not in bring-back.md, whose section 3 "
    neutral += "says how to send them)"
    assert setup_report._local_work_line() == neutral.format(2, "their own")
    # a branch at main's own commit (packed) or origin/main's (loose) holds no commit of its own (L58)
    (git / "refs" / "heads" / "local-work-20261014-005444").write_text("c" * 40 + "\n", encoding="utf-8")
    one = neutral.format(1, "its own")
    assert setup_report._local_work_line() == one, "the branch at main's sha is not counted"
    (git / "refs" / "remotes" / "origin").mkdir(parents=True)
    (git / "refs" / "remotes" / "origin" / "main").write_text("b" * 40 + "\n", encoding="utf-8")
    assert setup_report._local_work_line() is None, "both branches sit on main or origin/main"
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert "local work kept" not in text and "local-work branch" not in text
    # a loose main overrides its packed line: the branch at the old packed sha has a commit of its own now
    (git / "refs" / "heads" / "main").write_text("d" * 40 + "\n", encoding="utf-8")
    assert setup_report._local_work_line() == neutral.format(1, "its own")
    # an empty local-work folder beside a branch of its own: no "kept", no section 3 (L40)
    nested.unlink()
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert neutral.format(1, "its own") + "\n" in text and "local work kept" not in text
    # B13 round 16 T82: section 3 is named only as where the remedy is, never as holding the patches
    assert text.count("section 3") == text.count("whose section 3 says how to send them") == 1, text


def test_a_rewrite_paths_hits_are_counted_as_proxy_values() -> None:
    """B13 round 9 K10 (a reviewer's finding): the separator put before a rewrite path's placeholder made
    the hit count as kind "other" and as one more value."""
    red = setup_report.Redactor()
    setup_report._add_proxy_host(red, "mirror.northwind.invalid")
    red.add("proxy", "/northwind-team", ignore_case=True, keep_lead=True)
    shown = red.redact("https://mirror.northwind.invalid/northwind-team/x.git and /northwind-team-tools")
    assert shown == "https://<proxy-1>/<proxy-2>/x.git and /<proxy-2>-tools", shown
    assert red.counts == {"proxy": 3} and red._used == {"<proxy-1>", "<proxy-2>"}, (red.counts, red._used)


def test_a_stopped_copys_install_commit_warn_is_expected(fake_mac: dict[str, Path]) -> None:
    """B13 round 8 J18: a copy the installer stopped leaves step 1's pull and no step 2 reinstall, so doctor
    warns install.commit with "fix: re-run install.sh", and the Summary counted it unexpected, while the
    report's own NEXT says to paste the current prompt (whose step 2 reinstalls). On a stopped copy it is
    expected, in the Summary and the Doctor section alike; on any other attempt it stays unexpected, and an
    install.commit that reads ok adds nothing."""
    (fake_mac["setup"] / "install.log").unlink()
    stopped = (
        "Attempt: 2026-09-29T09:58:00Z\nPrompt: v7 or older\nAgent: Contoso Bot\n"
        "2026-09-29T09:58:00Z | step 1 | error | install.sh --log-start: the pasted setup prompt is v7 or "
        "older and this installer is for setup prompt v16: the pasted copy is not the current one; setup "
        "stopped (exit 2) | copy the prompt again from README.md on the main branch\n"
        "2026-09-29T09:58:00Z | end | finished\n"
    )
    warn = (
        "[warn] install.commit — installed from 568024c88207, but the checkout at /src/x is at a350196b9f87 "
        "(fix: re-run install.sh (/src/x/scripts/install.sh))"
    )
    lines = ["[ok  ] python — fine", warn]
    why = "the installer stopped this copy in step 1, before step 2 reinstalls agentsync"
    write_friction(fake_mac, stopped)
    text, summary = summary_of(fake_mac, doctor=lambda config: lines)
    assert f"- doctor: 0 FAIL, 1 warn (2 checks) · expected 1: install.commit ({why}) · unexpected 0" in (
        summary
    ), summary
    doctor_part = section(text, "Doctor")
    assert (
        f"[warn] install.commit — installed from 568024c88207, but the checkout at /src/x is at a350196b9f87 "
        f"(expected: {why})" in doctor_part
    ), doctor_part
    assert "re-run install.sh" not in doctor_part
    # its intro said "nothing for this setup to do" beside a reason that says step 2 has not run, and a NEXT
    # that asks for a fresh copy (B13 round 9 K24)
    assert (
        doctor_part.strip()
        .splitlines()[0]
        .endswith(
            " 1 expected warn ends in (expected: <why>) instead of its fix: nothing for this setup to do "
            "beyond the report's NEXT line (the fresh copy of the prompt reinstalls in its step 2)."
        )
    ), doctor_part
    # the doctor shows install.commit ok: nothing is expected
    ok = ["[ok  ] python — fine", "[info] install.commit — installed from a350196b9f87, the checkout's HEAD"]
    _text, summary = summary_of(fake_mac, doctor=lambda config: ok)
    assert "expected 1" not in summary and "install.commit (" not in summary
    # an attempt the installer did not stop: the same warn is unexpected, with its fix
    write_friction(fake_mac, V11_HAPPY)
    text, summary = summary_of(fake_mac, doctor=lambda config: lines)
    assert "· unexpected 1: install.commit warn" in summary and why not in text
    assert "(fix: re-run install.sh" in section(text, "Doctor")


def test_an_attempt_that_only_stopped_for_the_folder_answer_is_stopped_not_failed(
    fake_mac: dict[str, Path],
) -> None:
    """B13 round 6 G23 (probe P3): an earlier attempt whose only line is the step 1 deviation the prompt asks
    for ("stopped to wait for the folder answer") was "failed at step 1"; that session did what the prompt
    says and may still be waiting. It is "stopped at step 1 (waiting for the folder answer)"; an error, an
    install run or an end line keeps "failed", and the form's option stays step 1's."""
    (fake_mac["setup"] / "install.log").unlink()
    wait = "2026-10-13T00:51:30Z | step 1 | deviation | stopped to wait for the folder answer, as step 1 says"
    wait += " | -\n"
    head = "Attempt: 2026-10-13T00:51:20Z\nPrompt: v14\nAgent: Contoso Bot\n"
    err = "2026-10-13T00:52:20Z | step 1 | error | prompt v14, Claude Code, claude-opus-5-5: git pull failed"
    err += " | -\n"
    write_friction(fake_mac, head + wait + err)
    text, summary = summary_of(fake_mac)
    assert (
        "- attempt: 2 of 2; earlier: attempt 1 stopped at step 1 (waiting for the folder answer)" in summary
    )
    assert summary.strip().startswith("- **outcome: failed at step 1** (computed: no install.sh run; step 1 ")
    fr = section(text, "Agent friction log")
    assert "): stopped at step 1 (waiting for the folder answer); 1 event line: 1 deviation;" in fr, fr

    [only] = setup_report.parse_friction(head + wait).attempts
    stopped = setup_report.compute_outcome(only, ())
    assert (stopped.kind, stopped.step) == ("stopped", 1)
    assert stopped.why == ("no install.sh run and no error; step 1 logged only a deviation (F4)",)
    assert stopped.form_label == "Failed at step 1 (preflight)", "the form has no stopped option"
    # the title says what the Summary says (B13 round 7 H42); a v5 attempt's step 1 has v5's name
    assert stopped.title_label == "Stopped at step 1 (preflight)"
    write_friction(fake_mac, head + wait)  # the latest attempt, read by a direct setup-report
    text, summary = summary_of(fake_mac)
    assert summary.strip().startswith("- **outcome: stopped at step 1 (waiting for the folder answer)**")
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["title"][0].startswith("Setup report: Stopped at step 1 (preflight) · "), link["title"]
    assert link["outcome"] == ["Failed at step 1 (preflight)"]
    # B13 round 8 J36: the open attempt's WARNING told this session, which may still be waiting, to run
    # install.sh --report-only, whose end line turns "stopped" into "failed at step 1". It names no command.
    [warning] = [ln for ln in summary.splitlines() if ln.startswith("- WARNING: attempt 1 ")]
    assert warning == (
        '- WARNING: attempt 1 has no "end | finished" line: it stopped at step 1, where the prompt waits for '
        "the folder answer, and may still be waiting (no report is due then). It goes on once the folder "
        "answer is given, and step 3's last command `~/src/agent-context-sync/scripts/install.sh "
        "--report-only` closes it. Do not run that command before then: its end line would turn this "
        'outcome into "failed at step 1".'
    ), warning
    assert "if the session stopped early, run that command" not in summary
    write_friction(fake_mac, head + wait + "2026-10-13T00:53:00Z | end | finished\n")
    _text, closed = summary_of(fake_mac)
    assert closed.strip().startswith("- **outcome: failed at step 1** ("), "the WARNING's words stay true"
    assert "WARNING" not in closed
    v5 = setup_report.Outcome("stopped", 1, (), 5, "waiting for the folder answer")
    assert v5.title_label == "Stopped at step 1 (preflight and code)", v5.title_label
    other = head + wait.replace(
        "stopped to wait for the folder answer, as step 1 says", "asked which folders"
    )
    [only] = setup_report.parse_friction(other).attempts
    assert setup_report.compute_outcome(only, ()).text == (
        "stopped at step 1 (a deviation, no install run and no error)"
    )
    for log in (  # an end line, an error, a later step: a failure as before
        head + wait + "2026-10-13T00:53:00Z | end | finished\n",
        head + wait + "2026-10-13T00:51:40Z | step 1 | error | the list failed | -\n",
        head + wait + "2026-10-13T00:51:40Z | step 2 | deviation | ran it twice | -\n",
        # B13 round 7 H40: an approval with no deviation, or one at step 2, is no stop for the folder answer
        head + "2026-10-13T00:51:25Z | step 1 | approval | allowed git clone | -\n",
        head + wait + "2026-10-13T00:51:40Z | step 2 | approval | allowed install.sh | -\n",
    ):
        [only] = setup_report.parse_friction(log).attempts
        assert setup_report.compute_outcome(only, ()).kind == "failed", log

    # B13 round 7 H40: step 1 logs the approvals and go-aheads the prompt asks for and still stopped there
    for kind, what in (("approval", "allowed git clone"), ("question", "asked to go ahead")):
        line = f"2026-10-13T00:51:25Z | step 1 | {kind} | {what} | -\n"
        [only] = setup_report.parse_friction(head + line + wait).attempts
        stopped = setup_report.compute_outcome(only, ())
        assert stopped.text == "stopped at step 1 (waiting for the folder answer)", (kind, stopped)
        assert stopped.why == (
            f"no install.sh run and no error; step 1 logged a deviation (F5), 1 {kind} and nothing else",
        ), stopped.why
    [only] = setup_report.parse_friction(head + line + wait + wait).attempts
    assert setup_report.compute_outcome(only, ()).why == (
        "no install.sh run and no error; step 1 logged 2 deviations (F5, F6), 1 question and nothing else",
    )
    # the wait is read from the deviations alone: an approval's "wait" is not the folder answer
    line = "2026-10-13T00:51:25Z | step 1 | approval | allowed the wait for the clone | -\n"
    [only] = setup_report.parse_friction(
        head + line + wait.replace("to wait for the folder answer, as step 1 says", "asked which folders")
    ).attempts
    assert setup_report.compute_outcome(only, ()).text == (
        "stopped at step 1 (a deviation, no install run and no error)"
    )
    # B13 round 8 J44: the docstring said every event is a deviation; it names the kinds a wait may also log
    doc = " ".join((setup_report.compute_outcome.__doc__ or "").split())
    assert "at least one step 1 deviation" in doc and "every event is a step 1 deviation:" not in doc
    assert ":data:`_WAIT_OK_KINDS`" in doc


def test_the_installer_table_shows_the_config_steps_folder_counts() -> None:
    """B13 round 7 H10: ``config | done (add-source)`` for a run that added no folder: the row dropped the
    kept= and added= install.sh logs beside the note. It shows them now; a step without them is as before,
    and install.sh's note (which the first-sync label reads) is unchanged."""

    def row(fields: str) -> str:
        v = dict(field.split("=", 1) for field in fields.split())
        return setup_report._step_table([("config", v)])[-1]

    assert row("seconds=3 rc=0 result=done note=add-source kept=2 added=0") == (
        "| config |       3 | done (add-source: kept 2, added 0) |"
    )
    assert row("seconds=1 rc=0 result=skipped note=exists kept=1 added=0").endswith(
        "| skipped (exists: kept 1, added 0) |"
    )
    assert row("seconds=1 rc=0 result=done note=created kept=0 added=2").endswith(
        "| done (created: kept 0, added 2) |"
    )
    assert row("seconds=3 rc=0 result=done note=add-source") == "| config |       3 | done (add-source) |"
    assert row("seconds=3 rc=0 result=done note=add-source kept=2").endswith("| done (add-source) |")


def test_the_installer_table_shows_what_the_folder_list_read_of_the_config() -> None:
    """B13 round 8 J10, J31: ``list-folders | done (listed-6)`` for a list that marked two folders and for one
    that could not read the config: the row dropped the synced= and marks= install.sh logs beside the note.
    It shows them now, in the Summary's fixed words (never the log's own marks= text); a list from an
    installer that logged no synced= is as before.  B13 round 10 L22, L45 (2026-10-14): the cell named no
    file, so "could not load it" had nothing to refer to; it names sources.toml, as the Summary's note
    does."""

    def cell(fields: str) -> str:
        v = dict(field.split("=", 1) for field in fields.split())
        return setup_report._step_table([("list-folders", v)])[-1].split("|")[3].strip()

    head = "seconds=2 rc=0 result=done note=listed-6"
    # one colon, after the note: the words join the identifier as a clause (B13 round 9 K23, K36)
    unread = "done (listed 6 folders: could not read which folders sources.toml syncs, because "
    assert cell(f"{head} synced=2") == "done (listed 6 folders: 2 already synced)"
    assert cell(f"{head} synced=0") == "done (listed 6 folders: 0 already synced)"
    assert cell(head) == "done (listed 6 folders)"
    assert cell(f"{head} synced=unreadable marks=no-tool") == f"{unread}no agentsync was installed)"
    assert cell(f"{head} synced=unreadable marks=timeout") == f"{unread}the reader took over 10 s)"
    assert cell(f"{head} synced=unreadable marks=older:is_under") == (
        f"{unread}the installed agentsync was older than the installer and had no is_under)"
    )
    assert cell(f"{head} synced=unreadable marks=failed:ConfigError") == (
        f"{unread}the installed agentsync failed while reading it and raised ConfigError)"
    )
    for fields in ("marks=older:is_under", "marks=failed:ConfigError", "marks=timeout"):
        assert cell(f"{head} synced=unreadable {fields}").count(":") == 1, fields
    assert cell(f"{head} synced=unreadable") == f"{unread}the installed agentsync could not run the reader)"
    # the log's own text is never shown: a marks= detail that is no identifier, a synced= that is no count
    assert cell(f"{head} synced=unreadable marks=failed:Bad-Name!x") == (
        f"{unread}the installed agentsync failed while reading it)"
    )
    assert cell(f"{head} synced=Contoso") == "done (listed 6 folders)"
    assert cell(f"{head} synced=1234567") == "done (listed 6 folders)"
    # B13 round 12 N4: every note install.sh writes for the list is in words, as first-sync's are (M40);
    # install.log keeps its tokens, and a note the report does not know is shown as it is
    failed = "seconds=0 rc=3 result=failed"
    assert cell(f"{failed} note=no-folders") == "failed, rc 3 (no folders are synced yet in CloudStorage)"
    # B13 round 13 O1, O29, O39, O58, O66: the app and the missing count are the line's own; an older line
    # with a count names no app, and one with neither (the exit with no provider folder) is OneDrive's
    assert cell(f"{failed} note=not-signed-in synced=2 missing=1 apps=dropbox") == (
        "failed, rc 3 (Dropbox is not signed in on this Mac: 2 already synced, 1 missing)"
    )
    assert cell(f"{failed} note=not-signed-in apps=OneDrive,GoogleDrive missing=2 synced=3") == (
        "failed, rc 3 (OneDrive and Google Drive are not signed in on this Mac: 3 already synced, 2 missing)"
    )
    assert cell(f"{failed} note=not-signed-in synced=2") == (
        "failed, rc 3 (a sync app is not signed in on this Mac: 2 already synced)"
    )
    assert (
        cell(f"{failed} note=not-signed-in apps=onedrive")
        == "failed, rc 3 (OneDrive is not signed in on this Mac)"
    )
    # a token the map does not hold is dropped, never shown: the log's own text
    assert cell(f"{failed} note=not-signed-in apps=Contoso,other missing=1 synced=1") == (
        "failed, rc 3 (another sync app is not signed in on this Mac: 1 already synced, 1 missing)"
    )
    assert "Contoso" not in cell(f"{failed} note=not-signed-in apps=Contoso missing=1 synced=1")
    # B13 round 13 O9: a folder the list found gone is counted, never named; a gone= no count is dropped
    assert (
        cell(f"{head} synced=2 gone=1") == "done (listed 6 folders: 2 already synced, 1 of them not on disk)"
    )
    assert cell(f"{head} synced=2 gone=Contoso") == "done (listed 6 folders: 2 already synced)"
    assert cell(f"{head} synced=2 gone=0") == "done (listed 6 folders: 2 already synced)"
    assert cell("seconds=0 rc=4 result=failed note=denied") == (
        "failed, rc 4 (this terminal app was denied access to the folders)"
    )
    assert cell("seconds=0 rc=4 result=failed note=tcc-pending") == (
        "failed, rc 4 (macOS is asking whether this terminal app may read the folders)"
    )
    assert cell(f"{failed} note=some-new-note") == "failed, rc 3 (some-new-note)"
    assert "listed-" not in cell(f"{head} synced=2")
    # only the list step reads synced=: another step that logged one is as before
    config = setup_report._step_table([("config", {"result": "done", "note": "created", "synced": "2"})])[-1]
    assert config.split("|")[3].strip() == "done (created)"


def test_the_on_origin_main_line_dates_the_checkouts_last_fetch(tmp_path: Path) -> None:
    """B13 round 6 G27: "on origin/main: yes (as of the checkout's last fetch)" gave no date, and on a run
    whose pull had just failed that fetch was an earlier session's. One lstat of FETCH_HEAD, with real git:
    a clone writes none, a pull that works writes it, and a pull that fails truncates it to 0 bytes (its
    time is then the failed pull's, so the line says it failed)."""
    upstream = tmp_path / "upstream"
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)
    (upstream / "a.txt").write_text("one\n", encoding="utf-8")
    ident = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(["git", "-C", str(upstream), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(upstream), *ident, "commit", "-q", "-m", "one"], check=True)
    repo = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(upstream), str(repo)], check=True)

    def fresh() -> (
        setup_report._Run
    ):  # its 12 s budget covers one call: a loaded machine runs this test longer
        return setup_report._Run(tmp_path / "none.toml", setup_report.ReportHooks(), 12.0)

    assert setup_report._checkout_facts(fresh(), repo).endswith(
        " · on origin/main: yes (no fetch since the clone)"
    )

    subprocess.run(["git", "-C", str(repo), "pull", "-q"], check=True)
    head = repo / ".git" / "FETCH_HEAD"
    assert head.stat().st_size > 0
    os.utime(head, (1_790_000_000, 1_790_000_000))
    facts = setup_report._checkout_facts(fresh(), repo)
    assert facts.endswith(" · on origin/main: yes (as of the checkout's last fetch, 2026-09-21T14:13:20Z)"), (
        facts
    )

    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-url", "origin", str(tmp_path / "gone")], check=True
    )
    pulled = subprocess.run(["git", "-C", str(repo), "pull", "-q"], capture_output=True, check=False)
    assert pulled.returncode != 0 and head.stat().st_size == 0, "git truncates FETCH_HEAD on a failed pull"
    when = setup_report._iso(datetime.fromtimestamp(head.stat().st_mtime, UTC))
    assert setup_report._checkout_facts(fresh(), repo).endswith(
        f" · on origin/main: yes (the checkout's last fetch, {when}, failed; origin/main is from an earlier "
        "fetch)"
    )

    # A linked worktree has a FETCH_HEAD of its own, but shares origin/main: a fetch in the main checkout
    # dates it too, so the newer of the two is read (B13 round 6 G27, a reviewer's finding)
    subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin", str(upstream)], check=True)
    tree = tmp_path / "tree"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(tree), "-b", "side"], check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "-q"], check=True)
    os.utime(head, (1_790_000_000, 1_790_000_000))
    facts = setup_report._checkout_facts(fresh(), tree)
    assert facts.endswith("(as of the checkout's last fetch, 2026-09-21T14:13:20Z)"), facts
    # Both files there, each the newer in turn: only "the newer of the two" passes both (first-found,
    # last-found, common-only and worktree-only each fail one; B13 round 7 H45)
    subprocess.run(["git", "-C", str(tree), "fetch", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "-q"], check=True)
    out = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "--git-path", "FETCH_HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    own = Path(out) if Path(out).is_absolute() else tree / out
    assert own.is_file() and own.resolve() != head.resolve(), "a worktree's fetch writes its own FETCH_HEAD"
    older, newer = (1_780_000_000,) * 2, (1_790_000_000,) * 2
    for own_time, head_time in ((older, newer), (newer, older)):
        os.utime(own, own_time)
        os.utime(head, head_time)
        facts = setup_report._checkout_facts(fresh(), tree)
        assert facts.endswith("(as of the checkout's last fetch, 2026-09-21T14:13:20Z)"), (own_time, facts)


def test_a_context_organisation_is_matched_only_after_an_app_name_and_only_for_a_generic_word() -> None:
    """B13 round 6 G45, G46, G75 (one defect, built once). Context mode matched after any word and a hyphen,
    so an organisation named like a report word was replaced inside the report's own words (``first-<org-1>``,
    ``~/agent-<org-1>``) and could be read back, with no private-send advice; and it covered every report
    word, so a tenant named "Status" printed bare in a sentence. Context mode is now for a short list of
    generic words only, matched after a CloudStorage app's name (a whole word) or before a domain; any other
    report word is matched everywhere and makes the report private only."""
    fixed = (
        "~/agent-context/docs; install.sh --log-start; coding-agent; install-agent; first-sync; "
        "agent-context-sync; --list-folders; setup-report.md; agentsync-docs; inbox-sync; inbox-report"
    )
    for org in ("Sync", "Report", "Docs", "Folder", "Link"):
        assert org.lower() in setup_report._CONTEXT_ORG_WORDS
        red = setup_report.Redactor()
        red.add("org", "Contoso", fuzzy=True)
        red.add("org", org, fuzzy=True)
        assert red.redact(fixed) == fixed, org
        assert red.redact(
            f"OneDrive-{org}; OneDrive-SharedLibraries-{org}; Egnyte-{org}; Box-{org}; OneDrive\\ -\\ {org}; "
            f"OneDrive%20-%20{org}; OneDrive - {org}; {org.lower()}.sharepoint.com"
        ) == (
            "OneDrive-<org-2>; OneDrive-SharedLibraries-<org-2>; Egnyte-<org-2>; Box-<org-2>; "
            "OneDrive\\ -\\ <org-2>; OneDrive%20-%20<org-2>; OneDrive - <org-2>; <org-2>.sharepoint.com"
        ), org
        assert not red.word_named_shown, org
    red = setup_report.Redactor()
    red.add("org", "Sync", fuzzy=True)
    assert red.redact("Hubspot-Sync; CloudApp-Sync") == "Hubspot-Sync; CloudApp-Sync", "not a known app"
    red.set_app_heads({"CloudApp"})
    assert red.redact("Hubspot-Sync; CloudApp-Sync; OneDrive-Sync") == (
        "Hubspot-Sync; CloudApp-<org-1>; OneDrive-<org-1>"
    ), "an app this Mac's CloudStorage names, and the defaults"
    for org in ("Status", "Probe", "Stage", "Context", "Agent"):
        assert org.lower() not in setup_report._CONTEXT_ORG_WORDS
        red = setup_report.Redactor()
        red.add("org", org, fuzzy=True)
        assert red.redact(f"the {org} tenant asked for MFA; OneDrive-{org}") == (
            "the <org-1> tenant asked for MFA; OneDrive-<org-1>"
        ), org
        # each of the five, not the last alone (B13 round 7 H45)
        assert red.word_named_shown, f"{org}: a report word matched everywhere: the report is private only"


@pytest.mark.parametrize("org", ["Sync", "Status"])
def test_a_report_word_organisation_never_prints_and_a_generic_one_keeps_the_reports_words(
    fake_mac: dict[str, Path], org: str
) -> None:
    """B13 round 6 G45, G46, G75 end to end: a OneDrive-<org> entry beside OneDrive-Contoso and a friction
    line naming it. "Sync" (a generic word) is replaced in its names only: the report's own words and
    sentences keep the word and the report can go public. "Status" is replaced everywhere and the report is
    private only."""
    (fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{org}").mkdir()
    write_friction(
        fake_mac,
        V7_HAPPY.replace(
            "2026-09-29T10:01:10Z | end",
            f"2026-09-29T10:00:00Z | step 2 | deviation | the {org} tenant asked for MFA | -\n"
            "2026-09-29T10:01:10Z | end",
        ),
    )
    text = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())[0]
    assert f"OneDrive-{org}" not in text and "OneDrive-<org-2>" in text
    # a generic word in prose is the report's own word (F42): only a reviewed list is generic (G46)
    assert ("the <org-2> tenant" if org == "Status" else "the Sync tenant") in text
    assert "agent-<org-" not in text and "first-<org-" not in text and "~/agent-context/" in text
    private = "Private only:" in text
    assert private == (org == "Status"), org


@pytest.mark.parametrize("org", ["Context", "Agent"])
def test_an_organisation_named_like_agentsyncs_paths_leaves_them_whole(
    fake_mac: dict[str, Path], org: str
) -> None:
    """B13 round 7 H74: an organisation named Context or Agent is a report word matched everywhere (G45), so
    it cut agentsync's own paths to ``~/agent-<org-1>/setup/`` and ``~/src/<org-1>-context-sync``, from which
    a reader recovers the name; the shell report kept them whole for a capitalised spelling (a lower-case one
    cut them there too until B13 round 8 J71). Those fixed path names are held now, as the
    repository locator is; the organisation is replaced everywhere else, and the report stays private only."""
    (fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{org}").mkdir()
    (fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{org}" / f"Agent-{org} Team").mkdir()
    write_friction(
        fake_mac,
        V7_HAPPY.replace(
            "2026-09-29T10:01:10Z | end",
            f"2026-09-29T10:00:00Z | step 2 | deviation | the {org} tenant asked for MFA in "
            f"OneDrive-{org}/Agent-{org} Team; ran ~/src/agent-context-sync/scripts/install.sh | -\n"
            "2026-09-29T10:01:10Z | end",
        ),
    )
    text = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())[0]
    assert "~/agent-context/setup/" in text and "~/src/agent-context-sync/scripts/install.sh" in text
    assert "agent-<org-" not in text and "<org-1>-context" not in text and "<org-2>-context" not in text
    for named in (f"the {org} tenant", f"OneDrive-{org}", f"Agent-{org} Team"):
        assert named not in text, named
    # a value that begins with the held words is still replaced (a reviewer's finding)
    red = setup_report.Redactor()
    red.add("org", org, fuzzy=True)
    for value in ("agent-context Contoso", "agent-context (Contoso)", "agent-context-sync Contoso"):
        red.add("folder", value, fuzzy=True)
        shown = red.redact(f"~/OneDrive-x/{value}/notes; ~/agent-context/setup/; ~/src/agent-context-sync/x")
        assert "Contoso" not in shown and "~/agent-context/setup/; ~/src/agent-context-sync/x" in shown, shown
    # B13 round 8 J43: the held list spelled the .git name without the dot _norm keeps, so a value of that
    # name was never the held path: ~/src/agent-context-sync.git/ was cut to ~/src/<folder-1>/
    red = setup_report.Redactor()
    red.add("org", org, fuzzy=True)
    red.add("folder", "agent-context-sync.git", fuzzy=True)
    red.add("folder", "agent-context-sync.git Contoso", fuzzy=True)
    shown = red.redact("~/src/agent-context-sync.git/x; ~/OneDrive-x/agent-context-sync.git Contoso/notes")
    assert shown.startswith("~/src/agent-context-sync.git/x; ") and "Contoso" not in shown, shown
    assert all(setup_report._norm(n) == n for n in setup_report._HELD_PATH_NORMS)
    assert setup_report._norm("agent-context-sync.git") in setup_report._HELD_PATH_NORMS
    assert "Private only:" in text


def test_both_readers_of_the_folder_list_strip_every_mark_install_sh_prints() -> None:
    """B13 round 9 K45: the J69 readers stripped "[synced] " only, so a folder printed "[inside a synced
    folder]" was dropped and its name stayed in clear. Every mark install.sh defines is in both readers."""
    script = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    marks = re.findall(r'^(?:SYNCED|INSIDE|CONTAINS)_MARK="(\[[^"]+\])"', script, flags=re.M)
    assert len(marks) == 3 and len(re.findall(r'^[A-Z]+_MARK="', script, flags=re.M)) == 3, marks
    assert sorted(setup_report._LIST_MARKS) == sorted(f"{m} " for m in marks)
    [awk] = re.findall(r"list \{ sub\(/\^\\\[\(([^)]*)\)\\\] /", script)
    assert sorted(awk.split("|")) == sorted(m.strip("[]") for m in marks), awk


def test_a_listing_that_did_not_finish_takes_step_1s_list_or_makes_the_report_private(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 8 J69: when the CloudStorage listing for redaction raised (a stall past 3 s, a pending
    privacy prompt), an unconfigured folder that step 1's list showed and the agent quoted printed in clear,
    and the report stayed public. The folders install.out's --list-folders runs printed are registered too,
    with no folder inside a domain touched; with no such list the report is private only and ends with no
    link. A listing that finished with nothing in it changes nothing."""
    cs = fake_mac["home"] / "Library" / "CloudStorage"
    (cs / f"OneDrive-{ORG}" / "Northwind Merger").mkdir()
    write_friction(
        fake_mac,
        V11_HAPPY.replace(
            "2026-09-29T10:01:10Z | end",
            "2026-09-29T10:00:00Z | step 1 | deviation | the person picked Northwind Merger later | -\n"
            "2026-09-29T10:00:30Z | step 1 | deviation | and Fabrikam Bid inside Q3 Launch | -\n"
            "2026-09-29T10:01:10Z | end",
        ),
    )
    out = fake_mac["setup"] / "install.out"
    # a folder inside a synced one, and one holding a synced one, carry marks of their own (B13 round 9 K45)
    listed = (
        "# run=20260929T095900Z-4141 2026-09-29T09:59:00Z install.sh --list-folders\n"
        f"[synced] {cs}/OneDrive-{ORG}/Q3 Launch\n{cs}/OneDrive-{ORG}/Northwind Merger\n"
        f"[inside a synced folder] {cs}/OneDrive-{ORG}/Q3 Launch/Fabrikam Bid\n"
        f"[contains a synced folder] {cs}/OneDrive-{ORG}/Wingtip Clients\n"
        "# run=20260929T100000Z-4242 2026-09-29T10:00:00Z install.sh --source-local x\n"
    )

    def stalled(*_args: object, **_kwargs: object) -> list[tuple[str, int, str]]:
        raise TimeoutError("did not finish within 3.0s")

    def build() -> str:
        return setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())[0]

    found = setup_report.listed_folder_names(listed, str(fake_mac["home"]))
    assert found == (
        True,
        [
            (f"OneDrive-{ORG}", 2, "Q3 Launch"),
            (f"OneDrive-{ORG}", 2, "Northwind Merger"),
            (f"OneDrive-{ORG}", 2, "Q3 Launch"),
            (f"OneDrive-{ORG}", 3, "Fabrikam Bid"),
            (f"OneDrive-{ORG}", 2, "Wingtip Clients"),
        ],
    )
    assert setup_report.listed_folder_names(listed.replace(" --list-folders", ""), "/x") == (False, [])
    # a list run that printed no folder is no list (a reviewer's finding)
    assert setup_report.listed_folder_names(listed.split("[synced]", maxsplit=1)[0], "/x") == (False, [])
    monkeypatch.setattr(setup_report, "cloud_folder_names", stalled)
    out.write_text(listed, encoding="utf-8")
    text = build()
    assert "Northwind" not in text, [ln for ln in text.splitlines() if "Northwind" in ln]
    assert "Fabrikam" not in text, [ln for ln in text.splitlines() if "Fabrikam" in ln]
    assert "Private only:" not in text and text.rstrip("\n").splitlines()[-1].startswith(
        setup_report.ISSUE_URL
    )
    assert "- redaction: could not list ~/Library/CloudStorage at depth 2-3 for redaction: " in text
    # no list of step 1 to take the listing's place: private only, and no link
    out.write_text(listed.replace(" --list-folders", " --source-local x"), encoding="utf-8")
    text = build()
    assert (
        "Private only: the listing of ~/Library/CloudStorage for redaction did not finish and install.out "
        "holds no folder list of step 1, so a folder name may show in clear." in text
    ), text
    assert "never as a public issue: a name may be in clear." in text
    assert not text.rstrip("\n").splitlines()[-1].startswith(setup_report.ISSUE_URL)
    # a listing that finished with no folder is no failure
    monkeypatch.setattr(setup_report, "cloud_folder_names", lambda *_a, **_k: [])
    text = build()
    assert "Private only:" not in text and text.rstrip("\n").splitlines()[-1].startswith(
        setup_report.ISSUE_URL
    )


def test_a_listing_that_finishes_late_is_used_before_any_line_is_cut(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 9 K76 (J69's step 5): a CloudStorage listing past its 3 s was thrown away, so with no list of
    step 1 in install.out the report was private only. The report looks once more before the friction
    section and the Summary, the first parts that redact a line and then cut it: a listing that finished by
    then is used, and a friction line cut inside the folder's name shows no part of it. One that never
    finishes still makes the report private only."""
    cs = fake_mac["home"] / "Library" / "CloudStorage"
    (cs / f"OneDrive-{ORG}" / "Northwind Merger").mkdir()
    long_what = "the person read " + "x" * 196 + " then Northwind Merger"  # cut at 220, inside the name
    write_friction(
        fake_mac,
        V11_HAPPY.replace(
            "2026-09-29T10:01:10Z | end",
            "2026-09-29T10:00:00Z | step 1 | deviation | the person picked Northwind Merger later | -\n"
            f"2026-09-29T10:00:30Z | step 1 | deviation | {long_what} | -\n"
            "2026-09-29T10:01:10Z | end",
        ),
    )
    real = setup_report.cloud_folder_names
    finish = threading.Event()

    def late(*args: Any, **kwargs: Any) -> list[tuple[str, int, str]]:
        # past the listing's time: the doctor hook, which runs before the friction part, ends it
        finish.wait(60)
        return real(*args, **kwargs)

    def doctor(_config: object) -> list[str]:
        finish.set()
        return ["[ok  ] python — fine"]

    monkeypatch.setattr(setup_report, "_LISTING_S", 0.3)
    monkeypatch.setattr(setup_report, "_LATE_LISTING_S", 5.0)  # a loaded machine: the budget still bounds it
    monkeypatch.setattr(setup_report, "cloud_folder_names", late)
    text = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks(doctor=doctor))[0]
    assert "Nort" not in text, [ln for ln in text.splitlines() if "Nort" in ln]
    assert re.search(r"picked <folder-\d+> later", text), text
    assert "Private only:" not in text and text.rstrip("\n").splitlines()[-1].startswith(
        setup_report.ISSUE_URL
    )
    assert (
        "- redaction: the CloudStorage listing finished after 0.3s and was used for redaction\n"
        in section(text, "Environment")
    )
    # a listing that never finishes: J69's private-only report, as before (released once the report is done)
    finish.clear()
    monkeypatch.setattr(setup_report, "_LATE_LISTING_S", 0.2)
    try:
        text = setup_report.build_report(
            fake_mac["config"], hooks=setup_report.ReportHooks(doctor=lambda _c: ["[ok  ] python — fine"])
        )[0]
    finally:
        finish.set()
    assert "Private only: the listing of ~/Library/CloudStorage for redaction did not finish" in text, text
    assert "- redaction: could not list ~/Library/CloudStorage at depth 2-3 for redaction: " in text


def test_a_configured_generic_folder_leaves_the_report_public(fake_mac: dict[str, Path]) -> None:
    """B13 round 6 G48: a configured Documents, Desktop or Pictures folder is a report word, so it made the
    report private only, while the shell report kept the public option; every OneDrive has those folders, so
    reading <folder-N> back as "Documents" gives nothing away. A word-named folder that is not generic
    ("Status") still makes it private (F92)."""
    for name in ("Documents", "Downloads"):
        folder = fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{ORG}" / name
        folder.mkdir()
        assert cli.main(["add-source", str(folder), "--config", str(fake_mac["config"])]) == 0
    text = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())[0]
    assert "OneDrive-<org-1>/<folder-" in text and "/Documents" not in text, "still redacted"
    assert "Private only:" not in text and "Send it: the link on the last line opens" in text


def test_a_custom_domain_tenant_leaves_onedrives_organisation_org_1(fake_mac: dict[str, Path]) -> None:
    """B13 round 6 G49: a custom-domain graph.tenant was registered whole before the organisations, so it took
    <org-1>, OneDrive's organisation became <org-2> and <org-1> appeared nowhere. It is registered after
    them; the domain still never prints."""
    cfg = fake_mac["config"]
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[graph]\ntenant = "contoso.com"\n', encoding="utf-8")
    text = setup_report.build_report(cfg, hooks=setup_report.ReportHooks())[0]
    assert "OneDrive-<org-1>" in section(text, "Environment")
    assert "<org-2>" not in text and "contoso.com" not in text.lower()
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    assert red.redact("see contoso.com, CONTOSO.COM and https://contoso.com/x") == (
        "see <org-2>, <org-2> and https://<org-2>/x"
    ), "the domain is still whole and matched in any case"


def test_each_stopped_copy_constant_keeps_its_own_docstring() -> None:
    """B13 round 11 M53: round 10 put _STOPPED_COPY_BEYOND between _STOPPED_COPY_WARN and its attribute
    docstring, so an editor's hover gave "why install.commit warns" to the wrong constant."""
    tree = ast.parse(Path(setup_report.__file__).read_text(encoding="utf-8"))
    docs = {}
    for node, after in itertools.pairwise(tree.body):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            doc = (
                after.value.value
                if isinstance(after, ast.Expr) and isinstance(after.value, ast.Constant)
                else None
            )
            docs[node.targets[0].id] = doc
    assert str(docs["_STOPPED_COPY_WARN"]).startswith("Why ``install.commit`` warns on a stopped copy")
    assert str(docs["_STOPPED_COPY_BEYOND"]).startswith('What follows "nothing for this setup to do"')


@pytest.mark.usefixtures("clean_doctor")
def test_a_step_1_list_that_stopped_on_a_sign_in_asked_no_folder_question(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 11 M7: step 1's list exited 3 (OneDrive not signed in) and listed nothing, yet the report
    counted the unlogged folder question (``human turns: 1``, ``the folder question``). A list that stopped on
    an Allow click (exit 4) was asked no folder question either, but the click stays its turn. A failed list
    that a later list or an install run followed keeps the usual rules, as does a Mac that already syncs."""
    happy = V11_HAPPY.replace("Prompt: v11", f"Prompt: v{setup_report.PROMPT_VERSION}")
    write_friction(fake_mac, happy)

    def lines(list_step: str, *, relist: bool = False) -> tuple[str, str]:
        log = set_up_log(fake_mac, listed="", list_step=list_step)
        if relist:
            again = LIST_RUN.replace(" launchd=simulated", "").replace("T09:58:0", "T09:59:0")
            again = again.replace("20260929T095805Z-11", "20260929T095905Z-12")
            log.write_text(
                log.read_text(encoding="utf-8")
                + again.replace("result=done\n", "result=done note=listed-25\n"),
                encoding="utf-8",
            )
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        summary = section(text, "Summary")
        return _line(summary, "- human turns: "), _line(summary, "- expected turns: ")

    for note, why in (
        ("not-signed-in", "OneDrive is not signed in"),
        ("no-folders", "no folders are synced yet"),
    ):
        human, expected = lines(f"failed note={note}")
        assert human.startswith("- human turns: 0 (0 questions; "), (note, human)
        assert "beyond the announced" not in human, "no provider folder: no Allow click was announced"
        assert expected == (
            f"- expected turns: none (step 1's list stopped: {why}, so no folder was listed: no folder "
            "question and no Allow click)"
        ), expected
        # the person signed in and step 1 listed again: the folder question was asked
        human, expected = lines(f"failed note={note}", relist=True)
        assert human.startswith("- human turns: 1 (1 question; "), (note, human)
        assert expected.startswith("- expected turns: the folder question (step 1; not logged as a question)")
    for note in ("denied", "tcc-pending"):
        human, expected = lines(f"failed note={note}")
        assert human.startswith("- human turns: 0 (0 questions; "), (note, human)
        assert expected.startswith(
            "- expected turns: no folder question (step 1's list ended on an Allow click, before the folder "
            "question) · "
        ), expected
        assert "Allow click" in expected.split(" · ", 1)[1], "the click is the turn"
    # a list that finished printed the folders, whatever failed after it: the question was due (review)
    log = set_up_log(fake_mac, listed="", list_step="done note=listed-25")
    again = LIST_RUN.replace(" launchd=simulated", "").replace("T09:58:0", "T09:59:0")
    again = again.replace("20260929T095805Z-11", "20260929T095905Z-12")
    log.write_text(
        log.read_text(encoding="utf-8")
        + again.replace("result=done\n", "result=failed note=not-signed-in\n"),
        encoding="utf-8",
    )
    runs = setup_report.read_install_runs(log)
    assert setup_report._list_stopped(runs) is None
    # an exit 3 after an exit 4 is the exit 4's: its Allow click was announced
    log.write_text(
        log.read_text(encoding="utf-8").replace("result=done note=listed-25", "result=failed note=denied"),
        encoding="utf-8",
    )
    assert setup_report._list_stopped(setup_report.read_install_runs(log)) == "denied"
    # B13 round 12 N31: on a Mac that already syncs folders an exit 3 is the sign-in, with words of its own;
    # B13 round 13 O1, O42, O31: the app and the count of missing folders its line logs, and no bracket inside
    # the line's own
    stop = "- expected turns: none (step 1's list stopped: "
    for fields, words in (
        (
            "missing=1 apps=dropbox synced=2",
            "Dropbox is not signed in, so 1 of the 2 folders this Mac already "
            "syncs is missing; they are kept",
        ),
        (
            "missing=2 apps=onedrive synced=2",
            "OneDrive is not signed in, so the 2 folders this Mac already syncs are missing; they are kept",
        ),
        (
            "missing=1 apps=box synced=1",
            "Box is not signed in, so the 1 folder this Mac already syncs is missing; it is kept",
        ),
        (
            "missing=1 apps=dropbox,googledrive synced=3",
            "Dropbox and Google Drive are not signed in, so 1 of "
            "the 3 folders this Mac already syncs is missing; they are kept",
        ),
        # an older install.sh logged no app and no missing count: neither is guessed
        (
            "synced=2",
            "a sync app is not signed in, so some of the 2 folders this Mac already syncs are "
            "missing; they are kept",
        ),
    ):
        human, expected = lines(f"failed note=not-signed-in {fields}")
        assert expected == f"{stop}{words}: no folder question and no Allow click)", expected
        assert not re.search(r"\([^()]*\(", expected), expected
        assert "beyond the announced" not in human, human
    # the exit with no provider folder at all is OneDrive's, with or without apps=
    for fields in ("", " apps=onedrive"):
        _human, expected = lines(f"failed note=not-signed-in{fields}")
        assert expected.startswith(f"{stop}OneDrive is not signed in, so no folder was listed"), expected
    # an exit 4 on such a Mac keeps its Allow click
    human, expected = lines("failed note=denied synced=2")
    assert expected.startswith("- expected turns: no folder question (2 folders already synced: "), expected
    assert "Allow click" in expected.split(" · ", 1)[1], expected


@pytest.mark.usefixtures("clean_doctor")
def test_a_synced_macs_sign_in_stop_is_named_and_asks_no_allow_click(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 12 N31: step 1's list exited 3 not-signed-in on a Mac that already syncs folders, and the
    Summary kept the synced Mac's words: no stop named anywhere, and on a real Mac "the announced Allow click"
    in the expected turns and "beyond the announced Allow click" in the human turns, though OneDrive's folder
    was missing, so no Allow click was announced. The outcome now says why the list stopped, synced or not.
    Both states: the stopped synced Mac, and the same Mac's exit 4, which keeps its Allow click."""
    monkeypatch.setattr(setup_report, "is_sandbox_home", lambda home: False)  # a real Mac's run type
    write_friction(fake_mac, V11_HAPPY.replace("Prompt: v11", f"Prompt: v{setup_report.PROMPT_VERSION}"))

    def summary(list_step: str) -> str:
        set_up_log(fake_mac, listed="", list_step=list_step)
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        return section(text, "Summary")

    # B13 round 13 O1, O29, O66: the app is the one the line names (a Dropbox folder missing beside a present
    # OneDrive one), an older line with a count names none, and the exit with no provider folder is OneDrive's
    for note, why in (
        ("not-signed-in synced=2 missing=1 apps=dropbox", "Dropbox is not signed in"),
        ("not-signed-in synced=2 missing=1 apps=onedrive", "OneDrive is not signed in"),
        ("not-signed-in synced=2", "a sync app is not signed in"),
        ("not-signed-in apps=onedrive", "OneDrive is not signed in"),
        ("no-folders", "no folders are synced yet"),
    ):
        got = summary(f"failed note={note}")
        assert got.strip().splitlines()[0] == (
            "- **outcome: failed at step 1** (computed: no install run: only install.sh --list-folders ran, "
            f"in step 1; it stopped: {why})"
        ), got
        if "dropbox" in note:
            assert "OneDrive" not in got.strip().splitlines()[0] + _line(got, "- expected turns: "), got
        for start in ("- human turns: ", "- expected turns: "):
            assert "Allow click" not in _line(got, start).replace("no Allow click", ""), _line(got, start)
    got = summary("failed note=denied synced=2")
    assert "the announced Allow click (step 1; not logged)" in _line(got, "- expected turns: "), got
    assert "it stopped:" not in got.strip().splitlines()[0], "an exit 4 ended on its click, not a stop"


@pytest.mark.usefixtures("clean_doctor")
def test_the_sign_in_step_1_asked_for_is_no_extra_click(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 12 N5 (lane P builds the prompt half): on a set-up Mac signed out of OneDrive, step 1's list
    exited 3 not-signed-in, the person signed in, and the agent logged it as a click: the report said "worked
    with help (1 click beyond the Allow clicks)" and "1 click logged, though none is possible". From prompt
    v20 one step 1 click after such a list, which a later run followed, is the sign-in step 1 asked for: an
    expected turn, never an extra or impossible one. Any other click still counts; v19 is as before."""
    click = "2026-09-29T09:59:00Z | step 1 | click | I signed in to OneDrive and said so | -\n"
    relist = (
        LIST_RUN.replace(" launchd=simulated", "")
        .replace("T09:58:0", "T09:59:0")
        .replace("20260929T095805Z-11", "20260929T095905Z-12")
        .replace("result=done\n", "result=done note=listed-25 synced=2\n")
    )

    def summary(
        version: int,
        extra: str = "",
        *,
        logged: bool = True,
        synced: str = " synced=2",
        apps: str = " apps=onedrive missing=1",
    ) -> str:
        friction = V11_HAPPY.replace("Prompt: v11", f"Prompt: v{version}").replace(
            "2026-09-29T10:01:10Z | end", (click if logged else "") + extra + "2026-09-29T10:01:10Z | end"
        )
        write_friction(fake_mac, friction)
        log = set_up_log(
            fake_mac,
            ("--confirm-install-agent", "done note=exists kept=2 added=0"),
            listed=synced,
            list_step=f"failed note=not-signed-in{apps}",
        )
        text = log.read_text(encoding="utf-8")
        cut = text.index("2026-09-29T10:00:00Z")
        log.write_text(text[:cut] + relist.replace(" synced=2", synced) + text[cut:], encoding="utf-8")
        _rc, report_text, _ = report(tmp_path, fake_mac["config"])
        return section(report_text, "Summary")

    got = summary(20)
    assert got.strip().splitlines()[0].startswith("- **outcome: fully one command**"), got
    assert "the OneDrive sign-in step 1 asked for (F4)" in _line(got, "- expected turns: "), got
    human = _line(got, "- human turns: ")
    assert (
        "1 click logged: the OneDrive sign-in step 1 asked for, F4; Allow clicks: none possible" in human
    ), human
    assert "though none is possible" not in got, got
    items = got.split("Items that were not one command", 1)[1]
    assert "click" not in items, items
    # the wait with no click line: still expected, "not logged"
    got = summary(20, logged=False)
    assert "the OneDrive sign-in step 1 asked for (not logged)" in _line(got, "- expected turns: "), got
    assert got.strip().splitlines()[0].startswith("- **outcome: fully one command**"), got
    # a second click is still one beyond the Allow clicks
    got = summary(20, "2026-09-29T10:00:30Z | step 2 | click | clicked a dialog | -\n")
    assert (
        got.strip()
        .splitlines()[0]
        .startswith(
            "- **outcome: worked with help** (computed: install.sh exit 0; 1 click beyond the Allow clicks"
        )
    ), got
    assert "2 clicks logged, one of them: the OneDrive sign-in step 1 asked for, F4" in _line(
        got, "- human turns: "
    )
    # on a real Mac the sign-in follows the Allow clause, with no bracket of its own (review)
    sandbox = setup_report.is_sandbox_home
    monkeypatch.setattr(setup_report, "is_sandbox_home", lambda home: False)
    human = _line(summary(20), "- human turns: ")
    assert (
        "1 click beyond the announced Allow click (not logged), which is F4, the OneDrive sign-in step 1 "
        "asked for;" in human
    ), human
    monkeypatch.setattr(setup_report, "is_sandbox_home", sandbox)
    # the list stopped again after the sign-in: that sign-in is still the one expected turn (review)
    write_friction(
        fake_mac,
        V11_HAPPY.replace("Prompt: v11", "Prompt: v20").replace(
            "2026-09-29T10:01:10Z | end", click + "2026-09-29T10:01:10Z | end"
        ),
    )
    log = set_up_log(fake_mac, listed=" synced=2", list_step="failed note=not-signed-in apps=onedrive")
    log.write_text(
        log.read_text(encoding="utf-8")
        + relist.replace(
            "result=done note=listed-25 synced=2", "result=failed note=not-signed-in apps=onedrive synced=2"
        ),
        encoding="utf-8",
    )
    _rc, report_text, _ = report(tmp_path, fake_mac["config"])
    expected = _line(section(report_text, "Summary"), "- expected turns: ")
    assert expected.startswith(
        "- expected turns: the OneDrive sign-in step 1 asked for (F4) and no other (step 1's list stopped: "
    ), expected
    # a prompt before v20 never asked for the sign-in that way: as before
    got = summary(19)
    assert got.strip().splitlines()[0].startswith("- **outcome: worked with help**"), got
    # a new Mac (round 11 M7's relist) with a logged sign-in click: no extra click either
    got = summary(20, synced="")
    assert got.strip().splitlines()[0].startswith("- **outcome: fully one command**"), got
    # B13 round 13 O1, O29, O66: the sign-in is the app's the list named; an older line names none
    got = summary(21, apps=" apps=dropbox missing=1")
    assert "the Dropbox sign-in step 1 asked for (F4)" in _line(got, "- expected turns: "), got
    assert "the Dropbox sign-in step 1 asked for, F4" in _line(got, "- human turns: "), got
    assert "OneDrive" not in _line(got, "- expected turns: ") + _line(got, "- human turns: "), got
    got = summary(21, apps="")
    assert "the sign-in step 1 asked for (F4)" in _line(got, "- expected turns: "), got
    assert got.strip().splitlines()[0].startswith("- **outcome: fully one command**"), got
    # B13 round 13 O40, O59: from v21 step 1 asks for a sign-in only on a Mac that syncs a folder (N7); on one
    # that syncs none (no count, 0 or unreadable) a logged sign-in is a click beyond the Allow clicks
    got = summary(21)
    assert got.strip().splitlines()[0].startswith("- **outcome: fully one command**"), got
    assert "the OneDrive sign-in step 1 asked for (F4)" in _line(got, "- expected turns: "), got
    for synced in ("", " synced=0", " synced=unreadable marks=timeout"):
        got = summary(21, synced=synced)
        assert (
            got.strip()
            .splitlines()[0]
            .startswith("- **outcome: worked with help** (computed: install.sh exit 0; 1 click beyond the")
        ), (synced, got)
        assert "asked for" not in _line(got, "- expected turns: "), (synced, got)
        assert "I signed in to OneDrive" in got.split("Items that were not one command", 1)[1], got
        got = summary(21, synced=synced, logged=False)
        assert got.strip().splitlines()[0].startswith("- **outcome: fully one command**"), (synced, got)
        assert "(not logged)" not in _line(got, "- expected turns: ").split("Allow click")[0], got
    # B13 round 13 O43: a step 1 click logged before the failed list ran is not its sign-in
    early = "2026-09-29T09:58:02Z | step 1 | click | clicked a dialog | -\n"
    got = summary(20, early, logged=False)
    assert got.strip().splitlines()[0].startswith("- **outcome: worked with help**"), got
    assert "the OneDrive sign-in step 1 asked for (not logged)" in _line(got, "- expected turns: "), got
    got = summary(20, early)  # and the sign-in click after the list is the one named, wherever it is logged
    assert "the OneDrive sign-in step 1 asked for (F4)" in _line(got, "- expected turns: "), got
    assert got.strip().splitlines()[0].startswith("- **outcome: worked with help**"), got
    assert "F5 · step 1 · click · clicked a dialog" in got.split("Items that were not one command", 1)[1], got
    # a click logged in the second the list started is still the sign-in
    got = summary(20, "2026-09-29T09:58:05Z | step 1 | click | I signed in | -\n", logged=False)
    assert got.strip().splitlines()[0].startswith("- **outcome: fully one command**"), got


def test_an_unattended_sign_in_stop_is_an_item_and_its_fails_are_the_stops(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 13 O13: an unattended run whose step 1 list exited 3 on a sign-in went on to the report, so
    the Items list read ``- none`` beside "failed at step 1", and the doctor FAILs the signed-out app's gone
    folder causes read as unexpected. The stop is an installer-side item in the Summary's words, and those
    FAILs (a source's listable or volume check, the source under a CloudStorage folder not on disk) have a
    bucket of their own; the outcome is unchanged. Both states: an install run after the list keeps them
    unexpected and adds no item."""
    real = cli._status_checks

    def sources_only(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
        return [
            c
            for c in real(config, offline=offline)
            if c.ok or c.severity is not doctor.Severity.ERROR or c.name.startswith("source.")
        ]

    monkeypatch.setattr(cli, "_status_checks", sources_only)
    # a stale provider folder, holding only a hidden entry: install.sh's signed_out. Gone, it would be
    # another account's beside OneDrive-SharedLibraries-<org> (round 14 Q46, Q55)
    provider = Path.home() / "Library" / "CloudStorage" / f"OneDrive-{ORG}"
    shutil.rmtree(provider)
    provider.mkdir()
    (provider / ".hidden").write_text("", encoding="utf-8")
    write_friction(fake_mac, V11_HAPPY.replace("Prompt: v11", "Prompt: v21"))

    def summary(apps: str, *installs: tuple[str, str]) -> str:
        set_up_log(
            fake_mac, *installs, listed=" synced=2", list_step=f"failed note=not-signed-in{apps} missing=1"
        )
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        return section(text, "Summary")

    for apps, app in ((" apps=onedrive", "OneDrive"), (" apps=dropbox", "Dropbox")):
        got = summary(apps)
        assert got.strip().splitlines()[0] == (
            "- **outcome: failed at step 1** (computed: no install run: only install.sh --list-folders ran, "
            f"in step 1; it stopped: {app} is not signed in)"
        ), got
        items = got.split("Items that were not one command (attempt 1):", 1)[1].split("\n\n", 2)[1]
        assert items == (
            f"- install.sh · step 1 · the folder list stopped: {app} is not signed in, so 1 of the 2 folders "
            "this Mac already syncs is missing (not logged: an unattended run goes to the report)"
        ), items
        line = _line(got, "- doctor: ")
        stop_part, unexpected = line.split(" · from the sign-in stop ", 1)[1].split(" · unexpected ", 1)
        assert "listable FAIL" in stop_part and "FAIL" not in unexpected, line
    # an install run followed the list: the FAILs are unexpected again, and the stop is no item
    got = summary(" apps=onedrive", ("--confirm-install-agent", "done note=exists kept=2 added=0"))
    assert "from the sign-in stop" not in got and "the folder list stopped" not in got, got
    assert "listable FAIL" in _line(got, "- doctor: ").split(" · unexpected ", 1)[1], _line(got, "- doctor: ")


def test_the_sign_in_app_map_holds_the_tokens_install_sh_writes_and_no_others() -> None:
    """B13 round 14 Q52: ``_SIGN_IN_APPS`` held ``sharedlibraries`` and ``google%20drive``, which install.sh
    never logs, and ``_LIST_FOLDERS_NOTE_WORDS`` a ``not-signed-in`` entry no caller reaches (the Installer
    row words that note in its own branch). The map is install.sh's tokens: each label its ``case`` names,
    in lower case with no space, then ``other`` (and ``apps=onedrive``, the exit with no provider folder)."""
    script = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    [labels] = re.findall(r'^\s*(OneDrive \| "Google Drive" \|[^)]*)\) tok="\$\(printf', script, re.MULTILINE)
    tokens = {label.strip().strip('"').replace(" ", "").lower() for label in labels.split("|")}
    assert "apps=onedrive" in script and re.search(r"^\s*\*\) tok=other ;;$", script, re.MULTILINE)
    assert set(setup_report._SIGN_IN_APPS) == tokens | {"other"}
    assert "not-signed-in" not in setup_report._LIST_FOLDERS_NOTE_WORDS


@pytest.mark.parametrize(
    ("apps", "stop", "sign_in"),
    [
        (
            "onedrive,dropbox,box",
            "OneDrive, Dropbox and Box are not signed in",
            "the OneDrive, Dropbox and Box sign-ins",
        ),
        ("onedrive,other", "OneDrive and another sync app are not signed in", "the sign-ins"),
        ("dropbox,other,box", "Dropbox, another sync app and Box are not signed in", "the sign-ins"),
        ("other", "another sync app is not signed in", "the sign-in"),
        ("onedrive,dropbox", "OneDrive and Dropbox are not signed in", "the OneDrive and Dropbox sign-ins"),
        ("dropbox", "Dropbox is not signed in", "the Dropbox sign-in"),
    ],
)
def test_the_sign_in_words_join_three_apps_and_count_another_one(apps: str, stop: str, sign_in: str) -> None:
    """B13 round 14 Q48: three apps read ``the OneDrive and Dropbox and Box sign-ins``, and a list with
    ``other`` among two names read ``the sign-in``. The sign-in is joined as the stop's names are, and plural
    whenever more than one is named."""
    assert setup_report._signed_out({"note": "not-signed-in", "apps": apps}) == (stop, sign_in)


@pytest.mark.usefixtures("clean_doctor")
def test_a_new_macs_step_1_stop_goes_to_the_report_attended_or_not(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 14 Q47: on a Mac that syncs no folder, a step 1 list that stopped on a sign-in or found no
    folder goes to the report whether the run is attended or not (from prompt v21 a sign-in is no click
    there), yet its item blamed an unattended run. Only the prompt's first exception, a synced Mac that lost
    folders to a sign-in, keeps that reason (the O13 test's case)."""
    write_friction(fake_mac, V11_HAPPY.replace("Prompt: v11", "Prompt: v22"))
    for listed, list_step, words in (
        (" synced=0", "failed note=not-signed-in apps=onedrive", "OneDrive is not signed in"),
        ("", "failed note=not-signed-in apps=onedrive", "OneDrive is not signed in"),
        (" synced=0", "failed note=no-folders", "no folders are synced yet"),
        ("", "failed note=no-folders", "no folders are synced yet"),
    ):
        set_up_log(fake_mac, listed=listed, list_step=list_step)
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        got = section(text, "Summary")
        assert (
            f"- install.sh · step 1 · the folder list stopped: {words}, so no folder was listed (not logged: "
            "on this stop step 1 goes to the report)"
        ) in got, (listed, list_step, got)
        assert "an unattended run" not in got, got


def test_the_folders_line_says_step_1_found_a_synced_folder_not_on_disk(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 14 Q17, Q78: the Installer row read "2 already synced, 1 of them not on disk" while the
    Summary said only "kept the 2 already synced (none added)". The folders line says so by the list's own
    count when the list right before the install run logged ``gone=`` and this report's doctor still FAILs a
    source whose folder does not exist. Both states: no such FAIL, a list before an earlier install run, a
    sign-in exit, or a ``gone=`` that is no count adds nothing."""
    real = cli._status_checks

    def sources_only(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
        return [
            c
            for c in real(config, offline=offline)
            if c.ok or c.severity is not doctor.Severity.ERROR or c.name.startswith("source.")
        ]

    monkeypatch.setattr(cli, "_status_checks", sources_only)
    write_friction(fake_mac, V11_HAPPY.replace("Prompt: v11", "Prompt: v22"))
    clause = "step 1's list found 1 synced folder not on disk: see Doctor"

    def folders(
        *installs: tuple[str, str], listed: str = " synced=2 gone=1", step: str = "done note=listed-6"
    ) -> str:
        set_up_log(fake_mac, *installs, listed=listed, list_step=step)
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        line = _line(section(text, "Summary"), "- folders: ")
        for raw in RAW:
            assert raw not in line, raw
        return line

    kept = ("", "skipped note=exists kept=2 added=0")
    added = ("--source-local x", "done note=add-source kept=2 added=1")
    # no doctor FAIL: the folder is on disk again, so the line says nothing of it
    assert (
        folders(kept)
        == "- folders: kept the 2 already synced (none added) · 0 named with --source-local (install.log)"
    )
    fake_mac["one"].rename(fake_mac["one"].with_name("Client Beta"))
    assert folders(kept) == (
        f"- folders: kept the 2 already synced (none added; {clause}) · 0 named with --source-local "
        "(install.log)"
    )
    assert folders(added) == (
        f"- folders: 1 added to the 2 already synced ({clause}) · 1 named with --source-local (install.log)"
    )
    # the gone= list ran before an earlier install run, not the last one
    assert clause not in folders(kept, kept)
    assert clause not in folders(kept, listed=" synced=2 gone=Contoso")
    assert clause not in folders(kept, listed=" synced=2")
    # B13 round 15 S39: the sign-in exit install.sh writes (failed, no gone=), not a done row it never writes
    signed_out = "failed note=not-signed-in apps=onedrive missing=1"
    assert clause not in folders(kept, listed=" synced=2", step=signed_out)


def test_the_sign_in_stop_bucket_takes_the_folders_install_sh_calls_signed_out(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 14 Q46, Q55: the sign-in stop's doctor bucket took a source when its provider folder was not
    on disk, which is not install.sh's sign-in rule: a stale provider folder holding only hidden entries is
    signed out there (its FAILs read as unexpected here), while a SharePoint library gone beside its
    OneDrive-<org>, or a provider folder gone beside another folder of its app, is no sign-in (their FAILs
    landed in the stop's bucket). Doctor's rule (``provider_state``) is now the report's."""
    real = cli._status_checks

    def sources_only(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
        return [
            c
            for c in real(config, offline=offline)
            if c.ok or c.severity is not doctor.Severity.ERROR or c.name.startswith("source.")
        ]

    monkeypatch.setattr(cli, "_status_checks", sources_only)
    write_friction(fake_mac, V11_HAPPY.replace("Prompt: v11", "Prompt: v21"))
    cloud = Path.home() / "Library" / "CloudStorage"
    provider, library = cloud / f"OneDrive-{ORG}", cloud / f"OneDrive-SharedLibraries-{ORG}"
    # the report reads each provider folder once, its listing bounded by the report's budget, not doctor's
    # 120 s (a held folder would hold the Summary past its budget)
    read: list[tuple[Path, float]] = []
    real_state = doctor.provider_state

    def state(folder: Path, *, timeout: float | None = None) -> doctor.ProviderState:
        read.append((folder, 120.0 if timeout is None else timeout))
        return real_state(folder, timeout=timeout)

    monkeypatch.setattr(doctor, "provider_state", state)

    def doctor_line() -> str:
        set_up_log(
            fake_mac, listed=" synced=2", list_step="failed note=not-signed-in apps=onedrive missing=1"
        )
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        return _line(section(text, "Summary"), "- doctor: ")

    def bucket(line: str) -> tuple[str, str]:
        stop = line.split(" · from the sign-in stop ", 1)[1] if " · from the sign-in stop " in line else ""
        return stop.split(" · unexpected ", 1)[0], line.split(" · unexpected ", 1)[1]

    # a stale provider folder: there, with nothing but a hidden entry (install.sh's signed_out returns 0)
    shutil.rmtree(provider)
    provider.mkdir()
    (provider / ".849C9593-D756-4E56-8D6E-42412F2A707B").write_text("", encoding="utf-8")
    assert doctor.provider_state(provider) == "signed-out"
    read.clear()
    stop, unexpected = bucket(doctor_line())
    assert stop.startswith("(2 checks): ") and "listable FAIL" in stop and "FAIL" not in unexpected, (
        stop,
        unexpected,
    )
    # doctor's own status check passes its 120 s; the report's read is bounded and done once for 2 calls
    reports = [t for folder, t in read if folder == provider and t <= setup_report._LISTING_S]
    assert len(reports) == 1, read
    # an unreadable provider folder is signed in (N61): its FAILs stay unexpected
    provider.chmod(0)
    try:
        assert doctor.provider_state(provider) == "signed-in"
        stop, unexpected = bucket(doctor_line())
    finally:
        provider.chmod(0o755)
    assert stop == "" and "FAIL" in unexpected, (stop, unexpected)
    # the provider folder gone beside another folder of its app: another account's, not this stop's
    shutil.rmtree(provider)
    (cloud / "OneDrive-Fabrikam").mkdir()
    assert doctor.provider_state(provider) == "other-account"
    stop, unexpected = bucket(doctor_line())
    assert stop == "" and "listable FAIL" in unexpected, (stop, unexpected)
    # a SharePoint library gone beside its OneDrive-<org>: a library not synced, never a sign-in
    shutil.rmtree(cloud / "OneDrive-Fabrikam")
    fake_mac["one"].mkdir(parents=True)
    shutil.rmtree(library)
    assert doctor.provider_state(library) == "library"
    stop, unexpected = bucket(doctor_line())
    assert stop == "" and "listable FAIL" in unexpected, (stop, unexpected)
    library.mkdir()
    assert doctor.provider_state(library) == "library"  # there but empty: still a library
    # both gone, no other OneDrive folder beside them: each is signed out, as install.sh reads it
    shutil.rmtree(library)
    shutil.rmtree(provider)
    assert doctor.provider_state(provider) == doctor.provider_state(library) == "signed-out"
    stop, unexpected = bucket(doctor_line())
    assert stop.startswith("(4 checks): ") and "FAIL" not in unexpected, (stop, unexpected)


def test_the_folders_step_1_found_not_on_disk_have_their_own_doctor_bucket(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 14 Q3, Q18, Q36: step 1's list logged ``gone=1`` and its NEXT said step 2's status fails on
    that folder, yet the Doctor line called the folder's listable and volume FAILs unexpected. They have a
    bucket of their own, by check name and path; the outcome is unchanged. Both states: no ``gone=``, or more
    folders missing than step 1 counted, keeps them unexpected."""
    real = cli._status_checks

    def sources_only(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
        return [
            c
            for c in real(config, offline=offline)
            if c.ok or c.severity is not doctor.Severity.ERROR or c.name.startswith("source.")
        ]

    monkeypatch.setattr(cli, "_status_checks", sources_only)
    write_friction(fake_mac, V11_HAPPY.replace("Prompt: v11", "Prompt: v21"))
    install = ("--confirm-install-agent", "done note=exists kept=2 added=0")

    def assert_buckets_add_up(line: str) -> None:
        # B13 round 15 S12, S27: every bucket counts checks, so the buckets add up to the FAILs and warns
        fails, warns = (int(n) for n in re.search(r"- doctor: (\d+) FAIL, (\d+) warn", line).groups())
        # B13 round 16 T20, T34: a "from" bucket's count is in a bracket with its unit, no digit after "disk"
        counted = re.findall(
            r"(?:expected|yours|unexpected) (\d+)\b|(?:stop|disk|refuses|be read) \((\d+) checks?\)", line
        )
        assert sum(int(a or b) for a, b in counted) == fails + warns, line
        assert re.search(r"not on disk \d", line) is None, line

    def summary(listed: str) -> str:
        set_up_log(fake_mac, install, listed=listed, list_step="done note=listed-6")
        _rc, text, _ = report(tmp_path, fake_mac["config"])
        return section(text, "Summary")

    fake_mac["one"].rename(fake_mac["one"].with_name("Client Beta"))  # renamed: its provider folder stays
    baseline = summary(" synced=2")
    line = _line(baseline, "- doctor: ")
    assert "not on disk" not in line and "listable FAIL" in line.split(" · unexpected ", 1)[1], line
    got = summary(" synced=2 gone=1")
    line = _line(got, "- doctor: ")
    bucket, unexpected = line.split(" · from the 1 synced folder step 1 found not on disk (2 checks): ", 1)[
        1
    ].split(" · unexpected ", 1)
    assert bucket.count(" FAIL") == 2 and "listable FAIL" in bucket and "volume FAIL" in bucket, line
    assert "FAIL" not in unexpected, line
    assert_buckets_add_up(line)
    assert _line(got, "- **outcome") == _line(baseline, "- **outcome"), got  # the outcome is unchanged
    # a SharePoint library not synced beside its OneDrive-<org> is in install.sh's gone= count too
    shutil.rmtree(fake_mac["two"].parents[1])
    line = _line(summary(" synced=2 gone=2"), "- doctor: ")
    assert " · from the 2 synced folders step 1 found not on disk (4 checks): " in line, line
    assert_buckets_add_up(line)
    assert "FAIL" not in line.split(" · unexpected ", 1)[1], line
    # two folders missing where step 1 counted one: which one went later cannot be told, so both unexpected
    line = _line(summary(" synced=2 gone=1"), "- doctor: ")
    assert "not on disk" not in line and line.split(" · unexpected ", 1)[1].count(" FAIL") == 4, line


def test_the_sync_refusals_read_through_a_state_folder_it_cannot_search(fake_mac: dict[str, Path]) -> None:
    """B13 round 17 U58's review: ``_refusing_checks`` once kept its manifest read inside a suppress, and the
    shared ``_sync_refusals`` called ``Path.is_file`` outside any guard: a state folder with no search
    permission raised PermissionError and failed the whole Summary. It reads as not read, no refusal."""
    config = load_config(fake_mac["config"])
    state = expand(config.state_paths.db).parent
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0)
    try:
        assert setup_report._sync_refusals(config, setup_report.Redactor()) == ([], False)
        assert setup_report._refusing_checks(config) == frozenset()
    finally:
        state.chmod(0o700)


@pytest.mark.usefixtures("roomy_calls")
def test_the_fail_of_a_state_file_no_command_can_read_has_its_own_doctor_bucket(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 17 U5, U24, U41: with a damaged manifest.sqlite, step 2's NEXT and status call
    state_dir.manifest's FAIL the person's, while the Summary listed it, and the listing warn beside it,
    as ``unexpected``. Keyed on the report's own read of the file (doctor's ``manifest_unreadable``): the
    FAIL has its own bucket, and the warn there too, not judged (no Loop line can say whose it is, so not
    ``yours``). With a manifest that reads, or one that is only locked, both stay unexpected."""
    real = cli._status_checks
    extra: list[doctor.CheckResult] = []

    def these(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
        found = real(config, offline=offline)
        kept = [c for c in found if c.ok or c.severity is not doctor.Severity.ERROR]
        return kept + [c for c in found if not c.ok and c.name == "state_dir.manifest"] + extra

    monkeypatch.setattr(cli, "_status_checks", these)
    cfg = fake_mac["config"]
    (fake_mac["one"] / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(cfg)]) == 0
    db = expand(load_config(cfg).state_paths.db)
    listing = setup_report._LISTING_INCOMPLETE + "x (a page could not be listed)"
    extra.append(doctor.CheckResult("heartbeat.x", False, listing, doctor.Severity.WARN))
    injected = doctor._bad("state_dir.manifest", "manifest.sqlite cannot be read: x", fix=doctor.MANIFEST_FIX)
    added = re.compile(r"(?:expected|yours|unexpected) (\d+)\b|(?:refuses|be read) \((\d+) checks?\)")

    def doctor_line() -> str:
        _rc, text, _ = report(tmp_path, cfg)
        line = _line(section(text, "Summary"), "- doctor: ")
        found = re.search(r"- doctor: (\d+) FAIL, (\d+) warn", line)
        assert found is not None, line
        assert sum(int(a or b) for a, b in added.findall(line)) == sum(map(int, found.groups())), line
        return line

    def unexpected(line: str) -> str:
        return line.split(" · unexpected ", 1)[1]

    extra.append(injected)  # a manifest that reads: a FAIL by that name is not the file's
    line = doctor_line()
    assert "state_dir.manifest FAIL" in unexpected(line) and "heartbeat.x warn" in unexpected(line), line
    assert "from the state file" not in line, line
    conn = sqlite3.connect(db)
    try:  # one that is only locked: no command is refused for good
        conn.execute("BEGIN EXCLUSIVE")
        line = doctor_line()
    finally:
        conn.close()
    assert "state_dir.manifest FAIL" in unexpected(line) and "from the state file" not in line, line
    extra.remove(injected)
    db.write_bytes(b"not a database, " * 256)
    line = doctor_line()
    bucket = (
        " · from the state file that cannot be read (2 checks): state_dir.manifest FAIL (the person's; see "
        "Doctor), heartbeat.x warn (not judged: the loop cannot read the state) · "
    )
    assert bucket in line and "FAIL" not in unexpected(line) and "heartbeat" not in unexpected(line), line
    assert "yours" not in line, "no WAITING ON YOU line for a pointer to land on"


@pytest.mark.usefixtures("roomy_calls")
def test_the_fail_of_a_configuration_a_sync_refuses_has_its_own_doctor_bucket(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 16 T4, T14: a vanished source (CF), a policy.toml that does not pass (PT) and a bad
    ``[governance]`` key (GV) each made the Summary's doctor line say ``unexpected 1: <check> FAIL`` beside a
    Configuration line naming that very refusal. The FAIL of a configuration the report itself reads as
    refused now has its own bucket; the outcome still counts it. Keyed on that reading, not on doctor's
    fix text: a ``policy`` FAIL whose policy loads, and an unrelated FAIL beside a refused one, stay
    unexpected."""
    real = cli._status_checks
    extra: list[doctor.CheckResult] = []

    def these(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
        refused = ("policy", "governance.config", "config.vanished_sources")
        kept = [c for c in real(config, offline=offline) if c.ok or c.severity is not doctor.Severity.ERROR]
        kept += [c for c in real(config, offline=offline) if not c.ok and c.name in refused]
        return kept + extra

    monkeypatch.setattr(cli, "_status_checks", these)
    cfg = fake_mac["config"]
    whole = cfg.read_text(encoding="utf-8")

    def doctor_line() -> str:
        _rc, text, _ = report(tmp_path, cfg)
        return _line(section(text, "Summary"), "- doctor: ")

    def bucket(check: str) -> str:
        return f" · from the configuration a sync refuses (1 check): {check} FAIL (see Configuration) · "

    def unexpected(line: str) -> str:  # fake_mac's own warns are unexpected too: the FAILs are the point
        return line.split(" · unexpected ", 1)[1]

    # CF: a source's block deleted while its pages are mirrored
    (fake_mac["two"] / "notes.txt").write_text("notes\n", encoding="utf-8")
    assert cli.main(["sync", "--config", str(cfg)]) == 0
    gone = next(src.id for src in load_config(cfg).sources if src.path == fake_mac["two"])
    head, *blocks = whole.split("[[source]]")
    cfg.write_text("[[source]]".join([head, *(b for b in blocks if f'id = "{gone}"' not in b)]), "utf-8")
    line = doctor_line()
    assert bucket("config.vanished_sources") in line and "FAIL" not in unexpected(line), line
    # beside an unrelated FAIL, that one is still unexpected
    extra.append(doctor.CheckResult("launchd.agent", False, "not loaded", fix="install.sh"))
    line = doctor_line()
    assert bucket("config.vanished_sources") in line and "launchd.agent FAIL" in unexpected(line), line
    extra.clear()
    cfg.write_text(whole, encoding="utf-8")
    # PT: a policy.toml that does not pass
    (cfg.parent / "policy.toml").write_text('[policy]\nexclude_label_ids = ["Contoso Zebra"]\n', "utf-8")
    line = doctor_line()
    assert bucket("policy") in line and "FAIL" not in unexpected(line) and "Zebra" not in line, line
    (cfg.parent / "policy.toml").unlink()
    # GV: a [governance] key that does not pass
    cfg.write_text('[governance]\nremote_url_prefixes = ["zeta-git.example"]\n\n' + whole, encoding="utf-8")
    line = doctor_line()
    assert bucket("governance.config") in line and "FAIL" not in unexpected(line), line
    cfg.write_text(whole, encoding="utf-8")
    # a policy FAIL the report does not read as a refusal (its policy loads): unexpected, as before
    extra.append(doctor.CheckResult("policy", False, "something else", fix="fix [policy] / policy.toml"))
    line = doctor_line()
    assert "a sync refuses" not in line and "policy FAIL" in unexpected(line), line
    extra.clear()
    # B13 round 17 U58: two refusals at once (the vanished source and the policy): the loads line names
    # both, in the sync's order, so the bucket's "see Configuration" holds for each check in it
    cfg.write_text("[[source]]".join([head, *(b for b in blocks if f'id = "{gone}"' not in b)]), "utf-8")
    (cfg.parent / "policy.toml").write_text('[policy]\nexclude_label_ids = ["Contoso Zebra"]\n', "utf-8")
    _rc, text, _ = report(tmp_path, cfg)
    line = _line(section(text, "Summary"), "- doctor: ")
    loads = _line(section(text, "Configuration"), "- loads: ")
    both = " · from the configuration a sync refuses (2 checks): config.vanished_sources FAIL, policy FAIL"
    assert both + " (see Configuration) · " in line, line
    assert loads.startswith(
        "- loads: yes, but a sync refuses it: [policy] exclude_label_ids in policy.toml"
    ), loads
    assert "; once that is fixed, a sync also refuses it: a source (" in loads, loads
    assert "is mirrored but no longer in sources.toml" in loads, loads
    assert "Zebra" not in line and "Zebra" not in loads, (line, loads)


@pytest.mark.usefixtures("roomy_calls")
def test_a_docs_repo_remote_governance_does_not_allow_has_the_persons_doctor_bucket(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 17 U2, U16: a remote added to the docs repo that [governance] does not allow made the
    Summary's doctor line say ``unexpected 1: governance.remote FAIL``. No sync refuses a remote, so it is not
    the configuration a sync refuses either: it has a bucket of its own, the person's to remove or allow,
    keyed on the report's own reading of the docs repo, never on doctor's words."""
    real = cli._status_checks

    def these(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
        return [
            c
            for c in real(config, offline=offline)
            if c.ok or c.severity is not doctor.Severity.ERROR or c.name == "governance.remote"
        ]

    monkeypatch.setattr(cli, "_status_checks", these)
    cfg = fake_mac["config"]
    docs = expand(load_config(cfg).docs_repo)

    def doctor_line() -> str:
        _rc, text, _ = report(tmp_path, cfg)
        return _line(section(text, "Summary"), "- doctor: ")

    url = "https://git.contoso.example/team/kb.git"
    subprocess.run(["git", "-C", str(docs), "remote", "add", "origin", url], check=True)
    line = doctor_line()
    assert (
        " · from a docs-repo remote [governance] does not allow, the person's to remove or allow (1 check): "
        "governance.remote FAIL · " in line
    ), line
    assert "governance.remote" not in line.split(" · unexpected ", 1)[1] and "a sync refuses" not in line, (
        line
    )
    subprocess.run(["git", "-C", str(docs), "remote", "remove", "origin"], check=True)
    assert "docs-repo remote" not in doctor_line()


def test_the_docs_repo_remote_is_read_only_for_doctors_remote_fail_and_never_against_the_budget(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 18 V55: the Summary ran ``governance.remote_policy_findings`` (git remote, then get-url per
    remote) for every report with a config, before it knew doctor printed a ``governance.remote`` FAIL. It is
    read only for that FAIL, and not through the budget: the Summary is built after the Loop line, which can
    spend it, and a real remote FAIL must still land in its own bucket then."""
    asked: list[object] = []

    def refused(c: object) -> bool:
        asked.append(c)
        return True

    monkeypatch.setattr(setup_report, "_remote_refused", refused)
    r = setup_report._Run(fake_mac["config"], setup_report.ReportHooks(), 0.0)  # the deadline already passed
    r.facts.doctor = Counter({"ok": 3, "warn": 1})
    r.facts.doctor_problems = [("warn", "logs", "something odd")]
    assert "docs-repo remote" not in setup_report._doctor_line(r, []) and asked == []
    r.facts.doctor = Counter({"ok": 3, "FAIL": 1})
    r.facts.doctor_problems = [("FAIL", "governance.remote", "remote origin (<remote>) exists but ...")]
    line = setup_report._doctor_line(r, [])
    assert len(asked) == 1, asked
    assert "from a docs-repo remote [governance] does not allow" in line and line.endswith(
        " · unexpected 0"
    ), line


@pytest.mark.usefixtures("clean_doctor")
def test_the_installer_table_calls_the_sign_in_step_1_asked_for_stopped(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """B13 round 13 O19: the Summary counted the sign-in step 1 asked for as fully one command while the
    Installer table called its list ``failed``. That list's row reads ``stopped`` and says what came next;
    install.log keeps ``result=failed``. Both states: with no later run (an unattended stop), or a prompt
    before v20, the row is ``failed`` as before. Round 14 Q13: the "Every run" table's last step of that run
    read ``list-folders (failed, rc 3)`` beside it; it reads ``stopped`` by the same rule."""
    relist = (
        LIST_RUN.replace(" launchd=simulated", "")
        .replace("T09:58:0", "T09:59:0")
        .replace("20260929T095805Z-11", "20260929T095905Z-12")
        .replace("result=done\n", "result=done note=listed-25 synced=2\n")
    )

    def row(version: int, *, installed: bool = False, relisted: bool = False) -> tuple[str, str]:
        write_friction(fake_mac, V11_HAPPY.replace("Prompt: v11", f"Prompt: v{version}"))
        installs = [("--confirm-install-agent", "done note=exists kept=2 added=0")] if installed else []
        log = set_up_log(
            fake_mac,
            *installs,
            listed=" synced=2",
            list_step="failed note=not-signed-in apps=dropbox missing=1",
        )
        text = log.read_text(encoding="utf-8").replace(
            "seconds=4 rc=0 result=failed", "seconds=4 rc=3 result=failed"
        )
        if relisted:
            text += relist
        log.write_text(text, encoding="utf-8")
        _rc, report_text, _ = report(tmp_path, fake_mac["config"])
        installer = section(report_text, "Installer")
        [cell] = [
            ln.split("|")[3].strip()
            for ln in installer.splitlines()
            if ln.startswith("| list-folders ") and "not signed in" in ln
        ]
        # round 14 Q13: the "Every run" table's last step of that run uses the same word
        [every] = [
            ln.split("|")[5].strip() for ln in installer.splitlines() if ln.startswith("| 20260929T095805Z")
        ]
        return cell, every

    words = "Dropbox is not signed in on this Mac: 2 already synced, 1 missing"
    stopped, failed = "list-folders (stopped, rc 3)", "list-folders (failed, rc 3)"
    assert row(20, relisted=True) == (
        f"stopped, rc 3 ({words}; the Dropbox sign-in step 1 asked for, then listed again)",
        stopped,
    )
    assert row(21, installed=True) == (
        f"stopped, rc 3 ({words}; the Dropbox sign-in step 1 asked for, then installed)",
        stopped,
    )
    assert row(20) == (f"failed, rc 3 ({words})", failed)
    assert row(19, relisted=True) == (f"failed, rc 3 ({words})", failed)
    # install.log keeps its token
    log = (fake_mac["setup"] / "install.log").read_text(encoding="utf-8")
    assert "result=failed note=not-signed-in" in log and "result=stopped" not in log


def test_the_installer_table_puts_every_steps_note_in_words() -> None:
    """B13 round 13 O3: the launcher, agent and wait rows read ``skipped (not-requested)`` and the report row
    ``done (agentsync)``, beside worded first-sync, list and helpers rows. install.log keeps its tokens, and a
    note the report does not know is shown as it is."""

    def cell(step: str, note: str, result: str = "skipped") -> str:
        v = {"seconds": "0", "rc": "0", "result": result, "note": note}
        return setup_report._step_table([(step, v)])[-1].split("|")[3].strip()

    for step in ("launcher", "agent", "wait"):
        assert cell(step, "not-requested") == "skipped (background sync was not asked for)", step
    assert cell("launcher", "up-to-date") == "skipped (already up to date)"
    for step in ("agent", "wait"):
        assert cell(step, "no-sources") == "skipped (no folders to sync)", step
        assert cell(step, "status-failed") == "skipped (agentsync status reported failures)", step
    assert cell("wait", "agent-failed") == "skipped (the agent step failed)"
    assert cell("report", "agentsync", "done") == "done (agentsync's report)"
    assert cell("report", "fallback", "done") == "done (the installer's fallback report)"
    assert cell("wait", "some-new-note") == "skipped (some-new-note)"
    # a whole run's table, as install.sh logs one with no background sync
    steps = [
        (name, {"seconds": "0", "rc": "0", "result": result, "note": note})
        for name, result, note in (
            ("launcher", "skipped", "not-requested"),
            ("agent", "skipped", "not-requested"),
            ("wait", "skipped", "not-requested"),
            ("report", "done", "agentsync"),
        )
    ]
    table = "\n".join(setup_report._step_table(steps))
    assert "(not-requested)" not in table and "(agentsync)" not in table, table


_NEVER_PRINTED_CAMELCASE = frozenset(
    {
        # sw_vers, scutil, ioreg and Info.plist keys the report reads and never prints
        "BuildVersion",
        "ProductVersion",
        "CFBundleShortVersionString",
        "CFBundleVersion",
        "ComputerName",
        "LocalHostName",
        "IOPlatformExpertDevice",
        "IOPlatformSerialNumber",
        "FrictionEvent",  # a type name in a cast
        "CFBundleIdentifier",  # the environment variable whose value the Environment line prints
        "WarpTerminal",  # a TERM_PROGRAM the Environment line prints as Warp (_TERMINAL_APPS)
        # the speech engine's build names, which no report line prints
        "CtcHead",
        "FluidInference",
        "JointDecisionv3",
        "PldaRho",
        "NemoTextProcessing",
        "SwiftPM",
        "fluidAudioCommit",
    }
)
"""The CamelCase words of the report's string literals that never print (reviewed, B13 round 13 O54)."""


def test_every_camelcase_word_the_report_prints_is_held_whole() -> None:
    """B13 round 13 O54, O68: round 12 N57 held three CamelCase words, and an organisation was still cut out
    of others the report prints (``<org-1>Variables`` of EnvironmentVariables). Every CamelCase word of 4 or
    more letters in a string literal that is no docstring, in setup_report.py, ops/doctor.py and
    convert/speech.py, is in ``_HELD_IDENTIFIERS`` or on the reviewed list of words that never print: a new
    one fails here instead of quietly cutting."""
    src = Path(setup_report.__file__).parent
    found: set[str] = set()
    for path in (src / "setup_report.py", src / "ops" / "doctor.py", src / "convert" / "speech.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        loose = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in loose:
                found.update(re.findall(r"(?<![A-Za-z%])[A-Za-z]*[a-z][A-Z][A-Za-z0-9]*", node.value))
    words = {w for w in found if len(w) >= 4}
    assert {"EnvironmentVariables", "StartInterval", "TeamIdentifier"} <= words, "the scan is broken"
    assert sorted(words - setup_report._HELD_IDENTIFIERS - _NEVER_PRINTED_CAMELCASE) == []
    assert not (_NEVER_PRINTED_CAMELCASE & setup_report._HELD_IDENTIFIERS)


@pytest.mark.parametrize(
    ("program", "name"),
    [("WarpTerminal", "Warp"), ("Apple_Terminal", "Terminal"), ("iTerm.app", "iTerm"), ("tmux", "tmux")],
)
def test_the_environment_names_the_terminal_app_as_the_shell_report_does(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, program: str, name: str
) -> None:
    """B13 round 13 O54: the Python report printed ``terminal WarpTerminal``, out of which an organisation
    "Warp" was cut (``<org-1>Terminal``). It names the app as install.sh's terminal_app does, and the app's
    name is no report word."""
    monkeypatch.setenv("TERM_PROGRAM", program)
    monkeypatch.setenv("TERM_PROGRAM_VERSION", "1.2")
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert f"terminal {name} 1.2" in section(text, "Environment"), section(text, "Environment")
    assert not {"warp", "ghostty", "iterm"} & setup_report.report_words()


def test_the_ocr_part_calls_a_first_read_a_first_read(fake_mac: dict[str, Path]) -> None:
    """B13 round 11 M12: a stub read for the first time is counted in a run's ``reread`` and ``converted``
    (cycle.py), and its record says so in ``reread_first``. The Summary called it "first read" and the OCR
    part "read again". The part now splits the sum over the runs as the CLI does since round 10 L34: all first
    reads say "first read(s)", some say "of them a first read", none read as before."""
    seed_ocr(fake_mac)

    def ocr_part(*counts: dict[str, int]) -> str:
        seed = Seed(fake_mac["config"])
        for run_id, record in zip((5, 4), counts, strict=True):
            seed.m._db.execute(
                "UPDATE runs SET counts_json = ? WHERE run_id = ?", (json.dumps(record), run_id)
            )
        seed.close()
        return status_parts(fake_mac)[1]["OCR"]

    first = {"converted": 1, "reread": 1, "reread_first": 1, "ocr_ms": 1000}
    part = ocr_part(first, {"converted": 0, "ocr_ms": 1000})
    assert (
        " · 1 first read of a stub whose bytes had never been read, also in the converted count\n" in part
    ), part
    assert "read(s) again" not in part
    # B13 round 12 N49: when every re-read was a first read no kept clause follows, as cycle.py counts a kept
    # re-read and returns before it counts a first read
    assert setup_report._reread_clause(2, 2, 1).endswith("had never been read, also in the converted count")
    rows = [ln for ln in part.splitlines() if re.match(r"\| \d", ln)]
    # B13 round 12 N15: one stub read for the first time is one file, not "1 (0), 1 first read"
    assert rows[0].endswith("| 0 + 0 | 1 first read | - |") and rows[1].endswith("| 0 (0) | - |"), rows
    part = ocr_part(first, {"converted": 2, "reread": 2, "reread_kept": 1, "ocr_ms": 1000})
    assert (
        " · 3 reads again (1 of them a first read of a stub whose bytes had never been read, also in the "
        "converted count; 1 could not be converted and kept its page)\n" in part
    ), part
    rows = [ln for ln in part.splitlines() if re.match(r"\| \d", ln)]
    assert rows[0].endswith("| 1 first read | - |") and rows[1].endswith("| 2 (1) | - |"), rows
    part = ocr_part({"converted": 3, "reread": 3, "reread_kept": 1, "reread_first": 1, "ocr_ms": 1000}, first)
    rows = [ln for ln in part.splitlines() if re.match(r"\| \d", ln)]
    assert rows[0].endswith("| 3 (1), 1 of them a first read | - |"), rows
    part = ocr_part({"converted": 1, "reread": 1, "ocr_ms": 1000}, {"converted": 0, "ocr_ms": 1000})
    # B13 round 16 T49: one re-read is "it", never "0 of them ... their page"
    assert " · 1 read again (it was converted)\n" in part, part
    assert (
        setup_report._reread_clause(1, 0, 1) == "1 read again (it could not be converted and kept its page)"
    )
    assert (
        setup_report._reread_clause(2, 0, 1)
        == "2 reads again (1 of them could not be converted and kept its page)"
    )
    assert setup_report._reread_clause(2, 0, 2).endswith(
        "(2 of them could not be converted and kept their page)"
    )


@pytest.mark.parametrize(
    "note",
    [
        *setup_report._FIRST_SYNC_NOTE_WORDS,
        *(f"agentsync:{n}" for n in setup_report._AGENTSYNC_NOTE_WORDS),
        "agentsync:wait-ran-out-after-600s",
        "agentsync:lock-not-taken-rc75",
    ],
)
def test_no_summary_clause_nests_a_bracket_or_puts_two_side_by_side(
    fake_mac: dict[str, Path], note: str
) -> None:
    """B13 round 12 N14, N33: ``agentsync 62s (waited for another sync to end)`` sat inside the step list's
    own bracket, and ``rc 75 (lock busy: ...) (install.log)`` put two brackets side by side, also on the
    borrowed line of an attempt that ran no install.sh. A note follows a comma; the source tag is the line's
    one trailing bracket (round 6 G34)."""
    if note.startswith("agentsync:"):
        agentsync = f"seconds=62 rc=0 result=done note={note.split(':', 1)[1]}"
        first_sync = "seconds=34 rc=0 result=done"
    else:
        agentsync = "seconds=41 rc=0 result=done"
        first_sync = f"seconds=9 rc={'75' if note == 'lock-busy' else '0'} result=skipped note={note}"
    write_install_log(fake_mac, first_sync=first_sync)
    log = fake_mac["setup"] / "install.log"
    log.write_text(
        log.read_text(encoding="utf-8").replace(
            "step=agentsync seconds=41 rc=0 result=done", f"step=agentsync {agentsync}"
        ),
        encoding="utf-8",
    )
    for friction in (V5_HAPPY, STOPPED_V8):  # this attempt's run, and the borrowed earlier run
        write_friction(fake_mac, friction)
        _text, summary = summary_of(fake_mac)
        lines = [
            ln
            for ln in summary.splitlines()
            if ln.startswith(("- install.sh: ", "- first sync: ", "- sync: "))
        ]
        assert len(lines) == 2, summary
        for line in lines:
            assert not re.search(r"\([^()]*\(", line), line  # no bracket inside another
            assert not re.search(r"\)\s*\(", line), line  # no two side by side
    if not note.startswith("agentsync:") and note != "waited-for-the-lock":  # that one is the counts' clause
        [line] = [ln for ln in summary.splitlines() if ln.startswith(("- first sync: ", "- sync: "))]
        assert f", {setup_report._first_sync_note(note)} (install.log: the install run of " in line, line


@pytest.mark.parametrize(
    ("printed", "note"),
    [
        (
            "converted 2, deferred 0 online-only, first read 2 (stubs whose bytes had never been read, so "
            "also counted as converted)",
            "converted-2-deferred-0-reread-2-first-2",
        ),
        (
            "converted 3, deferred 1 online-only, read again 3 (1 of them a first read: a stub whose bytes "
            "had never been read, so also counted as converted) (of the converted: 0 unreadable, 1 failed, 2 "
            "refused, 2 of them withheld by the label rule)",
            "converted-3-deferred-1-reread-3-first-1",
        ),
        ("converted 2, deferred 0 online-only, read again 2", "converted-2-deferred-0-reread-2"),
        ("converted 4, deferred 0 online-only", "converted-4-deferred-0"),
    ],
)
def test_install_sh_logs_the_first_reads_among_the_files_read_again(printed: str, note: str) -> None:
    """B13 round 12 N35: install.sh added the sync's "read again" and "first read" heads into one R and
    logged only ``-reread-R``, so the Installer row could not say which the Summary's "first read 2" was. Its
    awk now logs ``-first-F`` after R when F of them were first reads (never "K of them withheld"), and the
    report reads both (test_the_first_sync_steps_note_is_in_words_in_the_table_and_the_summary)."""
    script = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    [program] = re.findall(
        r"FIRST_SYNC_NOTE=\"\$\(printf '%s\\n' \"\$summary\" \| awk '(.*?)'\)\"", script, re.S
    )
    out = subprocess.run(
        ["awk", program],
        input=f"first sync: {printed}\n",
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "LC_ALL": "C"},
    ).stdout
    assert out == note, out
    [row] = setup_report._step_table([("first-sync", {"result": "done", "note": note})])[-1:]
    assert "read again or read for the first time" not in row or "-first-" not in note, row


def test_the_helpers_steps_note_is_in_words_in_the_installer_table() -> None:
    """B13 round 12 N48: the helpers row read ``skipped (config-unreadable)`` beside worded first-sync and
    agentsync rows, and ``no-interpreter`` is explained nowhere else in the report. install.log keeps its
    tokens; a note the report does not know is shown as it is."""

    def cell(note: str) -> str:
        v = {"seconds": "2", "rc": "0", "result": "skipped", "note": note}
        return setup_report._step_table([("helpers", v)])[-1].split("|")[3].strip()

    assert cell("config-unreadable") == "skipped (sources.toml could not be read, so no helper was built)"
    assert cell("no-devtools") == "skipped (no Xcode or Command Line Tools, so no helper was built)"
    assert cell("no-interpreter") == "skipped (agentsync's own Python is missing, so no helper was built)"
    assert cell("some-new-note") == "skipped (some-new-note)"


@pytest.mark.parametrize(
    ("note", "rc", "result", "words"),
    [
        (
            "converted-2-deferred-0-reread-2",
            "0",
            "done",
            "converted 2, deferred 0 online-only, 2 read again or read for the first time",
        ),
        (
            "converted-0-deferred-0-waited-for-the-lock",
            "0",
            "done",
            "converted 0, deferred 0 online-only, after waiting for another sync to end",
        ),
        (  # B13 round 12 N35: install.sh logs the first reads among R, and the row says it as the Summary
            "converted-2-deferred-0-reread-2-first-2",
            "0",
            "done",
            "converted 2, deferred 0 online-only, first read 2: stubs whose bytes had never been read, also "
            "in the converted count",
        ),
        (
            "converted-3-deferred-0-reread-3-first-1",
            "0",
            "done",
            "converted 3, deferred 0 online-only, read again 3: 1 of them a first read of a stub whose bytes "
            "had never been read, also in the converted count",
        ),
        (
            "converted-1-deferred-0-reread-1-first-1-waited-for-the-lock",
            "0",
            "done",
            "converted 1, deferred 0 online-only, first read 1: a stub whose bytes had never been read, also "
            "in the converted count, after waiting for another sync to end",
        ),
        ("waited-for-the-lock", "0", "done", "waited for another sync to end"),
        ("lock-busy", "75", "skipped", "lock busy: another sync held the lock past the wait"),
        ("no-sources", "0", "skipped", "no folder to sync in sources.toml"),
        ("status-failed", "0", "skipped", "agentsync status reported a failure, so no sync ran"),
        ("tcc-denied", "80", "failed", "this terminal app may not read the folders"),
        # B13 round 18 V50: exit 65's note was printed as its token
        (
            "state-unreadable",
            "65",
            "failed",
            "agentsync's state file, manifest.sqlite, cannot be read: the person's to fix",
        ),
    ],
)
def test_the_first_sync_steps_note_is_in_words_in_the_table_and_the_summary(
    fake_mac: dict[str, Path], note: str, rc: str, result: str, words: str
) -> None:
    """B13 round 11 M40: the Installer table showed install.log's first-sync tokens (``reread-2``, which
    contradicts the Summary's "first read 2"; ``lock-busy``) while the agentsync row had words (round 10 L35),
    and the Summary's sync clause printed ``(lock-busy)``. install.log keeps its tokens; R is neutral, since
    install.sh adds the sync's "read again" and "first read" heads into it."""
    write_install_log(
        fake_mac, rc=0 if rc == "0" else 1, first_sync=f"seconds=9 rc={rc} result={result} note={note}"
    )
    write_friction(fake_mac, V5_HAPPY)
    text, summary = summary_of(fake_mac)
    [row] = [ln for ln in section(text, "Installer").splitlines() if ln.startswith("| first-sync ")]
    assert f"({words})" in row and note not in row, row
    [line] = [ln for ln in summary.splitlines() if ln.startswith("- first sync: ")]
    assert note not in line, line
    if not note.startswith("converted-") and note != "waited-for-the-lock":
        # B13 round 12 N14, N33: after a comma; the source tag is the line's one trailing bracket
        assert f", {words} (install.log)" in line and ") (" not in line, line


def _shell_local_work_line(home: Path, setup: Path) -> str | None:
    """install.sh's ``fallback_local_work_line`` run alone under /bin/bash, as the shell report runs it."""
    script = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    [where] = re.findall(r'(?m)^LOCAL_WORK_WHERE=".*"$', script)
    [again] = re.findall(r'(?m)^LOCAL_WORK_AGAIN=".*"$', script)
    names = (
        "checkout_ref_sha",
        "checkout_git_dirs",
        "local_work_counts",
        "utc_stamp",
        "patch_rule",
        "unread_patch_count",
        "fallback_local_work_line",
    )
    functions = [re.search(rf"(?ms)^{name}\(\) \{{.*?^\}}$", script) for name in names]
    body = "\n".join(
        [
            "set -euo pipefail",
            "say() { printf '%s\\n' \"$*\"; }",
            # no friction log and no ledger: section 3's rule (B13 round 16 T24) counts every unreadable patch
            "patch_ledgers() { prev='' begin=- wasnot='' wassent=''; }",
            where,
            again,
            *(f.group(0) for f in functions if f),
        ]
    )
    assert len([f for f in functions if f]) == len(names)
    cp = subprocess.run(
        ["/bin/bash", "-c", f"{body}\nfallback_local_work_line"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "SETUP_DIR": str(setup), "LC_ALL": "C"},
    )
    return cp.stdout.removesuffix("\n") or None


def test_both_readers_count_a_patch_this_account_cannot_read_apart(fake_mac: dict[str, Path]) -> None:
    """B13 round 14 Q29: a kept patch this account cannot read is not in bring-back.md, yet both readers'
    local-work line said each patch goes to its section 3. They count it apart, with the remedy, and say
    nothing more once it is readable."""
    home, setup = fake_mac["home"], fake_mac["setup"]
    patches = setup / "local-work"
    patches.mkdir(parents=True, exist_ok=True)
    for name in ("0001-a.patch", "0002-b.patch"):
        (patches / name).write_text("Subject: [PATCH] x\n", encoding="utf-8")
        os.utime(patches / name, (1791950084, 1791950084))
    line = (
        "- local work kept by setup prompt step 1: 2 patch files in the setup folder's local-work{}, "
        f"newest written 2026-10-14T03:54:44Z ({setup_report.LOCAL_WORK_WHERE}); 0 local-work branches in "
        "the checkout"
    )
    assert setup_report.LOCAL_WORK_AGAIN == "~/src/agent-context-sync/scripts/install.sh --report-only"
    locked = patches / "0002-b.patch"
    locked.chmod(0)
    try:
        python, shell = setup_report._local_work_line(), _shell_local_work_line(home, setup)
    finally:
        locked.chmod(0o600)
    unread = (
        ", 1 of them unreadable and not in bring-back.md (chmod u+r, then run "
        "~/src/agent-context-sync/scripts/install.sh --report-only again)"
    )
    assert shell == python == line.format(unread), (shell, python)
    assert _shell_local_work_line(home, setup) == setup_report._local_work_line() == line.format("")


def test_the_local_work_line_counts_unread_patches_by_section_3s_rule(
    fake_mac: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 16 T24 (probe bw5): a patch an earlier attempt's report carried, unreadable now, was called
    "unreadable and not in bring-back.md", though section 3 does not send it again. install.sh gives the
    count by section 3's rule (``UNREAD_PATCHES_ENV``); without it, or with no number, each unreadable patch
    counts."""
    setup = fake_mac["setup"]
    patches = setup / "local-work"
    patches.mkdir(parents=True, exist_ok=True)
    for name in ("0001-a.patch", "0002-b.patch"):
        (patches / name).write_text("Subject: [PATCH] x\n", encoding="utf-8")
        (patches / name).chmod(0)
    clause = ", {} of them unreadable and not in bring-back.md (chmod u+r, then run "
    try:
        assert clause.format(2) in (setup_report._local_work_line() or "")
        for told, n in (("1", 1), ("0", 0), ("x", 2), ("", 2), ("5", 2)):
            monkeypatch.setenv(setup_report.UNREAD_PATCHES_ENV, told)
            line = setup_report._local_work_line() or ""
            assert clause.format(n) in line if n else "unreadable" not in line, (told, line)
    finally:
        for name in ("0001-a.patch", "0002-b.patch"):
            (patches / name).chmod(0o600)


def test_both_readers_count_local_work_in_a_checkout_whose_git_is_a_file(fake_mac: dict[str, Path]) -> None:
    """B13 round 11 M49, M70 (one defect from two finders): v19 pulls a checkout whose ``.git`` is a file (a
    ``--separate-git-dir`` clone, a linked worktree) and keeps work there, but both readers returned at once
    when ``.git`` was no folder, so the public report said nothing while bring-back.md section 3 sent the
    patch. The patches are counted whatever the checkout is; the branches are read from its common dir.
    M54: a branch at main's commit with a patch named for it holds the work a keep whose fetch failed left.
    M71: v19's recovery moves main on to origin/main, so an empty keep's branch sits at a commit origin/main
    once had: its reflog says so, and the branch is not counted; a commit only local main had still is."""
    home, setup = fake_mac["home"], fake_mac["setup"]
    checkout = home / "src" / "agent-context-sync"
    dot = checkout / ".git"
    patches = setup / "local-work"
    patches.mkdir(parents=True, exist_ok=True)
    branch = "local-work-20261014-120000"
    sha = {k: k * 40 for k in "abcdef"}

    def repo(git: Path, *, branch_at: str = "a", main: str = "c", reflog: str = "") -> None:
        (git / "refs" / "heads").mkdir(parents=True, exist_ok=True)
        (git / "refs" / "heads" / branch).write_text(sha[branch_at] + "\n", encoding="utf-8")
        (git / "packed-refs").write_text(f"{sha[main]} refs/heads/main\n", encoding="utf-8")
        if reflog:
            (git / "logs" / "refs" / "remotes" / "origin").mkdir(parents=True, exist_ok=True)
            (git / "logs" / "refs" / "remotes" / "origin" / "main").write_text(reflog, encoding="utf-8")

    def both() -> str | None:
        python = setup_report._local_work_line()
        assert _shell_local_work_line(home, setup) == python, "the two reports print one line"
        return python

    def kept(n: int, b: int) -> str:
        return (
            f"- local work kept by setup prompt step 1: {setup_report._plural(n, 'patch file')} in the setup "
            f"folder's local-work, newest written 2026-10-14T03:54:44Z ({setup_report.LOCAL_WORK_WHERE}); "
            f"{setup_report._plural(b, 'local-work branch', 'local-work branches')} in the checkout"
        )

    shutil.rmtree(patches)
    repo(home / "sep.git")
    (home / "src" / "agent-context-sync").mkdir(parents=True, exist_ok=True)
    dot.write_text(f"gitdir: {home / 'sep.git'}\r\n", encoding="utf-8")  # a CRLF line, and no local-work
    neutral = "- local-work branches in the checkout: 1 with commits of its own, no patch file in the "
    neutral += "setup folder's local-work "
    neutral += "(moved, deleted or never written; those commits are not in bring-back.md, whose section 3 "
    neutral += "says how to send them)"
    assert both() == neutral
    patches.mkdir()
    patch = patches / f"0001-keep.{branch}.patch"
    patch.write_text("Subject: [PATCH] keep\n", encoding="utf-8")
    os.utime(patch, (1791950084, 1791950084))
    dot.unlink()
    assert both() == kept(1, 0), "no checkout at all: the patch is still counted"
    dot.parent.mkdir(parents=True, exist_ok=True)
    dot.write_text("not a gitdir line\n", encoding="utf-8")
    assert both() == kept(1, 0), "a .git file that names no git dir"
    separate = home / "sep.git"
    repo(separate)
    dot.write_text(f"gitdir: {separate}\n", encoding="utf-8")
    assert both() == kept(1, 1), "a --separate-git-dir clone, absolute"
    dot.write_text("gitdir: ../../sep.git\n", encoding="utf-8")
    assert both() == kept(1, 1), "relative to the checkout"
    # a linked worktree: its git dir names the common dir, which holds the branches and packed-refs
    common = home / "main-checkout" / ".git"
    (common / "worktrees" / "wt").mkdir(parents=True)
    (common / "worktrees" / "wt" / "commondir").write_text("../..\n", encoding="utf-8")
    repo(common)
    dot.write_text(f"gitdir: {common / 'worktrees' / 'wt'}\n", encoding="utf-8")
    assert both() == kept(1, 1), "a linked worktree"
    # M54: at main's commit, with a patch named for it: a keep whose fetch failed, work on it
    repo(common, branch_at="c")
    assert both() == kept(1, 1)
    patch.rename(patches / "0001-keep.patch")
    assert both() == kept(1, 0), "no patch names it: an empty keep, as before (L58)"
    (patches / "0001-keep.patch").rename(patch)
    # M71: v19's no-tracking keep with nothing to keep, then main moved on to origin/main
    zero = "0" * 40
    published = f"{zero} {sha['d']} x <x@example.invalid> 1791950000 +0000\tclone\n"
    published += (
        f"{sha['d']} {sha['e']} x <x@example.invalid> 1791950084 +0000\tpull --ff-only: fast-forward\n"
    )
    patch.unlink()
    repo(common, branch_at="d", main="e", reflog=published)
    assert both() is None, "the branch sits at a commit origin/main once had"
    repo(common, branch_at="f", main="e", reflog=published)
    assert both() == neutral, "a commit origin/main never had"
    # a reflog line that is not UTF-8 (a Latin-1 committer name) is read all the same, by both readers
    latin = published.replace("x <x@", "Jos\xe9 <x@").encode("latin-1")
    (common / "logs" / "refs" / "remotes" / "origin" / "main").write_bytes(latin)
    repo(common, branch_at="d", main="e")
    assert both() is None
