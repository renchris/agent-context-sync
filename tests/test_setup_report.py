"""``agentsync setup-report`` (src/agentsync/setup_report.py): headings, the summary, the embedded friction
log, redaction, background-run decoding, never failing hard and the time limit.  Everything runs in the
tmp HOME conftest sets up, with a fake ``~/Library/CloudStorage/OneDrive-Contoso`` (plain folders: no File
Provider, no prompt), and a canned ``launchctl print`` (this Mac's real LaunchAgent is never consulted)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from agentsync import cli, net, setup_report
from agentsync.config import Config, load_config
from agentsync.ops import doctor

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
        "'/Users/someone/Library/CloudStorage/OneDrive-Acme/Clients/Initech Deal/term sheet.docx'\n",
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    errors = section(text, "Recent errors")
    for name in ("Big Bank", "Merger", "Globex", "Northwind", "Initech", "term sheet", "Clients"):
        assert name not in text, name
    assert "src-x: <path>: [Errno 89] Operation canceled" in errors
    assert "inbox: <path>: read failed: Operation canceled" in errors
    assert "src-x: read failed: [Errno 89] Operation canceled: '<path>" in errors
    assert "(item paths and document names shown as <path>)" in errors


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


def test_no_redact_keeps_the_values_and_says_so(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    rc, text, _ = report(tmp_path, fake_mac["config"], "--no-redact")
    assert rc == 0
    top = text.split("\n## ", 1)[0]
    assert "NOT REDACTED" in top and "--no-redact" in top
    for raw in (ORG, FOLDERS[0], FOLDERS[1], LIBRARY, EMAIL, GUID, LOGIN, str(fake_mac["home"])):
        assert raw in text, raw
    assert "Redaction is OFF" in text and "<org-1>" not in text


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
    ), "the issue form's run-type label (K10)"
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
def test_issue_link_is_the_last_line_and_carries_only_the_four_fields(
    fake_mac: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_install_log(fake_mac)
    write_friction(fake_mac, V5_HAPPY.replace("claude-opus-5-5", f"claude-opus-5-5 on {ORG.lower()}-{HOST}"))
    assert cli.main(["setup-report", "--config", str(fake_mac["config"])]) == 0
    out = capsys.readouterr().out
    link = out.rstrip("\n").splitlines()[-1]
    assert setup_report.issue_link(out) == link and link.startswith(setup_report.ISSUE_URL + "&")
    query = parse_qs(urlsplit(link).query)
    assert query == {
        "template": ["setup-report.yml"],
        "title": ["Setup report: Fully one command · Sandbox with simulated launchd · v5"],
        "outcome": ["Fully one command"],
        "run_type": ["Sandbox with simulated launchd"],
        "prompt_version": ["v5"],
        "agent": ["Claude Code, claude-opus-5-5 on <org-1>-<host>"],
    }
    for raw in RAW:
        assert raw not in link, raw
    assert "%20" in link and " " not in link, "URL-encoded"
    # --no-redact keeps the report's values, never the link's
    _rc, text, _ = report(tmp_path, fake_mac["config"], "--no-redact")
    assert ORG in text and ORG.lower() not in text.splitlines()[-1] and HOST not in text.splitlines()[-1]


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
        "Residue check: 1 capitalised word(s) next to a placeholder in this report (Agent friction log: "
        "Board); check them." in red
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


def test_run_ids_that_embed_host_names_are_redacted(fake_mac: dict[str, Path], tmp_path: Path) -> None:
    log = fake_mac["setup"] / "install.log"
    log.write_text(
        log.read_text(encoding="utf-8").replace("run=20260929T100000Z-4242", f"run={HOST}.local-4242"),
        encoding="utf-8",
    )
    _rc, text, _ = report(tmp_path, fake_mac["config"])
    assert HOST not in text and "run <host>: exit 0 after 57s" in section(text, "Installer")


def test_setup_report_help_names_the_12_s_budget() -> None:
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command" or getattr(a, "choices", None))
    helps = {c.dest: c.help for c in sub._choices_actions}  # type: ignore[attr-defined]
    assert f"under {setup_report.TIME_BUDGET_S:.0f} s" in (helps["setup-report"] or "")
    assert setup_report.TIME_BUDGET_S == 12.0
    contracts = (Path(__file__).parents[1] / "docs" / "design" / "CONTRACTS.md").read_text(encoding="utf-8")
    assert "TIME_BUDGET_S = 12.0" in contracts and "--friction PATH" in contracts


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


def test_stdout_without_out(fake_mac: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["setup-report", "--config", str(fake_mac["config"])]) == 0
    out = capsys.readouterr().out
    assert out.startswith(setup_report.REPORT_TITLE) and ORG not in out


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
        "Residue check: 1 capitalised word(s) next to a placeholder in this report (Recent errors: Roadmap); "
        "check them." in section(text, "Redaction")
    )
    assert setup_report.residue_by_section(
        "# t\n## A\n<folder-1> Alpha\n## B\nnone\n## C\nBeta <org-1>\n"
    ) == [
        ("A", ["Alpha"]),
        ("C", ["Beta"]),
    ]


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
    assert "Residue check: no capitalised word next to a placeholder in this report." in red
    assert "<tmp> this account's temporary folder" in red
    assert setup_report.residue("/Users/<user>/Library/Application Support/<folder-1> Board <org-1>") == [
        "Board"
    ]
    assert setup_report.residue("/Users/<user>/Development/x and /Volumes/<folder-1>/Roadmap") == [
        "Roadmap"
    ], "a folder in the login's home is a path component; a folder below a redacted one is still checked"


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
