"""``agentsync setup-report`` (src/agentsync/setup_report.py): headings, the summary, the embedded friction
log, redaction, background-run decoding, never failing hard and the time limit.  Everything runs in the
tmp HOME conftest sets up, with a fake ``~/Library/CloudStorage/OneDrive-Contoso`` (plain folders: no File
Provider, no prompt), and a canned ``launchctl print`` (this Mac's real LaunchAgent is never consulted)."""

from __future__ import annotations

import functools
import itertools
import json
import logging
import os
import plistlib
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from agentsync import arm_local, cli, convert, cycle, governance, loop, materialise, net, policy, setup_report
from agentsync.config import Config, ConvertConfig, load_config
from agentsync.convert import image, ocr, pdf
from agentsync.convert.cache import ConverterCache
from agentsync.convert.image import ImageConverter
from agentsync.convert.registry import Registry
from agentsync.manifest import Manifest
from agentsync.model import CycleMode, PassKind
from agentsync.ops import doctor, launchd
from agentsync.paths import TEMPORARY_ROOTS, expand, is_temporary
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
        f"source={home}/src/agent-context-sync args=--source-local {one}\n"
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


RAW = (ORG, ORG.lower(), *FOLDERS, LIBRARY, LOGIN, FULL_NAME, "Jane", EMAIL, GUID, SERIAL, HOST)


def test_headings_redaction_and_runtime(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    rc, text, elapsed = report(tmp_path, fake_mac["config"])
    assert rc == 0
    assert elapsed < 15, f"setup-report took {elapsed:.1f}s"
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
    for placeholder in ("<org-1>", "<folder-1>", "<library-1>", "<user>", "<name>", "<email-1>", "<guid-1>"):
        assert placeholder in text, placeholder
    assert "<serial>" in text and "<host>" in text and "~/Library/CloudStorage/OneDrive-<org-1>/" in text
    # the install log names the source folder as placeholders; a job log line never carries a path at all
    path_one = "~/Library/CloudStorage/OneDrive-<org-1>/<folder-1>/<folder-2>"
    assert path_one in section(text, "Installer")
    assert "<email-1> (<name>, <user>) owns <path>" in section(text, "Recent errors")
    assert "OneDrive-SharedLibraries-<org-1>/<library-1>/<folder-3>" in text
    assert not re.search(r"<folder-[4-9]>", text), "three folders, three placeholders"
    m = re.search(r"^(\d+) replacement\(s\) of (\d+) value\(s\)", section(text, "Redaction"), re.MULTILINE)
    assert m and int(m.group(1)) > 10 and 10 <= int(m.group(2)) < int(m.group(1))
    assert f"- redaction: {m.group(1)} replacement(s) of {m.group(2)} value(s)" in section(text, "Summary")


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
        "(EPERM: Operation not permitted)\n",
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    errors = section(text, "Recent errors")
    for name in ("Big Bank", "Merger", "Globex", "Northwind", "Initech", "term sheet", "Clients", "Mooring"):
        assert name not in text, name
    assert "src-x: directory '<path>' is unknown (EPERM: Operation not permitted)" in errors
    assert "src-x: <path>: [Errno 89] Operation canceled" in errors
    assert "inbox: <path>: read failed: Operation canceled" in errors
    assert "src-x: read failed: [Errno 89] Operation canceled: '<path>" in errors
    assert "(item paths and document names shown as <path>)" in errors


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
        "2026-10-05 09:00:00,000 WARNING agentsync.arm_local: src-a: directory '<path>' has no children "
        "(4 times in a row, the last at 2026-10-05 09:05:00,100)\n" in errors
    )
    assert "09:05:00,000" not in errors and "Mooring" not in text and "Harbor" not in text
    assert "09:15:00,000 WARNING agentsync.arm_local: src-b: directory '<path>' has no children\n" in errors
    assert "09:20:00,000 WARNING agentsync.arm_local: src-a: directory '<path>' has no children\n" in errors
    assert "The last 5 of 5 WARNING/ERROR line(s) in " in errors
    assert (
        "(8 as logged: lines in a row that read the same here are shown once, with their count and last time)"
        in errors
    )


CONVERTED_SUFFIXES = sorted({*Registry.default(ConvertConfig()).extensions(), *ImageConverter.extensions})
"""Every suffix a converter claims: the default registry's, and the image converter's, which the registry
holds only on a Mac with an OCR engine."""
LOG_HEAD = "com.agentsync.poll.err.log: 2026-10-05 09:00:00,000 WARNING agentsync."


@pytest.mark.parametrize("suffix", CONVERTED_SUFFIXES)
def test_a_log_line_never_names_a_file_a_converter_reads(suffix: str) -> None:
    """A file at the top of a source has no slash to know it by: its extension is all the scrub has. A file
    the cycle fetches and converts is a file its WARNING lines can name, so every claimed suffix is known."""
    assert len(CONVERTED_SUFFIXES) == 32 and {".bmp", ".heif", ".webp", ".yml", ".log"} <= {
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


@pytest.mark.parametrize("module", ["pdf", "xlsx", "pptx", "eml", "markdown", "image"])
def test_a_logger_named_like_a_document_keeps_its_time_and_level(module: str) -> None:
    """A logger is named after its module: ``agentsync.convert.pdf`` is not a document, and a line that
    lost its first segment to ``<path>`` lost its time and its level with it."""
    line = f"{LOG_HEAD}convert.{module}: Contoso Plan.pdf: comments not read on 1 page(s), first on page 2"
    assert setup_report._scrub_item_paths(line) == (
        f"{LOG_HEAD}convert.{module}: <path>: comments not read on 1 page(s), first on page 2"
    )
    # Only a segment that is a whole log-line head is left alone: a file named like a logger is a file.
    for posing in (
        f"fetch of WARNING agentsync.convert.{module} failed",
        f"unreadable ERROR agentsync.convert.{module}",
    ):
        if module != "image":  # ``.image`` is no document extension: nothing to scrub in it
            line = f"{LOG_HEAD}cycle: src-x: {posing}: timed out"
            assert setup_report._scrub_item_paths(line) == f"{LOG_HEAD}cycle: src-x: <path>: timed out"


def test_sections_carry_the_facts(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    env = section(text, "Environment")
    for label in ("macOS", "architecture", "MDM enrollment", "Xcode Command Line Tools", "uv", "pandoc"):
        assert f"- {label}" in env, label
    assert "OneDrive.app:" in env and "Company Portal:" in env and "agentsync resolves:" in env
    assert "listable, 2 provider folder(s)" in env and "~/.local/bin on PATH:" in env
    assert "- agentsync on PATH: " in env
    inst = section(text, "Installer")
    assert "1 install.sh run(s)" in inst and "exit 0 after 57s" in inst and "57s in total" in inst
    assert re.search(r"^\| agentsync +\| +41 \| done +\|$", inst, re.MULTILINE), "the per-step table"
    assert re.search(r"^\| doctor +\| +3 \| done, rc 1 \(fail-lines\) +\|$", inst, re.MULTILINE)
    raw = inst.split("<details>", 1)[1]
    assert "step=doctor seconds=3 rc=1" in raw.split("</details>", 1)[0], "raw log collapsed"
    assert "step=doctor seconds=3 rc=1" not in inst.split("<details>", 1)[0]
    conf = section(text, "Configuration")
    assert "sources: 3 (by kind: inbox 1, local 2" in conf  # init keeps the inbox (KISS K05)
    assert "2 under ~/Library/CloudStorage" in conf
    assert "client_id: not set" in conf and "a git repo" in conf
    doctor = section(text, "Doctor")
    m = re.search(r"^(\d+) ok: (.+)$", doctor, re.MULTILINE)
    assert re.search(r"\d+ check\(s\): \d+ ok", doctor) and m and "python" in m.group(2).split(", ")
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
    tags = re.search(r"(\d+) check\(s\): \d+ ok, \d+ info, (\d+) warn, (\d+) FAIL", doctor)
    assert tags and f"- doctor: {tags[3]} FAIL, {tags[2]} warn ({tags[1]} checks) · " in summary
    assert (
        "- install.sh: 1 install run; the last exit 0 after 57s (uv 0s · agentsync 41s · launcher 12s · "
        "config 1s · doctor 3s · agent 0s)" in summary
    )
    assert "- background sync: poll not installed · reconcile not installed" in summary
    errors = section(text, "Recent errors")
    assert "The last 2 of 2 WARNING/ERROR line(s)" in errors and "not an error line" not in errors
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


def test_a_crashing_or_hanging_hook_never_takes_the_report_down(fake_mac: dict[str, Path]) -> None:
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
    assert "This section failed: TimeoutError" in section(text, "Doctor")


def test_missing_config_is_a_finding(tmp_path: Path) -> None:
    rc = cli.main(["setup-report", "--out", str(tmp_path / "r.md"), "--config", str(tmp_path / "none.toml")])
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert rc == 0
    assert "- loads: no: ConfigError" in section(text, "Configuration")
    assert "Not run: the config does not load" in section(text, "Doctor")
    assert "No install log at" in section(text, "Installer")


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

OLDER_COPY = "(older than the installer's v8: the pasted copy was not the current README)"
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
    assert "- agent friction: 0 deviation, 0 prompt, 0 error; none of these changes the outcome" in summary
    assert "- friction (attempt 1): 12 event line(s): 4 start, 4 end, 1 question, 2 click, 1 finished" in (
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
        r"Embedded from ~/agent-context/setup/friction\.md as this report read it: 15 line\(s\), last "
        r"modified \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ",
        fr,
    ), "the report records what it embedded (J4)"
    assert (
        "- attempt 1 (lines 1-15; started 2026-09-29T09:58:00Z, prompt v5, agent Claude Code, "
        "claude-opus-5-5): fully one command; 12 event line(s): 4 start, 4 end, 1 question, 2 click, 1 "
        "finished; finished" in fr
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
        "- **outcome: worked with help** (computed: install.sh exit 0; 1 question(s) beyond the folder "
        "question; 1 click(s) beyond the Allow clicks; 1 approval(s))"
    ), "person-facing facts only: the deviation, prompt and untyped lines are agent friction (K4)"
    assert (
        "- agent friction: 1 deviation, 1 prompt, 0 error (F15, F16); 1 line(s) without a prompt kind "
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
        "- **outcome: failed at step 3** (computed: install.sh exited 1 at its first-sync step (rc 1))"
    )
    assert "- first sync: failed in 9s, rc 1 (install.log)" in summary
    assert "outcome=Failed%20at%20step%202%20%28install%20and%20start%29" in text.splitlines()[-1], (
        "a v5 attempt's step 3 is the current form's step 2"
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
    assert "- install.sh: no run in the install log" in summary
    assert "- time: session 20s (first to last timestamp); steps (start to end): " not in summary
    assert "- time: session 20s (first to last timestamp)" in summary


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
    assert "- attempt: 2 of 2 (earlier: attempt 1 failed at step 1)" in summary
    assert "Items that were not one command (attempt 2):" in summary
    fr = section(text, "Agent friction log")
    assert "2 attempt(s):" in fr
    assert (
        "- attempt 1 (lines 1-6; started 2026-09-29T09:00:00Z, prompt v5, agent Claude Code): failed at "
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
        "no v5 event line (1 legacy v4 line(s)))"
    ), "a second Prompt: block is a second attempt (J5); legacy lines never make it fully one command"
    assert "- agent said: in progress" in summary and "- attempt: 2 of 2 (earlier: attempt 1 " in summary
    assert "- human turns: 0 (0 questions; clicks: none possible; 0 approvals)" in summary, (
        "no keyword guessing (J1)"
    )
    assert (
        "- expected turns: the folder question: not logged · Allow clicks: none possible (launchd " in summary
    )
    fr = section(text, "Agent friction log")
    assert (
        "no Attempt: line (v4 format)" in fr and "2 legacy v4 line(s) (F<n> | ...), shown, not counted" in fr
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


def test_first_sync_doctor_warns_and_it_draft_in_the_summary(fake_mac: dict[str, Path]) -> None:
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
    assert (
        "- first sync: done in 34s (install.log) · 1 of 2 source(s) listed completely (status: baseline "
        "complete)" in summary
    )
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
    text, summary = summary_of(fake_mac, doctor=doctor, status=status)
    assert (
        f"- IT draft: {setup_report.IT_DRAFT} exists; 4 field(s) left for you (<it-contact>, "
        "<requester-upn>, <team>, <wanted-by>); 3 left for IT" in summary
    )
    for raw in RAW:
        assert raw not in text, raw
    draft.write_text(draft_text.replace("`<team>`", "Finance").replace("`<wanted-by>`", "Friday"))
    _text, summary = summary_of(fake_mac, doctor=doctor, status=status)
    assert "exists; 2 field(s) left for you (<it-contact>, <requester-upn>); 3 left for IT" in summary
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
        "Residue check: 1 capitalised or joined word(s) next to a placeholder in this report (Agent friction "
        "log: Board); check them." in red
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
    assert re.search(r"\d+ check\(s\): \d+ ok", section(text, "Doctor"))


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
    assert "status: cannot read the state: ConfigError" in status


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
        "- **outcome: worked with help** (computed: install.sh exit 0; 1 unexpected doctor FAIL(s): tcc)"
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
        "Residue check: 1 capitalised or joined word(s) next to a placeholder in this report (Recent errors: "
        "Roadmap); check them." in section(text, "Redaction")
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
        "Residue check: 3 capitalised or joined word(s) next to a placeholder in this report (A: Alpha, "
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
        "1 NEXT: line(s) in 1 run(s) (exactly one per install.sh run is expected) and 3 other "
        "instruction-like line(s) (1 next:, 1 fix:, 1 run:) in the last 75 line(s) of install.out"
    )
    inst = section(text, "Installer")
    assert f"Installer output: {counted}." in inst
    assert f"- installer output: {counted}" in section(text, "Summary")
    head = "<details><summary>the last 60 line(s) of ~/agent-context/setup/install.out (what the agent saw)"
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
    """Field report 2026-10-07: "6 NEXT: line(s) in 5 run(s)", and no run had printed two. The report reads
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
        "2 NEXT: line(s) in 2 run(s) (exactly one per install.sh run is expected) and 2 other "
        "instruction-like line(s) (2 fix:) in the last 206 line(s) of install.out (whole runs only: the "
        f"{cut} line(s) read before the first run header are not counted)"
    )
    text, summary = summary_of(fake_mac)
    inst = section(text, "Installer")
    assert f"Installer output: {counted}." in inst
    assert f"- installer output: {counted}" in summary
    shown = inst.split("<details><summary>the last 60 line(s) of ", 1)[1].split("~~~text\n", 1)[1]
    assert shown.split("\n~~~", 1)[0].splitlines() == [
        ln.replace("-4242", "") for ln in lines[-setup_report.INSTALL_OUT_TAIL :]
    ]

    # One run longer than what is read: no header in it, so nothing is left out and no run is counted.
    out.write_text("\n".join(out_run(1, 3000)) + "\n", encoding="utf-8")
    window = setup_report._tail(out)
    assert not any(ln.startswith("# run=") for ln in window)
    text, summary = summary_of(fake_mac)
    counted = (
        "1 NEXT: line(s) (exactly one per install.sh run is expected) and 1 other instruction-like line(s) "
        f"(1 fix:) in the last {len(window)} line(s) of install.out"
    )
    assert f"- installer output: {counted}\n" in summary + "\n"
    assert f"Installer output: {counted}. An instruction-like line" in section(text, "Installer")

    # install.sh keeps the file's last 2000 lines, which cuts a run the same way in a file read whole.
    lines = [*out_run(1, 5)[1:], *out_run(2, 5)]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _text, summary = summary_of(fake_mac)
    assert (
        "- installer output: 1 NEXT: line(s) in 1 run(s) (exactly one per install.sh run is expected) and 1 "
        "other instruction-like line(s) (1 fix:) in the last 8 line(s) of install.out (whole runs only: the "
        "7 line(s) read before the first run header are not counted)" in summary
    )


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
    first = "already synced on this Mac: 5 folder(s) (marked [synced] below: first the 2 outside the list, "
    keep = (
        "NEXT: this Mac already syncs 5 folder(s), and a re-run keeps them: run "
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
    shown = section(text, "Installer").split("(what the agent saw)</summary>", 1)[1]
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
    assert "NEXT: this Mac already syncs 5 folder(s), and a re-run keeps them: run ~/src/" in shown
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
    shown = re.search(rf"args={under}(<folder-\d+>) {under}<path> {under}<path>\n", inst)
    assert shown, "the decoded name is a known folder; the other two are cut below the provider folder"
    assert inst.count(f"install.sh {under}{shown.group(1)} {under}<path> {under}<path>\n") == 2
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
        "ran install.sh --source-local ~/Library/CloudStorage/Dropbox/<folder-1>/<path> twice\n"
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
    assert "<folder-N> folder (under ~/Library/CloudStorage, or a configured source's folder)" in section(
        text, "Redaction"
    ), "the legend covers the folder it has just named outside CloudStorage"
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
        "- expected turns: the folder question (step 1; not logged) · Allow clicks: none possible (launchd "
        "simulated)" in summary
    )
    assert "- friction (attempt 1): 1 event line(s): 1 finished" in summary
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


V7_HAPPY = V6_HAPPY.replace("Prompt: v6", "Prompt: v7")
"""Setup prompt v7 run through with nothing to log (KISS K04/K16b): the same header and closing line as v6."""

V8_HAPPY = V7_HAPPY.replace("Prompt: v7", "Prompt: v8")
"""Setup prompt v8: v7's three steps and rules. Its ``Prompt:`` line is the version the pasted copy gave
``install.sh --log-start``, so a saved v7 copy still reads ``v7`` (or ``v7 or older``)."""

V9_HAPPY = V8_HAPPY.replace("Prompt: v8", "Prompt: v9")
"""Setup prompt v9, this build's: the same steps and rules. On a Mac that already syncs folders its step 1
asks at most whether to add one, and with nobody to ask it goes on."""


def test_prompt_layout_is_picked_by_explicit_version() -> None:
    """KISS K16b: v5 and earlier read as v5, v6 as v6, v7 as v7. The version moves with every change of the
    prompt's text and a layout only when a step moves, so v8, v9 and every later version are read with the
    newest layout at or below them, as an attempt that states no version is."""
    read_as = [setup_report.prompt_layout(v).version for v in (0, 4, 5, 6, 7, 8, 9, None, 99)]
    assert read_as == [5, 5, 5, 6, 7, 7, 7, 7, 7]
    assert setup_report.PROMPT_VERSION == 9 and setup_report.PROMPT_VERSION not in setup_report.PROMPT_LAYOUTS
    for version in (8, 9):  # v8 and v9 moved no step: each gets every rule written for v7
        assert setup_report.prompt_layout(version) is setup_report.PROMPT_LAYOUTS[7]
    v6, v7 = setup_report.PROMPT_LAYOUTS[6], setup_report.PROMPT_LAYOUTS[7]
    assert (v7.install_step, v7.report_step) == (v6.install_step, v6.report_step) == (2, 3)
    assert max(v7.steps) == 3 and max(v6.steps) == 4
    for version in (7, 8, 9):
        label = setup_report.Outcome("failed", 3, (), version).form_label
        assert label == "Failed at step 3 (IT request and report)", "the form's options are unchanged"
    assert setup_report.Outcome("failed", 3, ()).form_label == label, "the default is this build's prompt"


V8_BUSY_LINES = (
    "2026-09-29T09:58:30Z | step 1 | question | asked which terminal app this is | say it\n"
    "2026-09-29T10:00:20Z | step 2 | click | clicked Allow on a Keychain prompt | -\n"
    "2026-09-29T10:01:00Z | step 2 | prompt | the prompt does not say what a second run prints | say it\n"
    "2026-09-29T10:01:00Z | step 2 | error | Exit 0, no failure: the link shows a placeholder | -\n"
    "2026-09-29T10:01:00Z | step 3 | deviation | wrote the questions in three parts | -\n"
)
"""Lines that meet each rule keyed on the prompt version: a question and a click beyond the expected turns,
and a step 2 error logged after install.sh exited 0, which is agent friction since v7 and stopped a v6 run."""


@pytest.mark.usefixtures("clean_doctor")
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
                "1 question(s) beyond the folder question",
                "1 click(s) beyond the Allow clicks",
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
    assert "- **outcome: worked with help** (computed: install.sh exit 0; 1 question(s) beyond" in said
    assert "- expected turns: the folder question (step 1; not logged) · " in said
    assert "- agent friction: 1 deviation, 1 prompt, 1 error (F6, F7, F8); none of these changes" in said
    assert "IT draft" not in said, "since v7 there is no IT request step"
    assert links[8]["prompt_version"] == ["v8"] and links[7]["prompt_version"] == ["v7"]
    assert {k: v for k, v in links[7].items() if k not in ("prompt_version", "title")} == {
        k: v for k, v in links[8].items() if k not in ("prompt_version", "title")
    }


@pytest.mark.usefixtures("clean_doctor")
def test_an_attempt_a_stopped_copy_of_the_prompt_started_says_so_in_the_summary(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """``install.sh --log-start`` stops a copy of the prompt that is not its own, and logs the attempt with
    the version that copy gave: "v7 or older" for a copy from before v8, which names none. The report of
    that attempt reads it with v7's steps, fails it at step 1 by the installer's own error line, and its
    prompt line says the copy was older than the installer's. A copy newer than the installer reads the
    other way round. The installer's version and its reason come from that line, never from the number of
    the build that writes the report: here the installer was v8 or v12, whatever this build is. Only the
    installer's fixed words are shown."""
    what = "install.sh --log-start: the pasted setup prompt is {said} and this installer is for setup prompt"
    (fake_mac["setup"] / "install.log").unlink()  # the command stopped at --log-start: no install.sh run
    assert setup_report.PROMPT_VERSION < 12
    for header, installer, shown, why in (
        ("v7 or older", 8, f"v7 or older {OLDER_COPY}", "the pasted copy is not the current one"),
        ("v7", 8, f"v7 {OLDER_COPY}", "the pasted copy is not the current one"),
        ("v7 or older, said Fabrikam", 8, f"v7 {OLDER_COPY}", "the pasted copy is not the current one"),
        (
            "v9",
            8,
            "v9 (newer than the installer's v8: this Mac's checkout is older than the pasted copy)",
            "this checkout is older than the prompt",
        ),
        (
            "v9",
            12,
            "v9 (older than the installer's v12: the pasted copy was not the current README)",
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


@pytest.mark.usefixtures("clean_doctor")
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
    [current] = setup_report.parse_friction(V9_HAPPY).attempts
    assert setup_report._stopped_before_the_list(current, []) is False, "only the installer's stop counts"


# ---- a Mac that is already set up is not asked for its folders again (field report 2026-10-07) -----------

ASKED = "the folder question (step 1; not logged)"
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
            f"{at} run={run} start install.sh compat=9 commit=0123456789ab kind=checkout source=- "
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
    write_friction(fake_mac, V9_HAPPY)
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
            "kept the 1 already synced (none added) · 0 named with --source-local",
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
        write_friction(fake_mac, V9_HAPPY)
        rc, text, _ = report(tmp_path, fake_mac["config"])
        summary = section(text, "Summary")
        assert rc == 0 and _line(summary, "- expected turns: ").startswith(f"- expected turns: {question} · ")
        asked = 1 if question == ASKED else 0
        assert _line(summary, "- human turns: ").startswith(f"- human turns: {asked} ({asked} question")
        found = [ln for ln in summary.splitlines() if ln.startswith("- folders: ")]
        assert found == ([f"- folders: {folders} (install.log)"] if folders else []), (listed, installs)
        for raw in (*RAW, "Plans"):
            assert raw not in text, raw
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
        write_friction(fake_mac, V9_HAPPY)
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
        'lists them), then run `agentsync add-source "<folder>"` for each, then `agentsync sync`'
    )
    assert text("NEXT: keep about 10 in _eval/questions.md") == "NEXT: keep about 10 in _eval/questions.md"
    # The draft baseline's wait names the docs repo's _eval folder by path: the report keeps its last part.
    wait = "WAITING ON YOU: the baseline questions are a draft: in {}, keep about 10 in questions.md"
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
    assert "~/" not in loop and "/Users/" not in loop and "`agentsync sync`" in loop
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
        "note: 3 file(s) in one wait to be read again, and the last sync read none of them (a converter or "
        "on-device OCR that cannot run, or a folder that could not be listed): another sync does not clear "
        "it; it does not block the next step"
    )
    step = "NEXT: stop: the operator confirms the baseline questions (WAITING ON YOU below); session done"
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
    assert "NEXT: not read (RuntimeError)" in section(text, "Summary"), "the message may hold a path"


def test_the_loop_line_relays_the_first_wait_and_took_counts_the_hook(fake_mac: dict[str, Path]) -> None:
    """KISS K16b review: a WAIT is where setup stopped (a held listing's Allow click), so the Loop line
    carries it, path-free, and a count of the rest; the hook runs before ``took`` is measured."""

    def slow(config: object) -> list[str]:
        time.sleep(1.0)
        return [
            "NEXT: draft the baseline questions: run `~/.local/bin/agentsync sync`",
            "WAITING ON YOU: macOS held the listing of one for a privacy prompt: click Allow on the macOS "
            "prompt (it can sit behind other windows), then run `/Users/x/.local/bin/agentsync sync`",
            "WAITING ON YOU: 2 queued purge(s): run `~/.local/bin/agentsync purge --queue`",
            "note: 3 online-only file(s) in one wait for a later sync's download budget",
        ]

    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks(loop_next=slow))
    [loop] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert loop.endswith(
        "; NEXT: draft the baseline questions: run `agentsync sync`; WAITING ON YOU: macOS held the listing "
        "of one for a privacy prompt: click Allow on the macOS prompt (it can sit behind other windows), "
        "then run `agentsync sync` (+1 more)"
    ), loop
    took = re.search(r"^- took: ([0-9.]+)s", text, flags=re.MULTILINE)
    assert took is not None and float(took.group(1)) >= 1.0, "took counts the Loop line's hook"


def test_an_exclude_line_never_carries_its_folder_names_into_the_report(fake_mac: dict[str, Path]) -> None:
    """Bring-back S11: the wait for an empty cloud folder prints a ready-to-paste exclude line naming folders
    below a source root, which the Redactor has never seen. The report shows the line without its globs,
    also when a line was cut inside the list."""
    wait = (
        "WAITING ON YOU: 2 empty cloud folder(s) keep the listing of one incomplete (deletions held; another "
        'sync does not clear it): if they are meant to be empty, set exclude = ["~$*", "/Fabrikam Bids/", '
        "\"/Plans/Tailspin [[]old]/\"] in [[source]] id = 'one' in sources.toml; an excluded folder is not "
        "mirrored if it later gains files"
    )
    hooks = setup_report.ReportHooks(loop_next=lambda config: ["NEXT: session done", wait])
    text, _red = setup_report.build_report(fake_mac["config"], hooks=hooks)
    [loop] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert "set exclude = [<path>] in [[source]] id = " in loop and loop.endswith("later gains files"), loop
    assert "Fabrikam" not in text and "Tailspin" not in text
    red = setup_report.Redactor()
    cut = 'fix: set exclude = ["~$*", "/Fabrikam Bi'
    assert setup_report._redact_lines(red, [cut]) == "fix: set exclude = [<path>]"


@pytest.mark.usefixtures("clean_doctor")
def test_a_draft_baseline_shows_its_wait_on_the_loop_line(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    """Rule 5's NEXT says the wait is below: the Loop line carries it."""
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
        "- Loop: baseline drafted; NEXT: stop: the operator confirms the baseline questions (WAITING ON YOU "
        "below); session done; WAITING ON YOU: the baseline questions are a draft: in _eval, keep about 10 "
        "in questions.md, correct the answers in answers.md, and change both files to status: confirmed"
    ), loop


@pytest.mark.parametrize("under_home", [True, False])
def test_the_loop_line_keeps_no_part_of_a_docs_repo_path_with_spaces(
    fake_mac: dict[str, Path], tmp_path: Path, under_home: bool
) -> None:
    """Review of the v9 rehearsal fixes (2026-10-07): the draft baseline's wait names the docs repo's
    ``_eval`` folder by its path, and the Loop line shows a path as its last part, ``in _eval``. A path was
    read up to its first space, so a docs repo at "~/Client Alpha/kb docs" left ``in Client Alpha/kb
    docs/_eval`` in a line that holds no path. The folder is now taken out as the loop wrote it, with the
    loop's own lines here: under the home folder (``~/...``) and outside it (the full path)."""
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
    draft = "WAITING ON YOU: the baseline questions are a draft: in {}, keep about 10 in questions.md, "
    assert [ln for ln in written if ln.startswith(draft.format(shown))], written

    hooks = setup_report.ReportHooks(loop_next=lambda config: loop.next_lines(config, count_queue=False))
    text, _red = setup_report.build_report(cfg, hooks=hooks)
    [line] = [ln for ln in section(text, "Summary").splitlines() if ln.startswith("- Loop: ")]
    assert line.endswith(
        f"; {draft.format('_eval')}correct the answers in answers.md, and change both files to status: "
        "confirmed"
    ), line
    assert "Client" not in line and "kb docs" not in line and "/" not in line.split("; WAITING ON YOU: ")[1]


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
    purge = "WAITING ON YOU: 1 queued purge(s): run `agentsync purge --queue`"

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
    assert len(found) == 2 and "queued purge(s)" in found[0] and found[1].startswith(setup_report._WAIT_HELD)
    line = loop_line()
    assert re.search(
        r"; WAITING ON YOU: macOS held the listing of <(?:source|folder)-\d+> for a privacy prompt: click "
        r"Allow on the macOS prompt \(it can sit behind other windows\), then run `agentsync sync` "
        r"\(\+1 more\)$",
        line,
    ), line
    assert "queued purge" not in line and "(WAITING ON YOU below)" not in line

    # A draft baseline: rule 5's NEXT says the wait is below, and the loop prints that wait last.
    evals = expand(config.docs_repo) / "_eval"
    evals.mkdir(parents=True, exist_ok=True)
    (evals / "questions.md").write_text("status: draft\n\n1. Who approved it?\n", encoding="utf-8")
    (evals / "answers.md").write_text("status: draft\n\n1. Finance.\n", encoding="utf-8")
    found = waits()
    assert len(found) == 3 and found[-1].startswith(setup_report._WAIT_DRAFT)
    assert setup_report._WAIT_BELOW in loop.next_lines(config, count_queue=False)[0]
    assert loop_line().endswith(
        "; NEXT: stop: the operator confirms the baseline questions (WAITING ON YOU below); session done; "
        "WAITING ON YOU: the baseline questions are a draft: in _eval, keep about 10 in questions.md, "
        "correct the answers in answers.md, and change both files to status: confirmed (+2 more)"
    )


def test_the_wait_the_loop_stopped_on_is_picked_by_what_its_next_line_says() -> None:
    """The rule by itself, on lines in the loop's order. The draft wait is shown only when the NEXT line
    points at a wait; without that a held listing comes before it; and a NEXT that points at a wait the
    lines do not have falls back to the same order."""
    purge = "WAITING ON YOU: 14 queued purge(s): run `~/.local/bin/agentsync purge --queue`"
    breaker = "WAITING ON YOU: the deletion breaker tripped on work (3 file(s) gone)"
    held = "WAITING ON YOU: macOS held the listing of work for a privacy prompt: click Allow"
    draft = "WAITING ON YOU: the baseline questions are a draft: in ~/agent-context/docs/_eval, keep about 10"
    rule5 = "NEXT: stop: the operator confirms the baseline questions (WAITING ON YOU below); session done"
    other = "NEXT: run `~/.local/bin/agentsync curate` and follow its NEXT line"
    pick = setup_report._stopped_wait
    assert pick(rule5, [purge, breaker, held, draft]) == draft
    assert pick(other, [purge, breaker, held, draft]) == held
    assert pick(other, [purge, breaker, draft]) == purge
    assert pick(rule5, [purge, breaker, held]) == held
    assert pick(rule5, [purge, breaker]) == purge
    assert pick(None, [breaker]) == breaker


def test_a_doctor_fail_is_the_loop_lines_next_and_no_friction_log_hides_the_it_draft(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI wiring: the doctor hook's ERROR FAILs reach ``loop_next`` as rule 1 (Doctor runs first). With
    no friction log the report reads the newest prompt (v7), which has no IT request step."""
    real = cli._status_checks

    def failing(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
        broken = doctor.CheckResult(
            "manifest.integrity", False, "broken", doctor.Severity.ERROR, f"rm {fake_mac['home']}/state.db"
        )
        return [*(c for c in real(config, offline=offline) if c.ok), broken]

    assert cli.main(["sync", "--config", str(fake_mac["config"])]) == 0  # writes the skill (rule 1's first)
    monkeypatch.setattr(cli, "_status_checks", failing)
    rc, text, _ = report(tmp_path, fake_mac["config"])
    assert rc == 0
    summary = section(text, "Summary")
    [loop] = [ln for ln in summary.splitlines() if ln.startswith("- Loop: ")]
    assert (
        "; NEXT: the manifest.integrity check failed: do what the fix on its [FAIL] line below says" in loop
    )
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
        "- **outcome: worked with help** (computed: install.sh exit 0; 1 question(s) beyond the folder "
        "question; 1 click(s) beyond the Allow clicks; 1 approval(s))"
    )
    assert (
        "- human turns: 4 (2 questions; 1 click beyond the announced Allow clicks (not logged); 1 approval)"
        in summary
    )
    assert (
        "- expected turns: the folder question (step 1; not logged) · the announced Allow clicks (steps 1 "
        "and 2; not logged)" in summary
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
        "- expected turns: the folder question (step 1; not logged) · the announced Allow click (step 1; not "
        "logged)" in summary
    )
    assert "steps 1 and 2" not in summary


def test_v6_an_error_stops_the_run_unless_a_later_install_run_succeeded(fake_mac: dict[str, Path]) -> None:
    """v6 logs no step ends: install.log's later successful run of that step resolves an error (L-series)."""
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
        "2026-09-29T10:01:00Z | step 2 | error | background sync did not start (exit 3) | -\n",
        V6_HAPPY,
    )
    write_friction(fake_mac, late)
    text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: failed at step 2** (computed: install.sh exit 0; step 2 logged an error (F4) and did "
        "not finish: no later install.sh run of that step ended rc 0 and no later step before the report)"
    )
    assert "outcome=Failed%20at%20step%202%20%28install%20and%20start%29" in text.splitlines()[-1]
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
    assert "3 attempt(s):" in fr
    assert "- attempt 1 (lines 1-4; started 2026-09-29T09:58:00Z, prompt v6, " in fr
    assert re.search(r"^- attempt 1 \(.*\): fully one command; 1 event line\(s\): 1 finished", fr, re.M)
    assert (
        "- attempt 2 (lines 5-5; no Attempt: line (logged after the previous attempt finished), prompt not "
        'stated, agent not stated): failed at step 1; 1 event line(s): 1 error; no "end | finished" line'
        in fr
    )
    assert "- attempt 3 (lines 6-9; started 2026-10-03T09:00:00Z, prompt v7, " in fr
    assert "- attempt: 3 of 3 (earlier: attempt 1 fully one command, attempt 2 failed at step 1)" in summary
    assert "- friction (attempt 3): 1 event line(s): 1 finished" in summary


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
    assert "1 attempt(s):" in section(text, "Agent friction log") and "- attempt:" not in summary
    assert "**outcome: failed" not in summary and "- prompt: v7 · " in summary
    assert "0 prompt, 1 error (F5); none of these changes the outcome by itself" in summary


def test_a_step_1_error_logged_long_after_the_closing_line_is_the_latest_attempt(
    fake_mac: dict[str, Path],
) -> None:
    """A session whose step 1 stopped before ``install.sh --log-start`` logs one error and writes its own
    report ("log it and go to step 3's report"): it has no ``Attempt:`` header, and none follows. That
    session failed at step 1; folded into the attempt before it, the report read "fully one command"."""
    v6_install_log(fake_mac)
    late = "2026-10-06T02:16:01Z | step 1 | error | install.sh --version exited 127 | -\n"
    write_friction(fake_mac, V7_HAPPY + late)
    text, summary = summary_of(fake_mac)
    assert summary.strip().splitlines()[0] == (
        "- **outcome: failed at step 1** (computed: no install.sh run; step 1 logged an error)"
    )
    assert "- attempt: 2 of 2 (earlier: attempt 1 fully one command)" in summary
    assert (
        "- note: attempt 2 has no Attempt: line. It starts at a step 1 error logged after attempt 1"
        in summary
    )
    assert "WARNING" not in summary, "install.sh --report-only cannot close an attempt that has no header"
    assert "- F5 · step 1 · error · install.sh --version exited 127" in summary
    assert (
        "- attempt 2 (lines 5-5; no Attempt: line (logged after the previous attempt finished), prompt not "
        'stated, agent not stated): failed at step 1; 1 event line(s): 1 error; no "end | finished" line'
        in section(text, "Agent friction log")
    )
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["outcome"] == ["Failed at step 1 (preflight)"] and "prompt_version" not in link

    def attempts(minute: int) -> int:
        line = f"2026-09-29T10:{minute}:00Z | step 1 | error | git pull --ff-only exited 1 | -\n"
        return len(setup_report.parse_friction(V7_HAPPY + line).attempts)

    assert (attempts(11), attempts(12)) == (1, 2), "the closing line is 10:01:10: ten minutes is the line"


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
    assert "2 attempt(s):" in fr and "failed at step" not in fr
    assert re.search(r"^- attempt 1 \(lines 1-7; .*\): fully one command; 4 event line\(s\): ", fr, re.M)
    assert "- attempt: 2 of 2 (earlier: attempt 1 fully one command)" in summary


@pytest.mark.usefixtures("clean_doctor")
def test_v7_a_step_2_error_logged_after_the_install_ended_0_is_agent_friction(
    fake_mac: dict[str, Path], tmp_path: Path
) -> None:
    """Field report 2026-10-06: v7's step 2 is the one install.sh command. The agent logged its lines in a
    batch after that command exited 0, one of them a step 2 ``error`` that said "Exit 0, no failure", and
    the outcome read "failed at step 2". Any install run of the attempt that ended 0 resolves it; the
    Summary and the issue link agree."""
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
    assert "- install.sh: 2 install runs (+1 --list-folders) during this attempt; the last exit 0" in summary
    items = summary.split("Items that were not one command (attempt 1):", 1)[1].split("\n\n", 2)[1]
    assert items == (
        "- install.sh · step 2 · run 20260929T100000Z exited 1 at its status step; the same command was run "
        "again and ended 0"
    ), "the run is the item: the list said none beside worked with help"
    fr = section(text, "Agent friction log")
    assert "1 attempt(s):" in fr and "- attempt:" not in summary, "the late lines start no attempt"
    assert re.search(
        r"^- attempt 1 \(lines 1-7; .*, prompt v8, .*\): worked with help; 4 event line", fr, re.M
    )
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["outcome"] == ["Worked with help"]
    assert link["title"][0].startswith("Setup report: Worked with help · ") and link["title"][0].endswith(
        "v8"
    )

    # A later attempt is judged on its own runs: the failed run of the one before does not count against it,
    # and the earlier attempt keeps its outcome on the "earlier:" line.
    later = V9_HAPPY.replace("2026-09-29T09:58:00Z", "2026-10-03T09:00:00Z").replace(
        "2026-09-29T10:01:10Z", "2026-10-03T09:03:00Z"
    )
    log.write_text(log.read_text(encoding="utf-8") + install_run("2026-10-03T09:00:30Z", 0), encoding="utf-8")
    write_friction(fake_mac, retry_friction() + later)
    text, summary = summary_of(fake_mac)
    assert summary.strip().startswith("- **outcome: fully one command**")
    assert "- attempt: 2 of 2 (earlier: attempt 1 worked with help)" in summary
    assert "Items that were not one command (attempt 2):\n\n- none" in summary

    # The link is built from the same runs when the Summary could not be written. It was given every run
    # in install.log, so the earlier attempt's failed run would have counted against this one.
    def broken(*args: object, **kwargs: object) -> list[str]:
        raise RuntimeError("no summary")

    monkeypatch.setattr(setup_report, "_summary", broken)
    text, _red = setup_report.build_report(fake_mac["config"])
    assert "_This section failed: RuntimeError" in section(text, "Summary")
    link = parse_qs(urlsplit(text.rstrip("\n").splitlines()[-1]).query)
    assert link["outcome"] == ["Fully one command"] and link["prompt_version"] == ["v9"]


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
    """Who the rule is for. v7, v8 and v9 share their steps, so the same log reads the same under each. v5
    and v6 announce the launcher's Allow click in the install step: a run that timed out waiting for it was
    theirs to run again, and their logs are judged as before. A ``--list-folders`` run is no install run,
    so step 1's own click and the list's re-run cost nothing. An attempt with no time gets every run in
    install.log, an earlier attempt's too, so none is counted for it."""
    pair = (
        install_run("2026-09-29T10:00:00Z", 1, status_failed=True),
        install_run("2026-09-29T10:02:00Z", 0),
    )
    for happy in (V7_HAPPY, V8_HAPPY, V9_HAPPY):
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
    assert "4 expected warn(s) end in (expected: <why>) instead of their fix" in doc
    lines = {ln.split(" — ", 1)[0].split("] ", 1)[1].strip(): ln for ln in doc.splitlines() if " — " in ln}
    assert lines["launcher.signature"].endswith("new TCC subject (expected: ad hoc launcher)")
    assert lines["launcher.requirement"].endswith("cannot pin a cdhash) (expected: ad hoc launcher)")
    for name in ("launchd.poll", "launchd.reconcile"):
        assert lines[name].endswith("is not installed (expected: LaunchAgents not installed yet)"), name
    assert lines["network.proxy"].endswith("(fix: set [network] proxy)"), "an unexpected warn keeps its fix"
    assert "SIGN_IDENTITY" not in text and "install-agent)" not in doc
    assert "unexpected 1: network.proxy warn" in summary


def test_ocr_not_built_is_an_info_line_and_an_ocr_warn_is_unexpected(fake_mac: dict[str, Path]) -> None:
    """Doctor's ``ocr`` line when the helper is not built is INFO: shown, never counted as a problem, so
    ``expected_warn`` needs no entry for it.  An ``ocr`` warn means OCR is broken: unexpected, with its fix,
    and (OCR being optional) with the same outcome."""
    v6_install_log(fake_mac)
    write_friction(fake_mac, V6_HAPPY)
    info = "[info] ocr — the OCR helper is not built; scripts/install.sh builds it"
    warn = (
        "[warn] ocr — on-device OCR is not working: no Xcode or Command Line Tools (xcode-select -p names "
        "no folder) (fix: xcode-select --install, then run scripts/install.sh again)"
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
    assert "Residue check: no capitalised or joined word next to a placeholder in this report." in red
    assert "<tmp> this account's temporary folder" in red
    assert setup_report.residue("/Users/<user>/Library/Application Support/<folder-1> Board <org-1>") == [
        "Board"
    ]
    assert setup_report.residue("/Users/<user>/Development/x and /Volumes/<folder-1>/Roadmap") == [
        "Roadmap"
    ], "a folder in the login's home is a path component; a folder below a redacted one is still checked"
    assert setup_report.residue("~/Development/<folder-1> and /Users/<user>/Projects/Work/<folder-2>") == []
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
    assert (
        facts
        == f" @ {sha} · origin: other (redacted) · on origin/main: yes (as of the checkout's last fetch)"
    )
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
    assert f"(uncommitted changes, tree={tree})" in facts and "on origin/main: no " in facts


def test_environment_says_whether_each_provider_folder_is_a_file_provider_domain(
    fake_mac: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L10: a real OneDrive domain versus a sandbox's plain folder, from the folder's own attributes."""
    if sys.platform == "darwin":
        assert setup_report.file_provider_marker(tmp_path) is None, "a plain folder has none"
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    env = section(text, "Environment")
    assert "OneDrive-<org-1> (not a File Provider domain: no File Provider attribute (a plain folder))" in env
    monkeypatch.setattr(
        setup_report,
        "file_provider_marker",
        lambda p: "com.apple.file-provider-domain-id" if p.name == f"OneDrive-{ORG}" else None,
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    env = section(text, "Environment")
    assert "OneDrive-<org-1> (File Provider domain (com.apple.file-provider-domain-id))" in env
    assert "OneDrive-SharedLibraries-<org-1> (not a File Provider domain" in env


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
    assert (
        "- helper: off (AGENTSYNC_OCR=0)" in parts["OCR"] and "- label rule in [policy]: off" in parts["OCR"]
    )
    assert "- images: 0 file(s)\n" in parts["OCR"] and "- OCR time: no run is recorded" in parts["OCR"]
    assert parts["Quarantine by reason"].strip() == "- no file is quarantined or refused"
    assert parts["Purge queue"].strip() == "- no purge is queued"
    assert parts["Overlapping sources"].strip() == (
        "- none: no source's folder is inside another's (3 source(s) with a folder)"
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


def test_ocr_part_counts_images_documents_rereads_and_time(fake_mac: dict[str, Path]) -> None:
    seed_ocr(fake_mac)
    text, parts = status_parts(fake_mac)
    ocr_part = parts["OCR"]
    assert (
        "- images: 8 file(s): page 2 · no-text stub 1 · not-on-this-Mac stub 1 · no-converter stub on this "
        "Mac 1 · deferred on this Mac 1 · deferred online-only 1 · failed 1\n" in ocr_part
    )
    assert (
        "- images that are not on this Mac: 2 file(s), 8.0 MB (2 online-only, 0 of a Graph source; none is "
        "downloaded for OCR;" in ocr_part
    )
    assert (
        "- PDF (.pdf) files with a page: 1 with an OCR identity, 1 without an OCR identity\n" in ocr_part
    ), "the version is the cache row's when the page's key has one"
    assert (
        "- deck (.pptx) files with a page: 0 with an OCR identity, 0 without an OCR identity, 1 from the "
        "field build of OCR\n" in ocr_part
    )
    assert (
        "- Word (.docx) files with a page: 0 with an OCR identity, 1 without an OCR identity (1 "
        "online-only)\n" in ocr_part
    )
    assert (
        "- scanned PDFs with a stub: 1 no text layer (OCR not run), 1 no text layer (OCR found no text), 0 "
        "no text layer (over the OCR page limit)\n" in ocr_part
    )
    assert "- engine identities on pages: ocr-paper-vision-r2-h0.3.0-l1 (3 page(s))\n" in ocr_part
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
        "None of the last 3 run(s) says what its re-read looked for (a run of this build that reaches a "
        "local source does), so the second column is as last recorded, by whichever build wrote the record."
        in lead
    )
    assert (
        "The helper is not ready (its line above), so a cycle has no engine. Without an engine a cycle reads "
        "again only what needs none, such as a page of the field build. The third column is the report's own "
        "count of files on this Mac from before OCR, which a re-read looks at when there is an engine: "
        "images with the no-converter stub, scanned PDFs whose stub says OCR was not run and documents "
        "whose page has no OCR identity or is the field build's (fourth column)." in lead
    )
    assert (
        "- OCR time: of the last 3 run(s), 2 recorded these counts (a run of an earlier build did not) and 2 "
        "had an engine; 1 used up the cycle's OCR time; 1 ended with the helper not working; the helper ran "
        "184s in all\n" in ocr_part
    )
    assert (
        "- waiting for OCR: the newest run that had an engine (run 5) left 0 file(s) for a later cycle's "
        "OCR; the 3 run(s) add up to 3 wait(s), a file counted once in every run it waited in\n" in ocr_part
    )
    assert (
        "- in those runs, each count a sum over the runs (a file converted or read in two of them counts "
        "twice): 2 conversion(s) without OCR because the cycle's OCR time was used up · 2 without OCR "
        "because the helper had stopped working · 1 the engine failed on (a helper failure, or the file's "
        "own time limit) · 1 that say pages past the OCR page limit were not read · 1 that say pictures past "
        "the picture limit were not read · 4 read(s) again (1 of them could not be converted and kept "
        "their page)\n" in ocr_part
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
    assert "\nthe last runs that had an engine, newest first. A run starts no more re-reads once" in ocr_part
    assert (
        "The count includes the files given up (two failed tries). A file that failed once is tried once "
        "more while its source's scan is not finished; under a finished scan it is no longer among the files "
        "left to read again (read since, changed, online-only or gone), and the record keeps its count:"
        in lead
    )
    assert (
        "- <folder-2>, the 1 file(s) that failed once, by their row in the manifest now: 1 with no row\n"
        in ocr_part
    ), "the record's id is no row of this manifest"
    for raw in ("id-9", "id-10", 'id-1"', "a" * 16, "KeyError"):
        assert raw not in text, raw


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
        "7 run(s), 6 recorded these counts (a run of an earlier build did not) and 6 had an engine"
        in ocr_part
    )
    assert (
        "the helper ran 600s in all\n" in ocr_part and " · 442 read(s) again (0 of them could not" in ocr_part
    )
    title = next(ln for ln in ocr_part.splitlines() if ln.startswith("the last "))
    assert title == (
        "the last 5 of the 6 runs that had an engine, newest first (the sums above are over all 7 run(s) "
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
        "- <folder-2>, the 5 file(s) that failed once, by their row in the manifest now: 1 on this Mac · 1 "
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
    assert "\nthe last runs that had an engine, newest first. A run starts no more" in ocr_part
    assert " · 411 read(s) again (0 of them" in ocr_part
    # More runs than are shown and none had an engine: the same count of what is hidden.
    seed = Seed(fake_mac["config"])
    seed.m._db.execute("DELETE FROM runs")
    for run_id in range(1, 8):
        seed.run(run_id, {"converted": 1})
    seed.close()
    ocr_part = status_parts(fake_mac)[1]["OCR"]
    assert (
        "\nthe last 5 of the 7 runs (none had an engine), newest first (the sums above are over all 7 run(s) "
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
    assert "- images: 3 file(s): not-on-this-Mac stub 2 · no-converter stub on this Mac 1\n" in parts["OCR"]
    assert (
        "- images that are not on this Mac: 2 file(s), 0.0 MB (1 online-only, 1 of a Graph source; none is "
        "downloaded for OCR;" in parts["OCR"]
    )
    assert reread_rows(parts["OCR"]) == {"<folder-2>": ["no record", "1", "0", "0", "0", "0", "no"]}, (
        "a Graph source has no row: nothing of it is read again"
    )
    assert "tailspin-drive" not in text


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
        "- waiting for OCR: the newest run that had an engine (run 34) left 0 file(s) for a later cycle's "
        "OCR; the 35 run(s) add up to 16170 wait(s), a file counted once in every run it waited in\n"
        in ocr_part
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
    assert "- label rule in [policy]: on (no image is read under one)\n" in status_parts(fake_mac)[1]["OCR"]


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
        "| (not in the config, 1) | refused | duplicate | 1 | 0 | - |",
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
        more = re.search(r" \(\+(\d+) file\(s\) of \d+ more type\(s\)\)$", cells)
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
        "other 3 (1 online-only, 2 distinct)",
        "- <folder-3>, no converter by type: .zip 1",
    ], "most first, then by name; a deleted file and a file refused for its label are not counted"
    assert {src: sum(found.values()) for src, found in type_counts(part).items()} == table_counts(part)
    assert table_counts(part) == {"<folder-2>": 8, "<folder-3>": 1}
    assert ".final" not in text and "Secret" not in text, "SECRET_NAMES holds the other tail"
    assert residue_free(text), "no word beside the source's placeholder reads as a name the Redactor missed"


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
        f"- <folder-2>, no converter by type: {shown} · no extension 1 · other 1 (1 distinct) (+3 file(s) of "
        "3 more type(s))"
    )
    counts = type_counts(part)["<folder-2>"]
    assert counts["more"] == 3 and sum(counts.values()) == table_counts(part)["<folder-2>"] == 20


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
    assert set(REASONS.values()) == set(setup_report.QUARANTINE_CLASSES), "every class has a case here"


def test_repeat_conversions_part_reads_the_run_records_and_the_cache(fake_mac: dict[str, Path]) -> None:
    seed_ocr(fake_mac)
    seed = Seed(fake_mac["config"])
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
    assert (
        "- of the last 3 run(s), 2 recorded what they converted; 2 of those converted at least one file "
        "again from the bytes its page was made from, which the run just before had converted too (the sign "
        "of a loop; a copy of a file is a new file and is not counted)\n" in part
    )
    assert (
        "| files converted | of them failed | the same file from the same bytes as an earlier run | of "
        "those, as the run just before |\n" in part
    )
    assert [ln for ln in part.splitlines() if re.match(r"\| \d", ln)] == [
        "| 5 | reconcile | ok | 2026-10-06T10:00:00Z | 6 | 1 | 6 | 6 |",
        "| 4 | poll | ok | 2026-10-06T10:00:00Z | 6 | 0 | 6 | 6 |",
        "| 3 | poll | ok | 2026-09-30T10:00:00Z | not recorded | - | - | - |",
    ]
    assert (
        "- converter cache: 4 conversion(s) were used again by a later run than the one that made them (the "
        "same bytes converted again, by the same file or by a copy of it); 2 of them last by the newest run "
        "(5), 1 by the run before it" in part
    )
    # Run 5 has a failed conversion: the part says what a later run without one does not mean.
    note = "- a conversion that failed is counted in the run it failed in and is not tried again until"
    assert note in part and 'Quarantine by reason, class "conversion failed"\n' in part
    seed = Seed(fake_mac["config"])
    seed.m._db.execute("UPDATE runs SET counts_json = ? WHERE run_id = 5", (json.dumps({"converted": 2}),))
    seed.close()
    assert note not in status_parts(fake_mac)[1]["Repeat conversions"], "no run shown has a failure"


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
    text, parts = status_parts(fake_mac)
    part = parts["Purge queue"]
    assert (
        "- 9 purge(s) queued. For a queued stable id the last six columns say what the manifest holds" in part
    )
    header = "| source | reason | selector | queued (UTC day) | purges | same bytes live | same path live | "
    assert header + "still listed | no live twin | no row | not looked up |" in part

    def rows(part: str) -> list[str]:
        return [ln for ln in part.splitlines() if ln.startswith("| ") and "---" not in ln][1:]

    assert rows(part) == [
        "| (not in the config, 1) | label-escalation | stable-id | 2026-10-05 | 1 | 0 | 0 | 0 | 0 | 1 | 0 |",
        "| <folder-2> | erasure-request | path-glob | 2026-10-05 | 1 | 0 | 0 | 0 | 0 | 0 | 1 |",
        # an id that is now an alias is judged by the row it points at: listed again, and deleted again
        "| inbox | upstream-deleted | stable-id | 2026-10-02 | 7 | 1 | 1 | 2 | 2 | 1 | 0 |",
    ]
    assert [ln for ln in part.splitlines() if ln.startswith("- ")][1:] == [
        "- renamed or re-keyed: 2 queued id(s) are an earlier id of a file the manifest now holds under a "
        "later one (it was saved again, or its volume's id changed). Each is counted by that file's row, "
        "since a purge follows the alias to it",
        "- still listed: 2 queued purge(s) name a file the manifest lists now (1 of them under a later id). "
        "A run of the queue would erase that file's page and its history",
        "- no trace: 2 queued id(s) have no row and are no alias. A re-key leaves an alias, so what took "
        "such a row away is a purge that already ran, or an erasure. A run of the queue erases only what "
        "history still names for them and takes them off the queue",
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
        rows(part)[2] == "| inbox | upstream-deleted | stable-id | 2026-10-02 | 7 | 1 | 1 | 1 | 1 | 3 | 0 |"
    )
    assert [ln for ln in part.splitlines() if ln.startswith("- ")][1:] == [
        "- still listed: 1 queued purge(s) name a file the manifest lists now. A run of the queue would "
        "erase that file's page and its history",
        "- no row: 4 queued id(s) have no row. This manifest has no alias table, so none was looked up as an "
        "alias: an earlier id of a file listed now cannot be told from one a purge took away",
    ]
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
        f"- {outer} (local, live) contains, 2 folder level(s) down, {inner_label} (local, live); the outer "
        f"source's exclude list prunes the inner folder: yes; {outer}: live 0 (online-only 0), stubs 0, "
        f"tombstones 1, listing complete: no; {inner_label}: live 4 (online-only 1), stubs 1, tombstones 0, "
        "listing complete: no"
    )
    assert "tailspin-deal" not in text and "client-alpha" not in text
    # Without the exclude line the outer walk reaches the inner folder too.
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('exclude = ["Wingtip merger/"]\n', ""), "utf-8")
    assert "exclude list prunes the inner folder: no;" in status_parts(fake_mac)[1]["Overlapping sources"]


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
        "6 unknown: 1 dataless, 2 materialised-and-empty (1 of them with a link count of 2: no entry by the "
        "folder's own metadata); 1 gone, 0 not readable, 1 not a folder, 1 not checked; 6 of 6 checked; 0 "
        "of the checked folder(s) excluded in sources.toml now; the mirror still holds 4 file(s) below 1 of "
        "the checked folder(s)"
    )
    assert part.count("\n- ") == 1, "a source whose walk held none has no line"
    assert not [p for p in listed if str(root) in p], "no folder of the source was listed"
    cfg = fake_mac["config"]
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            'id = "client-alpha"', 'id = "client-alpha"\nexclude = ["Northwind/", "payroll"]', 1
        ),
        encoding="utf-8",
    )
    part = status_parts(fake_mac)[1]["Empty cloud folders"]
    assert "; 2 of the checked folder(s) excluded in sources.toml now;" in part
    seed = Seed(cfg)
    seed.m.set_meta("empty_cloud_dirs:" + one, json.dumps([f"Wingtip {n}" for n in range(60)]))
    seed.close()
    capped = status_parts(fake_mac)[1]["Empty cloud folders"]
    assert "60 unknown: 0 dataless, 0 materialised-and-empty" in capped and "50 gone" in capped
    assert "; 50 of 60 checked;" in capped


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
    assert "; 50 of 60 checked; 1 of the checked folder(s) excluded in sources.toml now;" in part
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
    assert f"; excluded in sources.toml now: {setup_report.NOT_MEASURED}; the mirror still holds" in part
    assert '- evidence: 1 line(s) at the end of Status say "not measured"' in section(text, "Summary")
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
        '- evidence: 5 line(s) at the end of Status say "not measured": write the report again when this Mac '
        "is idle (`install.sh --report-only`) before sending it\n" in section(text, "Summary")
    )


class Clock:
    """``setup_report``'s ``time``: the real monotonic clock, which a test can move on."""

    def __init__(self) -> None:
        self.ahead = 0.0

    def monotonic(self) -> float:
        return time.monotonic() + self.ahead


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
    monkeypatch.setattr(setup_report, "_PROBE_S", 0.3)
    try:
        text, parts = status_parts(fake_mac)
    finally:
        release.set()
    assert "- helper: did not answer within 0.3s (a built helper's `--version`;" in parts["OCR"]
    assert (
        "The helper did not say whether it is ready (its line above; Doctor's ocr line does)." in parts["OCR"]
    )
    assert "The helper is not ready" not in parts["OCR"]
    assert "- label rule in [policy]: off\n" in parts["OCR"] and "too late" not in text
    assert "not measured" not in section(text, "Status"), "fixed words: a second report would say the same"
    assert (
        "- images: 8 file(s): page 2" in parts["OCR"] and "- OCR time: of the last 3 run(s)" in parts["OCR"]
    )
    assert "| <folder-2> | quarantined | no text in image | 1 | 0 | - |" in parts["Quarantine by reason"]
    assert "of the last 3 run(s), 2 recorded what they converted" in parts["Repeat conversions"]
    assert "- evidence: the 6 parts at the end of Status were measured\n" in section(text, "Summary")
    # On the report's own clock: a probe that answers after more time than the six parts have between them.
    clock = Clock()
    monkeypatch.setattr(setup_report, "time", clock)

    def slow(_convert: ConvertConfig, _cache_dir: Path) -> tuple[str, str]:
        clock.ahead += setup_report.EVIDENCE_BUDGET_S + 1.0
        return "ready", "paper-vision revision 2, helper 0.3.0"

    monkeypatch.setattr(ocr, "probe", slow)
    text, parts = status_parts(fake_mac)
    assert "- helper: ready (paper-vision revision 2, helper 0.3.0)\n" in parts["OCR"]
    assert "not measured" not in section(text, "Status"), "the probe's time is not the manifest's"
    assert "- images: 8 file(s): page 2" in parts["OCR"]


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
    for name, kind in (("Wingtip thread.MSG", ".msg"), ("Makefile", "no extension"), ("a.b c", "other")):
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
        "- images: 17045 file(s): page 11933 · not-on-this-Mac stub 1704 · failed 1704 · other stub 1704"
        in text
    )
    assert "- files whose row carries a reason: 9091 quarantined, 4546 refused" in text
    by_type = re.findall(
        r"^- (?:\(source \d\)|inbox), no converter by type: \.png (\d+) \((\d+) online-only\)$", text, re.M
    )
    assert len(by_type) == 3 and sum(int(files) for files, _online in by_type) == 4546
    assert "; 50 of 60 checked;" in text and "of the last 200 run(s), 200 recorded" in text
    assert (
        "- (source 1), the 2 file(s) that failed once, by their row in the manifest now: 1 on this Mac · 1 "
        "with a stub\n" in text
    )
    assert (
        "the last 5 of the 200 runs that had an engine, newest first (the sums above are over all 200" in text
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
        "900, this build 300); 1 key(s) this build does not write\n" in bg
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
    for raw in (*SECRET_NAMES, "FOURTH", "Coffee", "Payroll", "python3", "/bin"):
        assert raw not in bg, raw
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
    assert "- com.agentsync.poll: not compared (InvalidFileException)\n" in bg
    assert "- com.agentsync.reconcile: not compared (ConfigError)\n" in bg, (
        "no launcher: this build writes none"
    )


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


def test_installer_lists_every_run_and_the_ones_with_no_end_line(fake_mac: dict[str, Path]) -> None:
    """The first bring-back file counted four runs and showed three. Every run is one line now, and a run
    with no end line says when it started and the step it reached."""
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
    assert "5 install.sh run(s) in" in inst and "the last 3 (run ids without their process id):" in inst
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
        "- run 20261006T021601Z: started 2026-10-06T02:16:01Z, reached agentsync (failed, rc 1) after 2 step "
        "line(s)" in inst
    )
    assert "Wingtip" not in inst and "-777" not in inst


def test_configuration_counts_the_folders_named_like_a_coding_agent(fake_mac: dict[str, Path]) -> None:
    """Whether the folder named like the agent's product is only listed or also a source's folder decides
    whether the Agent: line is readable. The count says which, with no name."""
    line = "- folders named only with a coding agent's product words: "
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    assert line + "0 listed under ~/Library/CloudStorage (a listed one keeps" in section(
        text, "Configuration"
    )
    assert ", 0 of the configured source folders (a configured one is" in section(text, "Configuration")
    cloud = fake_mac["home"] / "Library" / "CloudStorage" / f"OneDrive-{ORG}"
    (cloud / "Copilot").mkdir()
    (cloud / "GitHub Copilot").mkdir()
    assert cli.main(["add-source", str(cloud / "GitHub Copilot"), "--config", str(fake_mac["config"])]) == 0
    text, _red = setup_report.build_report(fake_mac["config"], hooks=setup_report.ReportHooks())
    conf = section(text, "Configuration")
    assert line + "2 listed under ~/Library/CloudStorage" in conf
    assert ", 1 of the configured source folders" in conf


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
