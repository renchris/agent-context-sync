"""``agentsync setup-report``: one Markdown report of how setting up this Mac went, redacted, for the setup
feedback loop (docs/deploy/setup-feedback.md; owner: integrator).

Read-only and bounded: no network, no sudo, no prompts, no ``tmutil``; every external command has a timeout
and the whole report a time budget (:data:`TIME_BUDGET_S`). Each section catches its own errors and records
them in the report, so a report is always produced. The doctor and status sections come from the CLI through
:class:`ReportHooks` (this module never imports ``agentsync.cli``): one offline ``agentsync status`` build,
its check lines in Doctor and its loop and detail lines in Status, without the Graph network probe or the TCC
canary.

The report opens with a computed ``## Summary``, then the friction log (``~/agent-context/setup/friction.md``,
embedded and redacted, one line per attempt), then the machine sections and the redaction legend, and its last
line is a prefilled "Setup report" issue link. The friction log is a sequence of attempts (``Attempt:
<time>``, ``Prompt:``, ``Agent:``, then ``<time> | step <n> | <kind> | <what> | <fix>`` lines and ``<time> |
end | finished``), written by ``install.sh --log-start``, ``--log`` and ``--report-only`` (``--log-end`` until
KISS K17) for setup prompts v6 and v7 (by the agent itself in v5); :class:`PromptLayout` keeps each version's
step numbers, picked by the ``Prompt:`` line's explicit version; v4 ``F<n> | ...`` lines are shown as legacy,
never counted. Everything the
Summary judges is computed from facts, never taken from the agent: the outcome (from what the person saw and
did: install.log's last install run exit, questions beyond the folder question, clicks beyond the Allow
clicks, approvals, unexpected doctor FAILs, and an error only when it stopped the run; the agent's deviation,
prompt and error lines are counted apart as "agent friction"), human turns (by kind), step and session times
(from timestamps), the run type (install.log's ``launchd=simulated``, else a HOME under a temporary folder),
the first sync, which doctor warns are expected, the IT draft's unfilled fields, and the ``Loop:`` line (KISS
K16b: how far the loop got past the install, :func:`loop_stage`, and its current NEXT line without paths, kept
apart from the outcome, which judges only the install). The agent's own
``Outcome:`` line is shown only as "agent said". The Installer section also embeds the tail of install.sh's
output copy (``install.out`` next to install.log: what the agent saw) and counts its instruction-like lines.

The Status section ends with the evidence parts (:data:`EVIDENCE_TITLES`, CONTRACTS.md 16.28): what a
maintainer would otherwise have to ask this Mac for after reading the report, so that one bring-back file is
enough. They are read from the manifest (opened read-only), the purge queue and one ``lstat`` per folder,
inside :data:`EVIDENCE_BUDGET_S`, and hold counts, states, seconds, version strings and fixed words only:
OCR's state and what it did, the quarantined files by reason class (:func:`quarantine_class`), the purge
queue by reason and day, sources whose folder is inside another's, empty cloud folders by their dataless
flag, and conversions repeated run after run. Background runs says how an installed plist's arguments
differ from what this build would write, by class (:func:`argument_roles`), and Installer lists every run
and the ones with no end line.

Redaction (always on) replaces, consistently (the same value always gets the same placeholder): the home
path (``~``), the login name (``<user>``), the full name (``<name>``), the organisation from
``~/Library/CloudStorage/OneDrive-<org>`` and ``OneDrive - <org>`` (``<org-N>``), SharePoint library names
(``<library-N>``), every folder name under ``~/Library/CloudStorage`` at depth 2-3 (what the setup prompt's
folder listing shows, configured or not; kept when only listed: a few generic names such as ``Documents``,
and a name made only of coding-agent product words such as ``Copilot``, so the agent's own name stays
readable) and every configured source folder path component (``<folder-N>``), a configured source folder
elsewhere under the home folder from its project folder down, and that project folder's name (``<folder-N>``
too; the inbox kept beside the docs repo is agentsync's own, and so is every folder beside it unless that
parent is itself a generic folder such as ``~/Documents``), every configured source id but agentsync's own
words such as ``inbox`` or ``mail`` (``<source-N>``), email addresses (``<email-N>``), GUIDs (``<guid-N>``),
hex fingerprints of 16 or more digits such as launcher cdhashes (``<hash-N>``), docs-repo commit ids
(``<commit-N>``), the serial number (``<serial>``), the host and computer names (``<host>``), proxy hosts
(``<proxy-N>``) and this account's temporary folder (``$TMPDIR``, any ``/var/folders/<x>/<y>``: ``<tmp>``).
Folder, library, organisation and full-name values also match their case, space, hyphen, underscore and
CamelCase variants, and the shell-escaped form install.sh logs its arguments in (``Client\\ Alpha``). A
``--source-local`` argument is also unquoted (``printf %q`` writes some names as ``$'...'`` with octal bytes)
and cut to ``<path>`` at the first path component the Redactor does not know. Names
nested below a source are registered nowhere, so agentsync's own WARNING and ERROR log lines (Recent errors,
and those in the install.out tail, with a sync's ``alarm:`` and ``error:`` lines there) show item paths,
document names and every quoted name as ``<path>``. install.sh run ids
are shown without their process id. The friction log is redacted with the same map. The login name is
registered before the full name (``janedoe`` for "Jane Doe" is ``<user>``), and the residue check runs over
the whole redacted report, listing its hits by section.
"""

from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import errno
import functools
import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import plistlib
import pwd
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar, cast
from urllib.parse import quote, unquote, urlencode, urlsplit

from agentsync import __version__, net
from agentsync.arm_local import CallTimedOutError, call_with_timeout
from agentsync.config import Config, load_config
from agentsync.paths import default_config_path, expand

REPORT_TITLE = "# agentsync setup report"
SECTION_TITLES = (
    "Summary",
    "Agent friction log",
    "Environment",
    "Installer",
    "Configuration",
    "Doctor",
    "Status",
    "Background runs",
    "Recent errors",
    "Redaction",
)
"""The ``## `` headings, in report order (stable: the issue template and triage depend on them)."""
RUN_METADATA_HEADING = "### Run metadata"
"""The Summary's last block (generated at, agentsync version, install source, time taken): a sub-heading, so
the ``## `` headings stay :data:`SECTION_TITLES`."""
FRICTION_HEADING = "## Agent friction log"
FRICTION_ENV = "AGENTSYNC_FRICTION_LOG"
"""Overrides the default friction log path (KISS K16a deleted ``--friction PATH``)."""
DEFAULT_OUT = "~/agent-context/setup-report.md"
"""Where ``agentsync setup-report`` writes without ``--out`` (hidden since KISS K16a); install.sh's too."""
FRICTION_KEYS = ("Prompt", "Agent", "Outcome", "Run")
"""The header lines read from each attempt of friction.md: ``install.sh --log-start`` (prompt v6; the v5 agent
itself) writes ``Prompt:`` and ``Agent:`` after the ``Attempt: <time>`` line; ``Outcome:`` and ``Run:``
(prompt v4) are shown as what the agent said, never used."""
FRICTION_KINDS = ("question", "click", "approval", "deviation", "error", "prompt")
"""The closed vocabulary of an event line ``<time> | step <n> | <kind> | <what happened> | <fix>`` (setup
prompt v6, written by ``install.sh --log``)."""
STEP_KINDS = ("start", "end")
"""Prompt v5's step bracket kinds (``start`` and ``end`` of each step): still read and counted in a v5 log;
prompt v6 logs no step lines (install.log times the steps)."""
TURN_KINDS = ("question", "click", "approval")
"""The kinds that are a human turn (counted by kind, never by keyword)."""
PROBLEM_KINDS = ("error", "deviation", "prompt")
"""The agent-side kinds: counted on the Summary's "agent friction" line, never in the outcome by themselves
(an error changes the outcome only when it stopped the run: :func:`stopping_error`)."""
PROMPT_VERSION = 7
"""The newest setup prompt this module knows (README "Set up on a new Mac: one prompt"): an attempt whose
``Prompt:`` line states no version is read with its step numbers."""
PROMPT_STEPS = {
    1: "preflight",
    2: "install and start",
    3: "IT request and report",
    4: "finish",
}
"""The issue form's Outcome step names: setup prompt v6's four steps, kept unchanged in v7 (KISS K16b) so
older reports keep their option; each layout's ``form_step`` maps its steps onto them."""
FOLDER_QUESTION_STEP = 1
"""The step that asks the one question fully one command allows (which folders to sync). Prompt v6 does not
log it (its ``question`` kind is "something other than which folders to sync"), so every logged question is
beyond it; in a v5 log it is the first question logged in v5's step 2."""
ALLOW_CLICK_STEPS = (1, 2)
"""Prompt v6's steps whose announced Allow click fully one command allows (the terminal's OneDrive access in
step 1, agentsync-launcher's in step 2). Prompt v6 does not log them (its ``click`` kind is "something other
than an Allow this prompt announced"); in a v5 log they are the first click logged in each of v5's steps 2
and 3. Prompt v7 announces only step 1's (:data:`V7_ALLOW_CLICK_STEPS`)."""
V7_ALLOW_CLICK_STEPS = (1,)
"""Prompt v7's announced Allow click: the terminal's in step 1 only. Its install starts no background job, so
agentsync-launcher asks for no Allow in step 2 (KISS K11b)."""
INSTALL_STEP = 2
"""The prompt step that runs scripts/install.sh: the installer's failure is a failure of this step."""
REPORT_STEP = 3
"""The prompt step every run ends at ("If a command fails ... log it and go to step 3"); its
``install.sh --report-only`` closes the attempt with ``<time> | end | finished`` (``--log-end`` until
KISS K17)."""


@dataclasses.dataclass(frozen=True, slots=True)
class PromptLayout:
    """The step numbers of one setup prompt version: what an attempt's events and its outcome refer to."""

    version: int
    steps: dict[int, str]  # step -> title
    folder_question_step: int
    allow_click_steps: tuple[int, ...]
    install_step: int
    report_step: int
    logs_expected_turns: bool  # v5 logs the folder question and the Allow clicks; v6 logs only other turns
    logs_steps: bool  # v5 brackets each step with start and end lines; v6 does not (install.log times them)
    form_step: dict[int, int]  # this version's step -> the PROMPT_STEPS step (the issue form's options)


PROMPT_LAYOUTS = {
    7: PromptLayout(
        version=7,
        steps={1: "preflight", 2: "install", 3: "sync loop and report"},
        folder_question_step=FOLDER_QUESTION_STEP,
        allow_click_steps=V7_ALLOW_CLICK_STEPS,
        install_step=INSTALL_STEP,
        report_step=REPORT_STEP,
        logs_expected_turns=False,
        logs_steps=False,
        form_step={1: 1, 2: 2, 3: 3},
    ),
    6: PromptLayout(
        version=6,
        steps=PROMPT_STEPS,
        folder_question_step=FOLDER_QUESTION_STEP,
        allow_click_steps=ALLOW_CLICK_STEPS,
        install_step=INSTALL_STEP,
        report_step=REPORT_STEP,
        logs_expected_turns=False,
        logs_steps=False,
        form_step={1: 1, 2: 2, 3: 3, 4: 4},
    ),
    5: PromptLayout(
        version=5,
        steps={
            1: "preflight and code",
            2: "choose folders",
            3: "install and start",
            4: "IT request",
            5: "report",
            6: "finish",
        },
        folder_question_step=2,
        allow_click_steps=(2, 3),
        install_step=3,
        report_step=5,
        logs_expected_turns=True,
        logs_steps=True,
        form_step={1: 1, 2: 1, 3: 2, 4: 3, 5: 3, 6: 4},
    ),
}
"""Setup prompt v7 (three steps: the IT request is gone and step 3 runs the sync loop, then the report), v6
and v5, each log read with its own step numbers (a v4 log is read as v5: its F<n> lines are legacy anyway). v6
and v7 share this module's step constants."""


def prompt_layout(version: int | None) -> PromptLayout:
    """The layout of setup prompt ``version``, picked by its explicit number: v5 for 5 and earlier, v6 for 6,
    v7 for 7; :data:`PROMPT_VERSION`'s when not stated or newer than this module knows."""
    if version is None or version > PROMPT_VERSION:
        return PROMPT_LAYOUTS[PROMPT_VERSION]
    return PROMPT_LAYOUTS[5] if version <= 5 else PROMPT_LAYOUTS[version]


SANDBOX_HOMES = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")
"""A HOME under one of these is a sandbox run (a throwaway home), whatever the agent says."""
APPROVAL_HIDDEN_TOOLS = ("claude code", "copilot")
"""Agent tools (lower-case substrings of the ``Agent:`` line) that ask the person to approve commands without
telling the model: an approval count of 0 from them is "not observable", not zero."""
IT_DRAFT = "~/agent-context/it-request-draft.md"
"""Where prompt step 3 (v5: step 4) has ``agentsync it-request`` write the IT request draft."""
IT_PERSON_FIELDS = (
    "<it-contact>",
    "<requester-name>",
    "<requester-upn>",
    "<team>",
    "<serial>",
    "<arch>",
    "<org>",
    "<wanted-by>",
)
"""docs/deploy/it-request.md placeholders the person (or ``agentsync it-request``) fills."""
IT_ADMIN_FIELDS = ("<it-owner>", "<tenant-id>", "<app-client-id>")
"""docs/deploy/it-request.md placeholders IT fills."""
SETUP_LOG_ENV = "AGENTSYNC_SETUP_LOG"
"""Where ``scripts/install.sh`` appends its per-step log (default ``~/agent-context/setup/install.log``)."""
INSTALL_OUT_NAME = "install.out"
"""scripts/install.sh's copy of what each run printed (what the agent saw), next to install.log."""
INSTALL_OUT_TAIL = 60
"""The lines of install.out the Installer section embeds (redacted, collapsed)."""
INSTRUCTION_KINDS = ("NEXT:", "next:", "fix:", "run:")
"""Instruction-like line kinds counted in install.out: install.sh's own ``NEXT:`` (exactly one per run is
expected), and any other hint an agent may act on before it (``next:``, ``(fix: ...)``, ``run:``)."""
TIME_BUDGET_S = 12.0
"""The whole report's wall-clock budget; external commands get what is left of it (minus a reserve)."""
_REPO_SLUG = "renchris/agent-context-sync"
ISSUE_URL = f"https://github.com/{_REPO_SLUG}/issues/new?template=setup-report.yml"
ISSUE_LINK_LABEL = "issue link (review the report first):"
"""What ``setup-report --out`` prints before the issue link, as its last stdout line."""
ISSUE_TITLE = "Setup report: "
"""The issue form's ``title:`` (the report's link appends the outcome, run type and prompt version)."""
ISSUE_FIELDS = {
    "outcome": "outcome",
    "run_type": "run_type",
    "prompt": "prompt_version",
    "agent": "agent",
    "loop_stage": "loop_stage",
}
"""What the report prefills -> the ``id`` of that field in .github/ISSUE_TEMPLATE/setup-report.yml."""
LOOP_STAGES = (
    "installed",
    "synced",
    "baseline drafted",
    "baseline confirmed",
    "before run",
    "topics N",
    "after run",
)
"""The Summary's ``Loop:`` stages in loop order (KISS K16b; ``topics N`` carries the curated page count): how
far past the install the setup got, apart from the outcome, which judges only the install."""
ISSUE_RUN_TYPES = {
    "real": "Real Mac",
    "sandbox": "Sandbox",
    "simulated launchd": "Sandbox with simulated launchd",
}
"""The computed run type -> the form's ``run_type`` dropdown option (prefilled by its exact label)."""
RECENT_ERROR_LINES = 40
INSTALL_RUNS_SHOWN = 3
FRICTION_MAX_BYTES = 256 * 1024
EXIT_MEANINGS = {
    0: "ok",
    1: "failed: a source failed or a blocking lint fired (see Recent errors)",
    2: "usage error",
    75: "skipped: another cycle held the lock; launchd retries",
    77: "sign-in required (agentsync graph login)",
    78: "configuration invalid (sources.toml or policy.toml)",
    79: "TCC_PENDING: the launcher waited for the Allow prompt and timed out",
    80: "TCC_DENIED: access to the synced folder was denied",
}
"""agentsync's meaning of a LaunchAgent's last exit code (``agentsync --help`` lists the same)."""

_RESERVE_S = 1.0
_LOOP_NEXT_S = 4.0  # the Summary's Loop line: the ``loop_next`` hook's own limit
_LOOP_FLOOR_S = 1.0  # held back from Doctor's share, so the hook gets at least 2 s past a slow doctor
_TAIL_BYTES = 64 * 1024
_LEVEL_RE = re.compile(r"\b(?:WARNING|ERROR|CRITICAL)\b|^(?:error|fatal):|^Traceback ")
_LAUNCHD_KEYS = ("state", "runs", "last exit code", "last terminating signal")
_LAUNCHD_LINE_RE = re.compile(r"^\t([a-z][a-z ]*?) = (.*)$")
_TCC_RE = re.compile(r"agentsync-launcher\[\d+\]: TCC_")
_DOCTOR_TAG_RE = re.compile(r"^\[(ok|FAIL|warn|info)\s*\]\s*(\S+)")
_INSTALL_LINE_RE = re.compile(r"^(?P<at>\S+) run=(?P<run>\S+) (?P<rest>.*)$")
_KV_RE = re.compile(r"(\w+)=(\S+)")
_END_RE = re.compile(r"^\S+ run=\S+ end ")
_SIMULATED_RE = re.compile(r"\blaunchd\s*[=:]\s*simulated\b", re.IGNORECASE)
_RUN_SIMULATED_RE = re.compile(r"simulated\b.*\blaunchd|\blaunchd\b.*\bsimulated", re.IGNORECASE)
_EMAIL_RE = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
_GUID_RE = r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
_HASH_RE = r"(?<![0-9A-Za-z])(?=[0-9A-Fa-f]*[A-Fa-f])(?=[0-9A-Fa-f]*[0-9])[0-9A-Fa-f]{16,}(?![0-9A-Za-z])"
_REFLOG_RE = re.compile(r"^([0-9a-f]{40}) ([0-9a-f]{40}) ")
_LAST_RUNS_RE = re.compile(r"\b\d+ \S+ \S+ ([0-9a-f]{7,40})\b")
_UNNUMBERED = frozenset({"home", "user", "name", "serial", "host", "tmp"})
_TMP_RE = r"/(?:private/)?var/folders/[^/\s]+/[^/\s]+"  # a per-user temporary folder id (stable per account)
_GENERIC_FOLDERS = frozenset(
    {
        "Apps",
        "Attachments",
        "Desktop",
        "Documents",
        "Downloads",
        "Microsoft Copilot Chat Files",
        "Microsoft Teams Chat Files",
        "Music",
        "My Drive",
        "Notebooks",
        "Personal Vault",
        "Pictures",
        "Recordings",
        "Shared",
        "Shared Documents",
        "Shared drives",
        "Videos",
    }
)
"""Folder names every OneDrive or Google Drive has: kept when only listed (a configured one is redacted)."""
_AGENT_WORDS = frozenset({"claude", "codex", "copilot", "cursor", "gemini", "github"})
"""Coding-agent product words, lower-case. A folder that is only listed (not configured) and named with only
these words is not registered, in any case: registered, "Copilot" would turn the ``Agent:`` line's "GitHub
Copilot CLI" into ``GitHub <folder-N> CLI`` in the Summary and the issue link (field report 2026-10-06). A
configured folder or source id with such a name is registered like any other (several of the words are also
first names and project codenames), and :func:`residue` still lists the word."""
_GENERIC_IDS = frozenset(
    {
        "agent",
        "agentsync",
        "archive",
        "calendar",
        "chats",
        "config",
        "docs",
        "drive",
        "files",
        "graph",
        "inbox",
        "local",
        "mail",
        "mirror",
        "notes",
        "onedrive",
        "sharepoint",
        "source",
        "sources",
        "status",
        "sync",
        "teams",
    }
)
"""Source ids that are agentsync's own words: kept when the source is not a cloud folder, because a registered
``mail`` or ``docs`` would turn ``graph_mail`` and "the docs repo" into placeholders all over the report."""
_GENERIC_HOME_DIRS = frozenset(
    {
        "applications",
        "code",
        "desktop",
        "dev",
        "development",
        "documents",
        "downloads",
        "git",
        "library",
        "projects",
        "repos",
        "sites",
        "source",
        "src",
        "work",
        "workspace",
    }
)
"""Folders below the home folder that name no project (lower-case): :func:`_project_values` skips them."""
_LISTING_MAX = 2000
_LEGEND = {
    "home": "~ home folder",
    "user": "<user> login name",
    "name": "<name> full name",
    "org": "<org-N> organisation",
    "library": "<library-N> SharePoint library",
    "folder": "<folder-N> folder under ~/Library/CloudStorage",
    "source": "<source-N> source id",
    "email": "<email-N> email address",
    "guid": "<guid-N> GUID",
    "hash": "<hash-N> hex fingerprint (launcher cdhash, content hash)",
    "commit": "<commit-N> docs-repo commit",
    "serial": "<serial> serial number",
    "host": "<host> host or computer name",
    "proxy": "<proxy-N> proxy host",
    "tmp": "<tmp> this account's temporary folder ($TMPDIR, /var/folders/<x>/<y>)",
}
_TEMPLATE_NOTE = (
    "Unnumbered <org>, <team>, <Org>, <TEAMID>, <serial> and the IT request's other <field> names inside "
    "doctor fixes or the IT-draft line are template placeholders, not redactions."
)
_SEP_RE = re.compile(r"[ \t_\\-]+")
_FUZZY_SEP = r"[ \t_\\-]*"
"""Between the words of a fuzzy value. The backslash covers ``printf %q`` text (install.log's ``args=``,
install.out's run header and re-run lines): ``Client\\ Alpha`` is ``Client Alpha``."""
_Q_ESCAPE_RE = re.compile(r"[^A-Za-z0-9]")  # in a word: ``printf %q`` may put a backslash before it
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_PID_SUFFIX_RE = re.compile(r"(\brun=\S+?)-\d+(?=\s|$)")
_RESIDUE_PLACEHOLDER = r"<(?:name|user|org-\d+|library-\d+|folder-\d+|source-\d+)>"
_RESIDUE_RE = re.compile(
    rf"{_RESIDUE_PLACEHOLDER}[ /_-]+([A-Z][\w&]*)|([A-Z][\w&]*)[ /_-]+(?={_RESIDUE_PLACEHOLDER})"
)
_USER_HOME_RE = re.compile(r"(?<![\w.-])/Users/<user>/")  # a login's home in a redacted path: like ~/
_PATH_COMPONENTS = frozenset(
    {"Users", "Library", "Application", "Support", "CloudStorage", "Volumes", "Applications"}
)
"""Fixed macOS path components (``/Users/<user>/Library/Application Support``): never residue."""
_RESIDUE_IGNORED = frozenset(
    {
        "A",
        "Allow",
        "An",
        "And",
        "At",
        "By",
        "For",
        "From",
        "I",
        "In",
        "My",
        "Of",
        "On",
        "OneDrive",
        "Or",
        "SharedLibraries",
        "The",
        "To",
        "With",
        *_PATH_COMPONENTS,
    }
)
_INSTRUCTION_RES = (
    ("NEXT:", re.compile(r"^\s*NEXT:")),
    ("next:", re.compile(r"^\s*next:")),
    ("fix:", re.compile(r"\bfix:")),
    ("run:", re.compile(r"(?:^\s*|\()run:")),
)
_OUT_RUN_RE = re.compile(r"^# run=\S+ ")  # install.out's header line of each run
_CONVERTED_NOTE_RE = re.compile(r"converted-(\d+)-deferred-(\d+)")  # install.log's first-sync note
_CONVERTED_LINE_RE = re.compile(r"\bconverted (\d+), deferred (\d+) online-only\b")  # sync's last line
_BASELINE_RE = re.compile(r"^\s+\S+ \([^)]*\): baseline (complete|INCOMPLETE)\b")
_LOOP_BASELINE_RE = re.compile(r"\bbaseline (\w+)")  # status's ``loop:`` line (loop.status_line)
_LOOP_TOPICS_RE = re.compile(r"\btopics (\d+)")
_NEXT_PATH_RE = re.compile(r"(?<![\w.~/-])[~/][^\s`'\"()<>]*/([^\s`'\"()<>/]+)")  # a path: its last part
_APPS = {
    "OneDrive": ("/Applications/OneDrive.app", "~/Applications/OneDrive.app"),
    "Company Portal": ("/Applications/Company Portal.app", "~/Applications/Company Portal.app"),
}
_REDACTION_MARK = "\x00redaction-summary\x00"

_T = TypeVar("_T")


@dataclasses.dataclass(frozen=True, slots=True)
class ReportHooks:
    """What the report needs from the CLI (injected, so this module never imports ``agentsync.cli``):
    ``doctor`` returns every check line of ``agentsync status`` (no network probe, no TCC canary), ``status``
    its loop line and detail lines (KISS K08a: the checks are rendered once, in Doctor)."""

    doctor: Callable[[Config], list[str]] | None = None
    status: Callable[[Config], list[str]] | None = None
    loop_next: Callable[[Config], list[str]] | None = None  # the loop's NEXT lines (KISS K16b), after doctor


def _tokens(value: str) -> list[str]:
    """The words of ``value``: split at spaces, hyphens and underscores, and at CamelCase boundaries."""
    out: list[str] = []
    for chunk in _SEP_RE.split(value):
        out += [t for t in _CAMEL_RE.split(chunk) if t]
    return out


def _norm(text: str) -> str:
    """``text`` lower-cased without spaces, hyphens, underscores and backslashes (what all variants of a
    value share)."""
    return _SEP_RE.sub("", text).lower()


def _word_pattern(word: str) -> str:
    """A regex for one word of a fuzzy value, or for a whole case-insensitive value, also as ``printf %q``
    writes it (``R&D`` and ``R\\&D``; ``Old Plans/in tray`` and ``Old\\ Plans/in\\ tray``)."""
    return _Q_ESCAPE_RE.sub(lambda m: r"\\?" + re.escape(m.group()), word)


def _agent_words(name: str) -> bool:
    """Whether ``name`` is made of :data:`_AGENT_WORDS` only ("Copilot", "github-copilot")."""
    words = [w for w in _SEP_RE.split(name.casefold()) if w]
    return bool(words) and all(w in _AGENT_WORDS for w in words)


class Redactor:
    """Replace known values and email/GUID/hex/temporary-folder patterns with placeholders, the same value
    always with the same placeholder (``<folder-1>``); counts every replacement per kind. Disabled, it returns
    text unchanged.

    A value registered ``fuzzy`` (folder, library, organisation and full-name values) also matches its case,
    space, hyphen, underscore and CamelCase variants, and its shell-escaped form: ``Client Alpha`` covers
    ``client-alpha``, ``CLIENT_ALPHA``, ``ClientAlpha`` and ``Client\\ Alpha``."""

    def __init__(self, *, enabled: bool = True) -> None:
        """An empty redactor; :meth:`add` and :meth:`add_commit` register values."""
        self.enabled = enabled
        self.counts: Counter[str] = Counter()
        self._exact: dict[str, str] = {}  # value -> placeholder (case-sensitive kinds)
        self._folded: dict[str, str] = {}  # value.lower() -> placeholder (case-insensitive kinds)
        self._fuzzy: dict[str, str] = {}  # _norm(value) -> placeholder (normalization-aware kinds)
        self._kind_of: dict[str, str] = {}  # placeholder -> kind
        self._numbers: Counter[str] = Counter()
        self._pattern_values: dict[tuple[str, str], str] = {}
        self._literals: list[tuple[str, str]] = []  # (value, "exact" | "fold" | "fuzzy")
        self._commits: dict[str, str] = {}  # 7-hex prefix -> placeholder
        self._used: set[str] = set()
        self._regex: re.Pattern[str] | None = None
        self._counting = True

    @property
    def total(self) -> int:
        """Every replacement made so far."""
        return sum(self.counts.values())

    @property
    def values(self) -> int:
        """How many distinct values were replaced so far (placeholders that appear in the output)."""
        return len(self._used)

    def kinds_used(self) -> list[str]:
        """The kinds with at least one replacement, in legend order."""
        order = [*_LEGEND, *sorted(k for k in self.counts if k not in _LEGEND)]
        return [k for k in order if self.counts.get(k)]

    def _placeholder(self, kind: str) -> str:
        if kind == "home":
            return "~"
        if kind in _UNNUMBERED:
            return f"<{kind}>"
        self._numbers[kind] += 1
        return f"<{kind}-{self._numbers[kind]}>"

    def _known(self, value: str) -> str | None:
        return (
            self._exact.get(value)
            or self._folded.get(value.lower())
            or self._folded.get(value.replace("\\", "").lower())  # the shell-escaped form of a fold value
            or self._fuzzy.get(_norm(value))
        )

    def add(self, kind: str, value: str | None, *, ignore_case: bool = False, fuzzy: bool = False) -> None:
        """Register ``value`` as a ``kind`` (home, user, name, org, library, folder, source, email, serial,
        host, proxy); a value already registered (or a variant of a fuzzy one) keeps its first placeholder.
        Values under 2 characters are ignored (they would match inside ordinary words). ``fuzzy`` implies
        ``ignore_case`` and adds the space/hyphen/underscore/CamelCase variants (not for a value whose
        normalized form is under 3 characters)."""
        value = (value or "").strip()
        if len(value) < 2 or value in ("~", "/"):
            return
        if self._known(value) is not None:
            return
        placeholder = self._placeholder(kind)
        self._kind_of[placeholder] = kind
        if fuzzy and len(_norm(value)) >= 3 and _tokens(value):
            self._fuzzy[_norm(value)] = placeholder
            self._literals.append((value, "fuzzy"))
        elif ignore_case or fuzzy:
            self._folded[value.lower()] = placeholder
            self._literals.append((value, "fold"))
        else:
            self._exact[value] = placeholder
            self._literals.append((value, "exact"))
        self._regex = None

    def add_commit(self, sha: str | None) -> None:
        """Register a git commit id (7 to 40 hex digits): every abbreviation of it that shares its first 7
        digits becomes the same ``<commit-N>``."""
        sha = (sha or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{7,40}", sha) or set(sha) == {"0"}:
            return
        prefix = sha[:7]
        if prefix in self._commits:
            return
        placeholder = self._placeholder("commit")
        self._kind_of[placeholder] = "commit"
        self._commits[prefix] = placeholder
        self._regex = None

    def _compile(self) -> re.Pattern[str]:
        if self._regex is None:
            alts = [f"(?P<email>{_EMAIL_RE})", f"(?P<guid>(?<![0-9A-Fa-f-]){_GUID_RE}(?![0-9A-Fa-f-]))"]
            if self._commits:
                prefixes = "|".join(sorted(self._commits))
                alts.append(f"(?P<commit>(?<![0-9A-Za-z])(?i:{prefixes})[0-9a-fA-F]{{0,33}}(?![0-9A-Za-z]))")
            literals = []
            # The alternative tried first wins, so the value with the longest match comes first. A fuzzy value
            # also matches without its separators: ordered by written length, a folder "A - B - C" came
            # before the id "a-b-c-x" and replaced only its front. The length without separators is the
            # shortest text a value matches, and two values that match at one place differ in it.
            for value, mode in sorted(self._literals, key=lambda item: (-len(_norm(item[0])), -len(item[0]))):
                if mode == "fuzzy":
                    body = _FUZZY_SEP.join(_word_pattern(t) for t in _tokens(value))
                elif mode == "fold":
                    body = _word_pattern(value)
                else:
                    body = re.escape(value)
                lead = r"(?<![A-Za-z0-9])" if value[0].isalnum() else ""
                trail = r"(?![A-Za-z0-9._-])" if value.startswith("/") else r"(?![A-Za-z0-9])"
                if not value[-1].isalnum() and not value.startswith("/"):
                    trail = ""
                literals.append(f"(?i:{lead}{body}{trail})" if mode != "exact" else f"{lead}{body}{trail}")
            if literals:
                alts.append("(?P<lit>" + "|".join(literals) + ")")
            alts.append(f"(?P<tmp>{_TMP_RE})")
            alts.append(f"(?P<hash>{_HASH_RE})")
            self._regex = re.compile("|".join(alts))
        return self._regex

    def _pattern(self, kind: str, value: str) -> str:
        key = (kind, value.lower())
        if key not in self._pattern_values:
            known = self._exact.get(value) or self._folded.get(value.lower())
            self._pattern_values[key] = known or self._placeholder(kind)
            self._kind_of.setdefault(self._pattern_values[key], kind)
        return self._pattern_values[key]

    def _replace(self, m: re.Match[str]) -> str:
        text = m.group(0)
        groups = m.groupdict()
        if groups.get("email") is not None:
            placeholder = self._pattern("email", text)
        elif groups.get("guid") is not None:
            placeholder = self._pattern("guid", text)
        elif groups.get("commit") is not None:
            placeholder = self._commits.get(text[:7].lower(), text)
        elif groups.get("tmp") is not None:
            placeholder = self._known(text) or self._pattern("tmp", text)
        elif groups.get("hash") is not None:
            placeholder = self._pattern("hash", text)
        else:
            placeholder = self._known(text) or text
        if placeholder != text and self._counting:
            self.counts[self._kind_of.get(placeholder, "other")] += 1
            self._used.add(placeholder)
        return placeholder

    def redact(self, text: str) -> str:
        """``text`` with every registered value and every email address, GUID and long hex string replaced."""
        if not self.enabled or not text:
            return text
        return self._compile().sub(self._replace, text)

    def scrub(self, text: str) -> str:
        """``text`` redacted even when this redactor is disabled, without counting (for the issue link: it is
        never allowed to carry an unredacted value)."""
        if not text:
            return text
        self._counting = False
        try:
            return self._compile().sub(self._replace, text)
        finally:
            self._counting = True


# ---------------------------------------------------------------------------------------------------------
# the friction log (friction.md, written by the setup prompt's agent)
# ---------------------------------------------------------------------------------------------------------


def parse_time(text: str) -> datetime | None:
    """An ISO-8601 time (``date -u +%FT%TZ``) as an aware UTC datetime; None when it is not one."""
    text = text.strip().strip("*`").strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


@dataclasses.dataclass(frozen=True, slots=True)
class FrictionEvent:
    """One v5 line ``<time> | step <n> | <kind> | <what happened> | <what would have avoided it>``, or the
    attempt's closing ``<time> | end | finished`` (step None, kind ``finished``)."""

    line: int  # 1-based line number in friction.md
    at: datetime | None
    step: int | None
    kind: str  # one of FRICTION_KINDS, "finished", or the agent's own word (untyped: not counted)
    what: str
    fix: str

    @property
    def typed(self) -> bool:
        """Whether the kind is one the prompt defines (untyped lines are shown, never counted)."""
        return self.kind in FRICTION_KINDS or self.kind in STEP_KINDS or self.kind == "finished"


@dataclasses.dataclass(frozen=True, slots=True)
class Attempt:
    """One run of the setup prompt: the lines from one ``Attempt: <time>`` header (or, in a v4 log, one
    ``Prompt:`` header block) to the next. A step 1 error logged well after an attempt's closing line starts
    an attempt of its own, with no header (:func:`parse_friction`)."""

    number: int
    started: datetime | None  # the Attempt: line's time
    header: dict[str, str]  # Prompt, Agent and, from v4 logs, Outcome and Run
    events: tuple[FrictionEvent, ...]
    legacy: tuple[str, ...]  # v4 "F<n> | step | status | minutes | what | fix" lines: shown, not counted
    first_line: int
    last_line: int

    @property
    def finished(self) -> bool:
        """Whether the attempt has its closing ``<time> | end | finished`` line (``install.sh --report-only``,
        before KISS K17 ``--log-end``, in prompt v6's step 3; the agent's own line in v5's step 5)."""
        return any(e.kind == "finished" for e in self.events)

    @property
    def version(self) -> int | None:
        """The setup prompt version of the ``Prompt:`` line (``v6`` -> 6), None when not stated."""
        m = re.search(r"\bv?(\d{1,3})\b", self.header.get("Prompt", ""))
        return int(m.group(1)) if m else None

    @property
    def layout(self) -> PromptLayout:
        """The step numbers this attempt's prompt used (:func:`prompt_layout`)."""
        return prompt_layout(self.version)

    @property
    def begin(self) -> datetime | None:
        """The Attempt: time, else the first event's time."""
        return self.started or next((e.at for e in self.events if e.at is not None), None)

    def of_kind(self, *kinds: str) -> list[FrictionEvent]:
        """The events of these kinds, in log order."""
        return [e for e in self.events if e.kind in kinds]

    def kinds(self) -> Counter[str]:
        """Events per kind (untyped ones under their own word)."""
        return Counter(e.kind for e in self.events)

    @property
    def untyped(self) -> list[FrictionEvent]:
        """Event lines whose kind is not one the prompt defines."""
        return [e for e in self.events if not e.typed]

    def timestamps(self) -> list[datetime]:
        """Every time in the attempt (the Attempt: line's and each event's)."""
        return [t for t in (self.started, *(e.at for e in self.events)) if t is not None]

    def wall_seconds(self) -> float | None:
        """First to last timestamp (None with fewer than two)."""
        ts = self.timestamps()
        return (max(ts) - min(ts)).total_seconds() if len(ts) >= 2 else None

    def step_seconds(self) -> list[tuple[int, float | None]]:
        """(step, seconds from its first ``start`` to its last ``end``) in the order the steps first appear;
        None when the step has no start or no end."""
        order: list[int] = []
        for e in self.events:
            if e.step is not None and e.step not in order:
                order.append(e.step)
        out: list[tuple[int, float | None]] = []
        for step in order:
            starts = [e.at for e in self.events if e.step == step and e.kind == "start" and e.at is not None]
            ends = [e.at for e in self.events if e.step == step and e.kind == "end" and e.at is not None]
            span = (max(ends) - min(starts)).total_seconds() if starts and ends else None
            out.append((step, span if span is None or span >= 0 else None))
        return out

    def extra_questions(self) -> list[FrictionEvent]:
        """Questions beyond the folder question: every logged question in prompt v6 (it logs only the
        others), every one but the first logged in v5's step 2."""
        questions = self.of_kind("question")
        if not self.layout.logs_expected_turns:
            return questions
        allowed = next((e for e in questions if e.step == self.layout.folder_question_step), None)
        return [e for e in questions if e is not allowed]

    def extra_clicks(self) -> list[FrictionEvent]:
        """Clicks beyond the announced Allow clicks: every logged click in prompt v6 (it logs only the
        others), in v5 every one but the first logged in each of its steps 2 and 3."""
        clicks = self.of_kind("click")
        if not self.layout.logs_expected_turns:
            return clicks
        allowed = [next((e for e in clicks if e.step == s), None) for s in self.layout.allow_click_steps]
        return [e for e in clicks if not any(e is a for a in allowed)]

    def expected(self, event: FrictionEvent) -> bool:
        """Whether ``event`` is the folder question or an Allow click (the turns fully one command allows)."""
        if event.kind == "question":
            return not any(event is e for e in self.extra_questions())
        if event.kind == "click":
            return not any(event is e for e in self.extra_clicks())
        return False


@dataclasses.dataclass(frozen=True, slots=True)
class Friction:
    """A parsed friction log: its attempts (oldest first), the text, and what the report read (the file's
    modification time and line count, so a reader can tell a later line is missing)."""

    attempts: tuple[Attempt, ...]
    text: str
    truncated: bool = False
    mtime: datetime | None = None

    @property
    def latest(self) -> Attempt | None:
        """The last attempt (what the Summary judges)."""
        return self.attempts[-1] if self.attempts else None

    @property
    def line_count(self) -> int:
        """Lines embedded."""
        return len(self.text.splitlines())


_FRICTION_ITEM_RE = re.compile(r"^\s*(?:[-*]\s+)?\**(F\d+)\**\s*\|(.*)$")
_FRICTION_KEY_RE = re.compile(
    r"^\s*(?:[-*]\s+)?\**(" + "|".join(FRICTION_KEYS) + r")\**\s*:\s*\**\s*(.*?)\s*$", re.IGNORECASE
)
_ATTEMPT_RE = re.compile(r"^\s*(?:[-*]\s+)?\**Attempt\**\s*:\s*\**\s*(.*?)\s*$", re.IGNORECASE)
_ISO_TIME = r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
_EVENT_RE = re.compile(rf"^\s*(?:[-*]\s+)?`?({_ISO_TIME})`?\s*\|(.*)$", re.IGNORECASE)
_STEP_RE = re.compile(r"^(?:step\s*)?(\d+)$", re.IGNORECASE)
_NEW_SESSION_GAP = timedelta(minutes=10)
"""A step 1 ``error`` line dated more than this after an attempt's closing line was logged by another session
(:func:`parse_friction`); sooner, it is a late line of the session that just finished."""


def _parse_event(line_no: int, at: str, rest: str) -> FrictionEvent:
    parts = [p.strip() for p in rest.split("|")]
    head = parts[0].strip("*` ").lower()
    moment = parse_time(at)
    if head in ("end", "finished"):  # the attempt's closing line (no step column)
        word = parts[1].strip("*` ").lower() if len(parts) > 1 else "finished"
        kind = "finished" if head == "finished" or word.startswith("finish") or not word else f"end {word}"
        return FrictionEvent(line_no, moment, None, kind, " | ".join(parts[1:]), "")
    step_m = _STEP_RE.match(head)
    if step_m is not None:
        step: int | None = int(step_m.group(1))
        rest_parts = parts[1:]
    else:  # no step column: "<time> | <kind> | ..."
        step, rest_parts = None, parts
    kind = rest_parts[0].strip("*` ").lower() if rest_parts else "?"
    tail = rest_parts[1:]
    what, fix = (" | ".join(tail[:-1]), tail[-1]) if len(tail) > 1 else ((tail or [""])[0], "")
    if fix in ("-", "\u2013", "\u2014"):  # a dash: no fix
        fix = ""
    return FrictionEvent(line_no, moment, step, kind or "?", what, fix)


def parse_friction(text: str) -> Friction:
    """Parse friction.md into attempts. An attempt starts at each ``Attempt: <time>`` line; lines before the
    first one (a v4 log) form an attempt of their own, and a second ``Prompt:`` line in one attempt starts
    another (a v4 re-run that appended a second header). In each attempt: the first ``Prompt:``, ``Agent:``,
    ``Outcome:``, ``Run:`` lines (a leading ``- `` or ``**`` is tolerated), every
    ``<time> | step <n> | <kind> | <what> | <fix>`` line (a ``|`` inside the what text is kept: the fix is the
    text after the last ``|``), the ``<time> | end | finished`` line, and v4 ``F<n> | ...`` lines as legacy.
    Other lines are kept only in the text.

    A session whose step 1 command stops before ``install.sh --log-start`` logs its error with no
    ``Attempt:`` header, after the previous attempt's ``end | finished`` line. Such a line, a step 1
    ``error`` dated more than :data:`_NEW_SESSION_GAP` after that closing line, starts a header-less attempt
    of its own (the lines after it, up to the next header, are its too): the session that failed is judged,
    not the one that had finished. Any other line after a closing line stays in its attempt (a line logged
    late in the same session must not become the latest attempt), and :func:`stopping_error` does not judge
    it."""
    attempts: list[Attempt] = []
    cur: dict[str, object] | None = None

    def orphan(event: FrictionEvent) -> bool:
        if cur is None or event.kind != "error" or event.step != 1 or event.at is None:
            return False
        closed = [e.at for e in cast("list[FrictionEvent]", cur["events"]) if e.kind == "finished" and e.at]
        return bool(closed) and event.at - max(closed) > _NEW_SESSION_GAP

    def close() -> None:
        if cur is not None:
            attempts.append(
                Attempt(
                    number=len(attempts) + 1,
                    started=cast("datetime | None", cur["started"]),
                    header=cast("dict[str, str]", cur["header"]),
                    events=tuple(cast("list[FrictionEvent]", cur["events"])),
                    legacy=tuple(cast("list[str]", cur["legacy"])),
                    first_line=cast(int, cur["first"]),
                    last_line=cast(int, cur["last"]),
                )
            )

    def begin(line_no: int, started: datetime | None) -> dict[str, object]:
        close()
        return {
            "started": started,
            "header": {},
            "events": [],
            "legacy": [],
            "first": line_no,
            "last": line_no,
        }

    for n, line in enumerate(text.splitlines(), start=1):
        attempt = _ATTEMPT_RE.match(line)
        if attempt is not None:
            cur = begin(n, parse_time(attempt.group(1)))
            continue
        key = _FRICTION_KEY_RE.match(line)
        event = _EVENT_RE.match(line) if key is None else None
        legacy = _FRICTION_ITEM_RE.match(line) if key is None and event is None else None
        if key is None and event is None and legacy is None:
            if cur is not None and line.strip():
                cur["last"] = n
            continue
        if key is not None:
            name = key.group(1).capitalize()
            header = cast("dict[str, str]", cur["header"]) if cur is not None else None
            if cur is None or (name == "Prompt" and header is not None and "Prompt" in header):
                cur = begin(n, None)
            cast("dict[str, str]", cur["header"]).setdefault(name, key.group(2).strip("*").strip())
        parsed = _parse_event(n, event.group(1), event.group(2)) if event is not None else None
        if cur is None or (parsed is not None and orphan(parsed)):
            cur = begin(n, None)
        if parsed is not None:
            cast("list[FrictionEvent]", cur["events"]).append(parsed)
        elif legacy is not None:
            cast("list[str]", cur["legacy"]).append(line.strip())
        cur["last"] = n
    close()
    return Friction(attempts=tuple(attempts), text=text)


def default_friction_path() -> Path:
    """``$AGENTSYNC_FRICTION_LOG``, else ``~/agent-context/setup/friction.md`` (the setup prompt's file)."""
    env = os.environ.get(FRICTION_ENV, "").strip()
    return expand(env) if env else expand("~/agent-context/setup/friction.md")


def read_friction(path: Path) -> Friction | None:
    """The parsed friction log at ``path`` (at most :data:`FRICTION_MAX_BYTES`), with the file's
    modification time; None when there is none."""
    target = expand(path)
    try:
        with target.open("rb") as fh:
            mtime = datetime.fromtimestamp(os.fstat(fh.fileno()).st_mtime, UTC)
            data = fh.read(FRICTION_MAX_BYTES + 1)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    truncated = len(data) > FRICTION_MAX_BYTES
    text = data[:FRICTION_MAX_BYTES].decode("utf-8", errors="replace")
    return dataclasses.replace(parse_friction(text), truncated=truncated, mtime=mtime)


# ---------------------------------------------------------------------------------------------------------
# install.log (scripts/install.sh) and the computed verdicts
# ---------------------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class InstallRun:
    """One scripts/install.sh run from install.log."""

    run_id: str
    started: datetime | None
    rc: int | None  # the end line's exit status; None: no end line (stopped early, or still running)
    seconds: int | None
    steps: tuple[tuple[str, dict[str, str]], ...]  # (step name, fields) in log order, the report step too
    simulated: bool  # a line says launchd=simulated
    lines: tuple[str, ...]

    def step(self, name: str) -> dict[str, str] | None:
        """The fields of step ``name``, if the run logged it."""
        return next((v for k, v in self.steps if k == name), None)

    def failed_step(self) -> tuple[str, dict[str, str]] | None:
        """The last step with ``result=failed`` (the report step excluded)."""
        failed = [(k, v) for k, v in self.steps if v.get("result") == "failed" and k != "report"]
        return failed[-1] if failed else None

    @property
    def display_id(self) -> str:
        """The run id without its process-id suffix (a weak fingerprint, of no use to triage)."""
        return re.sub(r"-\d+$", "", self.run_id)

    @property
    def report_after_end(self) -> bool:
        """Whether the ``step=report`` line comes after the run's ``end`` line (install.sh writes the setup
        report after closing the run: the report step is outside the run's seconds)."""
        end = next((i for i, ln in enumerate(self.lines) if _END_RE.search(ln)), None)
        report = next((i for i, ln in enumerate(self.lines) if " step=report " in f"{ln} "), None)
        return end is not None and report is not None and report > end

    @property
    def list_only(self) -> bool:
        """Whether this is an ``install.sh --list-folders`` run (setup prompt step 1), not an install run."""
        return self.step("list-folders") is not None


def install_runs_only(runs: Sequence[InstallRun]) -> list[InstallRun]:
    """``runs`` without the ``--list-folders`` runs (what the outcome, run type and agent state judge)."""
    return [r for r in runs if not r.list_only]


def read_install_runs(log: Path) -> list[InstallRun]:
    """install.log's runs, oldest first."""
    out: list[InstallRun] = []
    for run_id, fields, lines in _install_runs(log):
        end = fields.get("end", {})
        rc_text, secs = end.get("rc", ""), end.get("seconds", "")
        steps = tuple((k, v) for k, v in fields.items() if k not in ("start", "end"))
        out.append(
            InstallRun(
                run_id=run_id,
                started=parse_time(fields.get("start", {}).get("at", "")),
                rc=int(rc_text) if re.fullmatch(r"-?\d+", rc_text) else None,
                seconds=int(secs) if secs.isdigit() else None,
                steps=steps,
                simulated=any(_SIMULATED_RE.search(ln) for ln in lines),
                lines=tuple(lines),
            )
        )
    return out


def runs_for_attempt(friction: Friction | None, index: int, runs: Sequence[InstallRun]) -> list[InstallRun]:
    """The install.sh runs that started during attempt ``index`` (from its begin time to the next attempt's);
    every run when there is no friction log, or when the latest attempt has no time at all."""
    if friction is None or not friction.attempts:
        return list(runs)
    attempt = friction.attempts[index]
    lo = attempt.begin
    later = [a.begin for a in friction.attempts[index + 1 :] if a.begin is not None]
    hi = later[0] if later else None
    if lo is None:
        return list(runs) if index == len(friction.attempts) - 1 else []
    return [r for r in runs if r.started is not None and r.started >= lo and (hi is None or r.started < hi)]


@dataclasses.dataclass(frozen=True, slots=True)
class Outcome:
    """The computed outcome of one attempt: never what the agent declared."""

    kind: str  # "fully one command" | "worked with help" | "failed" | "unknown"
    step: int | None  # the failing prompt step, in the attempt's own prompt's numbering
    why: tuple[str, ...]
    version: int = PROMPT_VERSION  # the prompt whose step numbers ``step`` uses

    @property
    def text(self) -> str:
        """ "fully one command", "worked with help", "failed at step 2", "failed" or "unknown"."""
        if self.kind == "failed" and self.step is not None:
            return f"failed at step {self.step}"
        return self.kind

    @property
    def form_label(self) -> str | None:
        """The issue form's Outcome option (None when there is none to prefill); a v5 attempt's step is
        mapped to the current prompt's step that does the same work (v5's step 4, the IT request, is 3)."""
        if self.kind == "fully one command":
            return "Fully one command"
        if self.kind == "worked with help":
            return "Worked with help"
        if self.kind == "failed" and self.step is not None:
            step = prompt_layout(self.version).form_step.get(self.step, self.step)
            step = max(1, min(step, max(PROMPT_STEPS)))
            return f"Failed at step {step} ({PROMPT_STEPS[step]})"
        return None


def _run_events(attempt: Attempt) -> list[FrictionEvent]:
    """The attempt's events up to its closing ``finished`` line: a line logged after the run finished cannot
    have stopped it."""
    events = list(attempt.events)
    end = next((i for i, e in enumerate(events) if e.kind == "finished"), len(events))
    return events[:end]


def _unresolved_error(attempt: Attempt) -> FrictionEvent | None:
    """The last error with no later ``end`` of the same step."""
    events = _run_events(attempt)
    for i in range(len(events) - 1, -1, -1):
        e = events[i]
        if e.kind == "error" and not any(x.kind == "end" and x.step == e.step for x in events[i + 1 :]):
            return e
    return None


def stopping_error(attempt: Attempt, runs: Sequence[InstallRun] = ()) -> FrictionEvent | None:
    """The last error that stopped the run: logged in a step before the report step (v6: 3, v5: 5) and before
    the attempt's closing line, with no later event of a later step before the report (the prompt's "log it
    and go to step 3") and not resolved. v5 resolves it with a later ``end`` of its step; v6 (no step lines)
    with a later install.sh run in ``runs`` that ended rc 0: any such run for a step-1 error (a
    ``--list-folders`` re-run, or the install run that step 2 starts), an install run for an error of the
    install step. v7's install step is the one install.sh command, so any install run of the attempt that
    ended rc 0 resolves its error, whenever the line was logged: an agent logs after the command returns."""
    layout = attempt.layout
    events = _run_events(attempt)
    last_step = layout.report_step
    for i in range(len(events) - 1, -1, -1):
        e = events[i]
        if e.kind != "error" or e.step is None or e.step >= last_step:
            continue
        later = events[i + 1 :]
        resolved = any(x.kind == "end" and x.step == e.step for x in later)
        went_on = any(x.step is not None and e.step < x.step < last_step for x in later)
        if not layout.logs_steps:
            done = [r for r in runs if r.rc == 0]
            if e.step >= layout.install_step:
                done = install_runs_only(done)
            if layout.version < 7 or e.step < layout.install_step:
                at = e.at
                done = [r for r in done if at is not None and r.started is not None and r.started >= at]
            resolved = resolved or bool(done)
        if not resolved and not went_on:
            return e
    return None


def compute_outcome(
    attempt: Attempt | None, runs: Sequence[InstallRun], *, doctor_fails: Sequence[str] = ()
) -> Outcome:
    """Computed from what the person saw and did, never from the agent's own friction lines. Judged on the
    attempt's install runs (``--list-folders`` runs are not install runs):

    - fully one command: the last install run ended rc 0, and the attempt has no question beyond the folder
      question, no click beyond the Allow clicks, no approval, no error that stopped the run
      (:func:`stopping_error`) and ``doctor_fails`` (unexpected doctor FAIL names) is empty;
    - worked with help: the last install run ended rc 0 otherwise (also with no friction log, or an attempt
      with no v5 event line, whose human turns are unknown);
    - failed at step <n>: at the installer's step (3) when the last install run did not end rc 0; at the
      stopping error's step when install.sh ended rc 0; with no install run, at the step of the last error
      with no later end of that step (then the last error, then the last step logged).

    Deviation, prompt and error lines are agent friction (the Summary counts them on their own line): they
    never change the outcome by themselves, except an error that stopped the run."""
    installs = install_runs_only(runs)
    last = installs[-1] if installs else None
    layout = attempt.layout if attempt is not None else prompt_layout(None)
    version = layout.version
    if last is not None and last.rc == 0:
        why: list[str] = []
        if attempt is None:
            why.append("no friction log, so the human turns are unknown")
        else:
            stop = stopping_error(attempt, runs)
            if stop is not None:
                resolution = (
                    "no later end of that step"
                    if layout.logs_steps
                    else "no later install.sh run of that step ended rc 0"
                )
                return Outcome(
                    "failed",
                    stop.step,
                    (
                        "install.sh exit 0",
                        f"step {stop.step} logged an error (F{stop.line}) and did not finish: {resolution} "
                        "and no later step before the report",
                    ),
                    version,
                )
            if layout.logs_steps and not any(
                e.kind in (*FRICTION_KINDS, *STEP_KINDS) for e in attempt.events
            ):
                legacy = f" ({len(attempt.legacy)} legacy v4 line(s))" if attempt.legacy else ""
                why.append(f"human turns unknown: the attempt has no v5 event line{legacy}")
            elif not layout.logs_steps and attempt.started is None:
                why.append("human turns unknown: the attempt has no Attempt: line (install.sh --log-start)")
            if attempt.extra_questions():
                why.append(f"{len(attempt.extra_questions())} question(s) beyond the folder question")
            if attempt.extra_clicks():
                why.append(f"{len(attempt.extra_clicks())} click(s) beyond the Allow clicks")
            approvals = len(attempt.of_kind("approval"))
            if approvals:
                why.append(f"{approvals} approval(s)")
        if doctor_fails:
            why.append(f"{len(doctor_fails)} unexpected doctor FAIL(s): {', '.join(doctor_fails)}")
        if not why:
            return Outcome(
                "fully one command",
                None,
                ("install.sh exit 0", "no turn beyond the unavoidable ones"),
                version,
            )
        return Outcome("worked with help", None, ("install.sh exit 0", *why), version)
    if last is not None:
        failing = last.failed_step()
        where = f" at its {failing[0]} step (rc {failing[1].get('rc', '?')})" if failing else ""
        if last.rc is None:
            last_failed = f", last failed step {failing[0]}" if failing else ""
            detail = f"install.sh has no end line (stopped early{last_failed})"
        else:
            detail = f"install.sh exited {last.rc}{where}"
        return Outcome("failed", layout.install_step, (detail,), version)
    if attempt is None:
        return Outcome("unknown", None, ("no friction log and no install.sh run in install.log",), version)
    error = _unresolved_error(attempt) or next(iter(attempt.of_kind("error")[::-1]), None)
    if error is not None:
        why_error = f"no install.sh run; step {error.step} logged an error"
        return Outcome("failed", error.step, (why_error,), version)
    steps = [e.step for e in attempt.events if e.step is not None]
    if steps:
        why_step = f"no install.sh run and no error; the last step logged is {steps[-1]}"
        return Outcome("failed", steps[-1], (why_step,), version)
    if runs and not layout.logs_steps:  # v6: only --list-folders ran (step 1), and nothing was logged
        return Outcome("failed", 1, ("no install run: only install.sh --list-folders ran (step 1)",), version)
    return Outcome("unknown", None, ("no install.sh run in this attempt and no step logged",), version)


def home_path() -> str:
    """This process's HOME (module-level so tests replace it)."""
    return str(Path.home())


def is_sandbox_home(home: str) -> bool:
    """Whether ``home`` (or what it resolves to) is under a temporary folder (:data:`SANDBOX_HOMES`)."""
    candidates = {home}
    with contextlib.suppress(OSError):
        candidates.add(str(Path(home).resolve()))
    return any(c == p or c.startswith(p + "/") for c in candidates for p in SANDBOX_HOMES)


def compute_run_type(runs: Sequence[InstallRun], home: str) -> str:
    """ "simulated launchd" when the attempt's last install run (else its last run) says
    ``launchd=simulated``, "sandbox" when HOME is under a temporary folder, else "real"."""
    runs = install_runs_only(runs) or list(runs)
    if runs and runs[-1].simulated:
        return "simulated launchd"
    return "sandbox" if is_sandbox_home(home) else "real"


def loop_stage(baseline: str | None, topics: int | None, synced: bool) -> str:
    """How far the loop got (:data:`LOOP_STAGES`), from status's loop line (``baseline`` is
    ``loop.baseline_state``'s word, ``topics`` the curated page count) and whether a sync ran: the furthest
    stage reached, so a curated page outranks the baseline before it."""
    if baseline == "after":
        return "after run"
    if topics:
        return f"topics {topics}"
    stage = {"before": "before run", "confirmed": "baseline confirmed", "draft": "baseline drafted"}
    if baseline in stage:
        return stage[baseline]
    return "synced" if synced else "installed"


def loop_next_text(line: str) -> str:
    """A ``NEXT:`` line without paths: each ``~/...`` or ``/...`` path becomes its last part
    (``~/.local/bin/agentsync`` is ``agentsync``). The loop's wording names no mirror path or file name, so
    this leaves fixed text, counts and source ids (which the report redacts)."""
    return _NEXT_PATH_RE.sub(r"\1", line.strip())


def it_draft_fields(text: str) -> tuple[list[str], list[str]]:
    """(person fields, IT fields) still unfilled in an IT request draft: the placeholders of
    :data:`IT_PERSON_FIELDS` and :data:`IT_ADMIN_FIELDS` found in the email (below the draft's first ``---``
    line, whose header lists the fields as they were when it was written) outside a placeholder-legend row."""
    parts = re.split(r"^---[ \t]*$", text, maxsplit=1, flags=re.MULTILINE)
    email = parts[1] if len(parts) > 1 else text
    body = "\n".join(ln for ln in email.splitlines() if not ln.lstrip().startswith("| `<"))
    return [f for f in IT_PERSON_FIELDS if f in body], [f for f in IT_ADMIN_FIELDS if f in body]


def expected_warn(name: str, detail: str, *, agents_installed: bool) -> str | None:
    """Why a doctor warn line is expected, or None: the ad hoc launcher's signature and requirement (every
    build without a Developer ID: a Developer ID build is IT's, docs/deploy/mdm), and a LaunchAgent not
    installed when this setup has not installed them yet (no agent step, skipped, or simulated)."""
    if name in ("launcher.signature", "launcher.requirement") and "ad hoc" in detail:
        return "ad hoc launcher"
    if name.startswith("launchd.") and "is not installed" in detail and not agents_installed:
        return "LaunchAgents not installed yet"
    return None


_DOCTOR_NOTES = (
    " (for IT: Developer ID build (docs/deploy/mdm))",  # doctor.ADHOC_IT_NOTE under AGENTSYNC_NO_NEXT_HINT
    " (installed by the agent step below)",  # doctor.AGENT_STEP_NOTE under AGENTSYNC_AGENT_STEP_PENDING
)


def _without_fix(line: str) -> str:
    """A doctor line without its trailing ``(fix: ...)`` or note (the fix text may hold parentheses)."""
    if " (fix: " in line:
        return line.split(" (fix: ", 1)[0]
    for note in _DOCTOR_NOTES:
        if line.endswith(note):
            return line[: -len(note)]
    return line


def agents_installed(runs: Sequence[InstallRun]) -> bool:
    """Whether the last install run installed the LaunchAgents (its agent step done, not simulated)."""
    runs = install_runs_only(runs)
    agent = runs[-1].step("agent") if runs else None
    if agent is None or runs[-1].simulated:
        return False
    return agent.get("result") == "done" and agent.get("note") != "simulated"


def build_issue_url(
    *,
    outcome: str | None,
    run_type: str | None,
    prompt: str | None,
    agent: str | None,
    loop_stage: str | None = None,
) -> str:
    """The prefilled "Setup report" issue link: :data:`ISSUE_URL`, a title, and the form fields of
    :data:`ISSUE_FIELDS`, URL-encoded. Only these five values go in: never the report body."""
    title = ISSUE_TITLE + " · ".join(v for v in (outcome, run_type, prompt) if v)
    params: list[tuple[str, str]] = [("title", title)]
    values = (
        ("outcome", outcome),
        ("run_type", run_type),
        ("prompt", prompt),
        ("agent", agent),
        ("loop_stage", loop_stage),
    )
    for key, value in values:
        if value:
            params.append((ISSUE_FIELDS[key], value))
    return ISSUE_URL + "&" + urlencode(params, quote_via=quote)


def issue_link(report: str) -> str | None:
    """The prefilled issue link a report ends with (what the CLI prints last), or None."""
    lines = report.rstrip("\n").splitlines()
    return lines[-1] if lines and lines[-1].startswith(ISSUE_URL) else None


# ---------------------------------------------------------------------------------------------------------
# probes (module-level so tests replace them)
# ---------------------------------------------------------------------------------------------------------


def _login_name() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def _full_name() -> str:
    """The account's full name (what ``id -F`` prints)."""
    return pwd.getpwuid(os.getuid()).pw_gecos.split(",", 1)[0].strip()


def _serial_number(run: Callable[[Sequence[str], float], tuple[int, str]]) -> str | None:
    rc, out = run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], 3.0)
    m = re.search(r'"IOPlatformSerialNumber" = "([^"]+)"', out) if rc == 0 else None
    return m.group(1) if m else None


def _host_names() -> list[str]:
    names = {socket.gethostname(), platform.node()}
    out: list[str] = []
    for n in sorted(n for n in names if n):
        out.append(n)
        short = n.split(".", 1)[0]
        if short != n:
            out.append(short)
    return [n for n in out if n.lower() not in ("localhost", "local")]


def _computer_names(run: Callable[[Sequence[str], float], tuple[int, str]]) -> list[str]:
    """The ComputerName and LocalHostName (``scutil --get``), which may differ from the host name."""
    out: list[str] = []
    for key in ("ComputerName", "LocalHostName"):
        rc, text = run(["/usr/sbin/scutil", "--get", key], 2.0)
        if rc == 0 and text.strip() and "\n" not in text.strip():
            out.append(text.strip())
    return out


def _launchctl_print(run: Callable[[Sequence[str], float], tuple[int, str]], label: str) -> tuple[int, str]:
    """``launchctl print gui/<uid>/<label>`` (read-only)."""
    return run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"], 5.0)


def default_setup_log() -> Path:
    """``$AGENTSYNC_SETUP_LOG``, else ``~/agent-context/setup/install.log`` (scripts/install.sh writes it)."""
    env = os.environ.get(SETUP_LOG_ENV, "").strip()
    return expand(env) if env else expand("~/agent-context/setup/install.log")


def cloud_storage_root() -> Path:
    """``~/Library/CloudStorage`` (the File Provider roots; listing it reads no provider's files)."""
    return Path.home() / "Library" / "CloudStorage"


def cloud_folder_names(root: Path | None = None, limit: int = _LISTING_MAX) -> list[tuple[str, int, str]]:
    """(provider folder, depth, name) for every directory under ``root`` (default ~/Library/CloudStorage) at
    depth 2 and 3, hidden ones skipped and symlinks not followed: what the setup prompt's
    ``find ~/Library/CloudStorage -mindepth 2 -maxdepth 3 -type d ! -name '.*'`` lists. Names only; no file
    is opened. At most ``limit`` entries."""
    base = root if root is not None else cloud_storage_root()
    out: list[tuple[str, int, str]] = []

    def subdirs(path: Path) -> list[os.DirEntry[str]]:
        try:
            with os.scandir(path) as it:
                entries = [e for e in it if not e.name.startswith(".") and e.is_dir(follow_symlinks=False)]
        except OSError:
            return []
        return sorted(entries, key=lambda e: e.name)

    for provider in subdirs(base):
        for d2 in subdirs(Path(provider.path)):
            out.append((provider.name, 2, d2.name))
            if len(out) >= limit:
                return out
            for d3 in subdirs(Path(d2.path)):
                out.append((provider.name, 3, d3.name))
                if len(out) >= limit:
                    return out
    return out


_XATTR_NOFOLLOW = 0x0001


def file_provider_marker(path: Path) -> str | None:
    """The name of the first File Provider extended attribute on ``path`` itself (a name containing
    ``fileprovider`` or ``file-provider``, such as the domain root's ``com.apple.file-provider-domain-id``),
    None when it has none (a plain folder, as a sandbox's ``mkdir`` makes). ``listxattr`` on the folder only,
    symlinks not followed: no provider file is opened or read. Raises OSError (ENOSYS off macOS)."""
    if sys.platform != "darwin":
        raise OSError(errno.ENOSYS, "listxattr is read through libSystem on macOS only")
    libc = ctypes.CDLL(None, use_errno=True)
    fn = libc.listxattr
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
    fn.restype = ctypes.c_ssize_t
    raw = os.fsencode(path)
    size = fn(raw, None, 0, _XATTR_NOFOLLOW)
    if size < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(path))
    if size == 0:
        return None
    buf = ctypes.create_string_buffer(size)
    size = fn(raw, buf, size, _XATTR_NOFOLLOW)
    if size < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(path))
    names = [n.decode("utf-8", "replace") for n in buf.raw[:size].split(b"\0") if n]
    return next((n for n in names if "fileprovider" in n.lower() or "file-provider" in n.lower()), None)


def _domain_text(folder: Path) -> str:
    """Whether a ~/Library/CloudStorage folder is a File Provider domain, from its own extended attributes."""
    try:
        marker = file_provider_marker(folder)
    except OSError as exc:
        return f"File Provider domain: unknown ({exc.strerror or exc})"
    if marker is None:
        return "not a File Provider domain: no File Provider attribute (a plain folder)"
    return f"File Provider domain ({marker})"


def agentsync_on_path(path_env: str | None = None) -> list[str]:
    """Every executable ``agentsync`` on ``$PATH``, in PATH order, without duplicates."""
    found: list[str] = []
    for d in (path_env if path_env is not None else os.environ.get("PATH", "")).split(os.pathsep):
        if not d:
            continue
        candidate = str(Path(d) / "agentsync")
        if candidate not in found and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            found.append(candidate)
    return found


def _is_home_agentsync(candidate: str) -> bool:
    """Whether ``candidate`` is this home folder's ``~/.local/bin/agentsync`` (by path or same file)."""
    mine = {
        Path.home() / ".local" / "bin" / "agentsync",
        Path.home().resolve() / ".local" / "bin" / "agentsync",
    }
    if Path(os.path.normpath(candidate)) in mine:
        return True
    for m in mine:
        with contextlib.suppress(OSError):
            if m.exists() and m.samefile(candidate):
                return True
    return False


# ---------------------------------------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class _Facts:
    """What the sections found that the Summary repeats."""

    install_runs: int = 0
    install_seconds: int = 0
    last_install: str | None = None
    launchd_simulated: bool = False
    doctor: Counter[str] | None = None
    doctor_problems: list[tuple[str, str, str]] = dataclasses.field(
        default_factory=list
    )  # (tag, name, detail)
    baseline: tuple[int, int] | None = None  # (sources with a complete baseline, sources) from status
    background: list[str] = dataclasses.field(default_factory=list)
    shadow: str | None = None
    instructions: tuple[Counter[str], int, int] | None = None  # (per kind, lines read, runs among them)
    first_sync: tuple[int, int] | None = None  # the last "converted N, deferred M online-only" in install.out
    loop: tuple[str | None, int | None, bool] | None = None  # status's (baseline, topics, a sync ran)
    evidence: int | None = None  # lines of the evidence parts that say "not measured"; None: parts not run


class _Run:
    """One report: the deadline, the loaded config (or why not), and the facts sections share."""

    def __init__(self, config_path: Path | None, hooks: ReportHooks, budget_s: float) -> None:
        self.t0 = time.monotonic()
        self.deadline = self.t0 + budget_s
        self.hooks = hooks
        self.config_path = expand(config_path) if config_path is not None else default_config_path()
        self.config: Config | None = None
        self.config_error: str | None = None
        try:
            self.config = load_config(self.config_path)
        except Exception as exc:  # a missing or invalid config is a finding, not a crash
            self.config_error = f"{type(exc).__name__}: {exc}"
        self.system_proxy: net.SystemProxy | None = None
        with contextlib.suppress(Exception):
            self.system_proxy = net.system_proxy()
        self.red = Redactor()
        self.facts = _Facts()
        self.friction: Friction | None = None
        self.friction_path = default_friction_path()
        self.friction_error: str | None = None
        self.listing_note: str | None = None
        self.install_log = default_setup_log()
        self.install_runs: list[InstallRun] = []
        with contextlib.suppress(OSError):
            self.install_runs = read_install_runs(self.install_log)
        self.outcome: Outcome | None = None  # set by the Summary
        self.run_type: str | None = None
        self.loop_stage: str | None = None
        self.loop_line: str | None = None  # the Summary's Loop line, computed after Doctor (build_report)
        self.agent_named: tuple[int | None, int] | None = None  # folders named with agent words only
        self.evidence_steps = 0  # the evidence parts' looks at the clock while SQLite worked (_Mirror.steps)
        self.evidence_statements: tuple[str, ...] = ()  # the statements they ran

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def run(self, argv: Sequence[str], timeout: float = 5.0) -> tuple[int, str]:
        """``argv``'s (exit status, stdout + stderr), bounded by ``timeout`` and the budget; never raises
        (a missing program, a timeout or a spent budget is exit status 127 / 124 with a note)."""
        limit = min(timeout, self.remaining() - _RESERVE_S)
        if limit <= 0.2:
            return 124, "skipped: the report's time budget is used up"
        try:
            cp = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=limit,
                check=False,
                stdin=subprocess.DEVNULL,
                env={**os.environ, "LC_ALL": "C"},
            )
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {limit:.1f}s"
        except OSError as exc:
            return 127, f"{type(exc).__name__}: {exc.strerror or exc}"
        return cp.returncode, (cp.stdout + cp.stderr).strip()

    def call(self, fn: Callable[[], _T], timeout: float) -> _T:
        """``fn()`` in a daemon thread, bounded by ``timeout`` and the budget (TimeoutError past it; the
        thread is abandoned, so a hung probe never holds the report)."""
        limit = min(timeout, self.remaining() - _RESERVE_S)
        if limit <= 0.2:
            raise TimeoutError("the report's time budget is used up")
        try:
            return call_with_timeout(fn, limit, name="setup-report")
        except CallTimedOutError as exc:  # the report names it TimeoutError, as it always has
            raise TimeoutError(str(exc)) from None

    def devtools(self) -> bool:
        rc, out = self.run(["/usr/bin/xcode-select", "-p"], 3.0)
        return rc == 0 and Path(out.splitlines()[0] if out else "").is_dir()

    def log_dir(self) -> Path:
        return expand(self.config.log_dir) if self.config else Path.home() / "Library" / "Logs" / "agentsync"

    def label_prefix(self) -> str:
        return self.config.launchd_label_prefix if self.config else "com.agentsync"


def _fence(lines: Iterable[str]) -> list[str]:
    """A ``~~~`` code block (a GitHub issue form's ``render: markdown`` wraps the report in backticks, so a
    backtick fence inside would end it early: both kinds of fence are broken up inside the block)."""
    body = [ln.replace("~~~", "~ ~ ~").replace("```", "` ` `") for ln in lines]
    return ["~~~text", *body, "~~~"] if body else ["_(none)_"]


def _tail(path: Path, limit: int = _TAIL_BYTES) -> list[str]:
    with path.open("rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        fh.seek(max(0, size - limit))
        data = fh.read(limit)
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > limit else lines


def _plist_version(app: str) -> str | None:
    info = expand(app) / "Contents" / "Info.plist"
    try:
        data = plistlib.loads(info.read_bytes())
    except FileNotFoundError:
        return None
    short, build = data.get("CFBundleShortVersionString"), data.get("CFBundleVersion")
    return f"{short} ({build})" if build and build != short else str(short or build or "unknown")


def _shorten(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if text[limit - 1] != " " and " " in cut:  # end at a word's end, never inside a word
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


# ---- facts for redaction --------------------------------------------------------------------------------


def _cloud_parts(path: Path) -> tuple[str, list[str]] | None:
    """(provider folder, components below it) for a path under ~/Library/CloudStorage, else None."""
    root = cloud_storage_root()
    for base in (root, root.resolve()):
        with contextlib.suppress(ValueError):
            parts = expand(path).relative_to(base).parts
            if parts:
                return parts[0], list(parts[1:])
    return None


def _org_of(provider: str) -> str | None:
    for prefix in ("OneDrive-SharedLibraries-", "OneDrive-"):
        if provider.startswith(prefix):
            org = provider[len(prefix) :]
            return None if org in ("", "Personal") else org
    return None


def _proxy_hosts(r: _Run) -> list[str]:
    urls = [os.environ.get(k, "") for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")]
    urls += [os.environ.get(k, "") for k in ("HTTP_PROXY", "http_proxy")]
    if r.system_proxy is not None:
        urls += [r.system_proxy.https_proxy or "", r.system_proxy.http_proxy or ""]
        urls += [r.system_proxy.pac_url or ""]
    if r.config is not None and r.config.network.proxy:
        urls.append(r.config.network.proxy)
    hosts: list[str] = []
    for url in filter(None, (u.strip() for u in urls)):
        if url.lower() in ("direct", "none", "off"):
            continue
        host = urlsplit(url if "://" in url else f"http://{url}").hostname
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def _docs_commits(r: _Run) -> list[str]:
    """Commit ids the docs repo's HEAD reflog names (read as a file: no git process), newest last."""
    if r.config is None:
        return []
    reflog = expand(r.config.docs_repo) / ".git" / "logs" / "HEAD"
    shas: list[str] = []
    with contextlib.suppress(OSError):
        for line in _tail(reflog):
            m = _REFLOG_RE.match(line)
            if m:
                shas += [s for s in m.groups() if s not in shas]
    return shas[-500:]


def _project_values(path: Path) -> list[str]:
    """What to register for a configured source folder outside ~/Library/CloudStorage (a project checkout,
    say): below the home folder, the path from its first component that names something and that component
    alone, so a sibling path does not show the project's name either; anywhere else, the whole path. Leading
    containers are skipped: :data:`_GENERIC_HOME_DIRS`, dot folders, and a folder above the source named only
    with coding-agent product words (``~/Documents/GitHub/<project>``: registered alone, "GitHub" would
    replace the agent's name; the source folder itself is registered whatever its name). Components below
    the first are not registered alone: a registered ``correspondence`` or ``team-files`` would replace
    ordinary words."""
    home = Path.home()
    parts: list[str] | None = None
    for base in (home, home.resolve()):
        with contextlib.suppress(ValueError):
            parts = list(path.relative_to(base).parts)
            break
    if parts is None:
        return [str(path)]
    while parts and (
        parts[0].casefold() in _GENERIC_HOME_DIRS
        or parts[0].startswith(".")
        or (len(parts) > 1 and _agent_words(parts[0]))
    ):
        parts.pop(0)
    return ["/".join(parts), parts[0]] if len(parts) > 1 else parts


def _build_redactor(r: _Run) -> Redactor:
    """Register everything this Mac's report could name (see the module docstring)."""
    red = Redactor()
    home = Path.home()
    for h in sorted({str(home), str(home.resolve())}, key=len, reverse=True):
        red.add("home", h)
    with contextlib.suppress(Exception):  # after the home: a sandbox HOME inside $TMPDIR stays "~"
        tmpdir = os.environ.get("TMPDIR", "").strip() or tempfile.gettempdir()
        for t in sorted({tmpdir.rstrip("/"), os.path.realpath(tmpdir).rstrip("/")}, key=len, reverse=True):
            if t not in ("", "/tmp", "/private/tmp", "/var/tmp"):
                red.add("tmp", t)
    # The login first: registered after the fuzzy full name, a login that is the name run together
    # ("janedoe" for "Jane Doe") would be taken for a variant of the name and shown as <name>.
    with contextlib.suppress(Exception):
        red.add("user", _login_name())
    with contextlib.suppress(Exception):
        full = _full_name()
        red.add("name", full, fuzzy=True)  # "Jane Doe", "jane-doe", "JaneDoe", "JANE_DOE"
        for part in full.split():
            if len(part.strip(".,")) >= 3:  # a lone first or last name: its written and upper-case forms
                red.add("name", part.strip(".,"))
                red.add("name", part.strip(".,").upper())
    with contextlib.suppress(Exception):
        red.add("serial", _serial_number(r.run))
    with contextlib.suppress(Exception):
        for h in _host_names():
            red.add("host", h, ignore_case=True)
    with contextlib.suppress(Exception):
        for h in _computer_names(r.run):
            red.add("host", h, ignore_case=True)
    orgs: list[str] = []
    with contextlib.suppress(OSError):
        orgs += [o for o in (_org_of(p.name) for p in sorted(cloud_storage_root().iterdir())) if o]
    cloud_sources: list[tuple[str, str, list[str]]] = []  # (id, provider, components)
    project_paths: list[Path] = []  # configured folders outside CloudStorage and outside agentsync's own
    if r.config is not None:
        # agentsync's own, never a project: the inbox it keeps beside the docs repo, and every folder beside
        # the docs repo (~/agent-context). Not the latter when a hand-set docs_repo sits straight in a
        # container such as ~/Documents: the person's own projects live there too.
        beside = expand(r.config.docs_repo).parent
        own = {beside / "inbox", (beside / "inbox").resolve()}
        if beside.name.casefold() not in _GENERIC_HOME_DIRS:
            own |= {beside, beside.resolve()}
        own -= {home, home.resolve()}
        for src in r.config.sources:
            parts = _cloud_parts(src.path) if src.path is not None else None
            if parts is not None:
                cloud_sources.append((src.id, *parts))
                org = _org_of(parts[0])
                if org:
                    orgs.append(org)
            elif src.path is not None and not any(expand(src.path).is_relative_to(o) for o in own):
                project_paths.append(expand(src.path))
        tenant = r.config.graph.tenant
        if tenant and "." in tenant:
            orgs.append(tenant.split(".", 1)[0])
        for src in r.config.sources:
            if src.site:
                host, _, site_path = src.site.partition(":")
                if host.endswith(".sharepoint.com"):
                    orgs.append(host.split(".", 1)[0])
                name = site_path.rstrip("/").rsplit("/", 1)[-1]
                red.add("library", unquote(name), fuzzy=True)
    for org in orgs:
        red.add("org", org, fuzzy=True)
    for _sid, provider, comps in cloud_sources:
        shared = provider.startswith("OneDrive-SharedLibraries-") and bool(comps)
        if shared:
            red.add("library", comps[0], fuzzy=True)
        for c in comps[1:] if shared else comps:
            red.add("folder", c, fuzzy=True)
    # Every folder the setup prompt's listing showed, configured or not (the agent may have quoted them).
    listing_ok = True
    try:
        listed = r.call(cloud_folder_names, timeout=3.0)
    except Exception as exc:
        listed, listing_ok = [], False
        r.listing_note = f"could not list ~/Library/CloudStorage at depth 2-3 for redaction: {exc}"
    configured = sum(1 for _sid, _provider, comps in cloud_sources for c in comps if _agent_words(c))
    configured += sum(1 for path in project_paths if _agent_words(path.name))
    agent_listed = sum(1 for _provider, _depth, name in listed if _agent_words(name))
    r.agent_named = (agent_listed if listing_ok else None, configured)
    for provider, depth, name in listed:
        if name in _GENERIC_FOLDERS or _agent_words(name):
            continue
        shared = provider.startswith("OneDrive-SharedLibraries-")
        red.add("library" if shared and depth == 2 else "folder", name, fuzzy=True)
    for path in project_paths:
        for value in _project_values(path):
            red.add("folder", value, ignore_case=True)
    # Every configured id: an inbox or project source has one too, and a folder word inside an unregistered
    # id left it half-redacted (``<prefix>-<folder-N>-mail``, field report 2026-10-06).
    cloud_ids = {sid for sid, _provider, _comps in cloud_sources}
    for src in r.config.sources if r.config is not None else ():
        if src.id in cloud_ids or src.id not in _GENERIC_IDS:
            red.add("source", src.id)
    for host in _proxy_hosts(r):
        red.add("proxy", host, ignore_case=True)
    with contextlib.suppress(Exception):
        for sha in _docs_commits(r):
            red.add_commit(sha)
    return red


# ---- sections -------------------------------------------------------------------------------------------


def _install_source(r: _Run) -> str:
    where = "unknown"
    directory: Path | None = None
    with contextlib.suppress(Exception):
        raw = importlib.metadata.distribution("agentsync").read_text("direct_url.json")
        if raw:
            info = json.loads(raw)
            url = str(info.get("url", ""))
            path = Path(unquote(urlsplit(url).path)) if url.startswith("file:") else None
            if path is not None and "dir_info" in info:
                directory = path
                editable = " (editable)" if info["dir_info"].get("editable") else ""
                where = f"checkout {path}{editable}"
            elif path is not None:
                where = f"wheel {path.name}"
            elif url:
                where = url
    if directory is not None and (directory / ".git").exists() and r.devtools():
        where += _checkout_facts(r, directory)
    with contextlib.suppress(Exception):
        starts = [
            (fields["start"], lines)
            for _id, fields, lines in _install_runs(r.install_log)
            if "start" in fields
        ]
        if starts:
            start, lines = starts[-1]
            commit = start.get("commit")
            if commit:
                where += f"; last install.sh run: commit {commit}"
                tree = start.get("tree") or _legacy_tree(lines)
                if tree:
                    where += f" tree {tree} (the SHA-256 prefix of `git diff HEAD` in that checkout)"
    return where


def _git_ro(r: _Run, directory: Path, *args: str, timeout: float = 3.0) -> tuple[int, bytes]:
    """``git -C directory args`` read-only (``GIT_OPTIONAL_LOCKS=0``: not even the index's stat cache is
    rewritten), bounded by ``timeout`` and the report's budget: (exit status, stdout bytes); 124/127 on a
    timeout or a missing git."""
    limit = min(timeout, r.remaining() - _RESERVE_S)
    if limit <= 0.2:
        return 124, b""
    try:
        cp = subprocess.run(
            ["/usr/bin/git", "-C", str(directory), *args],
            capture_output=True,
            timeout=limit,
            check=False,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        return 124, b""
    except OSError:
        return 127, b""
    return cp.returncode, cp.stdout


def origin_label(url: str) -> str:
    """The checkout's ``origin`` as the report shows it: ``github.com/renchris/agent-context-sync`` (https,
    ssh or scp form, with or without ``.git``), else ``other (redacted)`` (a fork or mirror URL may name a
    person or an organisation), or ``none``."""
    url = url.strip()
    if not url:
        return "none"
    m = re.fullmatch(
        r"(?:(?:https?|ssh|git)://(?:[^@/]+@)?|[^@/]+@)github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?", url
    )
    if m is not None and (m.group(1).lower(), m.group(2).lower()) == ("renchris", "agent-context-sync"):
        return f"github.com/{_REPO_SLUG}"
    return "other (redacted)"


def tree_fingerprint(diff: bytes) -> str:
    """install.sh's local-change fingerprint: the first 12 hex digits of the SHA-256 of ``git diff HEAD``."""
    return hashlib.sha256(diff).hexdigest()[:12]


def _checkout_facts(r: _Run, directory: Path) -> str:
    """`` @ <sha>[ (uncommitted changes) tree=<fp>] · origin: ... · on origin/main: yes|no|unknown`` for the
    installed checkout (every git call read-only; the origin URL itself is never shown)."""
    rc, out = _git_ro(r, directory, "rev-parse", "--short=12", "HEAD")
    if rc != 0:
        return ""
    sha = out.decode("ascii", "replace").strip()
    text = f" @ {sha}"
    dirty, _ = _git_ro(r, directory, "diff", "--quiet", "HEAD", "--")
    if dirty == 1:
        rc, diff = _git_ro(r, directory, "diff", "--no-ext-diff", "--no-color", "HEAD", "--")
        text += " (uncommitted changes" + (f", tree={tree_fingerprint(diff)})" if rc == 0 else ")")
    rc, url = _git_ro(r, directory, "remote", "get-url", "origin")
    text += f" · origin: {origin_label(url.decode('utf-8', 'replace')) if rc == 0 else 'none'}"
    rc, _ = _git_ro(r, directory, "merge-base", "--is-ancestor", "HEAD", "origin/main")
    on_main = {0: "yes", 1: "no"}.get(rc, "unknown (no origin/main in this checkout)")
    return text + f" · on origin/main: {on_main} (as of the checkout's last fetch)"


def _legacy_tree(lines: Sequence[str]) -> str | None:
    """The dirty-tree fingerprint of a start line written before ``tree=<fp>`` (``commit=<sha>-dirty dirty
    <fp>``), or None."""
    for line in lines:
        m = re.search(r"\scommit=\S+-dirty dirty ([0-9a-f]{6,64})\b", line)
        if m:
            return m.group(1)
    return None


def _environment(r: _Run) -> list[str]:
    out: list[str] = []

    def fact(label: str, fn: Callable[[], str]) -> None:
        try:
            value = fn()
        except Exception as exc:
            value = f"(could not read: {type(exc).__name__}: {exc})"
        out.append(f"- {label}: {value}")

    def sw_vers() -> str:
        rc, text = r.run(["/usr/bin/sw_vers"], 3.0)
        if rc != 0:
            return f"sw_vers exited {rc}: {text}"
        kv = dict(ln.split(":", 1) for ln in text.splitlines() if ":" in ln)
        return " ".join(
            v.strip() for k, v in kv.items() if k.strip() in ("ProductName", "ProductVersion")
        ) + (f" ({kv['BuildVersion'].strip()})" if "BuildVersion" in kv else "")

    def arch() -> str:
        machine = platform.machine()
        rc, translated = r.run(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"], 2.0)
        return machine + (" (this Python runs under Rosetta)" if rc == 0 and translated == "1" else "")

    def enrollment() -> str:
        rc, text = r.run(["/usr/bin/profiles", "status", "-type", "enrollment"], 5.0)
        joined = "; ".join(ln.strip() for ln in text.splitlines() if ln.strip())
        return joined if rc == 0 else f"`profiles` exited {rc}: {joined}"

    def clt() -> str:
        rc, text = r.run(["/usr/bin/xcode-select", "-p"], 3.0)
        if rc == 0 and text and Path(text.splitlines()[0]).is_dir():
            return f"present ({text.splitlines()[0]})"
        return f"not installed (xcode-select -p exited {rc})"

    def shell() -> str:
        bits = [os.environ.get("SHELL", "unknown")]
        term = os.environ.get("TERM_PROGRAM")
        if term:
            bits.append(f"terminal {term} {os.environ.get('TERM_PROGRAM_VERSION', '')}".rstrip())
        bundle = os.environ.get("__CFBundleIdentifier")  # noqa: SIM112 - macOS sets it in this case
        if bundle:
            bits.append(f"app {bundle}")
        return " · ".join(bits)

    def uv() -> str:
        exe = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        if not os.access(exe, os.X_OK):
            return "not found"
        rc, text = r.run([exe, "--version"], 3.0)
        return f"{text} ({exe})" if rc == 0 else f"{exe} --version exited {rc}: {text}"

    def pandoc() -> str:
        if r.config is not None and r.config.convert.pandoc_path is not None:
            exe = r.config.convert.pandoc_path
        else:
            import pypandoc  # noqa: PLC0415 - lazy, as in doctor

            exe = Path(pypandoc.__file__).parent / "files" / "pandoc"
        rc, text = r.run([str(exe), "--version"], 5.0)
        return text.splitlines()[0] if rc == 0 and text else f"{exe} exited {rc}: {text[:200]}"

    def app(name: str) -> Callable[[], str]:
        def version() -> str:
            for candidate in _APPS[name]:
                found = _plist_version(candidate)
                if found is not None:
                    return f"{found} ({candidate})"
            return "not installed (" + ", ".join(_APPS[name]) + ")"

        return version

    def proxy() -> str:
        env_set = any(os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"))
        sysp = r.system_proxy or net.SystemProxy()
        configured = r.config.network.proxy if r.config is not None else None
        resolved = net.resolve_proxy(configured, system=sysp)
        yn = {True: "yes", False: "no"}
        return (
            f"HTTPS_PROXY/ALL_PROXY set in this shell: {yn[env_set]} · system manual HTTPS proxy: "
            f"{yn[bool(sysp.https_proxy)]} · PAC: {yn[sysp.pac_enabled]} · WPAD: {yn[sysp.wpad_enabled]} · "
            f"[network] proxy: {'set' if configured else 'not set'} · agentsync resolves: "
            f"{resolved.describe()}"
        )

    def cloud() -> str:
        root = cloud_storage_root()
        if not root.exists():
            return f"{root} does not exist (no File Provider sync client signed in)"
        try:
            names = sorted(p.name for p in root.iterdir() if not p.name.startswith("."))
        except OSError as exc:
            return f"this terminal cannot list {root}: {type(exc).__name__}: {exc.strerror or exc}"
        tagged = [f"{n} ({_domain_text(root / n)})" for n in names]
        return f"listable, {len(names)} provider folder(s)" + (f": {', '.join(tagged)}" if names else "")

    def bin_on_path() -> str:
        dirs = os.environ.get("PATH", "").split(os.pathsep)
        mine = {str(Path.home() / ".local" / "bin"), str(Path.home().resolve() / ".local" / "bin")}
        return "yes" if mine & set(dirs) else "no"

    def on_path() -> str:
        found = agentsync_on_path()
        if not found:
            return "none"
        tagged = [f"{p}{' (this home folder)' if _is_home_agentsync(p) else ''}" for p in found]
        if not _is_home_agentsync(found[0]):
            r.facts.shadow = found[0]
            return (
                "; ".join(tagged) + f" · SHADOW: the first agentsync on PATH is {found[0]}, not this home "
                "folder's ~/.local/bin/agentsync, so `agentsync` typed alone runs another install"
            )
        return "; ".join(tagged)

    fact("macOS", sw_vers)
    fact("architecture", arch)
    fact("MDM enrollment (`profiles status -type enrollment`)", enrollment)
    fact("Xcode Command Line Tools", clt)
    fact("shell", shell)
    fact("uv", uv)
    fact("python (agentsync's)", lambda: f"{platform.python_version()} ({sys.executable})")
    fact("pandoc", pandoc)
    fact("OneDrive.app", app("OneDrive"))
    fact("Company Portal", app("Company Portal"))
    fact("proxy", proxy)
    fact("~/Library/CloudStorage", cloud)
    fact("~/.local/bin on PATH", bin_on_path)
    fact("agentsync on PATH", on_path)
    if r.listing_note:
        out.append(f"- redaction: {r.listing_note}")
    return out


def _install_runs(log: Path) -> list[tuple[str, dict[str, dict[str, str]], list[str]]]:
    """install.log grouped by run id, oldest first: (run id, {"start"/"end"/step name: fields}, lines)."""
    runs: dict[str, tuple[dict[str, dict[str, str]], list[str]]] = {}
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _INSTALL_LINE_RE.match(line)
        if m is None:
            continue
        fields, lines = runs.setdefault(m["run"], ({}, []))
        lines.append(line)
        rest = m["rest"]
        kv = dict(_KV_RE.findall(rest.split(" args=", 1)[0]))
        kv["at"] = m["at"]
        if rest.startswith("start "):
            kv["args"] = rest.split(" args=", 1)[1] if " args=" in rest else ""
            fields["start"] = kv
        elif rest.startswith("end "):
            fields["end"] = kv
        elif "step" in kv:
            fields[kv["step"]] = kv
    return [(run_id, fields, lines) for run_id, (fields, lines) in runs.items()]


def _step_table(steps: list[tuple[str, dict[str, str]]]) -> list[str]:
    rows = [("step", "seconds", "result")]
    for name, v in steps:
        result = v.get("result", "?")
        rc = v.get("rc", "0")
        if rc not in ("0", ""):
            result += f", rc {rc}"
        if v.get("note"):
            result += f" ({v['note']})"
        rows.append((name, v.get("seconds", "?"), result))
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    out = []
    for n, (step, seconds, result) in enumerate(rows):
        out.append(f"| {step.ljust(widths[0])} | {seconds.rjust(widths[1])} | {result.ljust(widths[2])} |")
        if n == 0:
            out.append(f"|{'-' * (widths[0] + 2)}|{'-' * (widths[1] + 1)}:|{'-' * (widths[2] + 2)}|")
    return out


def _installer(r: _Run) -> list[str]:
    return [*_installer_runs(r), "", *_installer_output(r)]


def instruction_counts(lines: Iterable[str]) -> Counter[str]:
    """Instruction-like lines per :data:`INSTRUCTION_KINDS` kind, each line counted once (by its first kind):
    ``NEXT:`` and ``next:`` at the start of a line, ``fix:`` anywhere (doctor's ``(fix: ...)``), ``run:`` at
    the start of a line or after ``(``."""
    counts: Counter[str] = Counter()
    for line in lines:
        kind = next((k for k, rx in _INSTRUCTION_RES if rx.search(line)), None)
        if kind is not None:
            counts[kind] += 1
    return counts


def install_out_path(log: Path | None = None) -> Path:
    """install.sh's output copy: :data:`INSTALL_OUT_NAME` next to install.log (:func:`default_setup_log`)."""
    return (log if log is not None else default_setup_log()).parent / INSTALL_OUT_NAME


def _instruction_text(counts: Counter[str], read: int, runs: int) -> str:
    others = [f"{counts[k]} {k}" for k in INSTRUCTION_KINDS[1:] if counts[k]]
    other_total = sum(counts[k] for k in INSTRUCTION_KINDS[1:])
    in_runs = f" in {runs} run(s)" if runs else ""
    return (
        f"{counts['NEXT:']} NEXT: line(s){in_runs} (exactly one per install.sh run is expected) and "
        f"{other_total} other instruction-like line(s)"
        + (f" ({', '.join(others)})" if others else "")
        + f" in the last {read} line(s) of install.out"
    )


def _installer_output(r: _Run) -> list[str]:
    path = install_out_path(r.install_log)
    try:
        lines = _tail(path)
    except FileNotFoundError:
        return [
            f"No installer output at {path} (install.sh keeps a copy of what it printed there; an older "
            "install.sh does not)."
        ]
    except OSError as exc:
        return [f"Cannot read the installer output at {path}: {type(exc).__name__}: {exc.strerror or exc}"]
    counts = instruction_counts(lines)
    runs = sum(1 for ln in lines if _OUT_RUN_RE.match(ln))
    r.facts.instructions = (counts, len(lines), runs)
    synced = [m for m in (_CONVERTED_LINE_RE.search(ln) for ln in lines) if m is not None]
    if synced:
        r.facts.first_sync = (int(synced[-1].group(1)), int(synced[-1].group(2)))
    shown = [_PID_SUFFIX_RE.sub(r"\1", ln) for ln in lines[-INSTALL_OUT_TAIL:]]
    return [
        f"Installer output: {_instruction_text(counts, len(lines), runs)}. An instruction-like line other "
        "than NEXT: is a hint the agent may act on before install.sh's own next step. agentsync's own "
        "WARNING and ERROR log lines, and a sync's alarm and error lines, show item paths and document names "
        "as <path>, as in Recent errors.",
        "",
        f"<details><summary>the last {len(shown)} line(s) of {path} (what the agent saw)</summary>",
        "",
        *_fence(_scrub_item_paths(ln) if _own_line(ln) else ln for ln in shown),
        "",
        "</details>",
    ]


_RUNS_LISTED = 20
_STEP_WORD_RE = re.compile(r"[a-z][a-z0-9-]{0,23}")


def _step_word(value: str | None) -> str:
    """An install.sh step name or result as the report prints it: its own word, or ``?``."""
    return value if value is not None and _STEP_WORD_RE.fullmatch(value) else "?"


def _reached(run: InstallRun) -> str:
    """The last step a run logged, with its result: how far it got."""
    if not run.steps:
        return "no step line"
    name, fields = run.steps[-1]
    rc = fields.get("rc", "")
    return f"{_step_word(name)} ({_step_word(fields.get('result'))}" + (
        f", rc {rc})" if rc.isdigit() and rc != "0" else ")"
    )


def _run_list(runs: Sequence[InstallRun]) -> list[str]:
    """Every run on one line (the tables above show the last :data:`INSTALL_RUNS_SHOWN` only), and the runs
    with no end line by their start time and the step they reached."""
    listed = runs[-_RUNS_LISTED:]
    rows = [
        (
            run.display_id,
            _iso(run.started),
            "list-folders" if run.list_only else "install",
            len(run.steps),
            _reached(run),
            f"exit {run.rc} after {run.seconds if run.seconds is not None else '?'}s"
            if run.rc is not None
            else "no end line",
        )
        for run in listed
    ]
    open_runs = [run for run in runs if run.rc is None]
    out = [
        "",
        f"Every run, oldest first (the last {len(listed)} of {len(runs)}; run ids without their process id):",
        "",
        *_table(
            ("run", "started (UTC)", "kind", "step lines", "last step logged", "end"), rows, _RUNS_LISTED
        ),
        "",
        f"Runs with no end line (stopped early, or still running): {len(open_runs)}",
    ]
    for run in open_runs[-_RUNS_LISTED:]:
        newest = " (the newest run)" if run is runs[-1] else ""
        out.append(
            f"- run {run.display_id}{newest}: started {_iso(run.started)}, reached {_reached(run)} after "
            f"{len(run.steps)} step line(s)"
        )
    return out


def _installer_runs(r: _Run) -> list[str]:
    log = r.install_log
    if not log.exists():
        return [
            f"No install log at {log}: scripts/install.sh has not run on this Mac, or it predates the setup "
            "log (pull the checkout and re-run it)."
        ]
    runs = r.install_runs
    if not runs:
        return [f"{log} has no install.sh lines."]
    totals = [run.seconds for run in runs if run.seconds is not None]
    r.facts.install_runs = len(runs)
    r.facts.install_seconds = sum(totals)
    r.facts.launchd_simulated = runs[-1].simulated
    shown = runs[-INSTALL_RUNS_SHOWN:]
    out = [
        f"{len(runs)} install.sh run(s) in {log}, {sum(totals)}s in total (runs with an end line); "
        f"the last {len(shown)} (run ids without their process id):"
    ]
    for run in shown:
        steps = [(k, v) for k, v in run.steps]
        status = (
            f"exit {run.rc} after {run.seconds if run.seconds is not None else '?'}s"
            if run.rc is not None
            else "no end line (interrupted, or still running)"
        )
        if run.simulated:
            status += " · launchd: simulated"
        out += ["", f"run {run.display_id}: {status}", ""]
        out += _step_table(steps) if steps else ["_(no step lines)_"]
        if run.report_after_end:
            out += [
                "",
                "The report step is logged after this run's end line (install.sh writes the report once the "
                "run is closed), so the run's seconds do not include it.",
            ]
        if run is runs[-1]:
            r.facts.last_install = status
    out += _run_list(runs)
    raw = [_PID_SUFFIX_RE.sub(r"\1", ln) for run in shown for ln in run.lines]
    out += [
        "",
        "<details><summary>raw install.log lines of these runs</summary>",
        "",
        *_fence(raw),
        "",
        "</details>",
    ]
    return out


def _configuration(r: _Run) -> list[str]:
    out = [f"- config: {r.config_path} ({'exists' if r.config_path.exists() else 'missing'})"]
    if r.config is None:
        out.append(f"- loads: no: {r.config_error}")
        return out
    c = r.config
    out.append("- loads: yes")
    kinds = Counter(s.kind.value for s in c.sources)
    states = Counter(s.state.value for s in c.sources)
    under_cloud = sum(1 for s in c.sources if s.path is not None and _cloud_parts(s.path) is not None)
    out.append(
        f"- sources: {len(c.sources)}"
        + (f" (by kind: {', '.join(f'{k} {n}' for k, n in sorted(kinds.items()))}" if kinds else "")
        + (f"; by state: {', '.join(f'{k} {n}' for k, n in sorted(states.items()))})" if states else "")
        + f"; {under_cloud} under ~/Library/CloudStorage"
    )
    docs = expand(c.docs_repo)
    repo = "a git repo" if (docs / ".git").exists() else "not a git repo yet"
    out.append(f"- docs repo: {docs} ({'exists, ' + repo if docs.is_dir() else 'missing'})")
    out.append(f"- [graph] client_id: {'set' if c.graph.client_id else 'not set'} (the id is never shown)")
    tenant = c.graph.tenant not in ("", "organizations", "common")
    out.append(f"- [graph] tenant: {'set' if tenant else 'not set'}")
    out.append(f"- [network] proxy: {'set' if c.network.proxy else 'not set'}")
    out.append(
        f"- state dir: {expand(c.state_dir)} ({'exists' if expand(c.state_dir).is_dir() else 'missing'})"
    )
    if r.agent_named is not None:
        listed, configured = r.agent_named
        out.append(
            "- folders named only with a coding agent's product words: "
            + ("not listed" if listed is None else f"{listed} listed under ~/Library/CloudStorage")
            + f" (a listed one keeps its name, so the Agent: line stays readable), {configured} of the "
            "configured source folders (a configured one is a placeholder like any other)"
        )
    return out


def _doctor(r: _Run) -> list[str]:
    if r.config is None:
        return [f"Not run: the config does not load ({r.config_error})."]
    if r.hooks.doctor is None:
        return ["Not run: no doctor hook."]
    config = r.config
    doctor_fn = r.hooks.doctor
    lines = r.call(lambda: doctor_fn(config), timeout=max(1.0, r.remaining() - 2.0 - _LOOP_FLOOR_S))
    tags: Counter[str] = Counter()
    ok_names: list[str] = []
    shown: list[str] = []
    current_ok = False
    installed = agents_installed(r.install_runs)
    annotated = 0
    for raw in lines:
        line = raw
        m = _DOCTOR_TAG_RE.match(raw)
        if m is not None:
            tags[m.group(1)] += 1
            current_ok = m.group(1) == "ok"
            if current_ok:
                ok_names.append(m.group(2))
                continue
            if m.group(1) in ("warn", "FAIL"):
                detail = raw.split(" — ", 1)[1] if " — " in raw else raw
                r.facts.doctor_problems.append((m.group(1), m.group(2), detail))
                why = (
                    expected_warn(m.group(2), detail, agents_installed=installed)
                    if m.group(1) == "warn"
                    else None
                )
                if why is not None:  # expected: its fix is nothing for this setup to do
                    line = f"{_without_fix(raw)} (expected: {why})"
                    annotated += 1
        elif current_ok:
            continue  # a continuation of an ok line
        shown.append(line)
    r.facts.doctor = tags
    summary = f"{sum(tags.values())} check(s): " + ", ".join(
        f"{tags.get(t, 0)} {t}" for t in ("ok", "info", "warn", "FAIL")
    )
    note = (
        f" {annotated} expected warn(s) end in (expected: <why>) instead of their fix: nothing for this "
        "setup to do."
        if annotated
        else ""
    )
    return [
        summary + " (run without the Graph network probe and the TCC canary: `agentsync status` runs them). "
        f"Only the lines that are not ok:{note}",
        "",
        *_fence(shown),
        "",
        f"{len(ok_names)} ok: {', '.join(ok_names) or 'none'}",
    ]


def _status(r: _Run) -> list[str]:
    if r.config is None:
        return [f"Not run: the config does not load ({r.config_error})."]
    if r.hooks.status is None:
        return ["Not run: no status hook."]
    config = r.config
    status_fn = r.hooks.status
    lines = r.call(lambda: status_fn(config), timeout=4.0)
    baselines = [m.group(1) for m in (_BASELINE_RE.match(ln) for ln in lines) if m is not None]
    if baselines:
        r.facts.baseline = (baselines.count("complete"), len(baselines))
    loop_line = next((ln for ln in lines if ln.startswith("loop: ")), None)
    if loop_line is not None:
        baseline = _LOOP_BASELINE_RE.search(loop_line)
        topics = _LOOP_TOPICS_RE.search(loop_line)
        synced = any(ln.startswith("last runs:") and ln != "last runs: none" for ln in lines)
        r.facts.loop = (
            baseline.group(1) if baseline else None,
            int(topics.group(1)) if topics else None,
            synced,
        )
    for ln in lines:
        if ln.startswith("last runs:"):
            for sha in _LAST_RUNS_RE.findall(ln):
                r.red.add_commit(sha)
    return _fence(lines)


# ---- the next round's evidence (CONTRACTS.md 16.28): counts, states and fixed words, never a name -------

EVIDENCE_BUDGET_S = 3.0
"""The seconds the evidence parts (the ``### `` blocks under Status) may take in all, inside the report's own
budget. A part with no time left prints :data:`NOT_MEASURED`. The OCR probe is not counted
(:data:`_PROBE_S`)."""
NOT_MEASURED = "not measured (time limit)"
_PROBE_S = 1.5
"""The seconds the OCR part waits for ``convert.ocr.probe``, beside :data:`EVIDENCE_BUDGET_S`. The probe is
the one thing the evidence starts a program for (a built helper's ``--version``, which a cycle gives 5 s), so
a helper that hangs costs this much and nothing of what the manifest parts have."""
EVIDENCE_TITLES = (
    "OCR",
    "Quarantine by reason",
    "Purge queue",
    "Overlapping sources",
    "Empty cloud folders",
    "Repeat conversions",
)
"""The ``### `` headings of the evidence parts, in order, at the end of the Status section (sub-headings, so
the ``## `` headings stay :data:`SECTION_TITLES`)."""
QUARANTINE_CLASSES = (
    "no converter",
    "no text layer (OCR not run)",
    "no text layer (OCR found no text)",
    "no text layer (over the OCR page limit)",
    "no text in image",
    "image not readable",
    "encrypted",
    "credential",
    "label policy",
    "too large",
    "duplicate",
    "conversion failed",
    "path too long",
    "empty",
    "not the type its name says",
    "download refused by the OS",
    "no reason recorded",
    "other",
)
"""Every value of :func:`quarantine_class`: the fixed words a stub's reason is reported as."""

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif")
"""``ImageConverter.extensions`` (tests/test_setup_report.py holds the two equal): a file OCR alone reads."""
_OCR_DOCUMENTS = {".pdf": "PDF", ".pptx": "deck", ".docx": "Word", ".odt": "OpenDocument"}
"""The suffixes of the documents a converter reads pictures or page images in when it has an engine."""
_OCR_MARK = "+ocr-"  # in a converter version: an engine's identity follows (``OcrEngine.identity``)
_FIELD_MARKS = ("+ocr-off", "+helper-", "+macos-")  # versions of the field build of OCR (CONTRACTS 16.27)
_VERSION_WORDS = ("without an OCR identity", "from the field build of OCR", "with an OCR identity")
_IDENTITY_RE = re.compile(r"\+(ocr-[A-Za-z0-9._-]{1,80})")
_WORD_RE = re.compile(r"[a-z][a-z_-]{0,23}")
_DAY_RE = re.compile(r"\d{4}-\d\d-\d\d")
_STEP_TICK = 2000  # SQLite VM instructions between two looks at the clock
_RUNS_READ = 200
_RUNS_SHOWN = 5
_ROWS_SHOWN = 40
_PURGES_READ = 5000
_PURGE_LOOKUPS = 300
_EMPTY_DIRS_CHECKED = 50
_FOLDERS_S = 2.0  # one source's empty folders: the lstat calls, then the exclude rule, each at most this long
_PAIRS_SHOWN = 20
_REREAD_META = "reread:"  # cycle._REREAD_META
_EMPTY_DIRS_META = "empty_cloud_dirs:"  # cycle._EMPTY_DIRS_META
_PRESENT_SQL = "('live', 'dataless')"


def quarantine_class(reason: str | None) -> str:
    """The fixed class (one of :data:`QUARANTINE_CLASSES`) of a manifest row's ``state_reason``. A reason is
    free text in places (a duplicate names a mirror path, a failed conversion carries an exception's words),
    so the report prints the class and never the reason."""
    text = " ".join((reason or "").split()).casefold()
    if not text:
        return "no reason recorded"
    starts = (
        ("no converter for ", "no converter"),
        ("refused: ", "label policy"),
        ("contains a credential", "credential"),
        ("duplicate-of ", "duplicate"),
        ("conversion failed", "conversion failed"),
        ("hydration-refused", "download refused by the OS"),
        ("path too long", "path too long"),
    )
    for prefix, name in starts:
        if text.startswith(prefix):
            return name
    if text.startswith("no text layer"):
        if "over the ocr page limit" in text:
            return "no text layer (over the OCR page limit)"
        return (
            "no text layer (OCR found no text)" if "found no text" in text else "no text layer (OCR not run)"
        )
    if "too large" in text or "exceeds" in text:
        return "too large"
    if text.startswith(("no text found in the image", "image too small")):
        return "no text in image"
    if text.startswith(("not an image", "image not readable")):
        return "image not readable"
    if "encrypted" in text or "password-protected" in text or "irm-protected" in text:
        return "encrypted"
    if text.startswith(("empty-output", "empty pdf")):
        return "empty"
    if text.startswith(("not-ooxml", "not a zip")):
        return "not the type its name says"
    return "other"


def _version_class(version: str | None) -> int:
    """An index into :data:`_VERSION_WORDS` for a converter version: 0 without an engine's identity, 1 for a
    version of the field build of OCR (``+ocr-off`` holds ``+ocr-`` and is no identity), 2 with one."""
    text = version or ""
    if any(mark in text for mark in _FIELD_MARKS):
        return 1
    return 2 if _OCR_MARK in text else 0


def _suffix_sql(column: str, suffixes: Iterable[str]) -> str:
    """A SQL expression: the lower-case suffix of ``column`` when it is one of ``suffixes``, else NULL."""
    by_length: dict[int, list[str]] = {}
    for suffix in suffixes:
        by_length.setdefault(len(suffix), []).append(suffix)
    whens = " ".join(
        f"WHEN lower(substr({column}, -{n})) IN ({', '.join(repr(s) for s in sorted(group))}) "
        f"THEN lower(substr({column}, -{n}))"
        for n, group in sorted(by_length.items())
    )
    return f"CASE {whens} END"


class _OutOfTimeError(Exception):
    """The evidence's time is used up."""


class _NoManifestError(Exception):
    """No sync has run: there is no manifest to read."""


class _Mirror:
    """The manifest, read only, for the evidence parts. One connection opened ``mode=ro``: nothing is
    created, migrated or written. Every statement is stopped once the evidence's time is used up, and
    ``steps`` counts the looks at the clock (one per :data:`_STEP_TICK` VM instructions): the work done,
    whatever the Mac's speed. ``latest``: the time the deadline is never moved past (the report's own
    deadline less its reserve)."""

    def __init__(self, db: Path, budget_s: float, *, latest: float | None = None) -> None:
        self.db = db
        self.deadline = time.monotonic() + budget_s
        self.latest = latest
        self.steps = 0
        self.statements: list[str] = []
        self.kept: dict[str, object] = {}
        self._conn: sqlite3.Connection | None = None

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def not_counted(self, seconds: float) -> None:
        """Move the deadline on by ``seconds`` that went to something with a time of its own (the OCR
        probe): the manifest parts keep all of theirs, inside ``latest``."""
        moved = self.deadline + max(seconds, 0.0)
        self.deadline = moved if self.latest is None else min(moved, max(self.latest, self.deadline))

    def _tick(self) -> int:
        self.steps += 1
        return 1 if time.monotonic() >= self.deadline else 0

    def _open(self) -> sqlite3.Connection:
        if self._conn is None:
            if not self.db.is_file():
                raise _NoManifestError
            conn = sqlite3.connect(f"file:{quote(str(self.db))}?mode=ro", uri=True, timeout=0.5)
            conn.set_progress_handler(self._tick, _STEP_TICK)
            conn.create_function("reason_class", 1, quarantine_class, deterministic=True)
            conn.create_function("version_class", 1, _version_class, deterministic=True)
            self._conn = conn
        return self._conn

    def rows(self, sql: str, params: Sequence[object] = ()) -> list[tuple[Any, ...]]:
        """Every row of one statement; _OutOfTimeError once the time is used up, before or while it runs."""
        if self.left() <= 0:
            raise _OutOfTimeError
        conn = self._open()
        self.statements.append(sql)
        try:
            return conn.execute(sql, tuple(params)).fetchall()
        except sqlite3.OperationalError:
            if self.left() <= 0:
                raise _OutOfTimeError from None
            raise

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class _Labels:
    """How an evidence part names a source: never by an id in clear, and never by a part of one. A
    configured id is shown as the report's Redactor shows it only when that is one whole placeholder
    (``<source-N>``, ``<folder-N>``); one the Redactor leaves alone is kept only when it is one of
    agentsync's own words (``inbox``, ``mail``). Anything else, an id the Redactor replaced only a part of
    included, is ``(source N)`` by its place in sources.toml. An id the config does not have (a retired
    source's rows, a hand-edited queue) is registered with no one: it is ``(not in the config, N)``."""

    def __init__(self, config: Config, red: Redactor) -> None:
        self.red = red
        self.place = {src.id: n for n, src in enumerate(config.sources, 1)}
        self._shown: dict[str, str] = {}

    def of(self, source_id: object) -> str:
        if source_id is None or source_id == "":
            return "(any source)"
        text = str(source_id)
        if text not in self._shown:
            if text not in self.place:
                others = sum(1 for known in self._shown if known not in self.place)
                self._shown[text] = f"(not in the config, {others + 1})"
            else:
                shown = self.red.redact(text)
                whole = _PLACEHOLDER_RE.fullmatch(shown) is not None
                own_word = shown == text and text in _GENERIC_IDS
                self._shown[text] = shown if whole or own_word else f"(source {self.place[text]})"
        return self._shown[text]


def _lines(fn: Callable[[], list[str]]) -> list[str]:
    """``fn()``, or the one line that says why it was not measured. Never an exception's message: it can
    hold a path."""
    try:
        return fn()
    except _OutOfTimeError:
        return [f"- {NOT_MEASURED}"]
    except _NoManifestError:
        return ["- no manifest yet (no sync has run)"]
    except Exception as exc:
        return [f"- not measured ({type(exc).__name__})"]


def _table(header: Sequence[str], rows: Sequence[Sequence[object]], limit: int = _ROWS_SHOWN) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows[:limit]]
    if len(rows) > limit:
        out.append(f"(+{len(rows) - limit} more row(s) not shown)")
    return out


def _megabytes(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


def _word(value: object) -> str:
    """``value`` when it is one of agentsync's own lower-case words (a mode, a status, a state), else
    ``?``."""
    text = str(value) if value is not None else ""
    return text if _WORD_RE.fullmatch(text) else "?"


@dataclasses.dataclass(frozen=True, slots=True)
class _RunRow:
    """One ``runs`` row: when it ran and its counts (``cycle._Cycle._run_tally`` adds the lower-case keys)."""

    run_id: int
    mode: str
    status: str
    started: datetime | None
    seconds: float | None
    counts: dict[str, int]

    def n(self, key: str) -> int:
        return self.counts.get(key, 0)


def _run_rows(m: _Mirror) -> list[_RunRow]:
    """The last :data:`_RUNS_READ` runs, newest first, read once per report."""
    kept = m.kept.get("runs")
    if isinstance(kept, list):
        return cast(list[_RunRow], kept)
    out: list[_RunRow] = []
    for run_id, mode, status, started_at, finished_at, raw in m.rows(
        "SELECT run_id, mode, status, started_at, finished_at, counts_json FROM runs "
        "ORDER BY run_id DESC LIMIT ?",
        (_RUNS_READ,),
    ):
        try:
            doc = json.loads(raw or "{}")
        except ValueError:
            doc = {}
        counts = {
            str(k): v
            for k, v in (doc.items() if isinstance(doc, dict) else ())
            if isinstance(v, int) and not isinstance(v, bool)
        }
        started = parse_time(str(started_at or ""))
        finished = parse_time(str(finished_at or ""))
        took = (finished - started).total_seconds() if started and finished else None
        out.append(_RunRow(int(run_id), _word(mode), _word(status), started, took, counts))
    m.kept["runs"] = out
    return out


def _stub_rows(m: _Mirror) -> list[tuple[str, str, str, int, int, int | None, int | None]]:
    """(source id, state, reason class, online-only 0/1, files, oldest and newest run that built the stub)
    for every file whose row carries a reason: the quarantined and refused ones, and a present file whose
    download the OS refused. Read once per report; grouped by the class, so no reason leaves SQLite."""
    kept = m.kept.get("stubs")
    if isinstance(kept, list):
        return cast(list[tuple[str, str, str, int, int, int | None, int | None]], kept)
    rows = m.rows(
        "SELECT source_id, state, reason_class(state_reason), dataless, COUNT(*), MIN(built), MAX(built) "
        "FROM (SELECT i.source_id AS source_id, i.state AS state, i.state_reason AS state_reason, "
        "i.dataless AS dataless, (SELECT MAX(o.built_run) FROM outputs o WHERE o.source_id = i.source_id "
        "AND o.stable_id = i.stable_id) AS built FROM items i WHERE i.is_dir = 0 AND "
        "(i.state IN ('quarantined', 'refused') OR (i.state_reason IS NOT NULL AND i.state != 'tombstone'))) "
        "GROUP BY 1, 2, 3, 4"
    )
    out = [(str(s), _word(st), str(cls), int(dl), int(n), lo, hi) for s, st, cls, dl, n, lo, hi in rows]
    m.kept["stubs"] = out
    return out


def _run_days(m: _Mirror, run_ids: Iterable[object]) -> dict[int, str]:
    """run id -> the UTC day it started, for the ids given (a few)."""
    wanted = sorted({int(x) for x in run_ids if isinstance(x, int)})[:400]
    if not wanted:
        return {}
    marks = ", ".join("?" for _ in wanted)
    found = m.rows(f"SELECT run_id, substr(started_at, 1, 10) FROM runs WHERE run_id IN ({marks})", wanted)
    return {int(rid): str(day) for rid, day in found if _DAY_RE.fullmatch(str(day))}


def _span(days: dict[int, str], lo: object, hi: object) -> str:
    first = days.get(lo) if isinstance(lo, int) else None
    last = days.get(hi) if isinstance(hi, int) else None
    if first is None and last is None:
        return "-"
    if first == last or last is None or first is None:
        return str(first or last)
    return f"{first} to {last}"


# ---- OCR ------------------------------------------------------------------------------------------------


def _ocr_helper(r: _Run, m: _Mirror) -> list[str]:
    """The probe's state and detail (``convert.ocr.probe``: it looks, compiles nothing and stamps nothing;
    the one program it may start is a built helper's ``--version``), and whether a label rule is on.

    The probe has :data:`_PROBE_S` of its own, and the time it takes is not the manifest parts'
    (``_Mirror.not_counted``): a helper that hangs is the Mac the OCR evidence is wanted from, and it used
    to leave every part after it unmeasured. A probe that does not answer is a line of fixed words, not
    :data:`NOT_MEASURED`: writing the report again would say the same."""
    config = cast(Config, r.config)
    words = {"ready": "ready", "off": "off", "not-built": "not built", "failed": "failed"}
    room = r.remaining() - _RESERVE_S
    started = time.monotonic()
    try:
        from agentsync.convert import ocr  # noqa: PLC0415 - lazy: the report must import even if it is broken

        state, detail = r.call(lambda: ocr.probe(config.convert, config.cache_dir), timeout=_PROBE_S)
        out = [f"- helper: {words.get(state, 'unknown state')} ({_shorten(str(detail), 200)})"]
    except TimeoutError:
        if room < _PROBE_S:  # the report's own time ended the wait, not the helper
            out = [f"- helper: {NOT_MEASURED}"]
        else:
            out = [
                f"- helper: did not answer within {_PROBE_S:.1f}s (a built helper's `--version`; Doctor's "
                "ocr line gives the same probe more time)"
            ]
    except Exception as exc:
        out = [f"- helper: not measured ({type(exc).__name__})"]
    finally:
        m.not_counted(time.monotonic() - started)
    try:
        from agentsync import policy  # noqa: PLC0415 - lazy, as above

        active = policy.load_policy(config).labels_active
        out.append(f"- label rule in [policy]: {'on (no image is read under one)' if active else 'off'}")
    except Exception as exc:
        out.append(f"- label rule in [policy]: not read ({type(exc).__name__})")
    return out


_IMAGE_OUTCOMES = (
    "page",
    "no-text stub",
    "not-readable stub",
    "not-on-this-Mac stub",
    "no-converter stub on this Mac",
    "deferred on this Mac",
    "deferred online-only",
    "not converted yet",
    "failed",
    "other stub",
)


def _image_outcome(state: str, dataless: int, verdict: str, cls: str, page: int) -> str:
    """One of :data:`_IMAGE_OUTCOMES` for an image's row."""
    if state in ("live", "dataless"):
        if page:
            return "page"
        if verdict == "deferred":
            return "deferred online-only" if dataless else "deferred on this Mac"
        return "not converted yet"
    stubs = {
        "no converter": "not-on-this-Mac stub" if dataless else "no-converter stub on this Mac",
        "no text in image": "no-text stub",
        "image not readable": "not-readable stub",
        "too large": "not-readable stub",
        "conversion failed": "failed",
    }
    return stubs.get(cls, "other stub")


def _ocr_files(r: _Run, m: _Mirror, labels: _Labels) -> list[str]:
    """What OCR has done to the files the manifest holds, as counts: images by outcome, documents by
    whether their page was made with an engine, scanned PDFs by stub, and the re-read record per source."""
    out: list[str] = []
    waiting: Counter[str] = Counter()  # source id -> files on this Mac a re-read would look at
    outcomes: Counter[str] = Counter()
    online = [0, 0]  # online-only images: files, bytes
    image = _suffix_sql("i.name", _IMAGE_SUFFIXES)
    for sid, state, dataless, verdict, cls, page, files, size in m.rows(
        "SELECT i.source_id, i.state, i.dataless, COALESCE(i.last_verdict, ''), "
        "reason_class(i.state_reason), "
        "EXISTS (SELECT 1 FROM outputs o WHERE o.source_id = i.source_id AND o.stable_id = i.stable_id "
        "AND o.status = 'ok'), COUNT(*), COALESCE(SUM(i.size), 0) FROM items i "
        f"WHERE i.is_dir = 0 AND i.state != 'tombstone' AND {image} IS NOT NULL GROUP BY 1, 2, 3, 4, 5, 6"
    ):
        outcome = _image_outcome(str(state), int(dataless), str(verdict), str(cls), int(page))
        outcomes[outcome] += int(files)
        if dataless:
            online[0] += int(files)
            online[1] += int(size)
        if outcome == "no-converter stub on this Mac":
            waiting[str(sid)] += int(files)
    shown = " · ".join(f"{name} {outcomes[name]}" for name in _IMAGE_OUTCOMES if outcomes[name])
    out.append(f"- images: {sum(outcomes.values())} file(s)" + (f": {shown}" if shown else ""))
    out.append(
        f"- images that are online-only: {online[0]} file(s), {_megabytes(online[1])} (none is downloaded "
        "for OCR; a deferred image on this Mac waits for a cycle's OCR time or its file limit)"
    )
    documents: Counter[tuple[str, int, int]] = Counter()  # (suffix, version class, online-only) -> files
    ext = _suffix_sql("i.name", _OCR_DOCUMENTS)
    for sid, suffix, cls, dataless, files in m.rows(
        "SELECT source_id, ext, cls, dataless, COUNT(*) FROM (SELECT i.source_id AS source_id, "
        f"{ext} AS ext, i.dataless AS dataless, "
        "MIN(version_class(COALESCE(c.converter_version, o.converter_version))) AS cls FROM items i "
        "JOIN outputs o ON o.source_id = i.source_id AND o.stable_id = i.stable_id AND o.status = 'ok' "
        "LEFT JOIN cache c ON c.action_key = o.action_key "
        f"WHERE i.is_dir = 0 AND i.state IN {_PRESENT_SQL} AND {ext} IS NOT NULL "
        "GROUP BY i.source_id, i.stable_id) GROUP BY 1, 2, 3, 4"
    ):
        documents[(str(suffix), int(cls), int(bool(dataless)))] += int(files)
        if int(cls) != 2 and not dataless:
            waiting[str(sid)] += int(files)
    for suffix, name in _OCR_DOCUMENTS.items():
        cells = []
        for cls in (2, 0, 1):
            here, away = documents[(suffix, cls, 0)], documents[(suffix, cls, 1)]
            if here or away or cls != 1:
                cells.append(
                    f"{here + away} {_VERSION_WORDS[cls]}" + (f" ({away} online-only)" if away else "")
                )
        out.append(f"- {name} ({suffix}) files with a page: " + ", ".join(cells))
    scans: Counter[str] = Counter()
    for sid, state, cls, dataless, files, _lo, _hi in _stub_rows(m):
        if state == "quarantined" and cls.startswith("no text layer"):
            scans[cls] += files
            if cls == "no text layer (OCR not run)" and not dataless:
                waiting[sid] += files
    out.append(
        "- scanned PDFs with a stub: "
        + ", ".join(f"{scans[c]} {c}" for c in QUARANTINE_CLASSES if c.startswith("no text layer"))
    )
    identities: Counter[str] = Counter()
    for version, pages in m.rows(
        "SELECT COALESCE(c.converter_version, o.converter_version, ''), COUNT(*) FROM outputs o "
        "LEFT JOIN cache c ON c.action_key = o.action_key WHERE o.status = 'ok' GROUP BY 1"
    ):
        found = _IDENTITY_RE.search(str(version))
        if found is not None and _version_class(str(version)) == 2:
            identities[found.group(1)] += int(pages)
    out.append(
        "- engine identities on pages: "
        + (", ".join(f"{name} ({n} page(s))" for name, n in sorted(identities.items())[:6]) or "none")
    )
    return [*out, *_reread_lines(r, m, labels, waiting)]


def _reread_lines(r: _Run, m: _Mirror, labels: _Labels, waiting: Counter[str]) -> list[str]:
    """The re-read record of each local or inbox source (manifest meta ``reread:<source id>``, CONTRACTS
    16.27) as counts: whether its scan is finished, the files given up, those that failed once, and the
    report's own count of files on this Mac whose page a re-read would look at."""
    config = cast(Config, r.config)
    stored = {
        str(key)[len(_REREAD_META) :]: str(value)
        for key, value in m.rows("SELECT key, value FROM meta WHERE key LIKE ?", (_REREAD_META + "%",))
    }
    rows: list[tuple[object, ...]] = []
    for src in config.sources:
        if src.path is None or (src.id not in stored and not waiting[src.id]):
            continue
        try:
            doc = json.loads(stored.get(src.id) or "[]")
        except ValueError:
            doc = []
        records = [x for x in doc if isinstance(x, dict)] if isinstance(doc, list) else []
        first = records[0] if records else {}
        tried, failed = first.get("tried"), first.get("failed")
        older = sum(len(x.get("tried") or ()) for x in records[1:] if isinstance(x.get("tried"), list))
        rows.append(
            (
                labels.of(src.id),
                ("yes" if first.get("done") is True else "no") if records else "no record",
                waiting[src.id],
                len(failed) if isinstance(failed, dict) else 0,
                (len(tried) if isinstance(tried, list) else 0),
                older,
                "yes" if isinstance(first.get("reading"), str) else "no",
            )
        )
    if not rows:
        return ["- re-read: no source has a record, and no file on this Mac has a page from before OCR"]
    header = (
        "source",
        "scan finished",
        "on this Mac, from before OCR",
        "failed once",
        "given up",
        "given up (other engine or version)",
        "reading when a cycle died",
    )
    return [
        "- re-read, per source. The third column is the report's own count of files a re-read would "
        "look at: images with the no-converter stub, scanned PDFs whose stub says OCR was not run and "
        "documents whose page has no OCR identity. It includes the files given up (two failed tries) and "
        "those that failed once and are tried once more:",
        "",
        *_table(header, rows),
    ]


def _took(seconds: float | None) -> str:
    return "?" if seconds is None else _secs(max(seconds, 0.0))


def _ocr_time(m: _Mirror) -> list[str]:
    """OCR's time and what it left, from the run records (``runs.counts_json``; CONTRACTS 16.28)."""
    runs = _run_rows(m)
    if not runs:
        return ["- OCR time: no run is recorded"]
    engine = [x for x in runs if "ocr_ms" in x.counts]
    recorded = [x for x in runs if "converted" in x.counts]

    def total(key: str) -> int:
        return sum(x.n(key) for x in runs)

    out = [
        f"- OCR time: of the last {len(runs)} run(s), {len(recorded)} recorded these counts (a run of an "
        f"earlier build did not) and {len(engine)} had an engine; {total('ocr_over')} used up the cycle's "
        f"OCR time; {total('ocr_down')} ended with the helper not working; the helper ran "
        f"{total('ocr_ms') / 1000:.0f}s in all",
        f"- in those runs: {total('ocr_deferred')} file(s) waited for a later cycle's OCR · "
        f"{total('ocr_without_budget')} converted without OCR because the cycle's OCR time was used up · "
        f"{total('ocr_without_down')} converted without OCR because the helper had stopped working · "
        f"{total('ocr_failed')} the engine failed on (a helper failure, or the file's own time limit) · "
        f"{total('ocr_page_cap')} conversion(s) say pages past the OCR page limit were not read · "
        f"{total('ocr_picture_cap')} say pictures past the picture limit were not read · "
        f"{total('reread')} read again ({total('reread_kept')} kept the page they had)",
    ]
    shown = (engine or runs)[:_RUNS_SHOWN]
    rows = []
    for x in shown:
        has = "ocr_ms" in x.counts
        rows.append(
            (
                x.run_id,
                x.mode,
                x.status,
                _iso(x.started),
                _took(x.seconds),
                f"{x.n('ocr_ms') / 1000:.1f}s of {x.n('ocr_budget_s')}s" if has else "-",
                ("yes" if x.n("ocr_over") else "no") if has else "-",
                ("yes" if x.n("ocr_down") else "no") if has else "-",
                x.n("ocr_deferred"),
                f"{x.n('ocr_without_budget')} + {x.n('ocr_without_down')}",
                x.n("ocr_failed"),
                f"{x.n('ocr_page_cap')} + {x.n('ocr_picture_cap')}",
                f"{x.n('reread')} ({x.n('reread_kept')})",
            )
        )
    header = (
        "run",
        "mode",
        "status",
        "started (UTC)",
        "took",
        "OCR time used",
        "used up",
        "helper stopped",
        "waited",
        "without OCR (time + helper)",
        "engine failed",
        "past the page + picture limit",
        "read again (page kept)",
    )
    title = "the last runs that had an engine" if engine else "the last runs (none had an engine)"
    return [*out, "", f"{title}, newest first:", "", *_table(header, rows)]


def _ocr_part(r: _Run, m: _Mirror, labels: _Labels) -> list[str]:
    return [
        *_lines(lambda: _ocr_helper(r, m)),
        *_lines(lambda: _ocr_files(r, m, labels)),
        *_lines(lambda: _ocr_time(m)),
    ]


# ---- quarantine, purge queue ----------------------------------------------------------------------------


def _quarantine_part(r: _Run, m: _Mirror, labels: _Labels) -> list[str]:
    """Every file whose row carries a reason, by source, state and reason class. Counts and classes only."""
    stubs = _stub_rows(m)
    if not stubs:
        return ["- no file is quarantined or refused"]
    days = _run_days(m, [x for row in stubs for x in row[5:7]])
    grouped: dict[tuple[str, str, str], list[int | None]] = {}
    for sid, state, cls, dataless, files, lo, hi in stubs:
        cell = grouped.setdefault((labels.of(sid), state, cls), [0, 0, None, None])
        cell[0] = (cell[0] or 0) + files
        cell[1] = (cell[1] or 0) + (files if dataless else 0)
        cell[2] = lo if cell[2] is None else (cell[2] if lo is None else min(cell[2], lo))
        cell[3] = hi if cell[3] is None else (cell[3] if hi is None else max(cell[3], hi))
    order = {name: n for n, name in enumerate(QUARANTINE_CLASSES)}
    rows = [
        (source, state, cls, cell[0], cell[1], _span(days, cell[2], cell[3]))
        for (source, state, cls), cell in sorted(
            grouped.items(), key=lambda kv: (kv[0][0], kv[0][1], order.get(kv[0][2], 99))
        )
    ]
    by_state: Counter[str] = Counter()
    for _sid, state, _cls, _dataless, files, _lo, _hi in stubs:
        by_state[state] += files
    total = ", ".join(f"{n} {state}" for state, n in sorted(by_state.items()))
    header = ("source", "state", "reason class", "files", "online-only", "stub built (UTC day)")
    return [
        f"- files whose row carries a reason: {total} (a reason is shown as its class, never as its text; "
        "a live or dataless row here is a download the OS refused)",
        "",
        *_table(header, rows),
    ]


_PURGE_FATES = (
    "same bytes live",
    "same path live",
    "still listed",
    "no live twin",
    "no row",
    "not looked up",
)


def _purge_fate(m: _Mirror, source_id: str, stable_id: str) -> str:
    """What the manifest says of one queued stable id, as one of :data:`_PURGE_FATES`: the file is listed
    again, a live file elsewhere has its bytes (a renamed or re-exported copy), a live file with another id
    sits at its path (a safe-save or re-export), or nothing live takes its place."""
    found = m.rows(
        "SELECT state, canonical_sha256, rel_path FROM items WHERE source_id = ? AND stable_id = ?",
        (source_id, stable_id),
    )
    if not found:
        return "no row"
    state, canonical, rel_path = found[0]
    if state != "tombstone":
        return "still listed"
    if canonical and m.rows(
        f"SELECT 1 FROM items WHERE canonical_sha256 = ? AND state IN {_PRESENT_SQL} "
        "AND NOT (source_id = ? AND stable_id = ?) LIMIT 1",
        (canonical, source_id, stable_id),
    ):
        return "same bytes live"
    if m.rows(
        "SELECT 1 FROM items WHERE source_id = ? AND rel_path = ? AND stable_id != ? "
        f"AND state IN {_PRESENT_SQL} LIMIT 1",
        (source_id, rel_path, stable_id),
    ):
        return "same path live"
    return "no live twin"


def _purge_part(r: _Run, m: _Mirror, labels: _Labels) -> list[str]:
    """The queued purges by source, reason, selector kind and the UTC day they were queued, and for a queued
    stable id what the manifest holds in its place. Counts only: no selector text is printed."""
    from agentsync import governance  # noqa: PLC0415 - lazy: the report must import even if it is broken

    config = cast(Config, r.config)
    queue = governance.pending_purges(config.state_paths.root)
    if not queue:
        return ["- no purge is queued"]
    reasons = {reason.value for reason in governance.PurgeReason}
    grouped: dict[tuple[str, str, str, str], Counter[str]] = {}
    lookups = 0
    for entry in queue[:_PURGES_READ]:
        selector = entry.selector
        day = entry.enqueued_at[:10] if _DAY_RE.fullmatch(entry.enqueued_at[:10]) else "unknown day"
        reason = entry.reason.value if entry.reason.value in reasons else "other"
        fate = "not looked up"
        if selector.stable_id and selector.source_id and lookups < _PURGE_LOOKUPS:
            lookups += 1
            try:
                fate = _purge_fate(m, selector.source_id, selector.stable_id)
            except (_OutOfTimeError, _NoManifestError):
                lookups = _PURGE_LOOKUPS
        key = (labels.of(selector.source_id), reason, selector.kind(), day)
        grouped.setdefault(key, Counter())[fate] += 1
    rows = [
        (*key, sum(fates.values()), *(fates[fate] for fate in _PURGE_FATES))
        for key, fates in sorted(grouped.items())
    ]
    header = ("source", "reason", "selector", "queued (UTC day)", "purges", *_PURGE_FATES)
    more = f" (the first {_PURGES_READ} are counted)" if len(queue) > _PURGES_READ else ""
    return [
        f"- {len(queue)} purge(s) queued{more}. For a queued stable id the last six columns say what the "
        "manifest holds now: a live file elsewhere with the same bytes (a renamed or re-exported copy), a "
        "live file with another id at the same path, the file itself listed again, nothing live in its "
        "place, or no row at all.",
        "",
        *_table(header, rows),
    ]


# ---- overlapping sources, empty cloud folders -----------------------------------------------------------


def _source_counts(m: _Mirror, source_id: str) -> list[str]:
    """One line: a source's files by state, as status counts them, and whether its last listing was
    complete."""
    states: Counter[str] = Counter()
    for state, files in m.rows(
        "SELECT state, COUNT(*) FROM items WHERE source_id = ? AND is_dir = 0 GROUP BY state", (source_id,)
    ):
        states[str(state)] += int(files)
    complete = m.rows("SELECT enumeration_complete FROM sources WHERE source_id = ?", (source_id,))
    listing = ("yes" if complete[0][0] else "no") if complete else "never listed"
    return [
        f"live {states['live'] + states['dataless']} (online-only {states['dataless']}), stubs "
        f"{states['quarantined'] + states['refused']}, tombstones {states['tombstone']}, listing complete: "
        f"{listing}"
    ]


def _overlap_part(r: _Run, m: _Mirror, labels: _Labels) -> list[str]:
    """Pairs of sources whose configured folder is the same or one inside the other, with each one's file
    counts and whether the outer source's exclude list prunes the inner folder."""
    from agentsync import arm_local  # noqa: PLC0415 - _unexcluded: the walk's own pruning rule

    config = cast(Config, r.config)
    roots = [
        (src, Path(os.path.normpath(expand(src.path)))) for src in config.sources if src.path is not None
    ]
    out: list[str] = []
    pairs = 0
    for outer, outer_root in roots:
        for inner, inner_root in roots:
            if inner is outer or not inner_root.is_relative_to(outer_root):
                continue
            if inner_root == outer_root and outer.id > inner.id:
                continue  # the same folder twice: one line for the pair
            pairs += 1
            if pairs > _PAIRS_SHOWN:
                continue
            rel = inner_root.relative_to(outer_root).as_posix()
            if rel == ".":
                where = "has the same folder as"
                pruned = "n/a"
            else:
                where = f"contains, {len(rel.split('/'))} folder level(s) down,"
                pruned = "no" if arm_local._unexcluded(outer, [rel]) else "yes"
            counts = [
                _lines(functools.partial(_source_counts, m, sid))[0].removeprefix("- ")
                for sid in (outer.id, inner.id)
            ]
            out.append(
                f"- {labels.of(outer.id)} ({outer.kind.value}, {outer.state.value}) {where} "
                f"{labels.of(inner.id)} ({inner.kind.value}, {inner.state.value}); the outer source's "
                f"exclude list prunes the inner folder: {pruned}; {labels.of(outer.id)}: {counts[0]}; "
                f"{labels.of(inner.id)}: {counts[1]}"
            )
    if not out:
        return [f"- none: no source's folder is inside another's ({len(roots)} source(s) with a folder)"]
    if pairs > _PAIRS_SHOWN:
        out.append(f"- (+{pairs - _PAIRS_SHOWN} more pair(s) not shown)")
    return out


def _folder_facts(paths: Sequence[Path | None]) -> list[tuple[str, bool]]:
    """(what the folder is, its link count is 2) per path, from one ``lstat`` each: ``dataless`` when the
    folder itself carries SF_DATALESS (its child list is not on this Mac), ``materialised`` when it does
    not, else ``gone``, ``not readable``, ``not a folder`` or ``not checked``. No folder is listed, so
    nothing is downloaded and no child list is fetched. A link count of 2 is a folder with no entry by its
    own metadata (APFS counts 2 plus one per entry)."""
    from agentsync.materialise import is_dataless  # noqa: PLC0415

    out: list[tuple[str, bool]] = []
    for path in paths:
        if path is None:
            out.append(("not checked", False))
            continue
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            out.append(("gone", False))
        except OSError:
            out.append(("not readable", False))
        else:
            if not stat.S_ISDIR(st.st_mode):
                out.append(("not a folder", False))
            else:
                out.append(("dataless" if is_dataless(st) else "materialised", st.st_nlink == 2))
    return out


def _empty_part(r: _Run, m: _Mirror, labels: _Labels) -> list[str]:
    """Per source: the zero-child cloud folders its last walk held as unknown (manifest meta
    ``empty_cloud_dirs:<source id>``), and for up to :data:`_EMPTY_DIRS_CHECKED` of them whether the folder
    itself is dataless. Settles whether an empty cloud folder on this Mac can be told from one never
    listed (docs/research/corporate-bring-back-2026-10-06.md, O1)."""
    from agentsync import arm_local  # noqa: PLC0415 - _unexcluded: the walk's own pruning rule

    config = cast(Config, r.config)
    stored = {
        str(key)[len(_EMPTY_DIRS_META) :]: str(value)
        for key, value in m.rows("SELECT key, value FROM meta WHERE key LIKE ?", (_EMPTY_DIRS_META + "%",))
    }
    out: list[str] = []
    for src in config.sources:
        try:
            doc = json.loads(stored.get(src.id) or "[]")
        except ValueError:
            doc = []
        names = [d for d in doc if isinstance(d, str)] if isinstance(doc, list) else []
        if not names or src.path is None:
            continue
        if m.left() <= 0:
            raise _OutOfTimeError
        root = expand(src.path)
        checked = names[:_EMPTY_DIRS_CHECKED]
        paths = [
            None if Path(rel).is_absolute() or ".." in Path(rel).parts else root / rel for rel in checked
        ]
        try:
            facts = r.call(
                functools.partial(_folder_facts, paths), timeout=min(_FOLDERS_S, max(0.3, m.left()))
            )
        except TimeoutError:
            out.append(f"- {labels.of(src.id)}: {len(names)} unknown: {NOT_MEASURED}")
            continue
        kinds = Counter(kind for kind, _two in facts)
        no_entry = sum(1 for kind, two in facts if kind == "materialised" and two)
        held = folders = 0
        for rel in checked:
            below = m.rows(
                "SELECT COUNT(*) FROM items WHERE source_id = ? AND rel_path >= ? AND rel_path < ? "
                "AND is_dir = 0 AND state != 'tombstone'",
                (src.id, rel + "/", rel + "0"),
            )[0][0]
            held += int(below)
            folders += 1 if below else 0
        # The exclude rule costs names x folder levels x globs, and the sync's own advice for these folders
        # is one more glob each: asked of the checked folders only, and stopped like the lstat calls.
        try:
            reached = r.call(
                functools.partial(arm_local._unexcluded, src, checked),
                timeout=min(_FOLDERS_S, max(0.3, m.left())),
            )
            excluded = f"{len(checked) - len(reached)} of the checked folder(s) excluded in sources.toml now"
        except TimeoutError:
            excluded = f"excluded in sources.toml now: {NOT_MEASURED}"
        rest = ", ".join(
            f"{kinds[kind]} {kind}" for kind in ("gone", "not readable", "not a folder", "not checked")
        )
        out.append(
            f"- {labels.of(src.id)}: {len(names)} unknown: {kinds['dataless']} dataless, "
            f"{kinds['materialised']} materialised-and-empty ({no_entry} of them with a link count of 2: no "
            f"entry by the folder's own metadata); {rest}; {len(checked)} of {len(names)} checked; "
            f"{excluded}; the mirror still holds {held} file(s) below {folders} of the checked folder(s)"
        )
    if not out:
        return ["- none: no source's last walk held a zero-child cloud folder as unknown"]
    return [
        "Read from each folder's own metadata (one lstat; no folder is listed, so nothing is fetched). "
        '"Empty" is what the source\'s last walk found, not a new listing.',
        "",
        *out,
    ]


# ---- repeat conversions ---------------------------------------------------------------------------------


def _repeat_part(r: _Run, m: _Mirror, labels: _Labels) -> list[str]:
    """Whether the same files are converted run after run: per run, the files converted and how many of them
    from bytes an earlier run, or the run just before, had converted already (``runs.counts_json``), and what
    the converter cache says for the runs of an earlier build."""
    runs = _run_rows(m)
    if not runs:
        return ["- no run is recorded"]
    rows = []
    for x in runs[:_RUNS_SHOWN]:
        if "converted" in x.counts:
            cells: tuple[object, ...] = (
                x.n("converted"),
                x.n("converted_failed"),
                x.n("converted_seen"),
                x.n("converted_again"),
            )
        else:
            cells = ("not recorded", "-", "-", "-")
        rows.append((x.run_id, x.mode, x.status, _iso(x.started), *cells))
    header = (
        "run",
        "mode",
        "status",
        "started (UTC)",
        "files converted",
        "of them failed",
        "from bytes an earlier run converted",
        "from bytes the run just before converted",
    )
    recorded = [x for x in runs if "converted" in x.counts]
    looping = sum(1 for x in recorded if x.n("converted_again"))
    out = [
        f"- of the last {len(runs)} run(s), {len(recorded)} recorded what they converted; {looping} of those "
        "converted at least one file from bytes the run just before had converted too (the sign of a loop)",
        "",
        "the last runs, newest first:",
        "",
        *_table(header, rows),
    ]
    newest = runs[0].run_id
    again = dict.fromkeys((newest, newest - 1), 0)
    total = 0
    for last_used, files in m.rows(
        "SELECT last_used_run, COUNT(*) FROM cache WHERE last_used_run > created_run GROUP BY 1"
    ):
        total += int(files)
        if last_used in again:
            again[int(last_used)] += int(files)
    out += [
        "",
        f"- converter cache: {total} conversion(s) were used again by a later run than the one that made "
        f"them (the same bytes converted again); {again[newest]} of them last by the newest run "
        f"({newest}), {again[newest - 1]} by the run before it (a count that also holds for runs of an "
        "earlier build)",
    ]
    return out


def _evidence(r: _Run) -> list[str]:
    """The evidence parts, :data:`EVIDENCE_TITLES` in order, each under its ``### `` heading: what the
    maintainers need from this Mac to judge the OCR build and settle the open questions of the last
    bring-back file, so that no further round is needed. Read-only (the manifest is opened ``mode=ro``; a
    folder is lstat-ed, never listed), bounded by :data:`EVIDENCE_BUDGET_S` in all, and made of counts,
    states, seconds, version strings and fixed words: never a file name, a folder name or a reason's text."""
    if r.config is None:
        return []
    mirror = _Mirror(
        r.config.state_paths.db,
        min(EVIDENCE_BUDGET_S, r.remaining() - _RESERVE_S),
        latest=r.deadline - _RESERVE_S,
    )
    labels = _Labels(r.config, r.red)
    parts: tuple[Callable[[_Run, _Mirror, _Labels], list[str]], ...] = (
        _ocr_part,
        _quarantine_part,
        _purge_part,
        _overlap_part,
        _empty_part,
        _repeat_part,
    )
    out: list[str] = []
    try:
        for title, fn in zip(EVIDENCE_TITLES, parts, strict=True):
            out += ["", f"### {title}", "", *_lines(functools.partial(fn, r, mirror, labels))]
    finally:
        r.evidence_steps = mirror.steps
        r.evidence_statements = tuple(mirror.statements)
        mirror.close()
    r.facts.evidence = sum(1 for line in out if "not measured" in line)
    return out


def _evidence_line(r: _Run) -> str:
    """The Summary's line about the evidence parts: whether every one was measured. The person sends one
    report, so a part that ran out of time is said where it is seen first."""
    count = r.facts.evidence
    if count is None:
        return "- evidence: not read (the Status section's parts need a config that loads)"
    if count == 0:
        return f"- evidence: the {len(EVIDENCE_TITLES)} parts at the end of Status were measured"
    return (
        f'- evidence: {count} line(s) at the end of Status say "not measured": write the report again when '
        "this Mac is idle (`install.sh --report-only`) before sending it"
    )


def _status_section(r: _Run) -> list[str]:
    """The Status section: status's lines, then the evidence parts. A status hook that fails says so as any
    section does, and the evidence is still read."""
    try:
        body = _status(r)
    except Exception as exc:
        body = [f"_This section failed: {type(exc).__name__}: {exc}_"]
    return [*body, *_evidence(r)]


def parse_launchctl_print(text: str) -> tuple[dict[str, str], list[str]]:
    """(top-level ``key = value`` pairs, the ``arguments`` list) of a ``launchctl print`` service dump."""
    found: dict[str, str] = {}
    args: list[str] = []
    in_args = False
    for ln in text.splitlines():
        if in_args:
            if ln.startswith("\t}"):
                in_args = False
            elif ln.startswith("\t\t"):
                args.append(ln.strip())
            continue
        m = _LAUNCHD_LINE_RE.match(ln)
        if m is None:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == "arguments" and value == "{":
            in_args = True
            continue
        found.setdefault(key, value)
    return found, args


def decode_exit(value: str) -> str:
    """A launchd ``last exit code`` value with agentsync's meaning appended (``79`` -> ``79 (TCC_PENDING:
    ...)``); ``(never exited)`` explained."""
    value = value.strip()
    if "never exited" in value:
        return "(never exited: the first run has not finished, or not started)"
    m = re.match(r"^(-?\d+)", value)
    if m is None:
        return value
    meaning = EXIT_MEANINGS.get(int(m.group(1)))
    return f"{value} ({meaning})" if meaning else value


def _under_home(path: str) -> bool:
    homes = {str(Path.home()), str(Path.home().resolve())}
    return any(path == h or path.startswith(h.rstrip("/") + "/") for h in homes)


_HOME_LIKE = ("/Users/", "/private/var/folders/", "/var/folders/", "/private/tmp/", "/tmp/", "/Volumes/")


def _foreign_paths(values: Iterable[str]) -> list[str]:
    """The absolute paths in ``values`` that are in some user's or temporary folder other than this home."""
    return [v for v in values if v.startswith(_HOME_LIKE) and not _under_home(v)]


_PLIST_KEYS = (
    "Label",
    "ProgramArguments",
    "EnvironmentVariables",
    "StandardOutPath",
    "StandardErrorPath",
    "StartInterval",
    "StartCalendarInterval",
    "RunAtLoad",
    "ProcessType",
    "LowPriorityIO",
    "ThrottleInterval",
    "LimitLoadToSessionType",
    "MaterializeDatalessFiles",
    "Umask",
)
"""The keys of a job's plist as ``ops.launchd`` writes it: a key outside them is counted, never named."""
_LAUNCHER_VALUES = {
    "--timeout": "watchdog seconds",
    "--grace": "grace seconds",
    "--canary-timeout": "canary timeout",
    "--canary": "canary path",
}
_ARGUMENT_CLASSES = ("launcher", "interpreter", "config path", "mode", *_LAUNCHER_VALUES.values())
_POSITIONS_SHOWN = 12


def argument_roles(argv: Sequence[object], *, launcher: str, fixed: Sequence[str]) -> list[str]:
    """The class of each position of a job's ``ProgramArguments``, by the shape ``ops.launchd`` writes:
    ``launcher``, ``launcher option`` and its value (``watchdog seconds``, ``grace seconds``, ``canary
    timeout``, ``canary path``), ``separator``, then ``interpreter``, ``fixed argument``, ``mode`` and
    ``config path``; ``other`` for anything else. ``launcher``: the launcher's file name; ``fixed``: the
    child's fixed arguments. The report prints these classes, never an argument."""
    args = [a if isinstance(a, str) else "" for a in argv]
    roles: list[str] = []
    i = 0
    if args and Path(args[0]).name == launcher:
        roles.append("launcher")
        i = 1
        while i < len(args) and args[i] != "--":
            value = _LAUNCHER_VALUES.get(args[i])
            if value is not None and i + 1 < len(args) and args[i + 1] != "--":
                roles += ["launcher option", value]
                i += 2
            else:
                roles.append("launcher option" if args[i].startswith("--") else "other")
                i += 1
        if i < len(args):
            roles.append("separator")
            i += 1
    if i < len(args):
        roles.append("interpreter")
        i += 1
    while i < len(args):
        value = {"--mode": "mode", "--config": "config path"}.get(args[i])
        if value is not None and i + 1 < len(args):
            roles += ["fixed argument", value]
            i += 2
        else:
            roles.append("fixed argument" if args[i] in fixed else "other")
            i += 1
    return roles


def _same_file(a: str, b: str) -> str:
    try:
        return "yes" if Path(a).samefile(b) else "no"
    except OSError:
        return "no"


def _class_text(name: str, installed: list[str], expected: list[str]) -> str:
    """One class of argument compared: ``same``, or how it differs. A number is shown; a path never is."""
    if installed == expected:
        return f"{name} same" if installed else f"{name} in neither"
    if not installed or not expected:
        return f"{name} only {'installed' if installed else 'in this build'}"
    if all(v.isdigit() for v in (*installed, *expected)):
        return f"{name} differs (installed {', '.join(installed)}; this build {', '.join(expected)})"
    if name == "mode":
        return f"{name} differs"
    return (
        f"{name} differs (the same file: {_same_file(installed[0], expected[0])}; the installed one exists: "
        f"{'yes' if Path(installed[0]).exists() else 'no'})"
    )


def _argument_lines(installed: Sequence[object], expected: Sequence[str]) -> list[str]:
    """How an installed job's ``ProgramArguments`` differ from what this build would write: the positions
    that differ with the class of each, then each class compared. Classes and counts, never a value."""
    from agentsync.ops import launchd  # noqa: PLC0415

    have = [a if isinstance(a, str) else "" for a in installed]
    want = list(expected)
    roles_have = argument_roles(have, launcher=launchd.LAUNCHER_EXECUTABLE, fixed=launchd.CHILD_PREFIX)
    roles_want = argument_roles(want, launcher=launchd.LAUNCHER_EXECUTABLE, fixed=launchd.CHILD_PREFIX)
    positions = []
    for i in range(max(len(have), len(want))):
        a = roles_have[i] if i < len(have) else "nothing"
        b = roles_want[i] if i < len(want) else "nothing"
        if i >= len(have) or i >= len(want) or have[i] != want[i]:
            positions.append(f"{i} ({a})" if a == b else f"{i} ({a} installed, {b} in this build)")
    shown = ", ".join(positions[:_POSITIONS_SHOWN]) or "none"
    if len(positions) > _POSITIONS_SHOWN:
        shown += f" (+{len(positions) - _POSITIONS_SHOWN} more)"

    def of(name: str, args: list[str], roles: list[str]) -> list[str]:
        return [arg for arg, role in zip(args, roles, strict=True) if role == name]

    classes = [
        _class_text(name, of(name, have, roles_have), of(name, want, roles_want))
        for name in _ARGUMENT_CLASSES
        if name != "canary path"
    ]
    canaries_have, canaries_want = (
        set(of("canary path", have, roles_have)),
        set(of("canary path", want, roles_want)),
    )
    classes.append(
        f"canary paths: {len(canaries_have)} installed, {len(canaries_want)} in this build, "
        f"{len(canaries_have & canaries_want)} in both"
    )
    fixed = (
        "same"
        if of("fixed argument", have, roles_have) == of("fixed argument", want, roles_want)
        else "differ"
    )
    classes.append(f"fixed arguments {fixed}")
    classes.append(
        f"other arguments: {roles_have.count('other')} installed, {roles_want.count('other')} in this build"
    )
    return [
        f"  - ProgramArguments: {len(have)} installed, {len(want)} in this build; positions that differ "
        f"(from 0): {shown}",
        "  - by class: " + " · ".join(classes),
    ]


def _plist_lines(r: _Run) -> list[str]:
    """For each job with a plist in this home folder: whether it is what this build would write
    (``ops.launchd``), and when not, which keys differ and how the arguments do (:func:`_argument_lines`).
    Doctor's line says only that ``ProgramArguments`` differ.

    Building a job's spec logs a WARNING when no launcher is installed (doctor's own build of it has said
    so already): that logger is quiet here, so the report adds no line to what the installer prints."""
    from agentsync.ops import launchd  # noqa: PLC0415 - lazy: the report must import even if it is broken

    if r.config is None:
        return []
    out = ["", "Installed plist against what this build would write (classes and counts, never a value):", ""]
    quiet = logging.getLogger(launchd.__name__)
    was_disabled, quiet.disabled = quiet.disabled, True
    try:
        return out + _plist_compared(r, r.config)
    finally:
        quiet.disabled = was_disabled


def _plist_compared(r: _Run, config: Config) -> list[str]:
    """One or more lines per job (:func:`_plist_lines`)."""
    from agentsync.ops import launchd  # noqa: PLC0415

    out: list[str] = []
    builders = (("poll", launchd.poll_spec), ("reconcile", launchd.reconcile_spec))
    for suffix, build in builders:
        label = f"{r.label_prefix()}.{suffix}"
        try:
            path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
            if not path.exists():
                out.append(f"- {label}: no plist")
                continue
            installed = plistlib.loads(path.read_bytes())
            if not isinstance(installed, dict):
                raise ValueError("not a dictionary")
            expected = plistlib.loads(launchd.render_plist(build(config)))
        except Exception as exc:  # an unreadable plist, or a spec this config cannot give (no launcher)
            out.append(f"- {label}: not compared ({type(exc).__name__})")
            continue
        differing = [k for k in _PLIST_KEYS if installed.get(k) != expected.get(k)]
        unknown = sum(1 for k in set(installed) | set(expected) if k not in _PLIST_KEYS)
        if not differing and installed == expected:
            out.append(f"- {label}: the installed plist is what this build would write")
            continue
        named = []
        for key in differing:
            a, b = installed.get(key), expected.get(key)
            plain = all(isinstance(v, int) or v is None for v in (a, b))  # a bool is an int
            named.append(f"{key} (installed {a}, this build {b})" if plain else key)
        out.append(
            f"- {label}: differs in {', '.join(named) or 'no key this build writes'}"
            + (f"; {unknown} key(s) this build does not write" if unknown else "")
        )
        if "ProgramArguments" in differing:
            args = installed.get("ProgramArguments")
            out += _argument_lines(args if isinstance(args, list) else [], expected["ProgramArguments"])
        if "EnvironmentVariables" in differing:
            env_a, env_b = installed.get("EnvironmentVariables"), expected.get("EnvironmentVariables")
            keys_a = set(env_a) if isinstance(env_a, dict) else set()
            keys_b = set(env_b) if isinstance(env_b, dict) else set()
            changed = sum(
                1
                for k in keys_a & keys_b
                if cast(dict[str, object], env_a)[k] != expected["EnvironmentVariables"][k]
            )
            out.append(
                f"  - EnvironmentVariables: {len(keys_a)} installed, {len(keys_b)} in this build, "
                f"{len(keys_a & keys_b)} in both, {changed} of those with another value"
            )
    return out


def _background(r: _Run) -> list[str]:
    out: list[str] = []
    prefix = r.label_prefix()
    if r.facts.launchd_simulated:
        out += [
            "launchd: simulated (the last install.sh run says `launchd=simulated`): no LaunchAgent of this "
            "setup ran, so a job loaded below is not this setup's.",
            "",
        ]
    for suffix in ("poll", "reconcile"):
        label = f"{prefix}.{suffix}"
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        has_plist = plist.exists()
        rc, text = _launchctl_print(r.run, label)
        if rc != 0:
            if has_plist:
                line = (
                    f"plist present · NOT loaded (launchctl print exited {rc}; "
                    "`agentsync install-agent` loads it)"
                )
                short = "plist present but not loaded"
            else:
                line = f"not installed (no plist, not loaded: launchctl print exited {rc})"
                short = "not installed"
            out.append(f"- {label}: {line}")
            r.facts.background.append(f"{suffix} {short}")
            continue
        found, args = parse_launchctl_print(text)
        loaded_from = found.get("path", "")
        program = found.get("program", "")
        foreign = _foreign_paths([loaded_from, program, *args])
        own_plist = loaded_from in (str(plist), str(plist.resolve())) if loaded_from else has_plist
        if foreign or (loaded_from and not own_plist and not _under_home(loaded_from)):
            out.append(
                f"- {label}: loaded, but the job belongs to another install (its plist or ProgramArguments "
                "are outside this home folder); its runs and exit codes are not this setup's result"
                + (
                    " · this home folder's plist is present"
                    if has_plist
                    else " · no plist in this home folder"
                )
            )
            r.facts.background.append(f"{suffix} belongs to another install")
            continue
        details = []
        for key in _LAUNCHD_KEYS:
            if key in found:
                value = decode_exit(found[key]) if key == "last exit code" else found[key]
                details.append(f"{key} = {value}")
        state = " · ".join(details) or "yes"
        if not has_plist:
            out.append(
                f"- {label}: loaded, but the plist is absent (removed without `launchctl bootout`; "
                f"`agentsync install-agent` repairs it) · {state}"
            )
            r.facts.background.append(f"{suffix} loaded without its plist")
            continue
        out.append(f"- {label}: plist present · loaded: {state}")
        exit_code = found.get("last exit code")
        r.facts.background.append(
            f"{suffix} last exit {decode_exit(exit_code)}" if exit_code else f"{suffix} loaded, no run yet"
        )
    try:
        out += _plist_lines(r)
    except Exception as exc:  # the comparison is extra: it never costs the section its other lines
        out += [
            "",
            f"Installed plist against what this build would write: not compared ({type(exc).__name__})",
        ]
    out += ["", "Last launcher TCC lines (per job, newest last):", ""]
    tcc: list[str] = []
    log_dir = r.log_dir()
    for suffix in ("poll", "reconcile"):
        path = log_dir / f"{prefix}.{suffix}.err.log"
        try:
            hits = [ln for ln in _tail(path) if _TCC_RE.search(ln)]
        except FileNotFoundError:
            continue
        except OSError as exc:
            tcc.append(f"{path.name}: cannot read: {type(exc).__name__}: {exc.strerror or exc}")
            continue
        tcc += [f"{path.name}: {ln}" for ln in hits[-5:]]
    return out + _fence(tcc)


# An item's path or document name in a log line: a slash inside a word, or a document extension. Field report
# 2026-10-05: client folder and file names nested BELOW a configured source (never registered with the
# Redactor) reached the report through "fetch of <rel_path> failed" lines and inbox .eml names. The extension
# list holds every suffix a converter claims (tests/test_setup_report.py checks it against the registry and
# the image converter): a file the cycle reads is a file its log lines can name.
_PATH_IN_LOG_RE = re.compile(
    r"\S/|/\S|\.(?:docx?|docm|xlsx?|xlsm|xlsb|pptx?|pptm|pdf|eml|msg|txt|md|markdown|csv|tsv|rtf|odt|ods|odp|"
    r"pages|numbers|key|vsdx?|one|html?|json|xml|ya?ml|log|vtt|zip|png|jpe?g|gif|bmp|webp|hei[cf]|tiff?|mp4|"
    r"mov|m4a|wav)\b",
    re.IGNORECASE,
)
_LOG_HEAD_RE = re.compile(
    r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} (?:WARNING|ERROR|CRITICAL) agentsync(?:\.\w+)+"
)
"""A segment that is the head of one of agentsync's own log lines, whole: time, level, logger. A logger is
named after its module, and ``agentsync.convert.pdf`` is not a document."""
_PY_LOG_RE = re.compile(r"\b(?:WARNING|ERROR|CRITICAL) agentsync\.")
"""One of agentsync's own log lines (``<time> WARNING agentsync.<module>: ...``). install.sh's and git's
``error:`` and ``fatal:`` lines are not: they keep their path or URL in the install.out tail."""
_SYNC_OUT_RE = re.compile(r"^\s+(?:alarm|error): ")
"""An alarm or error line of ``agentsync sync``'s own report (indented under its source), which install.sh
prints in full when its sync step fails. install.sh's own ``error:`` lines start at the margin."""
_QUOTED_RE = re.compile(r"""(?<![A-Za-z0-9])(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")(?![A-Za-z0-9])""")
"""A ``%r`` of a string: in agentsync's own lines it is a folder, file or sentinel name."""
_KEY_PATH_RE = re.compile(r"\b(\w+=)(?:[^=]*?)(?=\s\w+=|$)")
_ABS_PATH_RE = re.compile(r"(?:(?<=\s)|(?<=^)|(?<=['\"(]))(?:~|/)\S*/.*$")


def _own_line(line: str) -> bool:
    """Whether ``line`` is agentsync's own text: one of its log lines, or an alarm or error line of a sync."""
    return _PY_LOG_RE.search(line) is not None or _SYNC_OUT_RE.match(line) is not None


def _scrub_item_paths(line: str) -> str:
    """``line`` (``<log file>: <log line>``, or a log line alone) with item paths and document names replaced
    by ``<path>``. In one of agentsync's own lines (:func:`_own_line`) every quoted ``%r`` first, between its
    quotes: a folder one level below a source root has no slash and no extension to know it by. Then, per
    ``: ``-separated segment after the first: a ``key=`` value or an absolute path (``/…`` or ``~/…``, to the
    segment's end) in place, and any segment still naming a path or document whole. The local log keeps the
    detail; the report, which may go to a public issue, never carries an item's path or name."""
    name, sep, rest = line.partition(": ")
    if _own_line(line):
        rest = _QUOTED_RE.sub(lambda m: f"{m.group(0)[0]}<path>{m.group(0)[-1]}", rest)
    return name + sep + ": ".join(_scrub_segment(part) for part in rest.split(": "))


def _scrub_segment(part: str) -> str:
    if _LOG_HEAD_RE.fullmatch(part) or not _PATH_IN_LOG_RE.search(part.replace("agentsync.", "")):
        return part
    out = _KEY_PATH_RE.sub(
        lambda m: m.group(1) + "<path>" if _PATH_IN_LOG_RE.search(m.group(0)) else m.group(0), part
    )
    out = _ABS_PATH_RE.sub("<path>", out)
    return "<path>" if _PATH_IN_LOG_RE.search(out.replace("agentsync.", "").replace("<path>", "")) else out


def _recent_errors(r: _Run) -> list[str]:
    log_dir = r.log_dir()
    if not log_dir.exists():
        return [f"{log_dir} does not exist yet (no background run has logged anything)."]
    files = sorted((p for p in log_dir.iterdir() if p.is_file()), key=lambda p: (p.stat().st_mtime, p.name))
    hits: list[str] = []
    problems: list[str] = []
    for path in files:
        try:
            hits += [_scrub_item_paths(f"{path.name}: {ln}") for ln in _tail(path) if _LEVEL_RE.search(ln)]
        except OSError as exc:
            problems.append(f"- {path.name}: cannot read: {type(exc).__name__}: {exc.strerror or exc}")
    head = (
        f"The last {min(len(hits), RECENT_ERROR_LINES)} of {len(hits)} WARNING/ERROR line(s) in {log_dir}"
        " (item paths and document names shown as <path>):"
    )
    return [*problems, head, "", *_fence(hits[-RECENT_ERROR_LINES:])]


def _secs(seconds: float) -> str:
    """``42s``, ``3m05s``, ``1h02m03s``."""
    total = round(seconds)
    if total < 60:
        return f"{total}s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{sec:02d}s"


def _iso(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment is not None else "no time"


def _prompt_version(attempt: Attempt | None) -> str | None:
    """``v5`` from the attempt's Prompt: line (only the version: the line is the agent's free text)."""
    m = re.search(r"\bv?(\d{1,3})\b", attempt.header.get("Prompt", "")) if attempt is not None else None
    return f"v{int(m.group(1))}" if m else None


def _doctor_fails(r: _Run) -> list[str]:
    """The doctor FAIL check names (every FAIL is unexpected: only warns are ever expected)."""
    return [name for tag, name, _detail in r.facts.doctor_problems if tag == "FAIL"]


def _attempt_outcome(r: _Run, index: int) -> Outcome:
    """Attempt ``index``'s outcome; the latest attempt's also counts this Mac's doctor FAILs (doctor shows
    the state now, which is the latest attempt's)."""
    fr = r.friction
    assert fr is not None
    latest = index == len(fr.attempts) - 1
    return compute_outcome(
        fr.attempts[index],
        runs_for_attempt(fr, index, r.install_runs),
        doctor_fails=_doctor_fails(r) if latest else (),
    )


def _kind_counts(attempt: Attempt) -> str:
    """Event lines per kind, every line counted once, so the counts add up to the event line count: v5's
    step kinds, the prompt's kinds, untyped words, then the closing ``finished`` line."""
    counts = attempt.kinds()
    known = (*STEP_KINDS, *FRICTION_KINDS, "finished")
    order = [*STEP_KINDS, *FRICTION_KINDS, *sorted(k for k in counts if k not in known), "finished"]
    return ", ".join(f"{counts[k]} {k}" for k in order if counts[k]) or "none"


def _headerless(fr: Friction, index: int) -> bool:
    """Whether attempt ``index`` is a session that never wrote its header: no header line, and it follows a
    finished attempt (:func:`parse_friction`)."""
    att = fr.attempts[index]
    return index > 0 and not att.header and att.started is None and fr.attempts[index - 1].finished


def _friction_section(r: _Run) -> list[str]:
    if r.friction_error is not None:
        return [f"Could not read the friction log at {r.friction_path}: {r.friction_error}"]
    fr = r.friction
    if fr is None:
        return [
            f"No friction log found at {r.friction_path} (setup prompt step 1's `install.sh --log-start` "
            f"creates it; ${FRICTION_ENV} names another file)."
        ]
    out = [
        f"Embedded from {r.friction_path} as this report read it: {fr.line_count} line(s), last modified "
        f"{_iso(fr.mtime)} (a line written after that is not in this report: run setup-report again). "
        "Redacted with the same placeholders as the rest of the report.",
        "",
        f"{len(fr.attempts)} attempt(s):",
    ]
    for i, att in enumerate(fr.attempts):
        outcome = _attempt_outcome(r, i)
        no_header = "logged after the previous attempt finished" if _headerless(fr, i) else "v4 format"
        bits = [
            f"started {_iso(att.started)}" if att.started else f"no Attempt: line ({no_header})",
            f"prompt {_prompt_version(att) or 'not stated'}",
            f"agent {att.header.get('Agent') or 'not stated'}",
        ]
        tail = [f"{len(att.events)} event line(s): {_kind_counts(att)}"]
        if att.legacy:
            tail.append(f"{len(att.legacy)} legacy v4 line(s) (F<n> | ...), shown, not counted")
        if att.untyped:
            tail.append(
                f"{len(att.untyped)} line(s) with a kind outside the prompt's list, shown, not counted"
            )
        tail.append("finished" if att.finished else 'no "end | finished" line')
        out.append(
            f"- attempt {att.number} (lines {att.first_line}-{att.last_line}; {', '.join(bits)}): "
            f"{outcome.text}; {'; '.join(tail)}"
        )
    if fr.truncated:
        out.append(f"Only the first {FRICTION_MAX_BYTES // 1024} KiB are shown.")
    out.append("Each line is shown with its line number: the F<n> ids in the Summary are these numbers.")
    body = [f"{n:>3}  {ln}" for n, ln in enumerate(fr.text.rstrip("\n").splitlines(), start=1)]
    return [*out, "", *_fence(body)]


def _no_clicks_why(run_type: str) -> str | None:
    """Why no Allow click is possible in this run type (None on a real Mac)."""
    return {"simulated launchd": "launchd simulated", "sandbox": "sandbox"}.get(run_type)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _allow_clicks(layout: PromptLayout) -> str:
    """The words for the announced Allow clicks: one in v7, two in v6."""
    return "Allow click" if len(layout.allow_click_steps) == 1 else "Allow clicks"


def _turns_line(att: Attempt, run_type: str) -> str:
    """``human turns: 1 (1 question; clicks: none possible; approvals: not observable)``: questions (with the
    folder question, which prompt v6 does not log), clicks and approvals, counted by kind. Approvals are "not
    observable" when none is logged and the agent's tool does not tell it (:data:`APPROVAL_HIDDEN_TOOLS`); the
    total then counts only what is known. Why no click is possible is on the expected-turns line."""
    counts = att.kinds()
    logged_q, c, a = counts["question"], counts["click"], counts["approval"]
    q = logged_q + (0 if att.layout.logs_expected_turns else 1)  # v6: the folder question is not logged
    agent = att.header.get("Agent", "").lower()
    hidden = a == 0 and any(t in agent for t in APPROVAL_HIDDEN_TOOLS)
    approvals = "approvals: not observable" if hidden else _plural(a, "approval")
    no_clicks = _no_clicks_why(run_type)
    if no_clicks is None:
        clicks = _plural(c, "click")
        if not att.layout.logs_expected_turns:
            clicks += f" beyond the announced {_allow_clicks(att.layout)} (not logged)"
    elif c == 0:
        clicks = "clicks: none possible"
    else:
        clicks = f"{_plural(c, 'click')} logged, though none is possible"
    return f"- human turns: {q + c + a} ({_plural(q, 'question')}; {clicks}; {approvals})"


def _expected_turns_line(att: Attempt, run_type: str) -> str:
    """The turns fully one command allows, on their own line: the folder question and the Allow clicks."""
    layout = att.layout
    no_clicks = _no_clicks_why(run_type)
    if not layout.logs_expected_turns:  # prompt v6 logs neither
        steps = " and ".join(str(s) for s in layout.allow_click_steps)
        steps = f"step{'s' if len(layout.allow_click_steps) > 1 else ''} {steps}"
        parts = [f"the folder question (step {layout.folder_question_step}; not logged)"]
        if no_clicks is not None:
            parts.append(f"Allow clicks: none possible ({no_clicks})")
        else:
            parts.append(f"the announced {_allow_clicks(layout)} ({steps}; not logged)")
        return "- expected turns: " + " · ".join(parts)
    folder = next((e for e in att.of_kind("question") if att.expected(e)), None)
    parts = [f"the folder question F{folder.line}" if folder else "the folder question: not logged"]
    clicks = [e for e in att.of_kind("click") if att.expected(e)]
    if clicks:
        parts += [f"Allow click F{e.line} (step {e.step})" for e in clicks]
    elif no_clicks is not None:
        parts.append(f"Allow clicks: none possible ({no_clicks})")
    else:
        parts.append("Allow clicks: none logged")
    return "- expected turns: " + " · ".join(parts)


def _agent_friction_line(att: Attempt, stop: FrictionEvent | None) -> str:
    """The agent-side lines (deviation, prompt, error): counted apart from the outcome."""
    counts = att.kinds()
    lines = att.of_kind(*PROBLEM_KINDS)
    ids = f" ({', '.join(f'F{e.line}' for e in lines)})" if lines else ""
    text = (
        f"- agent friction: {counts['deviation']} deviation, {counts['prompt']} prompt, {counts['error']} "
        f"error{ids}"
    )
    uncounted = len(att.untyped) + len(att.legacy)
    if uncounted:
        text += f"; {uncounted} line(s) without a prompt kind (untyped or v4), shown, not counted"
    if stop is not None:
        return text + f"; F{stop.line} stopped the run (the outcome says so)"
    return text + "; none of these changes the outcome by itself"


def _doctor_line(r: _Run, runs: Sequence[InstallRun]) -> str:
    d = r.facts.doctor
    if d is None:
        return "- doctor: not run (see Doctor)"
    installed = agents_installed(r.install_runs)
    expected: dict[str, list[str]] = {}
    unexpected: list[str] = []
    for tag, name, detail in r.facts.doctor_problems:
        why = expected_warn(name, detail, agents_installed=installed) if tag == "warn" else None
        if why is None:
            unexpected.append(f"{name} {tag}")
        else:
            expected.setdefault(why, []).append(name)
    n_expected = sum(len(v) for v in expected.values())
    parts = [f"{d.get('FAIL', 0)} FAIL, {d.get('warn', 0)} warn ({sum(d.values())} checks)"]
    if n_expected:
        parts.append(
            f"expected {n_expected}: " + "; ".join(f"{', '.join(v)} ({k})" for k, v in expected.items())
        )
    parts.append(f"unexpected {len(unexpected)}" + (f": {', '.join(unexpected)}" if unexpected else ""))
    return "- doctor: " + " · ".join(parts)


def _first_sync_line(r: _Run, runs: Sequence[InstallRun]) -> str:
    """The installer's first sync: its step from install.log, what it converted and left to background sync
    (the step's ``converted-N-deferred-M`` note, else the sync's own "converted N, deferred M online-only"
    line in install.out), and how many sources status lists completely."""
    pool = [run for run in (runs or r.install_runs) if run.step("first-sync") is not None]
    parts: list[str] = []
    counts: tuple[int, int] | None = None
    if pool:
        v = pool[-1].step("first-sync") or {}
        text = f"{v.get('result', '?')} in {v.get('seconds', '?')}s"
        if v.get("rc", "0") not in ("0", ""):
            text += f", rc {v['rc']}"
        note = _CONVERTED_NOTE_RE.fullmatch(v.get("note", ""))
        if note is not None:
            counts = (int(note.group(1)), int(note.group(2)))
        elif v.get("note"):
            text += f" ({v['note']})"
        parts.append(text + " (install.log)")
    else:
        parts.append("no install.sh run with a first-sync step (install.log)")
    source = "install.log"
    if counts is None and r.facts.first_sync is not None:
        counts, source = r.facts.first_sync, "install.out"
    if counts is not None:
        converted, deferred = counts
        later = f"; background sync downloads and converts the {deferred}" if deferred else ""
        parts.append(f"converted {converted}, deferred {deferred} online-only ({source}{later})")
    if r.facts.baseline is not None:
        done, total = r.facts.baseline
        parts.append(f"{done} of {total} source(s) listed completely (status: baseline complete)")
    return "- first sync: " + " · ".join(parts)


def _it_draft_line(step: int = REPORT_STEP) -> str:
    """The IT draft (written by prompt ``step``: v6's step 3, v5's step 4): whether it exists and which of its
    fields are still open."""
    path = expand(IT_DRAFT)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        command = f"agentsync it-request --out {IT_DRAFT}"
        return f"- IT draft: {IT_DRAFT} not written (prompt step {step}: `{command}`)"
    except OSError as exc:
        return f"- IT draft: {IT_DRAFT} cannot be read: {type(exc).__name__}: {exc.strerror or exc}"
    person, admin = it_draft_fields(text)
    mine = f"{len(person)} field(s) left for you" + (f" ({', '.join(person)})" if person else "")
    return f"- IT draft: {IT_DRAFT} exists; {mine}; {len(admin)} left for IT"


def _loop_line(r: _Run) -> str:
    """``- Loop: <stage>; NEXT: <the loop's NEXT line, without paths>`` (KISS K16b): the stage from status's
    loop line, the NEXT from the ``loop_next`` hook (run after doctor, whose FAILs are rule 1's), then the
    first ``WAITING ON YOU:`` line and how many more there are (rule 5's NEXT and a held listing point at
    one). Sets ``r.loop_stage`` for the issue link; :func:`build_report` runs it once, before ``took``."""
    if r.facts.loop is not None:
        r.loop_stage = loop_stage(*r.facts.loop)
    stage = r.loop_stage or "unknown (no status loop line)"
    if r.config is None:
        return f"- Loop: {stage}; NEXT: not read (the config does not load)"
    if r.hooks.loop_next is None:
        return f"- Loop: {stage}; NEXT: not read (no loop hook)"
    config, next_fn = r.config, r.hooks.loop_next
    try:
        lines = r.call(lambda: next_fn(config), timeout=_LOOP_NEXT_S)
    except Exception as exc:  # costs the NEXT, never the Summary; only the type (a message may hold a path)
        return f"- Loop: {stage}; NEXT: not read ({type(exc).__name__})"
    found = next((ln for ln in lines if ln.startswith("NEXT: ")), None)
    text = loop_next_text(found) if found else "NEXT: none (the loop state cannot be read)"
    waits = [ln for ln in lines if ln.startswith("WAITING ON YOU: ")]
    if waits:
        more = f" (+{len(waits) - 1} more)" if len(waits) > 1 else ""
        text += f"; {loop_next_text(waits[0])}{more}"
    return f"- Loop: {stage}; {text}"


def _install_line(r: _Run, runs: Sequence[InstallRun], scoped: bool) -> str:
    if not r.install_runs:
        return "- install.sh: no run in the install log"
    if scoped and not runs:
        return f"- install.sh: no run during this attempt ({len(r.install_runs)} earlier in the install log)"
    pool = list(runs) if scoped else r.install_runs
    installs = install_runs_only(pool)
    lists = len(pool) - len(installs)
    where = f"{len(installs)} install run{'' if len(installs) == 1 else 's'}"
    where += (f" (+{lists} --list-folders)" if lists else "") + (" during this attempt" if scoped else "")
    if not installs:
        return f"- install.sh: {where}"
    last = installs[-1]
    status = f"exit {last.rc} after {last.seconds}s" if last.rc is not None else "no end line (stopped early)"
    steps = " · ".join(f"{k} {v.get('seconds', '?')}s" for k, v in last.steps if k != "report")
    return f"- install.sh: {where}; the last {status}" + (f" ({steps})" if steps else "")


def _installer_step_times(runs: Sequence[InstallRun], layout: PromptLayout) -> list[str]:
    """Prompt v6's step times from install.log (it logs no step lines): the ``--list-folders`` runs (step 1)
    and the install runs (the install step), in seconds per run."""
    out: list[str] = []
    lists = [run.seconds for run in runs if run.list_only and run.seconds is not None]
    installs = [run.seconds for run in install_runs_only(runs) if run.seconds is not None]
    if lists:
        out.append(
            f"step {layout.folder_question_step} --list-folders " + " + ".join(_secs(t) for t in lists)
        )
    if installs:
        out.append(f"step {layout.install_step} install " + " + ".join(_secs(t) for t in installs))
    return out


def _item_line(r: _Run, att: Attempt, e: FrictionEvent) -> str:
    fix = f" → {_shorten(r.red.redact(e.fix), 160)}" if e.fix else ""  # redacted, then cut
    step = f"step {e.step}" if e.step is not None else "no step"
    return f"- F{e.line} · {step} · {e.kind} · {_shorten(r.red.redact(e.what))}{fix}"


def _summary(r: _Run, *, header: list[str]) -> list[str]:
    fr = r.friction
    att = fr.latest if fr is not None else None
    runs = (
        runs_for_attempt(fr, len(fr.attempts) - 1, r.install_runs) if fr and fr.attempts else r.install_runs
    )
    outcome = compute_outcome(att, runs, doctor_fails=_doctor_fails(r))
    run_type = compute_run_type(runs, home_path())
    r.outcome, r.run_type = outcome, run_type
    out: list[str] = [f"- **outcome: {outcome.text}** (computed: {'; '.join(outcome.why)})"]
    if att is not None and att.header.get("Outcome"):
        out.append(f"- agent said: {att.header['Outcome']}")
    out.append(r.loop_line if r.loop_line is not None else _loop_line(r))
    if fr is None:
        out.append(f"- friction log: none at {r.friction_path}")
    elif att is None:
        out.append(f"- friction log: {r.friction_path} has no attempt (no Attempt:, Prompt: or event line)")
    if fr is not None and len(fr.attempts) > 1:
        earlier = ", ".join(
            f"attempt {a.number} {_attempt_outcome(r, i).text}" for i, a in enumerate(fr.attempts[:-1])
        )
        out.append(f"- attempt: {len(fr.attempts)} of {len(fr.attempts)} (earlier: {earlier})")
    why_run = {
        "simulated launchd": "install.log says launchd=simulated",
        "sandbox": "HOME is under a temporary folder",
        "real": "HOME is not under a temporary folder and launchd was not simulated",
    }[run_type]
    if run_type == "simulated launchd" and is_sandbox_home(home_path()):
        why_run += "; HOME is also under a temporary folder"
    agent = (att.header.get("Agent") if att is not None else None) or "not stated"
    out.append(
        f"- prompt: {_prompt_version(att) or 'not stated'} · run: {ISSUE_RUN_TYPES[run_type]} (computed: "
        f"{why_run}) · agent: {agent}"
    )
    if att is not None and att.header.get("Run"):
        out.append(f"- agent said run: {att.header['Run']}")
    if fr is not None and att is not None and _headerless(fr, len(fr.attempts) - 1):
        out.append(
            f"- note: attempt {att.number} has no Attempt: line. It starts at a step 1 error logged after "
            f"attempt {att.number - 1} finished: that session stopped before `install.sh --log-start`, so "
            "`install.sh --report-only` had no attempt to close."
        )
    elif att is not None and not att.finished:
        step = att.layout.report_step
        closer = "install.sh --report-only" if not att.layout.logs_steps else "the agent's end line"
        out.append(
            f'- WARNING: attempt {att.number} has no "end | finished" line ({closer}), so this report may be '
            f"stale: it was written before prompt step {step}, or the agent stopped early. Run "
            f"`agentsync setup-report` again after step {step}."
        )
    stop = stopping_error(att, runs) if att is not None else None
    if att is not None:
        out.append(_turns_line(att, run_type))
        out.append(_expected_turns_line(att, run_type))
        out.append(_agent_friction_line(att, stop))
        extra = f"; {len(att.legacy)} legacy v4 line(s)" if att.legacy else ""
        out.append(
            f"- friction (attempt {att.number}): {len(att.events)} event line(s): {_kind_counts(att)}{extra}"
        )
        timing = []
        wall = att.wall_seconds()
        timing.append(
            f"session {_secs(wall)} (first to last timestamp)" if wall is not None else "session: no times"
        )
        steps = [f"{n} {_secs(t)}" for n, t in att.step_seconds() if t is not None]
        if steps:
            timing.append("steps (start to end): " + " · ".join(steps))
        elif not att.layout.logs_steps:
            timed = _installer_step_times(runs, att.layout)
            if timed:
                timing.append("install.sh (install.log): " + " · ".join(timed))
        out.append("- time: " + "; ".join(timing))
    out.append(_install_line(r, runs, scoped=att is not None and att.begin is not None))
    if r.facts.instructions is not None:
        out.append(f"- installer output: {_instruction_text(*r.facts.instructions)}")
    out.append(_first_sync_line(r, runs))
    out.append(_doctor_line(r, runs))
    if run_type == "simulated launchd" or r.facts.launchd_simulated:
        out.append("- background sync: launchd: simulated (no LaunchAgent of this setup ran)")
    elif r.facts.background:
        out.append("- background sync: " + " · ".join(r.facts.background))
    layout = att.layout if att is not None else prompt_layout(None)  # no friction log: the newest prompt
    if layout.version < 7 or expand(IT_DRAFT).exists():  # v7 has no IT request step
        out.append(_it_draft_line(4 if layout.version == 5 else REPORT_STEP))
    if r.facts.shadow:
        expected = " (expected in a sandbox)" if run_type != "real" else ""
        out.append(
            f"- PATH: SHADOW: the first agentsync on PATH is {r.facts.shadow}, not ~/.local/bin/agentsync"
            f"{expected}"
        )
    out.append(_evidence_line(r))
    out.append(f"- redaction: {_REDACTION_MARK}")
    title = (
        f"Items that were not one command (attempt {att.number}):"
        if att
        else "Items that were not one command:"
    )
    out += ["", title, ""]
    items: list[FrictionEvent] = []
    if att is not None:
        items = [stop] if stop is not None else []
        items += sorted(
            (e for e in att.of_kind(*TURN_KINDS) if not att.expected(e)),
            key=lambda e: (TURN_KINDS.index(e.kind), e.line),
        )
    if att is not None and items:
        out += [_item_line(r, att, e) for e in items]
    else:
        out.append("- none" + ("" if att is not None else " (no friction log)"))
    agent_side = [e for e in att.of_kind(*PROBLEM_KINDS) if e is not stop] if att is not None else []
    agent_side = sorted(agent_side, key=lambda e: (PROBLEM_KINDS.index(e.kind), e.line))
    if att is not None and (agent_side or att.untyped):
        out += ["", f"Agent friction (attempt {att.number}; it does not change the outcome by itself):", ""]
        out += [_item_line(r, att, e) for e in (*agent_side, *att.untyped)]
    return [*out, "", RUN_METADATA_HEADING, "", *header]


def _section(title: str, fn: Callable[[], list[str]]) -> list[str]:
    try:
        body = fn()
    except Exception as exc:  # one broken section never hides the others
        body = [f"_This section failed: {type(exc).__name__}: {exc}_"]
    return ["", f"## {title}", "", *body]


def residue(text: str) -> list[str]:
    """Capitalised words right next to a name, organisation, library, folder or source placeholder in
    ``text`` (redacted text): what redaction may have missed (a project's second word, say). A login's home
    ``/Users/<user>/`` is a path prefix like ``~/`` (the folder after it is a path component, not a name's
    second word), and the fixed macOS path components (Users, Library, Application Support, ...) are no
    hit."""
    found: list[str] = []
    for m in _RESIDUE_RE.finditer(_USER_HOME_RE.sub("~/", text)):
        word = m.group(1) or m.group(2)
        if word and word not in _RESIDUE_IGNORED and word not in _GENERIC_FOLDERS and word not in found:
            found.append(word)
    return found


def residue_by_section(report: str) -> list[tuple[str, list[str]]]:
    """:func:`residue` of each ``## `` section of a redacted report (the text before the first heading as
    "top"), in report order, only the sections with a hit."""
    out: list[tuple[str, list[str]]] = []
    title, body = "top", list[str]()
    for line in [*report.splitlines(), "## "]:
        if line.startswith("## "):
            words = residue("\n".join(body))
            if words:
                out.append((title, words))
            title, body = line[3:].strip(), []
        else:
            body.append(line)
    return out


def _redaction_section(red: Redactor, hits: list[tuple[str, list[str]]]) -> list[str]:
    kinds = ", ".join(f"{k} {red.counts[k]}" for k in red.kinds_used()) or "none"
    legend = " · ".join(_LEGEND[k] for k in red.kinds_used() if k in _LEGEND)
    listed = sum(len(found) for _title, found in hits)  # as listed: a word in two sections counts twice
    check = (
        f"Residue check: {listed} capitalised word(s) next to a placeholder in this report ("
        + "; ".join(f"{title}: {', '.join(found)}" for title, found in hits)
        + "); check them."
        if hits
        else "Residue check: no capitalised word next to a placeholder in this report."
    )
    return [
        "",
        "## Redaction",
        "",
        f"{red.total} replacement(s) of {red.values} value(s) ({kinds}).",
        f"Placeholders used: {legend or 'none'}. {_TEMPLATE_NOTE} Read the report before sending it: "
        "anything else confidential (a project name inside a log line, say) is yours to remove.",
        check,
        'Send it: the link on the last line opens a new "Setup report" issue with the Outcome, Run type, '
        "Prompt and Agent fields filled in (nothing else); paste this report into its Setup report field, or "
        "send it privately (docs/deploy/setup-feedback.md in the agentsync repository). Nothing is sent "
        "automatically.",
    ]


_EXCLUDE_LIST_RE = re.compile(r"\bexclude = \[.*?(?:\] in \[\[source\]\]|$)")
"""The ready-to-paste exclude line of ``arm_local.exclude_advice`` (a WAITING ON YOU line, the heartbeat
fix): it names folders below a source root, which the Redactor has never seen. Also a line cut short."""


_SOURCE_LOCAL_RE = re.compile(
    r"""(--source-local[ =])(\$'(?:[^'\\]|\\.)*'?|"(?:[^"\\]|\\.)*"?|'[^']*'?|(?:\\.|[^\s\\'"])+)"""
)
"""``--source-local`` and the shell word after it: ``$'...'``, ``"..."``, ``'...'`` or a bare word with
backslash escapes (install.log's ``args=``, install.out's run header and re-run lines, an agent's own
text)."""
_ANSI_C_RE = re.compile(r"\\(?:([0-7]{1,3})|x([0-9A-Fa-f]{1,2})|(.))", re.DOTALL)
_ANSI_C_LETTERS = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}
_PLACEHOLDER_RE = re.compile(r"(<[a-z]+(?:-\d+)?>)")
_PLACEHOLDERS_ONLY_RE = re.compile(r"(?:<[a-z]+(?:-\d+)?>)+")


def _shell_unquote(word: str) -> str:
    """The text a shell reads ``word`` as, for the forms bash's ``printf %q`` writes and the quotes a person
    types: ``$'...'`` (octal, hex and letter escapes, the bytes read as UTF-8), ``"..."``, ``'...'`` and a
    bare word's backslash escapes. Control characters become spaces (one argument, one line)."""
    if word.startswith("$'"):
        body = word[2:-1] if re.fullmatch(r"\$'(?:[^'\\]|\\.)*'", word) else word[2:]
        raw = bytearray()
        pos = 0
        for m in _ANSI_C_RE.finditer(body):
            raw += body[pos : m.start()].encode("utf-8")
            if m.group(1) is not None:
                raw.append(int(m.group(1), 8) & 0xFF)
            elif m.group(2) is not None:
                raw.append(int(m.group(2), 16))
            else:
                raw += _ANSI_C_LETTERS.get(m.group(3), m.group(3)).encode("utf-8")
            pos = m.end()
        raw += body[pos:].encode("utf-8")
        text = raw.decode("utf-8", errors="replace")
    elif word.startswith('"'):
        text = re.sub(r'\\(["\\$`])', r"\1", word[1:].removesuffix('"'))
    elif word.startswith("'"):
        text = word[1:].removesuffix("'")
    else:
        text = re.sub(r"\\(.)", r"\1", word)
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)


def _source_local_shown(red: Redactor, m: re.Match[str]) -> str:
    """One ``--source-local`` argument as the report shows it. The line is already redacted; the argument is
    unquoted and redacted once more (a name ``printf %q`` wrote as ``$'Z\\303\\274rich'`` only matches once
    decoded), then cut to ``<path>`` at the first path component that is not a placeholder, a fixed macOS
    component, the provider folder or a generic folder. So the argument never shows a folder name the
    Redactor does not know, whatever quoting the shell chose. A word with no quote, slash or backslash is
    not a path (an agent's prose): it is left as it is, and so is the punctuation that closes a bare word
    in prose (a backtick, a bracket, a full stop)."""
    word, tail = m.group(2), ""
    if not re.search(r"""[/\\'"]""", word):
        return m.group(0)
    if word[0] not in "$'\"":
        bare = re.fullmatch(r"((?:\\.|[^\\])*?)([)\]}.,;:`]*)", word, re.DOTALL)
        if bare is not None:
            word, tail = bare.group(1), bare.group(2)
    pieces = _PLACEHOLDER_RE.split(_shell_unquote(word))
    text = "".join(piece if i % 2 else red.redact(piece) for i, piece in enumerate(pieces))
    parts = text.split("/")
    for i, part in enumerate(parts):
        known = (
            part in ("", "~", ".", "..")
            or _PLACEHOLDERS_ONLY_RE.fullmatch(part) is not None
            or part in _PATH_COMPONENTS
            or part in _GENERIC_FOLDERS
            or part.casefold() in _GENERIC_HOME_DIRS
            or (i > 0 and parts[i - 1] == "CloudStorage")
        )
        if not known:
            return m.group(1) + "/".join([*parts[:i], "<path>"]) + tail
    return m.group(1) + text + tail


def _redact_lines(red: Redactor, lines: list[str]) -> str:
    """Redact every line but the report's own headings (a folder named like a section must not break it).
    An exclude line's globs become ``<path>`` first: they are folder names from inside a source. Each
    ``--source-local`` argument is then settled by structure (:func:`_source_local_shown`)."""
    keep = {REPORT_TITLE, *(f"## {t}" for t in SECTION_TITLES)}
    if not red.enabled:
        return "\n".join(lines)
    shown = functools.partial(_source_local_shown, red)

    def one(line: str) -> str:
        line = red.redact(_EXCLUDE_LIST_RE.sub(_exclude_placeholder, line))
        return _SOURCE_LOCAL_RE.sub(shown, line) if "--source-local" in line else line

    return "\n".join(ln if ln in keep else one(ln) for ln in lines)


def _exclude_placeholder(m: re.Match[str]) -> str:
    return "exclude = [<path>]" + (" in [[source]]" if m.group(0).endswith("]]") else "")


def _issue_link(r: _Run, red: Redactor) -> str:
    fr = r.friction
    att = fr.latest if fr is not None else None
    agent = att.header.get("Agent", "") if att is not None else ""
    agent = re.sub(r"[^\w .:/()+,@<>-]", "", red.scrub(" ".join(agent.split())))[:100].strip()
    outcome = r.outcome or compute_outcome(att, r.install_runs)
    return build_issue_url(
        outcome=outcome.form_label,
        run_type=ISSUE_RUN_TYPES.get(r.run_type or ""),
        prompt=_prompt_version(att),
        agent=agent or None,
        loop_stage=r.loop_stage,
    )


def build_report(
    config_path: Path | None = None,
    *,
    hooks: ReportHooks | None = None,
    friction_path: Path | None = None,
    now: datetime | None = None,
    budget_s: float = TIME_BUDGET_S,
) -> tuple[str, Redactor]:
    """The report text and the redactor that produced it (its ``total`` is the redaction count), always
    redacted (KISS K16b: the ``redact`` argument went with ``--no-redact``, its last caller). Never
    raises for a failed probe or section. ``friction_path`` (default :func:`default_friction_path`) is the
    agent's friction log, embedded redacted under :data:`FRICTION_HEADING` and summarised first. The last
    line is the prefilled issue link (:func:`issue_link` returns it)."""
    moment = now or datetime.now(UTC)
    r = _Run(config_path, hooks or ReportHooks(), budget_s)
    if friction_path is not None:
        r.friction_path = expand(friction_path)
    try:
        red = _build_redactor(r)
    except Exception:  # redaction facts are best effort; the patterns (email, GUID, hex) still apply
        red = Redactor()
        red.add("home", str(Path.home()))
    r.red = red
    try:
        r.friction = read_friction(r.friction_path)
    except Exception as exc:
        r.friction_error = f"{type(exc).__name__}: {exc}"
    header: list[str] = [f"- generated at: {moment.strftime('%Y-%m-%dT%H:%M:%SZ')}"]
    header.append(f"- agentsync: {__version__} (python {platform.python_version()}, {sys.executable})")
    try:
        header.append(f"- install source: {_install_source(r)}")
    except Exception as exc:
        header.append(f"- install source: (failed: {type(exc).__name__}: {exc})")
    # Cheap sections first, doctor last (it gets what is left of the budget, less the Loop line's floor), then
    # the friction log (the latest attempt's outcome counts doctor's FAILs); printed in heading order.
    bodies: dict[str, list[str]] = {}
    order: tuple[tuple[str, Callable[[_Run], list[str]]], ...] = (
        ("Environment", _environment),
        ("Installer", _installer),
        ("Configuration", _configuration),
        ("Background runs", _background),
        ("Recent errors", _recent_errors),
        ("Status", _status_section),
        ("Doctor", _doctor),
        ("Agent friction log", _friction_section),
    )
    for title, fn in order:
        bodies[title] = _section(title, functools.partial(fn, r))
    try:  # the Loop line's hook is a slow call: in its own pass, so ``took`` counts it
        r.loop_line = _loop_line(r)
    except Exception as exc:
        r.loop_line = f"- Loop: not read ({type(exc).__name__})"
    header.append(f"- took: {time.monotonic() - r.t0:.1f}s (time limit {budget_s:.0f}s)")
    bodies["Summary"] = _section("Summary", functools.partial(_summary, r, header=header))
    lines = [REPORT_TITLE]
    for title in SECTION_TITLES[:-1]:
        lines += bodies[title]
    main = _redact_lines(red, lines)
    counts = f"{red.total} replacement(s) of {red.values} value(s) (legend under Redaction)"
    main = main.replace(_REDACTION_MARK, counts)
    try:
        link = _issue_link(r, red)
    except Exception:  # the link is a convenience: the bare form always works
        link = ISSUE_URL
    tail = [*_redaction_section(red, residue_by_section(main)), "", link]
    return main + "\n" + "\n".join(tail) + "\n", red


def write_report(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file in the same directory, mode 0600, ``os.replace``);
    creates the parent directory. Raises OSError when it cannot be written."""
    target = expand(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".setup-report.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        Path(tmp).chmod(0o600)
        Path(tmp).replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
