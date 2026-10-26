"""``agentsync`` command line (owner: integrator).

Subcommands: add-source PATH · init (hidden) · sync (hidden options: --once, --mode poll|reconcile|dry_run,
--materialise-budget BYTES) · accept-deletions SOURCE · status · curate · materialise [--budget BYTES]
[PATH ...] (hidden) · adopt SRC_DIR · migrate (hidden, a no-op) · graph login|logout|whoami|discover (also as
top-level login · logout · whoami · discover; login [--device-code]; discover [--url URL ...] [--toml]) ·
install-agent (hidden) · uninstall-agent (hidden) · purge SELECTOR | --queue · compact-history [--keep-days N
(at least 1)] (hidden) · hold · offboard [--purge-data] [--confirm DOCS_REPO] · setup-report (hidden; hidden
--out PATH, default ~/agent-context/setup-report.md) · it-request --out PATH.
Every subcommand accepts ``--config PATH`` (default ~/agent-context/sources.toml) and ``-v/--verbose``.
``sync`` without ``--mode`` ends with the loop's ``NEXT:`` line and any ``WAITING ON YOU:`` lines
(:mod:`agentsync.loop`); ``status``, the single read-only check, starts with them (KISS K08a; ``doctor``
and ``policy show`` are hidden aliases of it). ``AGENTSYNC_NO_NEXT_HINT=1`` (scripts/install.sh sets it) drops
them from both.
``setup-report`` ends with the issue link, never a hint.

C15 section 9 item 32: the macOS trust store is injected into ``ssl`` (truststore) as the very first thing,
before any agentsync module imports ``msal``, ``requests`` or ``httpx``; tests/test_cli.py asserts the order.
"""

from __future__ import annotations

# The trust-store injection must precede every other import (C15 section 9 item 32).
import ssl as _ssl

import truststore as _truststore

if _ssl.SSLContext is not _truststore.SSLContext:  # same effect as agentsync.net.inject_system_trust()
    _truststore.inject_into_ssl()

import argparse
import contextlib
import dataclasses
import errno
import json
import logging
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeGuard

from agentsync import (
    __version__,
    curate,
    gitops,
    governance,
    it_request,
    lints,
    loop,
    net,
    setup_report,
    skill,
)
from agentsync import policy as content_policy
from agentsync.arm_local import ECHOED_ALARM, PROGRESS, exclude_advice, exclude_globs
from agentsync.config import (
    Config,
    SourceConfig,
    append_to_config,
    canonical_source_root,
    default_config_text,
    derive_source_id,
    ensure_inbox,
    load_config,
    local_source_table,
    parse_config,
    parse_size,
    set_source_exclude,
)
from agentsync.convert import CONVERTER_UNAVAILABLE
from agentsync.cycle import (
    _CONVERT_RETRIES,
    ECHOED_WHEN,
    INTERACTIVE_LOCK_WAIT_S,
    NEW_START_UNSAID_META,
    UNRECORDED_LOCK_HOLDER,
    _acquire_waiting,
    _holds_home,
    _kept_by_source,
    _tighten_own_paths,
    quiet_lock_wait,
    run_cycle,
    source_statuses,
    unowned_mirror_pages,
)
from agentsync.errors import (
    AgentSyncError,
    AuthError,
    AuthRequiredError,
    ConfigError,
    LockHeldError,
    LockNotAFileError,
    ManifestLostError,
    ManifestOpenError,
    ManifestPathError,
    ManifestSchemaError,
)
from agentsync.graph import auth as graph_auth  # the module, so a patch of MsalAuth holds (B67, as A91)
from agentsync.graph.auth import TokenProvider
from agentsync.graph.errors import AuthBlockedError
from agentsync.loop import NO_NEXT_HINT_ENV
from agentsync.manifest import (
    ACCESS_ERRNOS,
    LOST_MANIFEST,
    LOST_MANIFEST_EMPTY,
    LOST_MANIFEST_SHORT,
    LOST_MANIFEST_WORDS,
    Manifest,
    lost_error,
    lost_words,
)
from agentsync.model import CycleMode, CycleReport, LintFinding, SourceKind, Verdict
from agentsync.ops import doctor, launchd
from agentsync.ops.lock import EMPTY_BODY_HOLDER, SingleWriterLock, read_heartbeat
from agentsync.paths import (
    DocsLayout,
    StatePaths,
    default_config_path,
    default_state_dir,
    expand,
    is_under,
    provider_link_resolved,
)
from agentsync.publish import Publisher, archive_path, notice_gone_why
from agentsync.slug import collision_key
from agentsync.text import plural

EXIT_OK = 0
EXIT_FAILED = 1  # a source failed, or a blocking lint or curate finding fired
EXIT_USAGE = 2
EXIT_LOCK_HELD = 75  # EX_TEMPFAIL: another cycle holds the lock; says "skipped: lock held by <holder>" (the
# background job's run also logs it as a WARNING, to its err.log; B13 round 19 W13, W30)
EXIT_REAUTH = 77  # EX_NOPERM: auth REAUTH_REQUIRED
EXIT_CONFIG = 78  # EX_CONFIG: sources.toml invalid
EXIT_STATE = 65  # EX_DATAERR: the manifest is no database or a damaged one (doctor's state_dir.manifest)
_ACCESS_FIX = "give this account read and write access to the file and its folder, e.g. owner and mode 600"
_NOT_DAMAGED = "; the file itself is not damaged"
MANIFEST_OPEN_FIX = _ACCESS_FIX + _NOT_DAMAGED
"""The fix of a manifest this account cannot open (``ManifestOpenError``; B13 round 19 W65)."""
EXIT_TCC_PENDING = launchd.EXIT_TCC_PENDING  # 79: only from the signed launcher that wraps a LaunchAgent run

# Commands by their full path, which works with ~/.local/bin off PATH (B13 round 6 G79, as §16.51 Q49); the
# usage line keeps prog="agentsync".
_EPILOG = f"""\
exit codes:
  0   ok
  1   failed: a source failed, a blocking lint fired, curate found a blocking finding, a status check
      failed, a purge/compaction was not verified, discovery was incomplete, or the state file cannot be
      opened by this account (curate rows are not a failure)
  2   usage error (bad arguments)
  65  the state file (manifest.sqlite) is no database or a damaged one: the person's to fix (the message
      names the file and the fix; `{loop.BIN} status` prints it as state_dir.manifest)
  75  skipped: another agentsync cycle holds the single-writer lock (EX_TEMPFAIL; launchd retries later);
      `sync` (not a LaunchAgent's run) first waits up to 10 minutes for it
  77  sign-in required: a Graph source needs `{loop.BIN} graph login` (auth REAUTH_REQUIRED),
      or Entra blocked sign-in (blocked: device|policy|consent|assignment, config-invalid; the message
      names the fix)
  78  configuration invalid: sources.toml or policy.toml is missing or wrong (the message names the key)
  79  (LaunchAgent runs only) TCC_PENDING: the signed launcher's canary or watchdog timed out waiting for a
      macOS privacy prompt; the cycle never ran or was stopped (see the job's .err.log,
      `{loop.BIN} status`)

files: ~/agent-context/sources.toml (config) · ~/agent-context/docs (the docs git repo) ·
~/Library/Application Support/agentsync (manifest, lock, heartbeat, governance/) · ~/Library/Caches/agentsync
"""

_log = logging.getLogger("agentsync.cli")
_TCC_TOKENS = ("TCC_PENDING", "TCC_DENIED")

Handler = Callable[[argparse.Namespace], int]


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    # stdout first: in a pipe it is block-buffered and stderr is not, so a line printed before an error
    # landed after it (B13 round 11 M26)
    with contextlib.suppress(OSError, ValueError):
        sys.stdout.flush()
    sys.stderr.write(text + "\n")


# ---------------------------------------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------------------------------------


def _common(sub_default: object) -> argparse.ArgumentParser:
    """--config / -v, accepted before or after the subcommand. KISS K18: --config (another sources.toml,
    default ~/agent-context/sources.toml) still parses but is hidden from help."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--config", type=Path, default=sub_default, metavar="PATH", help=argparse.SUPPRESS)
    p.add_argument(
        "-v", "--verbose", action="count", default=sub_default, help="more logging (-v info, -vv debug)"
    )
    return p


def _graph_options(p: argparse.ArgumentParser) -> None:
    # KISS K18: --device-code (login: go straight to device-code sign-in, refused unless [graph]
    # allow_device_code = true) still works but is hidden: it is the only probe for Conditional Access.
    p.add_argument("--device-code", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--url",
        action="append",
        default=[],
        metavar="URL",
        help="discover: resolve this SharePoint/OneDrive site or sharing link instead (repeatable)",
    )
    p.add_argument("--toml", action="store_true", help="discover: print only the sources.toml snippet")


def build_parser() -> argparse.ArgumentParser:
    """Return the argparse parser for every subcommand."""
    parser = argparse.ArgumentParser(
        prog="agentsync",
        description="Keep an agent-readable docs/ git repo in sync with OneDrive/SharePoint/Outlook/Teams "
        "and local sync-client folders, processing only what changed.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[_common(None)],
    )
    parser.add_argument("--version", action="version", version=f"agentsync {__version__}")
    common = _common(argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add(name: str, help_text: str, handler: Handler, *, hidden: bool = False) -> argparse.ArgumentParser:
        kwargs: dict[str, Any] = {
            "description": help_text,
            "parents": [common],
            "epilog": _EPILOG,
            "formatter_class": argparse.RawDescriptionHelpFormatter,
        }
        # A hidden command gets no ``help=``: argparse then lists no entry for it (``help=SUPPRESS`` would
        # print "==SUPPRESS==" on Python 3.11), yet it still parses.
        p = sub.add_parser(name, **kwargs) if hidden else sub.add_parser(name, help=help_text, **kwargs)
        p.set_defaults(handler=handler)
        return p

    add(
        "init",
        "create whatever is missing of sources.toml, the docs repo (no remote), its scaffold, the inbox and "
        "the state dir (idempotent; add-source does the same and adds a folder)",
        _cmd_init,
        hidden=True,
    )

    p = add(
        "add-source",
        "sync a folder: append a live local source for it, creating whatever is missing of sources.toml, the "
        "docs repo (no remote), its scaffold, the inbox and the state dir (idempotent)",
        _cmd_add_source,
    )
    p.add_argument("path", type=Path, metavar="PATH", help="the folder to sync (it must exist)")

    p = add("sync", "run one sync cycle (what the launchd agents run)", _cmd_sync)
    # KISS K13b: sync takes no visible option. --once (a no-op: one cycle is the default), --mode (the launchd
    # agents' poll | reconcile; dry_run classifies only) and --materialise-budget BYTES (a download budget
    # per source for this run only; 0, install.sh's first sync, converts every local file and downloads
    # nothing) still parse; --dry-run and --source are deleted.
    p.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--mode", choices=[m.value for m in CycleMode], default=None, help=argparse.SUPPRESS)
    p.add_argument("--materialise-budget", metavar="BYTES", help=argparse.SUPPRESS)

    p = add(
        "accept-deletions",
        "the deletion is real: clear SOURCE's tripped deletion breaker and apply its removals (a pass "
        "that does not list SOURCE in full applies none and keeps the breaker)",
        _cmd_accept_deletions,
    )
    p.add_argument("source", metavar="SOURCE", help="the source id the breaker tripped on")

    p = add(
        "exclude-empty",
        "add SOURCE's empty cloud folders, those holding no mirrored file, to its exclude in sources.toml "
        "(the empty-folder WAITING ON YOU line names this command)",
        _cmd_exclude_empty,
        hidden=True,
    )
    p.add_argument("source", metavar="SOURCE", help="the source id the WAITING ON YOU line names")

    p = add(
        "reconcile",
        f"run one full-enumeration cycle (`{loop.BIN} sync` runs one when due)",
        _cmd_reconcile,
        hidden=True,
    )
    p.add_argument(
        "--source", action="append", default=[], metavar="ID", help="only this source (repeatable)"
    )
    p.add_argument(
        "--accept-deletions",
        action="store_true",
        help=f"old spelling of {loop.BIN} accept-deletions SOURCE (needs --source)",
    )

    add(
        "status",
        "the single read-only check: NEXT, the loop line, every preflight check with its fix (rc 1 on any "
        "FAIL), then policy, holds, queued purges and each source",
        _cmd_status,
    )
    add("doctor", f"renamed: run {loop.BIN} status", _cmd_status_renamed, hidden=True)
    add(
        "curate",
        "the curation work list: every lint finding, mirror changes since the last checkpoint, the refresh "
        "queue, then UNCOVERED mirror pages no curated page cites, then NEXT (rc 1 on a blocking finding or "
        "a state file this account cannot open, never on rows)",
        _cmd_curate,
    )
    for old_name in ("curate-queue", "lint", "refresh-queue"):
        add(old_name, f"renamed: run {loop.BIN} curate", _cmd_curate_renamed, hidden=True)
    add("checkpoint", "run sync (every sync records the checkpoint itself)", _cmd_checkpoint, hidden=True)
    add(
        "install-skill",
        "write the Claude Code skill now (every sync already writes it)",
        _cmd_install_skill,
        hidden=True,
    )

    p = add(
        "materialise",
        "hydrate + convert named files (or pending work) within a byte budget",
        _cmd_mat,
        hidden=True,
    )
    p.add_argument(
        "--budget", metavar="BYTES", help='byte budget for this run, e.g. "500MB" (default: config)'
    )
    p.add_argument("paths", nargs="*", type=Path, metavar="PATH", help="files inside a local/inbox source")

    p = add("adopt", "copy an existing hand-made docs tree into topics/ as hand-written pages", _cmd_adopt)
    p.add_argument("src_dir", type=Path, metavar="PATH")

    add("migrate", "a no-op: every command migrates the manifest itself", _cmd_migrate, hidden=True)

    # KISS K18: graph and its four top-level aliases are hidden (they still parse): on a zero-IT Mac they
    # lead an agent into Entra errors it cannot fix, and published fix strings and docs keep working.
    p = add("graph", "Microsoft Graph sign-in: login | logout | whoami | discover", _cmd_graph, hidden=True)
    p.add_argument("action", choices=["login", "logout", "whoami", "discover"])
    _graph_options(p)
    for name, text in (
        ("login", "sign in to Microsoft Graph: broker, then browser (PKCE), then device code if allowed"),
        ("logout", "remove the cached Graph sign-in (same as `graph logout`)"),
        ("whoami", "show the cached Graph account and sign-in method, offline (same as `graph whoami`)"),
        ("discover", "propose sources.toml tables for every drive, site, channel, chat and mail folder"),
    ):
        _graph_options(add(name, text, _cmd_graph, hidden=True))
        sub.choices[name].set_defaults(action=name)

    # KISS K11a: background sync is optional and operator-owned (docs/deploy), so both are hidden; the
    # intervals come from sources.toml and the Time Machine exclusions always apply.
    add(
        "install-agent",
        "install the poll + reconcile LaunchAgents (optional background sync; no admin rights)",
        _cmd_install,
        hidden=True,
    )
    add("uninstall-agent", "unload and remove both LaunchAgents", _cmd_uninstall, hidden=True)

    p = add("purge", "remove items from the docs repo's whole history, cache and manifest", _cmd_purge)
    p.add_argument(
        "selector",
        nargs="?",
        metavar="SELECTOR",
        help="id=<stable_id> | path=<source glob> | docs=<docs-repo glob>",
    )
    p.add_argument("--source", metavar="ID", help="only items of this source")
    p.add_argument(
        "--reason",
        choices=[r.value for r in governance.PurgeReason],
        default=governance.PurgeReason.OPERATOR.value,
        help="recorded in the audit trail (default: operator)",
    )
    p.add_argument("--queue", action="store_true", help="run every queued purge instead of SELECTOR")
    p.add_argument(
        "--forget",
        metavar="SELECTOR",
        help=(
            "with --queue: take off the queue the entry id=<queued id> (with --source) a run looked up and "
            "found nothing for, with an audit line; nothing is erased"
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be purged; change nothing (an older manifest is still migrated first)",
    )
    p.add_argument("--push", action="store_true", help="force-push the rewrite to an allowed tenant remote")

    p = add(
        "compact-history",
        "squash mirror history older than [governance] history_days",
        _cmd_compact,
        hidden=True,
    )
    p.add_argument("--keep-days", type=int, metavar="N", help=">= 1; default: [governance] history_days")
    p.add_argument("--dry-run", action="store_true", help="report only")

    p = add("hold", "legal/records hold: suspend purge and compaction for a scope", _cmd_hold)
    p.add_argument("scope", nargs="?", metavar="SCOPE", help="'all' or a source id")
    p.add_argument("--reason", help="why (required to set)")
    p.add_argument("--owner", help="records/legal owner setting or releasing the hold")
    p.add_argument("--release", action="store_true", help="release the hold on SCOPE")
    p.add_argument("--list", action="store_true", help="list active holds")

    p = add("offboard", "list (default) or remove every local copy agentsync made", _cmd_offboard)
    p.add_argument("--purge-data", action="store_true", help="also remove the docs repo and sources.toml")
    p.add_argument("--dry-run", action="store_true", help="list only (the default without --confirm)")
    p.add_argument("--confirm", metavar="DOCS_REPO", help="the docs repo path: required to remove anything")

    p = add("policy", f"renamed: run {loop.BIN} status", _cmd_status_renamed, hidden=True)
    p.add_argument("action", choices=["show"])

    p = add(
        "setup-report",
        f"write a redacted Markdown report of this Mac's setup to {setup_report.DEFAULT_OUT} (a summary, "
        "the agent's friction log, environment, installer, doctor, status, background runs, recent errors) "
        f"for the setup feedback loop; read-only, no network, under {setup_report.TIME_BUDGET_S:.0f} s",
        _cmd_setup_report,
        hidden=True,
    )
    # KISS K16a: --no-redact and --friction are deleted (the friction log is $AGENTSYNC_FRICTION_LOG, else the
    # setup prompt's file); --out is hidden, install.sh passes it with its AGENTSYNC_SETUP_REPORT override.
    p.add_argument("--out", type=Path, default=Path(setup_report.DEFAULT_OUT), help=argparse.SUPPRESS)

    p = add(
        "it-request",
        "write the IT request (docs/deploy/it-request.md) as an email draft with this Mac's values filled in "
        "and the fields still open listed first; never sends anything",
        _cmd_it_request,
        hidden=True,
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path(it_request.DEFAULT_OUT),
        metavar="PATH",
        help=f"write the draft here, mode 0600 (default {it_request.DEFAULT_OUT}); never inside the "
        "agentsync checkout or the docs repo",
    )
    return parser


# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------


def _config(args: argparse.Namespace) -> Config:
    path: Path | None = getattr(args, "config", None)
    return load_config(path)


_OVER_BUDGET = "exceeds the per-cycle budget ("
# why the sync line's W files were withheld unread: the cause the stub kinds W holds share (B13 round 9 K33;
# round 10 L50: a binary .msg on this Mac is read, so it is in R, named by the split's K clause; round 13 O34:
# the comma, since "an image or a .msg not on this Mac" read as an image not on this Mac too)
_WITHHELD_WHY = "a file whose label cannot be read: an image, or a .msg not on this Mac"


def _print_report(report: CycleReport, *, run_budget: int | None = None) -> None:
    """Print a cycle's report, ending with one line for the whole run, ``converted N, deferred M online-only``
    (N files read and converted, M online-only files left for a later run's download budget), then ``, read
    again R`` when R files were read again for what their converter has gained (a re-read for a capability of
    the same bytes is never in N; a no-converter stub's re-read is the first read of its bytes, so it is in N
    as well, and `` (F of them a first read: a stub whose bytes had never been read, so also counted as
    converted)`` says how many: B13 round 4 Q28; round 9 K34 reworded it, since "read for the first time",
    said of files read again, said both things of the same files; when all R are first reads the head says so
    instead, ``, first read R (stubs whose bytes had never been read, so also counted as converted)``, since
    "read again" was false of every one: round 10 L34; round 10 L16 named the count both brackets are in; ``a
    stub whose bytes ...`` for R = 1: round 11 M11),
    then `` (of the converted: U unreadable, F failed, R refused)`` when some of the N are UNREADABLE stubs
    (U), conversions that failed (F; §16.45 R2) or files refused after their bytes were read (R: a label rule,
    a .msg whose label cannot be read; B13 round 6 G35, which also prints the split for F or R alone; an inbox
    duplicate is quarantined, never in R: round 7 H49), with ``, K of them withheld: its label cannot be
    read`` (``their labels`` for more than one) after R when K of the R have a stub that says so (a binary
    .msg on this Mac, read and then refused: B13 round 10 L33; "withheld by the label rule" read as if the
    other R were refused for another cause, while the label rule refused them too: B13 round 25 D25), as
    ``; of the converted: ...`` after the Q28 clause, so two parentheses never stand side by side (B13 round 5
    F34), then ``; re-screened S for a rule change (also counted above, K of them unchanged)``: a
    [policy] or secret-rule re-screen converts a file again for that rule change, so it is in N, and counted
    again in S (F35): the bytes its pages were made from, or a stub a label rule withheld by its name that a
    [policy] change queued, this run's or an earlier one's, for which it may be the first read of its bytes
    (B13 round 20 X34, round 21 Y73; never a re-read, so not in R or the run record's reread), then ``; C
    waiting for its converter (it could not start; read once it does)`` when the converter of C files could
    not start: nothing read them, so they are in neither N nor F (B13 round 18
    V13; C, not W, which names the withheld-unread count below: round 19 W47), then ``; Q refused without a
    read (no converter for the type, or a rule that needs no bytes)`` when Q files were refused with no
    byte read: those are in each source line's ``refused=`` but not in N, so
    with R and W the line's refusals add up to the sources' (B13 round 7 H32). Before it, ``; W withheld
    unread by the label rule (a file whose label cannot be read: an image, or a .msg not on this Mac)`` counts
    the files refused with no byte read whose stub says a label rule withheld them, by the page's own test, so
    K + W are the stubs that say so. They are not in Q (W and Q are disjoint), and R + W + Q add up to the
    sources' ``refused=`` (B13 round 8 J29: the one clause named no label rule, so a withheld image read as a
    type with no converter; round 9 K54). The parenthesis names the cause the stub kinds W holds share:
    "without the file" was false for an image on this Mac (round 9 K33), and a binary .msg on this Mac is
    never in W (round 10 L50). With ``run_budget`` (``sync --materialise-budget``) the
    per-file "exceeds the per-cycle budget" alarms are
    one count per source: at 0 every changed online-only file would otherwise get one."""
    commit = report.commit_sha[:12] if report.commit_sha else "none (no content change)"
    counted = plural(len(report.changes), "change")  # a known count needs no "(s)" (B13 round 16 T32, T36)
    if report.commit_sha and not report.changes:
        counted += " (no source changed a mirror page; the commit holds other docs-repo files)"
    # its own line, never on the summary install.sh's awk sums (B13 round 24 B37)
    if report.new_manifest_pages:
        # the background sync's (its poll log alone had it: E74) in the sentence, after the count, never a
        # second bracket after the first (B13 round 27 P47); the "started over " prefix install.sh reads stays
        by = " by the background sync" if report.new_manifest_by_job else ""
        pages = plural(report.new_manifest_pages, "existing mirror page") + by
        gone, notices, kept = (
            report.new_manifest_removed,
            report.new_manifest_notices,
            report.new_manifest_kept,
        )
        if gone or notices or kept:  # not every one came back: say how many went (B13 round 26 E10)
            parts = [f"{report.new_manifest_pages - gone - notices - kept} written back"]
            if gone:
                parts.append(
                    f"{gone} removed ({'its' if gone == 1 else 'their'} file is gone or was not converted)"
                )
            if notices:  # no new deletion: its own clause (B13 round 27 P116)
                parts.append(f"{plural(notices, 'deletion notice')} removed ({notice_gone_why(notices)})")
            if kept:  # present, so no deletion: put back for the next pass (B13 round 27 P117)
                parts.append(f"{kept} kept for {'a file' if kept == 1 else 'files'} still settling")
            _out(f"note: a new manifest was started over {pages}; {', '.join(parts)}")
        else:
            note = f"note: a new manifest was started over {pages}; every file is converted again"
            _out(note)
    _out(f"run {report.run_id} · mode {report.mode.value} · commit {commit} · {counted}")
    for s in report.sources:
        counts = ", ".join(
            f"{k.value}={v}" + (f" ({s.new} new)" if k is Verdict.CHANGED and s.new else "")
            for k, v in sorted(s.counts.items(), key=lambda kv: kv[0].value)
        )
        if s.pass_kind is None:  # no listing this run: no completeness word, no cursor
            bits = [f"skipped ({s.skipped_reason or '-'})"]
        else:
            bits = [s.pass_kind.value, "complete" if s.enumeration_complete else "INCOMPLETE"]
        if s.deferred:
            bits.append(f"{s.deferred} deferred")
        if s.breaker_tripped:
            bits.append("BREAKER TRIPPED")
        # the counts line's deletion_candidate=N is the same on the marking and the removing pass: say which
        if s.absent_marked:
            bits.append(f"{plural(s.absent_marked, 'file')} marked absent")
        if s.absent_removed:
            # its pages are counted nowhere here (a workbook's sheets are pages of one file): "(s)" stays
            bits.append(
                f"{plural(s.absent_removed, 'file')} removed upstream "
                f"({'its' if s.absent_removed == 1 else 'their'} page(s) are now deletion notice(s))"
            )
        if s.pass_kind is not None:
            bits.append("cursor advanced" if s.cursor_advanced else "cursor held")
        _out(f"  {s.source_id}: {' · '.join(bits)}" + (f" · {counts}" if counts else ""))
        over = [a for a in s.alarms if run_budget is not None and _OVER_BUDGET in a]
        for a in s.alarms:
            if a not in over:
                _out(f"    alarm: {a}")
        if over:
            _out(
                f"    {plural(len(over), 'online-only file')} left for a later run (over this run's "
                f"--materialise-budget of {run_budget} bytes)"
            )
        for e in s.errors:
            _out(f"    error: {e}")
    for f in report.lint_findings:
        _out(f"  {'BLOCKING' if f.blocking else 'warning'} {f.code} {f.path}: {f.message}")
    if report.broke_stale_lock:
        _out("  note: a stale lock from a dead run was broken; every source ran a full pass")
    if report.auth_required:
        _out(f"  sign-in required: run `{loop.BIN} graph login` (errors above name any IT action)")
    _print_checkpoint(report)
    if report.legacy_retried:
        # the one-time retry after an upgrade, which only -v logged, so the same old error came back with
        # nothing saying why (B13 round 19 W18); no bare "converted" for install.sh's awk. The move takes
        # every source's failures, and one this sync does not read (--source, paused, out of time) is tried
        # in its own source's syncs, so the tries are counted in those, not "from this sync on" (round 20
        # X53). Split by outcome: the ones this sync made current say so, and the retry words are for the rest
        # (B13 round 21 Y28), the ones it settled as unreadable, refused or removed say that no try is left
        # (round 22 Z55); a ``no converter`` stub a later sync reads once its converter is ready (the OCR
        # engine for an image) is neither settled nor retried, and says so (round 23 A63); each note keeps
        # "an older build gave up on ", which install.sh's grep shows
        cleared = min(report.legacy_cleared, report.legacy_retried)
        settled = min(report.legacy_settled, report.legacy_retried - cleared)
        waiting = min(report.legacy_waiting, report.legacy_retried - cleared - settled)
        if cleared:
            _out(
                f"  note: {plural(cleared, 'conversion')} an older build gave up on "
                f"{'was' if cleared == 1 else 'were'} tried again in this sync and "
                f"{'is' if cleared == 1 else 'are'} now current"
            )
        if settled:
            _out(
                f"  note: {plural(settled, 'conversion')} an older build gave up on "
                f"{'was' if settled == 1 else 'were'} tried again in this sync and "
                f"{'is' if settled == 1 else 'are'} settled; this sync's counts say how (unreadable, refused "
                f"or removed), and {'it is' if settled == 1 else 'they are'} not tried again"
            )
        if waiting:
            one = waiting == 1
            _out(
                f"  note: {plural(waiting, 'conversion')} an older build gave up on "
                f"{'was' if one else 'were'} tried again in this sync and {'is' if one else 'are'} refused "
                f"until a converter for {'its' if one else 'their'} type is ready (the OCR engine for an "
                f"image); the next sync that has one reads {'it' if one else 'them'} again, and "
                f"{'its error' if one else 'their errors'} may show again"
            )
        if n := report.legacy_retried - cleared - settled - waiting:
            tries = plural(_CONVERT_RETRIES, "time")
            whose = "its source" if n == 1 else "their sources"
            are = "is" if n == 1 else "are"
            _out(
                f"  note: {plural(n, 'conversion')} an older build gave up on {are} tried "
                f"again, up to {tries} in the syncs of {whose} from this one on; "
                f"{'its error' if n == 1 else 'their errors'} may show again until those tries are used"
            )
    converted = sum(s.converted for s in report.sources)
    online = sum(s.deferred_online_only for s in report.sources)
    again = f", read again {report.read_again}" if report.read_again else ""
    # No bare "converted" field: install.sh's awk adds the one after it ("converted)" is no such field), and
    # no bare "again" in a bracket: R stays right after "read again" (B13 round 9 K34; setup_report reads
    # both). Each bracket names the count it is in: "the count above" pointed at nothing in install.sh's
    # one-line summary, and at "read again R" before it (B13 round 10 L16)
    if report.read_again and report.read_first == report.read_again:
        # all of them first reads: no "again" at all, and R right after "first read", which install.sh's awk
        # and setup_report read as they read "read again R" (B13 round 10 L34)
        what = "a stub whose bytes" if report.read_again == 1 else "stubs whose bytes"  # B13 round 11 M11
        again = f", first read {report.read_again} ({what} had never been read, so also counted as converted)"
    elif report.read_again and report.read_first:
        again += (
            f" ({report.read_first} of them a first read: a stub whose bytes had never been read, so also "
            "counted as converted)"
        )
    unreadable = sum(s.unreadable for s in report.sources)
    failed = sum(s.conversion_failed for s in report.sources)
    refused = sum(s.refused for s in report.sources)
    # last, so install.sh's and setup-report's "converted N, deferred M online-only" patterns still match
    of_them = ""
    if unreadable or failed or refused:
        # refused after failed, so an older reader's "U unreadable, F failed" still matches
        split = f"of the converted: {unreadable} unreadable, {failed} failed, {refused} refused"
        # no bracket of its own, so the split stays one parenthesis (B13 round 10 L33)
        if withheld_read := sum(s.withheld_read for s in report.sources):
            whose = "its label cannot" if withheld_read == 1 else "their labels cannot"
            split += f", {withheld_read} of them withheld: {whose} be read"
        of_them = f"; {split}" if report.read_again and report.read_first else f" ({split})"
    # No bare "converted", "deferred" or "read again" in it: install.sh's awk adds the number after each.
    rescreened = (
        f"; re-screened {report.rescreened} for a rule change (also counted above, "
        f"{report.rescreened_unchanged} of them unchanged)"
        if report.rescreened
        else ""
    )
    # No bare "converted", "deferred" or "read again" in it either, and nothing when Q is 0 (B13 round 7 H32).
    # A file whose stub says a label rule withheld it is counted apart, by the page's own test, so the line
    # names the cause its page names (B13 round 8 J29), the one an image, and a .msg not on this Mac, share
    # (round 9 K33; round 10 L50); setup_report reads only the prefix up to "label rule".
    withheld = sum(s.withheld_unread for s in report.sources)
    unread = sum(s.refused_unread for s in report.sources)
    unread_tail = "".join(
        f"; {n} {what}"
        for n, what in (
            (withheld, f"withheld unread by the label rule ({_WITHHELD_WHY})"),
            (unread, "refused without a read (no converter for the type, or a rule that needs no bytes)"),
        )
        if n
    )
    # A file whose converter could not start was not read: it is no conversion, so it is not in N or F, and
    # the clause names it with no bare "converted", "deferred" or "read again" (B13 round 18 V13)
    waiting = sum(s.converter_waiting for s in report.sources)
    waits = (
        f"; {waiting} waiting for {'its' if waiting == 1 else 'their'} converter (it could not start; "
        "read once it does)"
        if waiting
        else ""
    )
    tail = f"{again}{of_them}{rescreened}{waits}{unread_tail}"
    _out(f"converted {converted}, deferred {online} online-only{tail}")


def _print_checkpoint(report: CycleReport) -> None:
    """KISS K06: one line on the automatic checkpoint when this run had session topic pages (nothing
    when not); a held checkpoint names every error that holds it."""
    if report.checkpoint == "advanced":
        _out(
            f"checkpoint advanced: curated at {report.checkpoint_detail[:12]}; the next curate lists mirror "
            "changes since here"
        )
        if report.snapshot_tag is not None:
            tag = report.snapshot_tag
            _out(f"  snapshot {tag}: a permanent tag; read a past page with git show {tag}:<path>")
    elif report.checkpoint == "held":
        held = len(report.checkpoint_blockers)
        _out(
            f"checkpoint held: {plural(held, 'curation error')}; {'fix it' if held == 1 else 'fix them'}, "
            "then sync again (every sync retries it)"
        )
        for f in report.checkpoint_blockers:
            _out(f"  ERROR {f.code} {f.path}: {f.message}")
    elif report.checkpoint == "failed":
        _out(
            f"checkpoint not recorded (the sync itself landed; the next sync retries it): "
            f"{report.checkpoint_detail}"
        )


class _HeldAlarms(logging.Filter):
    """Holds back the records :data:`ECHOED_ALARM` or :data:`ECHOED_WHEN` marks, whose words the run's
    report prints; :meth:`log` logs the ones it did not print, once the filter is off."""

    def __init__(self, handlers: Sequence[logging.Handler]) -> None:
        super().__init__()
        self.handlers = list(handlers)
        self.held: list[logging.LogRecord] = []

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, ECHOED_ALARM, False) or getattr(record, ECHOED_WHEN, None):
            self.held.append(record)
            return False
        return True

    def log(self, records: Sequence[logging.LogRecord]) -> None:
        for record in records:
            for h in self.handlers:
                if record.levelno >= h.level:
                    h.handle(record)


def _printed(record: logging.LogRecord, report: CycleReport) -> bool:
    """Whether ``report`` prints what a held ``record`` says: an arm_local alarm always does; a
    :data:`ECHOED_WHEN` record only when its alarm line, its held checkpoint or its re-screen count is
    in this report (B13 round 16 T33, T56: never hidden when the report leaves it out); an ``"error"`` one (a
    converter that could not start) when an error line of this report says a converter was unavailable
    (B13 round 17 U3)."""
    when = getattr(record, ECHOED_WHEN, None)
    if when == "error":
        return any(CONVERTER_UNAVAILABLE in e for s in report.sources for e in s.errors)
    if when == "alarm":
        return any(record.getMessage() in s.alarms for s in report.sources)
    if when == "checkpoint":
        return report.checkpoint == "held"
    if when == "rescreen":  # re-screened this run, none of the [policy] change's files still waiting
        return report.rescreened > 0 and report.rescreen_left == 0
    return bool(getattr(record, ECHOED_ALARM, False))


@contextlib.contextmanager
def _alarms_in_the_report(config: Config) -> Iterator[_HeldAlarms | None]:
    """A run that prints its own report drops the WARNING its alarms were logged with from agentsync's stderr
    handler, since each one prints as that source's alarm line (B13 round 15 S2, S13), and holds back the
    cycle's WARNINGs whose words the report may print (OCR or media helper down, a held checkpoint, a
    [policy] re-screen; round 16 T33, T56), which the caller logs with :func:`_printed` once it has the
    report. The LaunchAgent's job keeps them all: its stderr is the err.log the setup report's Recent errors
    reads, which no alarm line on its out.log matches (None is yielded). A cycle that raises prints no
    report, so the held records are logged then."""
    handlers = [h for h in logging.getLogger().handlers if getattr(h, "_agentsync", False)]
    if not handlers or launchd.started_by_our_job(config):
        yield None
        return
    held = _HeldAlarms(handlers)
    for h in handlers:
        h.addFilter(held)
    try:
        yield held
    except BaseException:
        for h in handlers:
            h.removeFilter(held)
        held.log(held.held)
        raise
    finally:
        for h in handlers:
            h.removeFilter(held)


class StateUnreadableError(AgentSyncError):
    """The manifest is no database or a damaged one (``doctor.manifest_unreadable``): exit 65, one line."""


@contextlib.contextmanager
def _state_readable(config: Config) -> Iterator[None]:
    """A database error whose cause is the manifest itself, no database or a damaged one, becomes one line
    naming the file and the person's fix (exit 65), never a traceback; init and add-source then stop where
    install.sh names the owner, not "fix the error above, then re-run" (B13 round 16 T12). A locked or busy
    database is raised as it was. The read-only probe runs first, so damage under a table this command never
    reads stops it as it FAILs status (B13 round 19 W41: a sync passed over a damaged table that status, which
    probes each table, then called damaged)."""
    db = expand(config.state_paths.db)
    found = doctor.manifest_unreadable(db)
    if found is not None:
        raise StateUnreadableError(f"{db} cannot be read: {found}") from found
    try:
        yield
    except sqlite3.DatabaseError as exc:
        # the manifest is the one database these commands open: its own NOTADB or CORRUPT, or one the
        # read-only probe finds, names it (damage under a table shows only when a command reads that table)
        if not doctor.unreadable_code(exc) and doctor.manifest_unreadable(db) is None:
            raise
        raise StateUnreadableError(f"{db} cannot be read: {exc}") from exc
    # the command read the manifest whole: a record of damage on a file since restored or written goes (B13
    # round 20 X33); status never removes it, as it writes nothing
    doctor.clear_stale_unreadable(db)


LOCK_WAITED_MARK = "lock wait: this sync waited for another sync's lock"
"""The stdout line a quiet lock wait (install.sh's first sync) prints when install.sh's probe did not see the
lock busy, so its install.log note books the wait, not conversion (B13 round 17 U68; round 4 Q29)."""


def _lock_say(line: str) -> None:
    """The lock wait's plain line, on stderr with no logger header (B13 round 17 U105); under the quiet
    wait (said only when install.sh did not announce it, U68) it also marks stdout with
    :data:`LOCK_WAITED_MARK`."""
    _err(line)
    if quiet_lock_wait():
        _out(LOCK_WAITED_MARK)


def _run(config: Config, **kwargs: object) -> int:
    return _run_report(config, **kwargs).exit_code


def _run_report(config: Config, **kwargs: object) -> CycleReport:
    """One cycle, its report printed (:func:`_run` gives its exit code). A foreground run that broke a stale
    lock and then raised says so on stderr, once, beside the error line ``_main`` prints next: its report,
    which carries the note, is never printed, the lock's own line is DEBUG for it, and release empties the
    file. Not for the LaunchAgent's job, whose err.log keeps the WARNING (B13 round 22 Z89)."""
    kwargs.setdefault("say", _lock_say)
    # an interactive sync says a refused heartbeat as its own error line and NEXT (_cmd_sync); every other
    # run, the LaunchAgent's included, as one WARNING (B13 round 23 A84)
    heartbeat_said = bool(kwargs.pop("heartbeat_said", False))
    broke: list[bool] = []
    if not launchd.started_by_our_job(config):
        kwargs.setdefault("on_stale_break", lambda: broke.append(True))
    try:
        with _alarms_in_the_report(config) as held, _state_readable(config):
            report = run_cycle(config, **kwargs)  # type: ignore[arg-type]
    except BaseException:
        if broke:  # not "every source ran a full pass": none may have run, and the next sync runs none
            _err("note: a stale lock from a dead run was broken; this sync stopped before it finished")
        raise
    if held is not None:
        held.log([r for r in held.held if not _printed(r, report)])
    budget = kwargs.get("budget_bytes")
    _print_report(report, run_budget=budget if isinstance(budget, int) else None)
    if report.heartbeat_refused is not None and not heartbeat_said:
        _log.warning("heartbeat not written: %s", _heartbeat_line(config, report.heartbeat_refused))
    if kwargs.get("mode") is not CycleMode.DRY_RUN and report.exit_code in (EXIT_OK, EXIT_FAILED):
        # C15 req 42 outside install-agent: exclude mirror/, .git, the cache and the manifest from Time
        # Machine once they exist (a cheap xattr check; tmutil runs only for a path still missing it).
        try:
            for line in governance.ensure_time_machine_exclusions(config):
                _log.warning("time machine: %s", line)
        except (OSError, AgentSyncError) as exc:
            _log.warning("time machine exclusions not applied: %s", exc)
    return report


def _owner_only_dirs(config: Config) -> list[Path]:
    """Directories that hold tenant data and must not be group/world readable (existing ones).  A cache,
    log or state folder that is the home folder, or holds it, is not one of them, in any spelling: the
    sync leaves it as it is, and so does setup (:func:`agentsync.cycle._holds_home`)."""
    named = (expand(config.cache_dir), expand(config.log_dir), expand(config.state_dir))
    candidates = [
        expand(config.docs_repo),
        expand(config.docs_repo) / ".git",
        *(d for d in named if not _holds_home(d)),
    ]
    home_ctx = expand(config.config_path).parent
    if home_ctx != Path.home() and home_ctx in expand(config.docs_repo).parents:
        candidates.insert(0, home_ctx)
    return [d for d in candidates if d.is_dir() and not d.is_symlink()]


def _refuse_remotes(config: Config, gov: governance.GovernanceConfig) -> list[str]:
    """C15 req 36: the docs repo has no remote unless [governance] names a tenant-owned one."""
    if not (expand(config.docs_repo) / ".git").exists():
        return []
    return governance.remote_policy_findings(config.docs_repo, gov)


# ---------------------------------------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------------------------------------


def _write_config(cfg_path: Path, text: str) -> None:
    """Write a new sources.toml (0600), its folder included."""
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(text, encoding="utf-8")
    cfg_path.chmod(0o600)
    _out(f"wrote {cfg_path}")


def _remote_clause(config: Config, gov: governance.GovernanceConfig) -> str:
    """The docs-repo line's word on its remotes, read from git: ``no remote`` (what init creates), else the
    names, no URL (the governance line beside it shows that, redacted), and whether [governance] refuses or
    allows them. It said "no remote" one line above a refusal of the repo's remote (B13 round 17 U14, U17).
    No "below": the refusal goes to stderr, this line to stdout. Each remote's verdict is the policy's own
    test (``governance.remote_allowed``, the one ``remote_policy_findings`` makes), so when [governance]
    refuses one remote and allows another the line names each, not every remote as refused (B13 round 18
    V58)."""
    repo = expand(config.docs_repo)
    if not (repo / ".git").exists():
        return "no remote"
    names = sorted(gitops.run_git(repo, "remote", check=False).stdout.split())
    if not names:
        return "no remote"
    refused = [
        name
        for name in names
        if not governance.remote_allowed(
            gitops.run_git(repo, "remote", "get-url", name, check=False).stdout.strip(), gov
        )
    ]
    word = "remote" if len(names) == 1 else "remotes"
    if not refused or len(refused) == len(names):
        return f"{word} {', '.join(names)}, {'refused' if refused else 'allowed'} by [governance]"
    allowed = [name for name in names if name not in refused]
    return (
        f"{word} {', '.join(names)}: {', '.join(refused)} refused by [governance], "
        f"{', '.join(allowed)} allowed"
    )


def _ensure_setup(config: Config) -> int:
    """Create whatever is missing for ``config`` (KISS K14, shared by init and add-source): the inbox, the
    docs repo (no remote) and its scaffold, the state dir, owner-only modes, the Time Machine exclusions.
    Exit 1 when the docs repo has a remote [governance] does not allow (C15 req 36), else 0."""
    config = _ensure_inbox(config)
    gov = governance.load_governance(config.config_path)
    created = gitops.ensure_repo(config.docs_repo)
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for d in _owner_only_dirs(config):
        mode = d.stat().st_mode & 0o777
        if mode & 0o077:
            d.chmod(0o700)
            _out(f"tightened {d} to 0700 (was {mode:04o}): it holds tenant data")
    # What is inside them too, as every sync does: install.sh runs status right after this command and
    # stops on a docs_repo.permissions FAIL, so the paths a sync would clear are cleared here first.
    inside = _tighten_own_paths(config)
    if inside:
        _out(
            f"tightened {plural(inside, 'path')} inside the docs repo, the cache or the logs: "
            f"{'it holds' if inside == 1 else 'they hold'} tenant data"
        )
    with _state_readable(config), Manifest(config.state_paths.db) as manifest:
        written = Publisher(config, manifest).ensure_scaffold()
    # The count carries its verb: install.sh calls add-source once per folder, and a bare "0 scaffold
    # file(s)" on the second call read as the first call's files undone. One is "1 scaffold file" (B13
    # round 14 Q39).
    scaffold = f"{plural(len(written), 'scaffold file')} written" if written else "scaffold up to date"
    findings = _refuse_remotes(config, gov)
    remotes = _remote_clause(config, gov)
    _out(f"docs repo {config.docs_repo} ({'created' if created else 'exists'}, {remotes}; {scaffold})")
    for f in findings:
        _err(f"governance: {f}")
    for line in governance.ensure_time_machine_exclusions(config):
        _out(f"time machine: {line}")
    if any(s.kind is not SourceKind.INBOX for s in config.sources):
        _out(f"sources: {', '.join(s.id for s in config.sources)}")
    elif config.sources:  # an inbox is not a source to sync (KISS K05): point at the one verb that adds one
        # a Mac may hold hand-added inbox sources too: they are counted and named (B13 round 11 M13), after a
        # colon, so the line has no two brackets side by side (round 12 N13)
        inbox = [s.id for s in config.sources]
        besides = "the inbox" if len(inbox) == 1 else f"{len(inbox)} inbox sources: {', '.join(inbox)}"
        _out(f"sources: no folder yet besides {besides}{_add_folder_hint()}")
    else:
        _out(f"sources: none yet{_add_folder_hint()}")
    return EXIT_FAILED if findings else EXIT_OK


def _add_folder_hint() -> str:
    """How the no-folder sources line ends: the command that adds a folder, for a bare run; nothing under
    scripts/install.sh (:data:`NO_NEXT_HINT_ENV` is 1 there), whose NEXT line names its own re-run with
    ``--source-local``, so step 2 never gives two commands for one step (B13 round 13 O17: the prompt says not
    to use add-source)."""
    if os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
        return ""
    return f"; run {loop.BIN} add-source <folder>"


def _cmd_init(args: argparse.Namespace) -> int:
    """Hidden since KISS K14 (add-source does the same and adds a folder): idempotent, no options."""
    cfg_path = expand(args.config or default_config_path())
    if cfg_path.exists():
        # loaded first: a config that fails to load is named by its error, never announced as in use
        # (B13 round 11 M26)
        config = load_config(cfg_path)
        _out(f"using existing {cfg_path}")
    else:
        text = default_config_text()
        config = parse_config(text, config_path=cfg_path)  # validate before writing anything
        _write_config(cfg_path, text)
    if _not_a_file_stop(args, config):  # a -shm folder let init's open pass, so it said nothing (E65)
        return EXIT_FAILED
    return _ensure_setup(config)


def _ensure_inbox(config: Config) -> Config:
    """:func:`agentsync.config.ensure_inbox` for init and add-source (KISS K05): prints the inbox it added.
    An inbox that cannot be added is a warning, never a failure of the command that asked for more."""
    try:
        config, added = ensure_inbox(config.config_path)
    except (ConfigError, OSError) as exc:
        _err(f"inbox: not added: {exc}")
        return config
    if added is not None and added.path is not None:
        # The path is stored resolved (canonical_source_root: how a OneDrive link reaches the CloudStorage
        # checks), so a home reached through a link stores /private/tmp/... beside a docs repo and config
        # printed as /tmp/...: the line leads with the form the other lines use and names what is stored
        # (B13 round 4 Q36).
        typed = expand(config.docs_repo).parent / "inbox"
        shown = f"{added.path}" if typed == added.path else f"{typed}, stored as {added.path}"
        _out(f"added inbox source {added.id!r} ({shown}): drop files you save by hand here")
    return config


def _same_folder(configured: Path, path: Path) -> bool:
    """Whether ``path`` (an existing folder) is the folder a source already has as ``configured``. macOS
    takes a name in any case and in either Unicode form for the same folder, so a path typed back in lower
    case, or with a composed é where the disk holds e and an accent, is not a second folder. The key is the
    one the manifest and the docs repo use for "the same path" (:func:`agentsync.slug.collision_key`); the
    file system then confirms it, so on a case-sensitive volume two folders that differ only in case stay
    two (and a configured folder that is gone is not the one that exists). A folder stored through a link
    that is a provider entry in ~/Library/CloudStorage (``OneDrive-Link -> OneDrive-Contoso``) is compared
    by its real name, read with one readlink of that entry (:func:`paths.provider_link_resolved`, B13 round
    6 G39), so typing it by that name is not a second folder."""
    if configured == path:
        return True
    keys = [collision_key(str(provider_link_resolved(p))) for p in (configured, path)]
    if keys[0] != keys[1]:
        return False
    try:
        return configured.samefile(path)
    except OSError:
        return False


def _cmd_add_source(args: argparse.Namespace) -> int:
    """KISS K14: the one setup verb. Validates PATH before writing anything; a missing sources.toml is
    written from the template with the folder's table; then :func:`_ensure_setup` creates the rest. A
    folder a source already has, in any spelling macOS takes for it (:func:`_same_folder`), is left as it
    is."""
    cfg_path = expand(args.config or default_config_path())
    fresh = not cfg_path.exists()
    template = default_config_text()
    # A broken sources.toml exits 78 before anything is written; a missing one is checked as the template.
    config = parse_config(template, config_path=cfg_path) if fresh else load_config(cfg_path)
    raw: Path = args.path
    path = canonical_source_root(expand(raw))  # a symlinked cloud root stays protected
    if not path.exists():
        _err(f"add-source {raw}: no such folder")
        return EXIT_USAGE
    if not path.is_dir():
        _err(f"add-source {raw}: not a directory")
        return EXIT_USAGE
    docs = expand(config.docs_repo)
    # Compared as written and canonical: the source path is canonical, docs_repo is not (e.g. /var vs
    # /private/var, or a symlinked ~/agent-context).
    if any(is_under(path, d) or is_under(d, path) for d in {docs, canonical_source_root(docs)}):
        _err(f"add-source {raw}: {path} and the docs repo {docs} must not contain each other")
        return EXIT_USAGE
    # PATH is the inbox folder of a config with no inbox (one from before K05): the inbox first, so that
    # folder becomes kind "inbox", never a local source install.sh would count as a folder to sync.
    if path == canonical_source_root(docs.parent / "inbox") and not any(
        s.kind is SourceKind.INBOX for s in config.sources
    ):
        if fresh:
            _write_config(cfg_path, template)
            fresh = False
        config = _ensure_inbox(config)
    known = next((s for s in config.sources if s.path is not None and _same_folder(s.path, path)), None)
    typed = expand(raw)
    typed = typed if typed.is_absolute() else Path.cwd() / typed
    if known is not None:
        # The folder as typed when it is not the stored path (a provider link, another case or Unicode form,
        # /tmp as /private/tmp), so the line says why two paths are one folder; after the config path, which
        # "has path <stored> in " ends with, and naming no cause, since several reach here (B13 round 8 J35).
        same = "" if typed == known.path else f": {typed} is the same folder by another name or path"
        _out(
            f"already configured: source {known.id!r} ({known.kind.value}, {known.state.value}) has path "
            f"{known.path} in {config.config_path}{same}"
        )
    else:
        sid = derive_source_id(path, {s.id for s in config.sources})
        table = local_source_table(sid, path)
        try:
            if fresh:
                text = template.rstrip("\n") + "\n" + table
                config = parse_config(text, config_path=cfg_path)  # validate before writing anything
                _write_config(cfg_path, text)
            else:
                config = append_to_config(config.config_path, table)  # validated before anything is written
        except ConfigError as exc:
            _err(f"add-source {raw}: refused, sources.toml would not load: {exc}")
            return EXIT_USAGE
        # The folder as typed when the stored form differs (a link resolved, /tmp as /private/tmp), as the
        # inbox line says it (Q36; B13 round 5 F49): the table below names only the stored one.
        note = "" if typed == path else f" ({typed}, stored as {path})"
        _out(f"added source {sid!r} to {config.config_path}{note}:")
        _out(table.strip("\n"))
    return _ensure_setup(config)


def _cmd_sync(args: argparse.Namespace) -> int:
    """One cycle; without ``--mode`` (an agent's or a person's run, never a LaunchAgent's) the report ends
    with the loop's NEXT and WAITING ON YOU lines, unless :data:`NO_NEXT_HINT_ENV` is 1 (KISS K01)."""
    config = _config(args)
    if _not_a_file_stop(args, config, manifest=False, lost=True):  # the lock, heartbeat, governance (E38)
        return EXIT_FAILED
    mode = CycleMode(args.mode) if args.mode else None
    hints = mode is None and os.environ.get(NO_NEXT_HINT_ENV, "").strip() != "1"
    if args.materialise_budget is None:
        report = _run_report(config, mode=mode, heartbeat_said=hints)
    else:
        budget = parse_size(args.materialise_budget, where="--materialise-budget")
        report = _run_report(config, mode=mode, budget_bytes=budget, heartbeat_said=hints)
    if hints and report.heartbeat_refused is not None:
        # a heartbeat this account cannot open: said once, as status's [FAIL] heartbeat line gives its fix,
        # and the sync exits 1 so the setup loop's state-file step meets the agent's chmod (round 23 ruling
        # 5; B13 round 23 A1, A34, A84: it exited 0 with four WARNING lines and the loop's NEXT)
        said = f"error: state: {_heartbeat_line(config, report.heartbeat_refused)}"
        _err(said)
        _out(loop.NEXT_PREFIX + HEARTBEAT_REFUSED_STEP)
        # the loop's WAITING ON YOU lines, which the empty-folder alarm points at "at the end of a sync's
        # output", but not its NEXT (a second, contrary one) or its notes ("sync again" before status), and
        # quiet, so stderr keeps its one line (B13 round 24 B43)
        for line in loop.next_lines(config, fixes=_unavailable_steps(config, report), quiet=True):
            if line.startswith(loop.WAIT_PREFIX):
                _out(line)
        _record_sync_stop(args, said, config.state_paths.heartbeat)
        return report.exit_code or EXIT_FAILED
    if hints:
        for line in loop.next_lines(config, fixes=_unavailable_steps(config, report)):
            _out(line)
    return report.exit_code


HEARTBEAT_REFUSED_STEP = (
    "this sync ran, but this account cannot open its heartbeat file, a state file: run "
    f"`{loop.BIN} status` and do what its NEXT says; its [FAIL] line names the fix"
)
"""The NEXT of an interactive sync whose heartbeat this account cannot open (round 23 ruling 5). Not
:data:`STATE_REFUSED_STEP`, whose "so this sync did not run" is false here: the sync ran and published its
changes (B13 round 23 A1, A34, A84). The loop's WAITING ON YOU lines follow it, never its NEXT (B13 round 24
B43)."""


def _heartbeat_line(config: Config, exc: PermissionError) -> str:
    """``<path> cannot be opened: <reason> (fix: <fix>)`` for a heartbeat this account cannot open, with the
    fix status's [FAIL] heartbeat line gives it (``doctor.state_path_fix``: this account's ``chmod 600``,
    another account's chown, the person's)."""
    path = expand(config.state_paths.heartbeat)
    root = expand(config.state_paths.root)
    fix = doctor.state_path_fix(Path(os.path.realpath(root)), Path(os.path.realpath(path)))
    return f"{path} cannot be opened: {exc.strerror or exc} (fix: {fix})"


def _unavailable_steps(config: Config, report: CycleReport) -> list[str]:
    """Rule 1's fix when a file's converter could not start in this sync and the pandoc check fails: the
    file's error line said why it was not converted while the NEXT went on to the baseline step (B13 round 17
    U3). It names status, whose [FAIL] pandoc line carries the fix and whose NEXT says whose it is; the sync
    prints no check line. The cycle converts the file once the converter starts. A cycle whose converter could
    not start for a file with no error line (a stub an older build settled, which only the re-read pass reads)
    gets it too (``CycleReport.converter_down``, B13 round 18 V57)."""
    unavailable = any(CONVERTER_UNAVAILABLE in e for s in report.sources for e in s.errors)
    if not (unavailable or report.converter_down):
        return []
    if all(r.ok for r in doctor._check_pandoc(config)):
        return []
    return [
        "the pandoc check failed (a file's converter could not start in this sync, so it is not converted "
        f"yet): run `{loop.BIN} status`, whose [FAIL] pandoc line names the fix"
    ]


def _cmd_reconcile(args: argparse.Namespace) -> int:
    config = _config(args)
    if args.accept_deletions and not args.source:
        _err(f"--accept-deletions needs a source: run {loop.BIN} accept-deletions SOURCE")
        return EXIT_USAGE
    if _not_a_file_stop(args, config, manifest=False, lost=True):  # sync's guard (P43, P136; C172)
        return EXIT_FAILED
    accept = tuple(args.source) if args.accept_deletions else ()
    return _run(
        config,
        mode=CycleMode.RECONCILE,
        only=tuple(args.source),
        accept_deletions=accept,
        recordings="none",  # an operator's run, which a tool's timeout can end: it reads no recording
    )


def _cmd_accept_deletions(args: argparse.Namespace) -> int:
    """The operator asserts SOURCE's absent files really were deleted: a RECONCILE of that source alone that
    clears its breaker and applies the held removals (the breaker itself is unchanged).  A pass that leaves a
    known file unlisted confirms no absence: it applies no deletion, keeps the breaker and says so.  Like
    interactive sync it waits for a running cycle's lock: launchd never re-runs an operator's assertion after
    exit 75."""
    config = _config(args)
    if _not_a_file_stop(args, config, manifest=False, lost=True):  # sync's guard (P43, P136; C172)
        return EXIT_FAILED
    return _run(
        config,
        mode=CycleMode.RECONCILE,
        only=(args.source,),
        accept_deletions=(args.source,),
        wait_for_lock=True,
        recordings="none",
    )


def _cmd_exclude_empty(args: argparse.Namespace) -> int:
    """Add SOURCE's empty cloud folders to its ``exclude`` in sources.toml (B13 round 4 Q71): the folders its
    last walk stored as zero-child (manifest meta, never a folder read here) that today's exclude does not
    prune and that hold no mirrored file, all of them, each as ``/<path>/``. A folder that held mirrored
    files is left as it is: excluding it would retire its pages at once, with no deletion check. The file is
    edited as text and checked before it is written (:func:`config.set_source_exclude`); under the cycle's
    lock, as an interactive sync waits for it. Nothing to exclude exits 0; a refused edit prints the line to
    paste instead and exits 78. sources.toml is read again once the lock is held, and the write refuses an
    ``exclude`` that changed after that read (B13 round 5 F70)."""
    config = _config(args)
    src = _exclude_empty_source(config, args.source)
    if src is None:
        return EXIT_USAGE
    where = loop._shown(config.config_path)
    db = config.state_paths.db
    if not db.is_file():
        _out(f"exclude-empty {src.id}: nothing to exclude: no sync has stored an empty cloud folder yet")
        return EXIT_OK
    lock = SingleWriterLock(config.state_paths.lock, "exclude-empty")
    _acquire_waiting(lock, INTERACTIVE_LOCK_WAIT_S, say=_lock_say)
    try:
        # the wait can be minutes: an exclude edited by hand meanwhile is read again, not written over (F70)
        try:
            config = load_config(config.config_path)
        except ConfigError as exc:
            _err(f"exclude-empty {args.source}: sources.toml does not load: {exc}")
            return EXIT_CONFIG
        src = _exclude_empty_source(config, args.source)
        if src is None:
            return EXIT_USAGE
        with Manifest(db) as manifest:
            found = loop._empty_dirs(manifest, src)
            below = found.below
            # A folder's glob that would also prune a folder holding mirrored files (a "[" is written as
            # "?") is left as well: excluding that folder would retire its pages at once. The loop's wait
            # names this command by the same rule (B13 round 5 F65).
            clear, collides = loop._excludable(found)
        gone = len(below) - len(clear)
        # the noun the alarm's folders take, by the stored count it names, as the loop's waits (B13 round 17
        # U49; round 12 N40's "it"/"them" had no noun before it)
        left = "the folder" if found.stored == 1 else "the folders"
        # the alarm is from the walk before this exclude (or one by hand since): it names folders now excluded
        # (B13 round 15 S32), so the pointer says so, as the loop's waits do
        since = bool(clear) or len(below) < found.stored
        if found.stored > 5:
            # past five the alarm names five and how to list the rest (B13 round 14 Q41's rule), and the left
            # folder may be among the rest: the loop's own words, as its gone wait says (round 15 S42: "names
            # it, up to five" of a folder the alarm does not name)
            left = loop._alarm_names("the folders", found.stored)
        # Each cause in its own words, with its own count, as the loop's waits say them (B13 round 16 T38: one
        # hedge, "held file(s) ..., or its exclude would also match ...", for both); noun, verb and pronoun
        # by the count (B13 round 14 Q42, Q57). The gone folders' files are summed: theirs may differ.
        kept = [d for d, n in below.items() if n]
        n_files = sum(below[d] for d in kept)
        # one file is one page (B13 round 17 U51); "its" stays the folder's, never "their"; two or more kept
        # folders are "them" and "those pages", whatever each held (B13 round 18 V43)
        if len(kept) == 1:
            retires = (
                f"(excluding such a folder retires {'its page' if n_files == 1 else 'its pages'} at once, "
            )
        else:
            retires = "(excluding them retires those pages at once, "
        retires += "with no deletion check)"
        parts: list[str] = []
        if kept and collides:
            # both causes in one sentence: one head with the total, then each cause with its own count, noun
            # and verb (B13 round 17 U50: the head said twice read as a stutter; the total is `gone`, which
            # the pointer after it is keyed on)
            two = len(collides) == 1
            parts.append(
                f"{gone} empty cloud folders in {src.id} were left as they are: {len(kept)} "
                f"held {loop._files(n_files)} the mirror still has {retires}; "
                f"{len(collides)} "
                + (
                    "has a [ in its name, so an exclude of it"
                    if two
                    else "have a [ in the name, so an exclude of one"
                )
                + " would also match a folder whose files are mirrored: rename or remove "
                f"{'it' if two else 'them'} in the cloud drive"
            )
        elif kept:
            one = len(kept) == 1
            parts.append(
                f"{len(kept)} empty cloud {'folder' if one else 'folders'} in {src.id} "
                f"{'was left as it is: it' if one else 'were left as they are: they'} held "
                f"{loop._files(n_files)} the mirror still has {retires}"
            )
        elif collides:
            one = len(collides) == 1
            parts.append(
                f"{len(collides)} empty cloud {'folder' if one else 'folders'} in {src.id} "
                + (
                    "was left as it is: it has a [ in its name, so an exclude of it"
                    if one
                    else "were left as they are: they have a [ in the name, so an exclude of one"
                )
                + " would also match a folder whose files are mirrored: rename or remove the empty "
                f"{'folder' if one else 'folders'} in the cloud drive"
            )
        held = (
            "; "
            + "; ".join(parts)
            + "; "
            + (loop._named_since(config, src.id, gone) if since else loop._named(config, [src.id], left))
            if gone
            else ""
        )
        if not clear:
            _out(f"exclude-empty {src.id}: nothing to exclude{held}")
            return EXIT_OK
        exclude = exclude_globs(src, clear)
        try:
            set_source_exclude(config.config_path, src.id, exclude, expected=src.exclude)
        except ConfigError as exc:
            _err(f"exclude-empty {src.id}: sources.toml was not changed: {exc}")
            _out(f"instead, {exclude_advice(src, clear, where)}")
            return EXIT_CONFIG
    finally:
        lock.release()
    listed = ", ".join(json.dumps(g, ensure_ascii=False) for g in exclude)
    _out(
        f'exclude-empty {src.id}: wrote exclude = [{listed}] in [[source]] id = "{src.id}" in {where} '
        f"({len(clear)} empty cloud {'folder' if len(clear) == 1 else 'folders'} added); an excluded "
        "folder is not mirrored if it later gains "
        # the folders by their count (B13 round 12 N40: "without them" after one folder, beside "files")
        f"files; the next sync lists {src.id} without {'that folder' if len(clear) == 1 else 'those folders'}"
        f"{held}"
    )
    return EXIT_OK


def _exclude_empty_source(config: Config, source_id: str) -> SourceConfig | None:
    """The local or inbox source ``exclude-empty`` edits, or None after printing why there is none."""
    src = next((s for s in config.sources if s.id == source_id), None)
    if src is None or src.kind not in (SourceKind.LOCAL, SourceKind.INBOX):
        _err(f"exclude-empty {source_id}: no local or inbox source with that id in {config.config_path}")
        return None
    return src


def _cmd_mat(args: argparse.Namespace) -> int:
    config = _config(args)
    if _not_a_file_stop(args, config, manifest=False, lost=True):  # sync's guard (P43, P136; C172)
        return EXIT_FAILED
    budget = parse_size(args.budget, where="--budget") if args.budget is not None else None
    # an online-only recording whose download failed in this run is named once, by the cycle's alarm
    return _run(config, mode=CycleMode.POLL, budget_bytes=budget, materialise_paths=tuple(args.paths))


_LOG_TAIL_BYTES = 64 * 1024
_CLEARED_TOKENS = ("CANARY_OK", "CHILD_EXIT")


def _log_tail(path: Path, limit: int = _LOG_TAIL_BYTES) -> list[str]:
    """The last lines of ``path`` from at most ``limit`` trailing bytes (never the whole file)."""
    with path.open("rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        fh.seek(max(0, size - limit))
        data = fh.read(limit)
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > limit else lines  # the first line of a mid-file window is partial


_CANARY_EVENTS = (*_TCC_TOKENS, *_CLEARED_TOKENS)
_LAUNCHER_PID = re.compile(r"agentsync-launcher\[(\d+)\]")
_CANARY_PATH = re.compile(r'\bpath=("(?:[^"\\]|\\.)*"|\S+)')


@dataclasses.dataclass(frozen=True, slots=True)
class _LauncherRun:
    """A job's newest launcher run (its newest canary event and the events just before it with the same pid):
    when that event was logged, and its TCC_PENDING / TCC_DENIED lines that no later line of the run cleared.
    The launcher logs one canary line per protected path and keeps going after TCC_DENIED, so a CANARY_OK
    clears only its own path; CHILD_EXIT clears them all (the child ran)."""

    stamp: datetime | None
    pending: tuple[str, ...]


def _newest_launcher_run(config: Config, suffix: str) -> _LauncherRun | None:
    """:class:`_LauncherRun` of the ``suffix`` job's stderr log; None when it logged no canary event."""
    log_path = expand(config.log_dir) / f"{config.launchd_label_prefix}.{suffix}.err.log"
    try:
        lines = [ln.strip() for ln in _log_tail(log_path)]
    except OSError:
        return None
    events = [ln for ln in lines if any(t in ln for t in _CANARY_EVENTS)]
    if not events:
        return None

    def pid(line: str) -> str | None:
        m = _LAUNCHER_PID.search(line)
        return m.group(1) if m is not None else None

    newest = pid(events[-1])
    start = len(events) - 1
    while start > 0 and pid(events[start - 1]) == newest:
        start -= 1
    pending: dict[str, str] = {}
    for line in events[start:]:
        m = _CANARY_PATH.search(line)
        key = m.group(1) if m is not None else ""
        if any(t in line for t in _TCC_TOKENS):
            pending[key] = line
        elif "CANARY_OK" in line:
            pending.pop(key, None)
        else:  # CHILD_EXIT
            pending.clear()
    return _LauncherRun(_parse_log_time(events[-1].split(" ", 1)[0]), tuple(pending.values()))


def _launcher_events(config: Config, *, now: datetime | None = None) -> list[str]:
    """Each job's TCC_PENDING / TCC_DENIED launcher lines of its newest run that no later line of that run
    cleared, with their age (an older run's event is not a current problem)."""
    moment = now or datetime.now(UTC)
    out: list[str] = []
    for suffix in ("poll", "reconcile"):
        run = _newest_launcher_run(config, suffix)
        for line in run.pending if run is not None else ():
            stamp = _parse_log_time(line.split(" ", 1)[0])
            age = f" ({_age_text(moment - stamp)} ago)" if stamp is not None else ""
            out.append(f"{suffix}: {line}{age}")
    return out


def _parse_log_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _age_text(delta: timedelta) -> str:
    secs = max(0, int(delta.total_seconds()))
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 172800:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _status_lines(config: Config, *, checks_beside: bool = True) -> list[str]:
    """What ``agentsync status`` prints (read-only); ``setup-report`` embeds the same lines. ``checks_beside``
    False (the setup report's Status, whose Doctor prints only the lines that are not ok): a line that points
    at an [ok] check line beside it gives that line's words itself (B13 round 28 I18)."""
    out: list[str] = [f"config: {config.config_path}", f"docs repo: {config.docs_repo}"]
    lock_path = config.state_paths.lock
    odd = doctor.own_kind(lock_path)  # never "free" over a folder, fifo, socket or link (E38)
    held = odd is None and lock_path.exists() and SingleWriterLock.is_held(lock_path)
    if odd is not None:
        out.append(f"lock: {odd}, not a file; the lock line says why")
    else:
        out.append(f"lock: {SingleWriterLock.describe(lock_path) if held else 'free'}")
    # B13 round 18 V10: a [governance] that does not load hides only the lines that need it (HOLD, retention);
    # the manifest's ``last runs:`` and the sources below still print, so the setup report reads the loop
    gov: governance.GovernanceConfig | None = None
    try:
        gov = governance.load_governance(config.config_path)
    except Exception as exc:  # its [FAIL] governance.config line says why
        out.append(
            f"status: cannot read the state: {_unread_words(exc, refused='governance.config', config=config)}"
        )
    # a governance file that is not a file: one line each, and the rest still print (B13 round 26 E43); read
    # apart, so a queue that is not a file never hides the holds read, and a hold list that is not one never
    # hides sources.toml's own hold (B13 round 27 P44)
    holds: Sequence[governance.Hold] = ()
    if gov is not None:
        try:
            holds = governance.active_holds(config.state_paths.root, gov)
        except governance.GovernanceUnreadError as exc:
            out.append(f"status: cannot read the state: {_unread_words(exc, config=config)}")
            holds = governance.config_holds(gov)
    try:
        queued, kept = governance.partition_purges(config.state_paths, config=config)
    except governance.GovernanceUnreadError as exc:
        line = f"status: cannot read the state: {_unread_words(exc, config=config)}"
        if line not in out:  # governance/ itself: one line, not one per file under it (I89)
            out.append(line)
        queued, kept = [], []
    for h in holds:
        out.append(f"HOLD: {h.describe()} (purge and compaction suspended)")
    if queued and governance.hold_list_unread(config.state_paths.root):
        # read apart from the holds (P44), so a hold list that cannot be read invites no run here (P131)
        out.append(
            f"queued purges: {len(queued)} (not run while the hold list cannot be read: see governance.hold)"
        )
    elif queued:
        runnable, on_hold = _held_split(config, gov, queued)
        if on_hold:  # the records owner's move, never a run that refuses (B13 round 28 I91)
            out.append(f"queued purges held: {len(on_hold)} ({governance.held_words(on_hold)})")
        if runnable:
            exits = "; the run exits 1 with the held ones still queued" if on_hold else ""
            out.append(
                f"queued purges: {len(runnable)} (preview with `{loop.BIN} purge --queue --dry-run` before "
                f"`{loop.BIN} purge --queue`{exits})"
            )
    # the count alone: the governance.purge_queue line beside it gives the sentence (B13 round 27 P118)
    if kept and not checks_beside:  # B13 round 28 I18: no [ok] line beside it in the report to point at
        out.append(f"queued purges kept: {len(kept)}: {'; '.join(governance.kept_lines(kept))}")
    elif kept:
        out.append(f"queued purges kept: {len(kept)} (the governance.purge_queue line says why)")
    if gov is not None:
        try:
            cstate, cdetail = governance.compaction_state(config.docs_repo, gov)
        except AgentSyncError as exc:
            cstate, cdetail = "unknown", str(exc)
        out.append(f"retention: {cstate}: {cdetail}")
    for event in _launcher_events(config):
        out.append(f"launcher: {event}")
    db = config.state_paths.db
    lost = lost_words(expand(db))
    # a file there but too short is "empty or too short", never "missing" (B13 round 28 I92)
    state = "empty or too short" if lost in LOST_MANIFEST_SHORT else "missing"
    if lost == LOST_MANIFEST:  # never "no sync has run" over a heartbeat that records passes (A29)
        out.append("manifest: missing (earlier syncs recorded; the person decides: see state_dir.manifest)")
        return out
    if lost == LOST_MANIFEST_EMPTY:
        decides = "the person decides: see state_dir.manifest"
        out.append(f"manifest: empty or too short (earlier syncs recorded; {decides})")
        return out
    if lost is not None:  # a heartbeat.json that cannot be read rules no earlier sync out (B13 round 27 P111)
        unread = "heartbeat.json cannot be read; the person decides: see state_dir.manifest"
        out.append(f"manifest: {state} ({unread})")
        return out
    if doctor.manifest_refused(expand(db)):
        # a link to nothing in a folder it cannot use, or a loop: never "the next sync starts a new one"
        # beside the FAIL that sync stops on (B13 round 26 E41)
        out.append("manifest: none (the next sync stops on it; the person decides: see state_dir.manifest)")
        return out
    if not db.exists():
        # over the pages earlier syncs wrote (the person's new start, heartbeat.json aside), "no sync has run"
        # was false; the sync's own note counts the same pages (B13 round 25 D26). "manifest: none yet" stays
        # first: the setup report reads it as no run recorded, which is still true
        try:
            pages = len(unowned_mirror_pages(config))
        except OSError:
            pages = 0
        if pages:
            over = f"the next sync starts a new one over {plural(pages, 'existing mirror page')}"
            out.append(f"manifest: none yet ({over}; every file is converted again)")
        else:
            out.append("manifest: none yet (no sync has run)")
        return out
    with Manifest(db) as manifest:
        runs = manifest.last_runs(5)
        out.append(
            "last runs: " + ("; ".join(f"{r} {m} {s} {(c or '-')[:12]}" for r, m, s, c in runs) or "none")
        )
        out += _new_start_unsaid_line(config, manifest.get_meta(NEW_START_UNSAID_META))
        out += _new_start_kept_lines(_kept_by_source(config, manifest))
        statuses = source_statuses(config, manifest, now=datetime.now(UTC), quiet=True)
        absent = manifest.absent_counts()  # not in SourceStatus: that feeds the committed snapshot
    try:
        heartbeat = read_heartbeat(config.state_paths.heartbeat)
        never = "never"
    except ValueError:  # corrupt: the [warn] heartbeat line says why, and whether a pass rewrites it (I62).
        # Never "cannot read the state: ValueError" in place of every line here (B13 round 24 B88, B90)
        heartbeat, never = {}, "unknown (heartbeat.json is not valid; the heartbeat line says why)"
    for st in statuses:
        hb = heartbeat.get(st.source_id, {})
        waiting = absent.get(st.source_id, 0) if st.breaker == "ok" else 0  # tripped: nothing is removed
        out.append(
            f"  {st.source_id} ({st.kind.value}, {st.state}): baseline "
            f"{'complete' if st.baseline_complete else 'INCOMPLETE'} · complete "
            f"{'yes' if st.enumeration_complete else 'no'} · live {st.live} (dataless {st.dataless}) · "
            f"quarantined {st.quarantined} · refused {st.refused} · deferred {st.deferred} · "
            f"breaker {st.breaker} · auth {st.auth} · "
            f"last success {hb.get('last_success_at', never)}"
            + (
                f" · absent {plural(waiting, 'file')} ({'its' if waiting == 1 else 'their'} page(s) become "
                "deletion notice(s) on a later complete pass if still missing)"
                if waiting
                else ""
            )
        )
    state_md = config.layout.state_md
    if state_md.is_file():
        out.append(f"details: {state_md}")
    return out


_RECORDINGS_UNDER_LABELS = (
    "; recordings are converted on this Mac under it (a recording's label cannot be read)"
)
"""The clause ``status`` adds to the ``labels_active: true`` line while ``[convert] recordings`` is on."""


def _policy_lines(config: Config) -> list[str]:
    """The effective content policy (what ``policy show`` printed; a broken policy is the ``policy`` FAIL)."""
    try:
        pol = content_policy.load_policy(config)
    except ConfigError:
        return ["policy: invalid (see the [FAIL] policy line)"]
    sidecar = config.config_path.parent / content_policy.POLICY_FILE_NAME
    # Spec S0 rule 1 (ruling 2): a label rule does not stop recordings, whose label cannot be read locally.
    recordings = _RECORDINGS_UNDER_LABELS if pol.labels_active and config.convert.recordings else ""
    return [
        f"policy: {config.config_path} [policy]" + (f" + {sidecar}" if sidecar.is_file() else ""),
        f"  labels_active: {'true' if pol.labels_active else 'false'}{recordings}",
        f"  exclude_label_ids: {', '.join(pol.exclude_label_ids) or '-'}",
        f"  exclude_label_names: {', '.join(pol.exclude_label_names) or '-'}",
        f"  refuse_unlabelled: {'true' if pol.refuse_unlabelled else 'false'}",
        f"  fingerprint: {pol.fingerprint()}",
        "  always: encrypted Office (EncryptedPackage) and encrypted PDFs become UNREADABLE stubs",
    ]


@dataclasses.dataclass(frozen=True, slots=True)
class _Status:
    """What ``status`` prints, in order (KISS K08a): the loop's NEXT / WAITING ON YOU / note lines, the one
    loop line, every check (doctor's ``[FAIL] name — detail (fix: ...)`` lines, byte for byte), then the
    detail lines (lock, holds, queued purges, retention, launcher events, last runs, each source with its
    breaker, the policy)."""

    loop: tuple[str, ...]
    line: str
    checks: tuple[doctor.CheckResult, ...]
    detail: tuple[str, ...]

    @property
    def failed(self) -> bool:
        return any(not r.ok and r.severity is doctor.Severity.ERROR for r in self.checks)

    def check_lines(self) -> list[str]:
        return doctor.format_results(list(self.checks)).splitlines()

    def lines(self) -> list[str]:
        return [*self.loop, self.line, *self.check_lines(), *self.detail]


def _fail_step(result: doctor.CheckResult) -> str:
    """Rule 1's NEXT for a FAIL. It never copies the fix: a fix may name a path or a file (CONTRACTS §16.20
    keeps those out of NEXT) or a bare ``agentsync`` that is not on PATH; the [FAIL] line carries it. A
    fix that is the person's (``doctor.is_persons_fix``: the kinds ``doctor.PERSONS_KINDS`` names; a third
    hand-kept list went stale, B13 round 17 U69) is named as theirs, as install.sh's NEXT names it, not as a
    step for the agent (B13 round 14 Q1, Q9, Q33; round 15 S7, S47, S53; round 16 T2, T5, T9; round 17 U2,
    U16, U11). No catalog of the person's kinds: the [FAIL] line right below states the one fix, and the
    whole list named every kind but that one (B13 round 23 A31; no count, which went stale: B13 round 25
    D48). "not a step for the
    agent" stays: the setup report counts a person's stop by it."""
    if doctor.is_persons_fix(result.fix):
        return (
            f"the {result.name} check failed: its fix on the [FAIL] line below is the person's, not a step "
            "for the agent: show it to them"
        )
    if result.fix:
        return f"the {result.name} check failed: do what the fix on its [FAIL] line below says"
    return f"the {result.name} check failed: its [FAIL] line below says why"


def _blocks_install(result: doctor.CheckResult) -> bool:
    """Whether a check's result stops install.sh's first sync: an ERROR FAIL other than the launcher's own
    TCC_PENDING (install.sh's blocking_fails)."""
    if result.ok or result.severity is not doctor.Severity.ERROR:
        return False
    return not (result.name.startswith("tcc.") and result.detail.startswith("TCC_PENDING: "))


def _fail_steps(results: Sequence[doctor.CheckResult]) -> list[str]:
    """Rule 1's fixes, one per ERROR FAIL, the agent's own first: the NEXT names only the first, so it never
    sends the agent to show the person a fix while a re-run or a click of its own waits (B13 round 14 Q1)."""
    fails = [r for r in results if not r.ok and r.severity is doctor.Severity.ERROR]
    return [_fail_step(r) for r in sorted(fails, key=lambda r: doctor.is_persons_fix(r.fix))]


def _held_split(
    config: Config, gov: governance.GovernanceConfig | None, queued: Sequence[governance.QueuedPurge]
) -> tuple[list[governance.QueuedPurge], list[tuple[governance.QueuedPurge, list[governance.Hold]]]]:
    """``governance.held_purges`` of ``queued``, or all of them runnable when [governance] or the holds do
    not load (their own FAIL lines speak for that; the hold-list-unread branch comes first)."""
    if gov is None:
        return list(queued), []
    try:
        return governance.held_purges(config.state_paths.root, gov, queued)
    except Exception:  # the governance lines say why
        return list(queued), []


def _says_why(config: Config | None, filename: object) -> str | None:
    """The words that point at the status line judging ``filename``, a path in the state folder this account
    was refused: the state_dir lines for the folder, any path in a folder it cannot search, and the files
    state_dir.files judges (the manifest, its -wal and -shm, the token cache); the lock, heartbeat,
    governance.hold or governance.purge_queue [FAIL] line for its own file; None for any other path, which no
    line judges (B13 round 21 Y43, Y45: "the state_dir lines say why" was added for any EACCES, over
    state_dir lines that read [ok]). With no config (``None``), the state_dir lines, as before."""
    if config is None:
        return "the state_dir lines say why"
    if not isinstance(filename, str | bytes | os.PathLike):
        return None
    sp = config.state_paths

    def real(p: Path) -> Path:
        return Path(os.path.realpath(expand(p)))

    root, path = real(sp.root), Path(os.path.realpath(os.fsdecode(filename)))
    files = {real(sp.db), *(real(side) for side in doctor.manifest_sides(expand(sp.db)))}  # P42
    if not (path == root or path.is_relative_to(root)):
        # a linked manifest's target, or a side beside it, outside the state folder: state_dir.files judges
        # it there, so its line says why; any other path outside no line judges (B13 round 28 I70)
        return "the state_dir lines say why" if path in files else None
    if doctor.state_folder_shut(config):  # its one FAIL, named (B13 round 27 P122)
        return doctor.state_folder_says_why(config)
    if path == root or path in files | {real(sp.token_cache_fallback)}:
        return "the state_dir lines say why"
    checks = {
        real(sp.lock): "lock",
        real(sp.heartbeat): "heartbeat",
        real(governance._holds_file(root)): "governance.hold",
        real(governance._queue_file(root)): "governance.purge_queue",
    }
    check = checks.get(path)
    return f"the {check} [FAIL] line says why" if check else None


def _folder_refused(exc: Exception, config: Config | None) -> TypeGuard[OSError]:
    """Whether ``exc`` is this account's refused access (EACCES, EPERM) to a path in the state folder while
    the folder, or a folder above it, refuses it a look inside (``doctor.state_folder_shut``): the folder's
    fault, not a file's. Keyed on the folder, never on the path refused, which is a file in it (the lock);
    a manifest at 0000 in a folder at 0700 stays a file (B13 round 22 Z5)."""
    if config is None or not isinstance(exc, OSError) or exc.errno not in (errno.EACCES, errno.EPERM):
        return False
    filename = exc.filename if exc.filename is not None else getattr(exc.__cause__, "filename", None)
    if filename is None or _says_why(config, filename) is None:  # a path in the state folder (reviewer's)
        return False
    return doctor.state_folder_shut(config)


def _unread_words(
    exc: Exception, *, refused: str = "governance.config or policy", config: Config | None = None
) -> str:
    """Why a part of status cannot read the state, in words (B13 round 17 U21): the setup report copies
    these lines, so no Python class name for a failure a setup meets. sqlite's own message is plain English
    and holds no path; a refused configuration's words are on its [FAIL] line, which names the value
    (``refused``: the check whose FAIL that is, where the caller knows it); an error no setup should meet
    keeps its class and message, for whoever debugs it. No bracket of its own around a reason: the report
    quotes these words after "status says", and sqlite's message can hold one (B13 round 18 V11, V54)."""
    if isinstance(exc, ConfigError):
        return f"the configuration is refused: the {refused} [FAIL] line says why"
    if config is not None and doctor.state_root_not_folder(config) is not None:
        # nothing under a state folder that is a file (or under a file where a folder above it belongs) can be
        # read: state_dir's one FAIL, the person's, says why, never "GovernanceError: ... [Errno 20]" (B13
        # round 23 A22, A33; round 23 ruling 4)
        return "the state folder is not a folder; the state_dir line says why"
    if isinstance(exc, governance.GovernanceUnreadError):  # no class name (B13 round 26 E43; round 28 I90)
        # the check by the error's own file, never "its governance [FAIL] line", which names no check (I25)
        says = f"the {doctor.governance_check(exc.path)} [FAIL] line says why"
        if isinstance(exc, governance.GovernanceDamagedError):
            return f"{exc.role} cannot be read; {says}"
        what = "a folder" if exc.role == governance.GOVERNANCE_DIR_ROLE else "a file"  # I89
        return f"{exc.role} is not {what}; {says}"
    if isinstance(exc, StateUnreadableError):
        return f"the state file cannot be read: {exc.__cause__ or 'see the state_dir.manifest [FAIL] line'}"
    if doctor.contended(exc):  # locked, not unreadable: another run (a sync, a status migrating) holds it
        return "the state file is busy: another agentsync run holds it; try again when it ends"
    side = doctor.side_not_a_file_at(expand(config.state_paths.db)) if config is not None else None
    if isinstance(exc, sqlite3.Error) and doctor.side_may_explain(exc) and side is not None:
        # a -wal or -shm that is not a file: no account's access, "cannot be opened" read as one, beside the
        # state_dir.files FAIL that names the person's move aside (B13 round 25 D8)
        role = doctor.SIDE_ROLES[doctor.side_suffix(side)]  # a linked manifest's side by its suffix (P42)
        return f"{role} is not a file; the state_dir.files line says why"
    if isinstance(exc, sqlite3.Error) and doctor.cannot_open(exc):
        # CANTOPEN, PERM, READONLY: no damage; the file's owner or mode, or its folder's (B13 round 19 W64;
        # round 20 X48: by name, so SQLITE_PERM is 3, not SQLITE_AUTH's 23)
        return f"the state file cannot be opened: {exc}; the state_dir lines say why"
    if isinstance(exc, sqlite3.Error):
        return f"the state file cannot be read: {exc}"
    if _folder_refused(exc, config):
        # the state folder, or a folder above it, refuses a look inside: "a file" named the lock the first
        # read was refused at, beside status's state_dir lines that name the folder (B13 round 22 Z5)
        reason = exc.reason if isinstance(exc, ManifestPathError) else exc.strerror
        said = (
            f"the state folder cannot be opened: {reason}" if reason else "the state folder cannot be opened"
        )
        pointer = (
            doctor.state_folder_says_why(config) if config is not None else "the state_dir lines say why"
        )
        return f"{said}; {pointer}"  # its one FAIL, named (P122)
    if isinstance(exc, ManifestLostError):  # not "a file cannot be opened" (A29's reviewer)
        said = "the state file " + (exc.reason or LOST_MANIFEST)  # an unread heartbeat too (P111)
        return f"{said}; the state_dir.manifest [FAIL] line says why"
    if isinstance(exc, ManifestPathError):  # its reason is kept apart from strerror (B13 round 20 X47)
        said = f"a file cannot be opened: {exc.reason}" if exc.reason else "a file cannot be opened"
        if isinstance(exc, ManifestOpenError):
            return f"{said}; the state_dir lines say why"
        # no access matter: the line that FAILs the path, by its errno, none for a full disk (Z51)
        check = _misplaced_check(config, exc) if config is not None else None
        if (
            check is not None
            and check.name == "state_dir.manifest"
            and "whose folder does not exist" in check.detail
        ):
            # a link to nothing at the manifest is no "not a file" (B13 round 25 D43)
            return "the state file's link points into a missing folder; the state_dir.manifest line says why"
        if check is not None and check.name == "state_dir.manifest":
            # a folder where the manifest belongs (EISDIR on it), as a file where the state folder belongs
            # is worded: "a file cannot be opened: Is a directory" was the system's (B13 round 24 B11)
            return "the state file is not a file; the state_dir.manifest line says why"
        return f"{said}; the {check.name} [FAIL] line says why" if check is not None else said
    if isinstance(exc, OSError):
        said = f"a file cannot be opened: {exc.strerror}" if exc.strerror else "a file cannot be opened"
        # this account's access, which the line that judges the path names (B13 round 19 W64; round 21 Y43)
        why = _says_why(config, exc.filename) if exc.errno in (errno.EACCES, errno.EPERM) else None
        if why and exc.filename is not None:
            # a state file a check names (the lock, the heartbeat) by its name, as a governance file's is
            # below (Y45): "a file cannot be opened" beside a state file that reads (B13 round 25 D12)
            said = f"{Path(os.fsdecode(exc.filename)).name} cannot be opened: {exc.strerror or exc}"
        return f"{said}; {why}" if why else said
    cause = exc.__cause__
    if isinstance(exc, AgentSyncError) and isinstance(cause, PermissionError):
        # a governance file this account cannot open (holds.json, the purge queue): its name and the system's
        # words, never "GovernanceError:", and the check whose line FAILs (B13 round 21 Y45)
        name = Path(os.fsdecode(cause.filename)).name if cause.filename is not None else "a file"
        said = f"{name} cannot be opened: {cause.strerror or cause}"
        why = _says_why(config, cause.filename)
        return f"{said}; {why}" if why else said
    return f"{type(exc).__name__}: {exc}"


def _guarded(
    name: str,
    fn: Callable[[], list[str]],
    *,
    refused: str = "governance.config or policy",
    config: Config | None = None,
) -> list[str]:
    """``fn()``, or one line naming the error: a part of status that cannot be read never hides the checks
    (the matching FAIL, e.g. governance.config or a manifest check, explains it; ``refused``: which one a
    refused configuration's is, as :func:`_unread_words` names it)."""
    try:
        return fn()
    except Exception as exc:
        return [f"{name}: cannot read the state: {_unread_words(exc, refused=refused, config=config)}"]


def _next_or_fix(config: Config, fixes: Sequence[str]) -> list[str]:
    """The loop's NEXT with the FAILs' fixes; when the loop's state cannot be read and a check failed, rule
    1's NEXT from the first fix, so a FAIL that says why (state_dir.manifest) is named, never "cannot read
    the state" (B13 round 16 T12)."""
    try:
        # a FAIL explains a state the loop cannot read, and NEXT is built from it: no WARNING (B13 round 20
        # X30)
        lines = loop.next_lines(config, fixes=fixes, quiet=bool(fixes))
    except Exception:
        if not fixes:
            raise
        lines = []
    if lines:
        return lines  # the step's own lines, which carry its waits
    return [loop.NEXT_PREFIX + fixes[0], *_docs_waits(config)] if fixes else []


def _manifest_first(checks: Sequence[doctor.CheckResult]) -> str | None:
    """The NEXT step of the first state_dir.manifest FAIL (:func:`_fail_step`), or None: that FAIL leads,
    never another FAIL's fix sorted ahead of it (an agent's "then run sync", which stops on the manifest) nor
    the loop's skill or sign-in step (B13 round 25 D43, lane B's reviewer)."""
    fail = next((r for r in checks if r.name == "state_dir.manifest" and not r.ok), None)
    return None if fail is None else _fail_step(fail)


def _heartbeat_chmod_first(config: Config, checks: Sequence[doctor.CheckResult]) -> str | None:
    """The heartbeat FAIL's step when it is this account's own chmod (``chmod 600 <heartbeat.json>``, which
    ``doctor.state_path_fix`` gives only for its own file at a refusing mode) and the state_dir.manifest FAIL
    is a lost manifest beside a heartbeat that cannot be read: the chmod settles whether earlier passes exist
    (after it the manifest reads ``records earlier passes``, the person's, or a first start), so it leads,
    the agent's once, never the person's decision taken blind (B13 round 28 I42). None otherwise: damaged
    JSON, a fifo, a folder, another account's file or an ACL keep the person's fix first."""
    manifest = next((r for r in checks if r.name == "state_dir.manifest" and not r.ok), None)
    if manifest is None or not manifest.detail.endswith(doctor.LOST_UNREAD):
        return None
    heartbeat = expand(config.state_paths.heartbeat)
    want = f"chmod 600 {doctor._shell_path(heartbeat)}"
    fail = next((r for r in checks if r.name == "heartbeat" and not r.ok and r.fix == want), None)
    return None if fail is None else _fail_step(fail)


def _state_stop_first(config: Config, checks: Sequence[doctor.CheckResult]) -> str | None:
    """The NEXT step of the FAIL a sync stops on (:func:`_fail_step`), or None: a state_dir.manifest FAIL
    (:func:`_manifest_first`), the state folder that is not a folder or refuses a look inside, or the state
    file that is not a file the sync's own guard finds (``doctor.state_not_a_file``: the lock, heartbeat.json,
    a governance file, a -wal or -shm). It leads, never the loop's skill or sign-in step, whose sync stops on
    it, so status and the sync sent the agent back and forth (B13 round 27 P23). A FAIL the sync reads past
    (a loose mode, the docs repo's permissions) keeps the loop's order."""
    first = _manifest_first(checks)
    if first is not None:
        chmod = _heartbeat_chmod_first(config, checks)
        return first if chmod is None else chmod
    try:
        if doctor.state_folder_unchecked(config) is not None:
            fail = next((r for r in checks if r.name.startswith("state_dir") and not r.ok), None)
            return None if fail is None else _fail_step(fail)
        found = doctor.state_not_a_file(config)  # the sync's own stop, by stat
    except Exception:  # the NEXT path never raises over the checks
        return None
    if found is None:
        return None
    _where, result = found
    fail = next(
        (r for r in checks if not r.ok and r.name == result.name and r.detail == result.detail), result
    )
    return _fail_step(fail)


def _manifest_first_lines(config: Config, step: loop.NextStep | None, first: str) -> list[str]:
    """:func:`_manifest_first`'s NEXT with the loop's waits and notes when it read the state (a lost or
    recorded-unreadable manifest: a queued purge, a breaker, a held listing stay), else the docs repo's."""
    if step is not None:
        return dataclasses.replace(step, step=first, rule=1).lines()
    return [loop.NEXT_PREFIX + first, *_docs_waits(config)]


def _new_start_unsaid_line(config: Config, raw: str | None) -> list[str]:
    """Status's one line for a new start the background job ran and no foreground sync has said yet (its
    note and WARNINGs went to the poll log alone); read-only, it never clears the key (B13 round 26 E74)."""
    try:
        saved = json.loads(raw) if raw else None
        orphans = saved.get("orphans", []) if isinstance(saved, dict) else None
    except ValueError:
        return []
    if orphans is None:
        return []
    gone = sum(1 for rel in orphans if not (expand(config.docs_repo) / rel).is_file())
    if not gone:
        return ["new start: the background sync started a new manifest; the next sync says so"]
    removed = plural(gone, "page")
    return [
        f"new start: the background sync started a new manifest and removed {removed} it did not write back; "
        "the next sync names them"
    ]


def _new_start_kept_lines(kept: dict[str, list[str]]) -> list[str]:
    """Status's line per source for the pages a new start kept for files still settling, which no shard
    lists until their file's pass converts it (B13 round 27 P117); read-only, it never clears the key (B13
    round 28 I108). The file and the pronoun agree with the count, as the sync's note does (``a file`` and
    ``it`` for one page; B13 round 29 C116, C131); ``its`` is the source's."""
    return [
        f"new start: {plural(len(pages), 'page')} kept for {'a file' if len(pages) == 1 else 'files'} still "
        f"settling in {sid}; its next complete pass converts or removes {'it' if len(pages) == 1 else 'them'}"
        for sid, pages in sorted(kept.items())
    ]


def _docs_waits(config: Config) -> list[str]:
    """The WAITING ON YOU lines that read the docs repo alone (``loop.docs_waits``: the baseline draft), for
    a NEXT the state could not build; never one that reads the state (the purge queue), whose read is the
    one that failed. A docs repo that cannot be read costs the wait, never the NEXT (B13 round 25 D11, D29:
    a manifest or state folder that is not one dropped the baseline wait from status, the report's Loop line
    and its finish)."""
    try:
        return [loop.WAIT_PREFIX + w for w in loop.docs_waits(config)]
    except Exception:
        return []


def _status_checks(
    config: Config,
    *,
    offline: bool = False,
    loop_checks: Sequence[str] | None = None,
    loop_waits: Mapping[str, int] | None = None,
) -> list[doctor.CheckResult]:
    """Every check of ``status``. ``offline`` (setup-report): no Graph probe and no TCC canary (it can raise
    a privacy prompt). ``loop_checks`` and ``loop_waits``: the loop's ``NextStep.checks`` and
    ``NextStep.wait_counts``, for the heartbeat check."""
    checks = doctor.run_checks(
        config,
        tcc_canary=not offline and _tcc_canary_due(config),
        loop_checks=loop_checks,
        loop_waits=loop_waits,
    )
    return checks + _extra_checks(config, offline=offline)


def _loop_line(config: Config) -> str:
    try:
        return loop.status_line(config)
    except Exception as exc:
        return f"loop: cannot read the state: {_unread_words(exc, config=config)}"


def _with_damaged_manifest(
    config: Config, checks: list[doctor.CheckResult], exc: Exception | None
) -> list[doctor.CheckResult]:
    """``checks`` with the state_dir.manifest FAIL when the loop's own read of the manifest (status's, and the
    ``doctor`` alias install.sh runs) raised SQLITE_NOTADB or SQLITE_CORRUPT: damage under a table the schema
    probe does not read, which status read and threw away, so it exited 0 with every check [ok] and a sync
    then exited 65 (B13 round 17 U64, U95). The manifest is the one database the loop opens, so the error is
    its; a locked or busy one is no FAIL, and a FAIL the schema probe already gave is not doubled. It goes
    after the other state_dir checks; no read is added."""
    if not (isinstance(exc, sqlite3.DatabaseError) and doctor.unreadable_code(exc)):
        return checks
    if any(r.name == "state_dir.manifest" for r in checks):
        return checks
    at = max((i + 1 for i, r in enumerate(checks) if r.name.startswith("state_dir.")), default=len(checks))
    return [*checks[:at], doctor.manifest_damaged(expand(config.state_paths.db), exc), *checks[at:]]


def _listings_unread(
    checks: list[doctor.CheckResult], exc: Exception | None, state_root: Path | None = None
) -> list[doctor.CheckResult]:
    """``checks`` with each incomplete-listing heartbeat warn worded for a state file that cannot be read
    (``doctor.LISTING_STATE_UNREADABLE_NOTE``, its fix dropped): no sync lists the source until it can, and
    the report's note pointed at its Loop line for a WAITING ON YOU line and the fix by hand at those lines
    (B13 round 19 W11). Unreadable as the setup report's Summary keys it: the loop's read raised SQLITE_NOTADB
    or SQLITE_CORRUPT, or a state_dir.manifest FAIL stands (doctor's probe, or a command's record of what it
    read: B13 round 20 X46 keeps that trigger, as a sync fix or a WAITING ON YOU pointer is wrong there too);
    a locked or busy manifest is not. A loop read this account cannot open gets
    ``doctor.LISTING_STATE_SHUT_NOTE`` (B13 round 20 X15), keyed on that read's error, never on the
    state_dir.files check, which also FAILs a loose mode the loop reads past."""
    read = isinstance(exc, sqlite3.DatabaseError) and doctor.unreadable_code(exc)
    lost = any(  # missing after earlier passes, not unreadable: its own note (B13 round 24 B36)
        r.name == "state_dir.manifest" and not r.ok and r.detail.endswith(LOST_MANIFEST_WORDS) for r in checks
    )
    db = Path(state_root) / "manifest.sqlite" if state_root is not None else None
    short = any(  # there but too short: never "is missing" (B13 round 29 C128)
        r.name == "state_dir.manifest" and not r.ok and r.detail.endswith(LOST_MANIFEST_SHORT) for r in checks
    ) or (isinstance(exc, ManifestLostError) and exc.reason in LOST_MANIFEST_SHORT)
    if short:
        note = doctor.LISTING_STATE_SHORT_NOTE
    elif lost or isinstance(exc, ManifestLostError):
        note = doctor.LISTING_STATE_LOST_NOTE
    elif db is not None and doctor.state_file_kind(db) is not None:
        note = doctor.LISTING_STATE_NOT_FILE_NOTE  # a folder there: not "cannot be read" (D23)
    elif read or any(r.name == "state_dir.manifest" and not r.ok for r in checks):
        note = doctor.LISTING_STATE_UNREADABLE_NOTE
    elif db is not None and doctor.side_not_a_file_at(db) is not None:
        # the person's move aside, not this account's access (D8), each side by its role (P16, P146)
        note = doctor.listing_state_side_note(doctor.sides_not_a_file_at(db))
    elif doctor.state_unopenable(exc, state_root):
        note = doctor.LISTING_STATE_SHUT_NOTE
    else:
        return checks
    return [
        dataclasses.replace(r, note=note, fix=None)
        if r.name.startswith("heartbeat.") and not r.ok and r.detail.startswith(doctor.LISTING_INCOMPLETE)
        else r
        for r in checks
    ]


def _loop_read(config: Config) -> tuple[loop.NextStep | None, Exception | None]:
    """The loop's step without rule 9's count of the curation queue, or the error its read raised: status
    and the setup report's doctor read it before the checks, so a manifest damaged under a table FAILs in
    both (:func:`_with_damaged_manifest`; B13 round 18 V14, V52, V56, V71)."""
    try:
        return loop.next_step(config, count_queue=False), None
    except Exception as exc:  # the NEXT read names the error; the checks keep their default wording
        return None, exc


def _build_status(config: Config, *, brief: bool = False) -> _Status:
    """``status``'s output. ``brief`` (the ``doctor`` alias, or AGENTSYNC_NO_NEXT_HINT=1, both install.sh's):
    no detail and no policy lines, so install.sh's output (and setup-report's tail of it) holds what the old
    doctor printed and no label names.

    The loop is read first, without rule 9's count of the curation queue, so the heartbeat check points at
    WAITING ON YOU only for a wait the loop has (``NextStep.checks``, which neither a FAIL nor that count
    changes). Its NEXT is that read's when no check failed and the step came before rule 9, which is all
    the count changes; otherwise the loop is read again as before, with the FAILs' fixes."""
    hint_off = os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1"
    step, state_exc = _loop_read(config)
    checks = _status_checks(
        config,
        loop_checks=None if step is None else step.checks,
        loop_waits=None if step is None else step.wait_counts,
    )
    root = expand(config.state_paths.root)
    checks = _listings_unread(_with_damaged_manifest(config, checks, state_exc), state_exc, root)
    fixes = _fail_steps(checks)
    if hint_off and any(_blocks_install(r) for r in checks):  # install.sh runs no sync (B13 round 14 Q10)
        checks = [
            dataclasses.replace(r, note=doctor._LISTING_SKIPPED_NOTE)
            if r.note == doctor._LISTING_YOURS_NOTE
            else r
            for r in checks
        ]
    next_lines: list[str] = []
    if not hint_off:
        if step is not None and not fixes and step.rule < 9:
            next_lines = step.lines()
        elif (first := _state_stop_first(config, checks)) is not None:
            # the person's state_dir.manifest FAIL first: a link to nothing, a loop or a lost manifest let the
            # loop read past it, so its "run sync" NEXT sent them to the sync that stops on it (B13 round 25
            # D43); the loop's own waits stay when it read the state, else the docs repo's
            next_lines = _manifest_first_lines(config, step, first)
        else:
            next_lines = _guarded("NEXT", lambda: _next_or_fix(config, fixes), config=config)
            if next_lines and next_lines[0].startswith("NEXT: cannot read the state"):
                next_lines += _docs_waits(config)  # no FAIL to name: the wait still follows (D11)
    detail: list[str] = []
    if not (brief or hint_off):
        detail = _guarded("status", lambda: _status_lines(config), refused="governance.config", config=config)
        detail += _guarded("policy", lambda: _policy_lines(config), config=config)
    return _Status(tuple(next_lines), _loop_line(config), tuple(checks), tuple(detail))


def _cmd_status(args: argparse.Namespace, *, brief: bool = False) -> int:
    config = _config(args)
    status = _build_status(config, brief=brief)
    for line in status.lines():
        _out(line)
    return EXIT_FAILED if status.failed else EXIT_OK


def _cmd_status_renamed(args: argparse.Namespace) -> int:
    """The hidden ``doctor`` and ``policy show`` aliases: run ``status`` (under AGENTSYNC_NO_NEXT_HINT=1 the
    rename note is left out too). install.sh still calls ``doctor`` and reads its ``[FAIL`` lines, so
    ``doctor`` prints only the NEXT, loop and check lines, as the old doctor did; ``policy show`` keeps the
    policy detail."""
    if os.environ.get(NO_NEXT_HINT_ENV, "").strip() != "1":
        _err(f"renamed: run {loop.BIN} status")
    return _cmd_status(args, brief=args.command == "doctor")


def _agents_installed_at(config: Config) -> datetime | None:
    """When the poll LaunchAgent's plist was last written (``install-agent``), to the second; None when it is
    not installed."""
    try:
        mtime = launchd.plist_path(f"{config.launchd_label_prefix}.poll").stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(int(mtime), UTC)


def _tcc_canary_due(config: Config) -> bool:
    """Whether ``status`` runs the TCC canary (up to 15 s per protected source, and it may raise the privacy
    prompt): only when the newest launcher run of either job left a TCC_PENDING or TCC_DENIED line uncleared
    (on any of its paths, not only the last one logged), or when no event is logged since the LaunchAgents
    were installed (or ever).  Never with no LaunchAgent installed (KISS K11a): nothing runs the launcher,
    so a canary would only raise a privacy prompt for it."""
    if not doctor.agents_wanted(config):
        return False
    last: tuple[datetime, bool] | None = None
    for suffix in ("poll", "reconcile"):
        run = _newest_launcher_run(config, suffix)
        if run is not None and run.stamp is not None and (last is None or run.stamp > last[0]):
            last = (run.stamp, bool(run.pending))
    if last is None:
        return True
    installed = _agents_installed_at(config)
    if installed is not None and last[0] < installed:
        return True
    return last[1]


# ---- doctor additions (C15 sections 1, 5 and 7; the ops checks live in ops/doctor.py) ------------------


def _check(
    name: str, ok: bool, detail: str, severity: doctor.Severity, fix: str | None = None
) -> doctor.CheckResult:
    return doctor.CheckResult(name, ok, detail, doctor.Severity.INFO if ok else severity, fix)


_PURGE_YOURS_NOTE = "yours: see WAITING ON YOU"

_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")


def _launchctl_env(names: Sequence[str]) -> dict[str, str]:
    """``launchctl getenv`` for ``names``: what every LaunchAgent of this session inherits (empty off
    macOS)."""
    exe = Path("/bin/launchctl")
    if not exe.exists():
        return {}
    out: dict[str, str] = {}
    for name in names:
        try:
            cp = subprocess.run(
                [str(exe), "getenv", name], capture_output=True, text=True, timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError):
            continue
        value = cp.stdout.strip()
        if cp.returncode == 0 and value:
            out[name] = value
    return out


_JOB_PROXY_URL_VARS = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy")


def _job_proxy_urls(config: Config | None) -> list[str]:
    """The proxy URLs a LaunchAgent run of ``config`` gets (``launchctl getenv``, then the plist's
    EnvironmentVariables), for setup-report's redaction: URL variables only, never NO_PROXY."""
    env = _launchctl_env(_JOB_PROXY_URL_VARS)
    if config is not None:
        with contextlib.suppress(ConfigError):
            env.update(launchd.poll_spec(config).environment)
    return [env[k] for k in _JOB_PROXY_URL_VARS if env.get(k)]


def _job_environment(config: Config) -> dict[str, str]:
    """The environment a LaunchAgent run of this config gets: the plist's EnvironmentVariables over the
    session's ``launchctl setenv`` values (never this shell's)."""
    env = _launchctl_env(_PROXY_VARS)
    try:
        env.update(launchd.poll_spec(config).environment)
    except ConfigError:
        env.update({"PATH": launchd.LAUNCHD_PATH})
    return env


_PROXY_UNUSED_NOTE = "only Graph sources use the proxy, and none is live"


def _unused_proxy_line(detail: str, name: str = "network.proxy") -> doctor.CheckResult:
    """A proxy finding that nothing acts on: no live Graph source (field N13)."""
    return doctor.CheckResult(name, False, detail, doctor.Severity.INFO, note=_PROXY_UNUSED_NOTE)


def _network_checks(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
    live_graph = any(s.kind.is_graph and s.is_live for s in config.sources)
    system = net.system_proxy()
    proxy = net.resolve_proxy(config.network.proxy, system=system)
    diag = net.proxy_diagnostics(proxy)
    out: list[doctor.CheckResult] = []
    # The background job resolves the proxy from ITS environment, not this shell's (review deploy-ops); with
    # no LaunchAgent installed there is no job to compare (KISS K11a: background sync is optional).
    job = (
        net.resolve_proxy(config.network.proxy, environ=_job_environment(config), system=system)
        if doctor.agents_wanted(config)
        else proxy
    )
    if (job.url, job.policy_error) != (proxy.url, proxy.policy_error):
        mismatch = f"the LaunchAgent resolves {job.describe()} but this shell resolves {proxy.describe()}"
        if live_graph:
            out.append(
                _check(
                    "network.proxy.job",
                    False,
                    mismatch,
                    doctor.Severity.ERROR,
                    fix='set [network] proxy = "<url>" (or "direct") in sources.toml: both then use it',
                )
            )
        else:  # the job's proxy (even a PAC policy error) is unused without a live Graph source (field N13)
            out.append(_unused_proxy_line(mismatch, "network.proxy.job"))
    # Only Graph uses the proxy (cycle.py gates every consumer on a live Graph source): without one, a PAC or
    # WPAD setting is INFO with a note, never a fix to chase (field N13).
    if proxy.policy_error and live_graph:
        out.append(
            _check(
                "network.proxy",
                False,
                proxy.policy_error,
                doctor.Severity.ERROR,
                fix='set [network] proxy = "http://<proxy>:<port>" (PAC files are not evaluated)',
            )
        )
    elif proxy.policy_error:
        out.append(_unused_proxy_line(proxy.policy_error))
    else:
        out.append(_check("network.proxy", True, diag[0], doctor.Severity.INFO))
    if live_graph:
        out += [_check("network.proxy", False, w, doctor.Severity.WARN) for w in diag[1:]]
    elif not proxy.policy_error:  # a PAC policy error already says what its warning would (one line)
        out += [_unused_proxy_line(w) for w in diag[1:]]
    if offline:  # setup-report: no network, ever
        if live_graph:
            out.append(
                _check(
                    "network.graph",
                    False,
                    f"not probed (setup-report makes no network calls; `{loop.BIN} status` probes it)",
                    doctor.Severity.INFO,
                )
            )
    elif live_graph:  # the probe runs whenever a Graph source is live (KISS K08a: no --network)
        reach = net.probe_reachability(config.graph.base_url, proxy)
        if reach.online:
            out.append(
                _check(
                    "network.graph", True, f"{config.graph.base_url}: {reach.detail}", doctor.Severity.INFO
                )
            )
        elif reach.skipped:
            out.append(_check("network.graph", False, f"offline: {reach.detail}", doctor.Severity.WARN))
        else:
            out.append(
                _check(
                    "network.graph",
                    False,
                    f"failed: {reach.state}: {reach.detail}",
                    doctor.Severity.ERROR,
                    fix=net.TLS_HINT if reach.state == net.POLICY_TLS else net.PROXY_HINT,
                )
            )
    return out


def _broker_check(config: Config) -> list[doctor.CheckResult]:
    if config.graph.client_id is None:
        return []
    if not config.graph.broker:
        return [_check("graph.broker", False, "disabled by [graph] broker = false", doctor.Severity.INFO)]
    reason = graph_auth.broker_unavailable_reason()  # the call loads pymsalruntime, not the import
    if reason is None:
        return [_check("graph.broker", True, "macOS broker available (sign-in rung 1)", doctor.Severity.INFO)]
    return [
        _check(
            "graph.broker",
            False,
            f"unavailable: {reason}; sign-in falls back to the browser (PKCE)"
            + (", then device code" if config.graph.allow_device_code else ""),
            doctor.Severity.WARN,
            fix="on a managed Mac install Company Portal (the SSO extension) so compliant-device CA passes",
        )
    ]


# A [governance] or [policy] value that does not load is the person's to fix, as install.sh's NEXT for the
# same config error says: policy.toml is their compliance team's file, and the agent never edits either
# (B13 round 16 T2, T9). Only policy.toml's [policy] reaches the policy check: one in sources.toml stops
# the config from loading (exit 78), as a bad [governance] value does not.
GOVERNANCE_FIX = "the person fixes [governance] in sources.toml: what the error names"
POLICY_FIX = (
    "the person fixes [policy] in policy.toml, which their compliance team owns: what the error names"
)

# A docs-repo remote [governance] does not allow (C15 req 36) is the person's to remove or to allow: it may
# be one they set up on purpose, and the prompt's rules forbid the agent deleting anything. The fix starts
# "the person ", which doctor.is_persons_fix and install.sh's persons_fails read (B13 round 17 U2, U16).
# Allowing it takes both keys: remote_allowed needs allow_remote on and the URL under a remote_url_prefixes
# entry.
REMOTE_FIX = (
    "the person removes the remote, or sets allow_remote = true and names its tenant-owned prefix in "
    "[governance] remote_url_prefixes (C15 req 36)"
)


def _refused_check(config: Config, name: str, exc: AgentSyncError) -> doctor.CheckResult:
    """``name``'s FAIL for a governance file in the state folder this account cannot open (``exc`` caused by
    a PermissionError), with the fix of what refused it; any other error is raised (B13 round 20 X73: the
    whole governance check crashed on holds.json at 0000)."""
    if isinstance(exc, governance.GovernanceUnreadError):
        # a folder, fifo or socket at holds.json, the queue or the suppression list: the person's move aside,
        # never "check crashed ... a fault in agentsync to report" (B13 round 26 E43; ruling 13)
        return _check(name, False, exc.detail, doctor.Severity.ERROR, fix=exc.fix)
    cause = exc.__cause__
    refused = doctor.refused_state_path(config, name, cause) if isinstance(cause, PermissionError) else None
    if refused is None:
        raise exc
    return refused


def _governance_folder_checks(config: Config) -> list[doctor.CheckResult]:
    """``governance.hold``'s FAIL for a governance/ folder this account cannot write into, with
    ``doctor.state_path_fix``'s fix (this account's chmod 700, another account's chown, the person's ACL or
    volume fix): a hold, its release and a queued purge each write a temporary file there, while a read of
    holds.json in it still passes (B13 round 24 B61: status passed a 0500 folder every hold stopped on). A
    second FAIL, with the person's chown (``doctor._persons_chown``), for a folder that takes a write but is
    sticky and another account's, holding a file a hold replaces (holds.json, the purge queue, the suppression
    list) that is also another account's: no rename replaces it (``governance.sticky_blocker``; B13 round 25
    D45). Another account's folder this account can write otherwise passes (B13 round 26 E47). Nothing for a
    folder not there yet (a fresh install) or not a folder (no mode fixes the wrong kind)."""
    root = expand(config.state_paths.root)
    gov = governance.governance_dir(root)
    try:
        st = gov.lstat()
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(st.st_mode):
        return []
    if not os.access(gov, os.R_OK | os.W_OK | os.X_OK):
        fix = doctor.state_path_fix(Path(os.path.realpath(root)), Path(os.path.realpath(gov)))
        detail = f"{gov} refuses this account a write: a hold, its release and a queued purge each stop there"
        return [_check("governance.hold", False, detail, doctor.Severity.ERROR, fix=fix)]
    # by what a hold does, never by the owner: another account's folder this account can write works, as
    # B58's lock does; only a sticky one holding another account's file a hold replaces stops it (B13 round
    # 25 D45: "refuses this account a write" over a 0777 folder a hold wrote into)
    theirs = governance.sticky_blocker(root)
    if theirs is None:
        return []
    detail = f"{gov} is a sticky folder and {theirs.name} is another account's: a hold cannot replace it"
    return [_check("governance.hold", False, detail, doctor.Severity.ERROR, fix=doctor._persons_chown(root))]


def _audit_checks(config: Config) -> list[doctor.CheckResult]:
    """``governance.audit`` and ``governance.suppressed``: a FAIL, with the state folder's fix for it, for an
    audit trail this account cannot append to or a suppression list it cannot read; nothing when each opens
    or is not there yet. A hold, its release and a queued purge each need the audit line first, so a refused
    one stops them (B13 round 23 A79: no check read either file, and status said 0 FAIL). The audit trail is
    opened for append, as a hold writes it, and closed at once: a 0400 file passes a read, and reading every
    line would make one bad line a crash."""
    root = expand(config.state_paths.root)
    out: list[doctor.CheckResult] = []
    audit = governance.audit_path(root)
    kind = doctor.governance_file_kind(audit)  # a link to nothing too, which a hold's O_CREAT follows (P131)
    if kind is None:
        try:
            # O_NONBLOCK: a fifo there that nothing reads fails at once (ENXIO), where the open hung status;
            # the fstat closes the race between the kind's lstat and the open (B13 round 24 B63)
            fd = os.open(audit, os.O_WRONLY | os.O_APPEND | os.O_NONBLOCK | os.O_CLOEXEC)
        except FileNotFoundError:
            pass
        except OSError as exc:  # a read-only volume (EROFS): its own line, never the group's
            if exc.errno == errno.ENXIO:
                kind = "a fifo"
            else:
                out.append(doctor.stopped(config, "governance.audit", exc))
        else:
            try:
                kind = doctor._not_a_file(os.fstat(fd).st_mode)
            finally:
                os.close(fd)
    if kind is not None:  # the words and the person's fix a hold's error gives (B13 round 25 D22, D41)
        detail, fix = governance.audit_not_a_file(audit, kind)
        out.append(_check("governance.audit", False, detail, doctor.Severity.ERROR, fix=fix))
    try:
        governance.load_suppressions(root)
    except governance.GovernanceUnreadError as exc:  # the person's move aside, never "crashed" (E43)
        out.append(_refused_check(config, "governance.suppressed", exc))
    except AgentSyncError as exc:
        cause = exc.__cause__
        refused = (
            doctor.refused_state_path(config, "governance.suppressed", cause)
            if isinstance(cause, OSError)
            else None
        )
        out.append(refused or doctor.stopped(config, "governance.suppressed", exc))
    except Exception as exc:  # valid JSON of the wrong shape: this line only (A79's reviewer)
        out.append(doctor.stopped(config, "governance.suppressed", exc))
    else:
        lost = governance.suppressions_lost(root)  # purges the audit trail records that the list lost (C172)
        if lost is not None:
            out.append(
                _check("governance.suppressed", False, lost.detail, doctor.Severity.ERROR, fix=lost.fix)
            )
    return out


def _time_machine_checks(config: Config) -> list[doctor.CheckResult]:
    """``governance.time_machine``: a warn for each tenant-data path not excluded from Time Machine, and an
    [info] line, no warn, for one this account cannot read the exclusion of (a file it cannot open, a folder
    it cannot look inside): it was excluded, and the sync the warn named could not fix a mode; the state_dir
    lines give that fix when the path is in the state folder (B13 round 20 X12, X27)."""
    transient = ("-wal", "-shm")
    status = [(p, ok) for p, ok in governance.time_machine_status(config) if not p.name.endswith(transient)]
    root = expand(config.state_paths.root)
    out: list[doctor.CheckResult] = []
    folder_unchecked = doctor.state_folder_unchecked(config)
    if folder_unchecked is not None:
        # every path in a folder this account cannot search read "Permission denied", a missing one too: one
        # not-checked line, as governance.hold's and purge_queue's, and only the paths outside it are judged;
        # the line also keeps "tenant-data paths excluded" off a folder nobody checked (B13 round 21 Y51), and
        # a state folder that is not a folder says so (round 23 ruling 4)
        status = [(p, ok) for p, ok in status if not (p == root or p.is_relative_to(root))]
        out.append(_check("governance.time_machine", False, folder_unchecked, doctor.Severity.INFO))
    else:
        # a folder (or other non-file) where the manifest belongs has no exclusion to judge, and the sync the
        # warn named stops before it applies one: state_dir.manifest FAILs it, the person's (B13 round 23 A10)
        db = expand(config.state_paths.db)
        kind = doctor.state_file_kind(db)
        # a manifest lost as too short holds no rows, and the sync the warn named refuses on it (C129)
        short = kind is None and lost_words(db) in LOST_MANIFEST_SHORT
        if kind is not None or short:
            status = [(p, ok) for p, ok in status if p != db]
            said = "is empty or too short to be a manifest" if short else "is not a file"
            out.append(
                _check(
                    "governance.time_machine",
                    False,
                    f"not checked: the state file {said}; the state_dir.manifest line says why",
                    doctor.Severity.INFO,
                )
            )
    missing = [p for p, ok in status if ok is False]
    unchecked = [(p, ok) for p, ok in status if isinstance(ok, str)]
    if missing:
        out.append(
            _check(
                "governance.time_machine",
                False,
                "not excluded from Time Machine: " + ", ".join(str(p) for p in missing),
                doctor.Severity.WARN,
                fix=f"{loop.BIN} sync (or: tmutil addexclusion " + " ".join(map(str, missing)) + ")",
            )
        )
    if unchecked:
        theirs = "its" if len(unchecked) == 1 else "their"
        detail = f"not checked: this account cannot read {theirs} exclusion: " + ", ".join(
            f"{p} ({why})" for p, why in unchecked
        )
        if any(p == root or p.is_relative_to(root) for p, _why in unchecked):
            detail += "; the state_dir lines say why"
        out.append(_check("governance.time_machine", False, detail, doctor.Severity.INFO))
    if not out:
        out.append(
            _check("governance.time_machine", True, "tenant-data paths excluded", doctor.Severity.INFO)
        )
    return out


def _governance_checks(config: Config) -> list[doctor.CheckResult]:
    try:
        gov = governance.load_governance(config.config_path)
    except ConfigError as exc:
        return [_check("governance.config", False, str(exc), doctor.Severity.ERROR, fix=GOVERNANCE_FIX)]
    out: list[doctor.CheckResult] = []
    findings = _refuse_remotes(config, gov)
    if findings:
        out += [
            _check("governance.remote", False, f, doctor.Severity.ERROR, fix=REMOTE_FIX) for f in findings
        ]
    else:
        out.append(
            _check("governance.remote", True, "no disallowed remote on the docs repo", doctor.Severity.INFO)
        )
    # a state folder this account cannot look inside: the holds and the purge queue are files in it, so they
    # are not checked, never a crash of the whole check (B13 round 20 X73). The config-level hold is
    # sources.toml's, so it is shown first either way (B13 round 21 Y44: the not-checked line hid it)
    out += [
        _check("governance.hold", True, h.describe(), doctor.Severity.INFO)
        for h in governance.config_holds(gov)
    ]
    shut = doctor.state_folder_unchecked(config)  # shut, or not a folder (round 23 ruling 4)
    folder = governance.governance_dir_error(expand(config.state_paths.root)) if shut is None else None
    if shut is not None:
        out.append(_check("governance.hold", False, shut, doctor.Severity.INFO))
    elif folder is not None:
        # governance/ itself is not a folder: one FAIL, the person's, never "check crashed" nor a file under
        # it, and nothing under it is read (B13 round 28 I88, I89)
        out.append(_check("governance.hold", False, folder.detail, doctor.Severity.ERROR, fix=folder.fix))
        shut = "not checked: agentsync's governance folder is not a folder; the governance.hold line says why"
    else:
        root = config.state_paths.root
        try:
            holds = governance.state_holds(root)
            lost = governance.lost_holds(root, holds)
        except AgentSyncError as exc:  # holds.json this account cannot open: one FAIL, not a crash (X73)
            out.append(_refused_check(config, "governance.hold", exc))
        else:
            out += [_check("governance.hold", True, h.describe(), doctor.Severity.INFO) for h in holds]
            for h in lost:  # the audit trail keeps it; holds.json lost it: the person's (B13 round 28 I87)
                lost_detail, lost_fix = governance.lost_hold_words(root, h)
                out.append(_check("governance.hold", False, lost_detail, doctor.Severity.ERROR, fix=lost_fix))
        said = {r.fix for r in out if not r.ok}  # at 0000, holds.json's refusal names the folder's chmod
        out += [r for r in _governance_folder_checks(config) if r.fix not in said]
        said = {r.fix for r in out if not r.ok}
        out += [r for r in _audit_checks(config) if r.fix not in said]  # one folder's fix is said once
    if sys.platform == "darwin" and os.environ.get("AGENTSYNC_TM_EXCLUDE", "1") != "0":
        try:
            out += _time_machine_checks(config)
        except OSError as exc:  # its own line, never the hold lines above with it (B13 round 22 Z46)
            out.append(doctor.stopped(config, "governance.time_machine", exc))
    try:
        state, detail = governance.compaction_state(config.docs_repo, gov)
    except AgentSyncError as exc:
        state, detail = "overdue", f"cannot read the docs repo history: {exc}"
    severity = {"ok": doctor.Severity.INFO, "due": doctor.Severity.WARN}.get(state, doctor.Severity.ERROR)
    # A runnable command is named by its full path, as the loop's waits are: ~/.local/bin may not be on PATH
    # (B13 round 3 R12).
    compact_fix = f"{loop.BIN} sync (its next full pass compacts); release any hold"
    fix = None if state == "ok" else compact_fix
    out.append(_check("governance.compaction", state == "ok", f"{state}: {detail}", severity, fix=fix))
    if shut is not None:
        out.append(_check("governance.purge_queue", False, shut, doctor.Severity.INFO))
        return out
    try:
        split = governance.partition_queue(config.state_paths, config=config)
    except AgentSyncError as exc:
        out.append(_refused_check(config, "governance.purge_queue", exc))
        return out
    queued, kept = split.runnable, split.kept
    gone = governance.queue_lost(config.state_paths.root)  # queued purges the file lost (B13 round 29 C172)
    if gone is not None:
        out.append(_check("governance.purge_queue", False, gone.detail, doctor.Severity.ERROR, fix=gone.fix))
    kept_text = "; ".join(governance.kept_lines(kept))
    also = f"; {kept_text}" if kept else ""
    if not queued and kept:
        out.append(_check("governance.purge_queue", True, kept_text, doctor.Severity.INFO))
    if queued:
        # each reason actually queued, never a fixed list that misnamed a published credential (I91)
        detail = f"{plural(len(queued), 'purge request')} queued ({governance.reason_counts(queued)}){also}"
        unread = governance.hold_list_unread(config.state_paths.root)
        runnable, held = ([], []) if unread else _held_split(config, gov, queued)
        # the ones from before the new start a run looks up in the docs history first (C102, C124)
        detail += governance.looked_up_words(split.not_looked_up, runnable)
        if unread:
            # a hold list that cannot be read is no empty one: no run is invited, its FAIL says why (P131)
            detail += "; not run while the hold list cannot be read: the governance.hold line says why"
            out.append(_check("governance.purge_queue", False, detail, doctor.Severity.WARN))
        elif held and not runnable:
            # suspended by a hold: no fix, no run invited; the release is its records owner's (I91)
            detail += f"; {governance.held_words(held)}"
            out.append(_check("governance.purge_queue", False, detail, doctor.Severity.WARN))
        elif os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
            if held:
                detail += f"; {governance.held_words(held)}"
            # install.sh prints the loop's WAITING ON YOU line for the queue: no second instruction here.
            out.append(
                doctor.CheckResult(
                    "governance.purge_queue", False, detail, doctor.Severity.WARN, note=_PURGE_YOURS_NOTE
                )
            )
        else:
            exits = ""
            if held:  # the held ones stay queued, and the run exits 1 over them (I91)
                detail += f"; {governance.held_words(held)}"
                exits = ", which exits 1 with the held ones still queued"
            out.append(
                _check(
                    "governance.purge_queue",
                    False,
                    detail,
                    doctor.Severity.WARN,
                    fix=(
                        f"{loop.BIN} purge --queue --dry-run (a preview that writes nothing); "
                        f"then {loop.BIN} purge --queue{exits}"
                    ),
                )
            )
    return out


def _policy_check(config: Config) -> list[doctor.CheckResult]:
    try:
        pol = content_policy.load_policy(config)
    except ConfigError as exc:
        return [_check("policy", False, str(exc), doctor.Severity.ERROR, fix=POLICY_FIX)]
    if not pol.labels_active:
        detail = "no label rules (encrypted Office/PDF files are still stubbed, never converted)"
    else:
        detail = (
            f"{plural(len(pol.exclude_label_ids), 'excluded label id')}, "
            f"{plural(len(pol.exclude_label_names), 'name')}, "
            f"refuse_unlabelled={'true' if pol.refuse_unlabelled else 'false'} ({pol.fingerprint()[:19]})"
        )
    return [_check("policy", True, detail, doctor.Severity.INFO)]


_INSTALL_STAMP = ".agentsync-install-source"
"""scripts/install.sh's record, inside the uv tool environment, of the clean checkout it installed this build
from: ``commit=<sha12> source=<checkout path>`` (removed for a dirty checkout or a wheel)."""


def _install_stamp() -> Path:
    return Path(sys.prefix) / _INSTALL_STAMP


def _build_installed_at() -> datetime | None:
    """When this build was installed: its environment's ``pyvenv.cfg`` (``uv tool install --force``
    recreates it), to the second; None when unreadable."""
    try:
        return datetime.fromtimestamp(int((Path(sys.prefix) / "pyvenv.cfg").stat().st_mtime), UTC)
    except OSError:
        return None


def _checkout_head(checkout: Path) -> str | None:
    """The commit HEAD names in the git checkout at ``checkout``, read from its files (never by running git:
    without the developer tools /usr/bin/git offers to install them); None when it cannot be read."""
    try:
        git_dir = checkout / ".git"
        if git_dir.is_file():  # a linked worktree: "gitdir: <path>"
            pointer = git_dir.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir: "):
                return None
            git_dir = checkout / pointer.removeprefix("gitdir: ")
        common = git_dir
        if (git_dir / "commondir").is_file():
            common = git_dir / (git_dir / "commondir").read_text(encoding="utf-8").strip()
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head or None
        ref = head.removeprefix("ref: ")
        for base in (git_dir, common):
            if (base / ref).is_file():
                return (base / ref).read_text(encoding="utf-8").strip() or None
        for line in (common / "packed-refs").read_text(encoding="utf-8").splitlines():
            sha, _, name = line.partition(" ")
            if name == ref:
                return sha
    except OSError:
        return None
    return None


def _install_check(config: Config) -> list[doctor.CheckResult]:
    """WARN when the checkout install.sh installed this build from has moved to another commit."""
    try:
        stamp = _install_stamp().read_text(encoding="utf-8").strip()
    except OSError:
        return []  # not installed by install.sh from a clean checkout
    commit, _, source = stamp.removeprefix("commit=").partition(" source=")
    if not stamp.startswith("commit=") or not commit or not source:
        return []
    head = _checkout_head(Path(source))
    if head is None:
        return []
    if head[:12] == commit[:12]:
        return [
            _check(
                "install.commit",
                True,
                f"installed from {commit[:12]}, the checkout's HEAD",
                doctor.Severity.INFO,
            )
        ]
    return [
        _check(
            "install.commit",
            False,
            f"installed from {commit[:12]}, but the checkout at {source} is at {head[:12]}",
            doctor.Severity.WARN,
            fix=f"re-run install.sh ({source}/scripts/install.sh)",
        )
    ]


def _skill_check(config: Config) -> list[doctor.CheckResult]:
    """The agentsync-docs skill copies: FAIL when a copy is missing or stale, its folder (or the nearest one
    that exists) cannot be written, and a sync ran with this build (so that sync's write failed); otherwise a
    not-ok info line naming the copies the next sync writes. A copy a sync never tried to write (a
    ``$CLAUDE_CONFIG_DIR`` that sync ran without) is never a FAIL."""
    paths = skill.skill_paths()
    text = skill.skill_text(expand(config.docs_repo))
    off: list[tuple[Path, str]] = []
    for path in paths:
        try:
            if path.read_text(encoding="utf-8") != text:
                off.append((path, "stale"))
        except (OSError, UnicodeDecodeError):
            off.append((path, "missing"))
    if not off:
        return [_check("skill", True, f"current: {', '.join(str(p) for p in paths)}", doctor.Severity.INFO)]

    def writable(path: Path) -> bool:
        folder = path.parent
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        return os.access(folder, os.W_OK)

    blocked = [(p, s) for p, s in off if not writable(p)]
    started: str | None = None
    if blocked and config.state_paths.db.exists():
        with Manifest(config.state_paths.db) as manifest:
            started = manifest.last_run_started()
    ran_at = _parse_log_time(started) if started is not None else None
    built_at = _build_installed_at()
    if not blocked or ran_at is None or (built_at is not None and ran_at < built_at):
        where = ", ".join(f"{s} {p}" for p, s in off)
        return [
            _check("skill", False, f"{off[0][1]}: the next sync writes it ({where})", doctor.Severity.INFO)
        ]
    where = ", ".join(f"{s} {p}" for p, s in blocked)
    return [
        _check(
            "skill",
            False,
            f"{blocked[0][1]} although a sync ran with this build: its folder cannot be written ({where})",
            doctor.Severity.ERROR,
            fix=f"make that folder writable, then run `{loop.BIN} sync`",
        )
    ]


def _extra_checks(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
    """Integrator-owned checks: proxy/TLS reachability, broker, governance, content policy, the skill copies
    and the installed commit (``offline``: never the Graph reachability probe, whatever the sources say)."""
    out: list[doctor.CheckResult] = []
    checks: tuple[tuple[str, Callable[[], list[doctor.CheckResult]]], ...] = (
        ("network", lambda: _network_checks(config, offline=offline)),
        ("graph.broker", lambda: _broker_check(config)),
        ("governance", lambda: _governance_checks(config)),
        ("policy", lambda: _policy_check(config)),
        ("skill", lambda: _skill_check(config)),
        ("install.commit", lambda: _install_check(config)),
    )
    for name, fn in checks:
        doctor.saw_group(name)  # after run_checks' last: the check a cut-off doctor was in (Y22)
        try:
            out += fn()
        except Exception as exc:  # one broken check must not hide the others
            out.append(doctor.stopped(config, name, exc))
    return out


def _report_hooks() -> setup_report.ReportHooks:
    """setup-report's hooks, split along status's cost: the Doctor section runs the offline checks once (under
    the rest of the report's budget), the Status section only the cheap loop line and detail lines (under its
    own 4 s). No policy detail (label names stay out of a report meant for sharing). The Summary's Loop line
    (KISS K16b) takes the loop's NEXT from ``loop_next``, with the doctor checks' FAILs as rule 1's fixes and
    without rule 9's mirror walk; the Status section still prints no NEXT line.

    The Doctor hook reads the loop first, as status does, so a manifest damaged under a table gives the same
    state_dir.manifest FAIL there (B13 round 18 V14, V52, V56, V71: the report said 0 FAIL, NEXT none, and
    pointed at a Doctor line it never printed); ``state_error`` hands that read's error to the report, whose
    Summary files the FAIL under the state file that cannot be read. The checks still get no
    ``loop_checks``: the heartbeat check's report path is the one without them (B13 round 9 K75, round 10
    L49), so a read that succeeds changes no line."""
    fixes: list[str] = []
    manifest_first: list[str] = []  # the state_dir.manifest FAIL's step, as status leads with it (D43)
    read: list[tuple[loop.NextStep | None, Exception | None]] = []
    seen: list[str] = []  # the check groups doctor started, the last the one a cut-off doctor was in (Y22)

    def checks(config: Config) -> list[str]:
        step, state_exc = _loop_read(config)
        read[:] = [(step, state_exc)]
        with doctor.groups_seen(seen.append):
            found = _status_checks(config, offline=True)
        seen.append("")  # every check has run: what is left names none (the report's "a check")
        results = _with_damaged_manifest(config, found, state_exc)
        results = _listings_unread(results, state_exc, expand(config.state_paths.root))
        fixes[:] = _fail_steps(results)
        first = _state_stop_first(config, results)  # status's own lead (P23)
        manifest_first[:] = [] if first is None else [first]
        # The report has no "end of this run": its own note (B13 round 5 F12, F45). _status_checks never
        # carries _LISTING_SKIPPED_NOTE: only _build_status swaps it in (B13 round 15 S43).
        results = [
            dataclasses.replace(r, note=doctor.LISTING_YOURS_REPORT_NOTE)
            if r.note == doctor._LISTING_YOURS_NOTE
            else r
            for r in results
        ]
        return doctor.format_results(results).splitlines()

    def loop_next(config: Config) -> loop.NextStep | list[str]:
        """The loop's NextStep (its ``checks`` group doctor's warns); when the state cannot be read (a
        damaged manifest raises sqlite3.DatabaseError too), the NEXT ``agentsync status`` prints then:
        rule 1's from the first FAIL's fix, or [] (B13 round 17 U7). With no FAIL, the Doctor hook's own
        read is that step: it is not read twice."""
        if read and read[0][0] is not None and not fixes:
            return read[0][0]
        if manifest_first:  # status's NEXT for a state_dir.manifest FAIL (B13 round 25 D43)
            try:
                step: loop.NextStep | None = loop.next_step(config, fixes=fixes, count_queue=False)
            except Exception:
                step = None
            if step is not None:
                return dataclasses.replace(step, step=manifest_first[0], rule=1)
            return _manifest_first_lines(config, None, manifest_first[0])
        try:
            return loop.next_step(config, fixes=fixes, count_queue=False)
        except Exception as exc:
            # the report records the error (state_error), and _next_or_fix decides whether a warning is due,
            # so setup-report logs it at most once (B13 round 20 X16, X30)
            _log.debug("next step: cannot read the loop state: %s", exc)
            try:
                return _next_or_fix(config, fixes)
            except Exception:
                # no FAIL to name: the baseline wait still follows, as status prints it (B13 round 26 E44)
                return _docs_waits(config)

    return setup_report.ReportHooks(
        doctor=checks,
        status=lambda config: [
            _loop_line(config),
            *_guarded(
                "status",
                lambda: _status_lines(config, checks_beside=False),
                refused="governance.config",
                config=config,
            ),
        ],
        loop_next=loop_next,
        job_proxy=_job_proxy_urls,
        state_error=lambda: read[0][1] if read else None,
        doctor_at=lambda: seen[-1] if seen else None,
    )


def _cmd_setup_report(args: argparse.Namespace) -> int:
    """Never fails hard: every section records its own error; exit 1 only when --out cannot be written
    (the report then goes to stdout instead)."""
    out = expand(args.out)
    friction = setup_report.default_friction_path()
    text, red = setup_report.build_report(
        getattr(args, "config", None), hooks=_report_hooks(), friction_path=friction
    )
    link = f"{setup_report.ISSUE_LINK_LABEL} {setup_report.issue_link(text) or setup_report.ISSUE_URL}"
    # the report says to send it privately: no public issue (B13 round 6 G47, round 8 J69)
    if red.private_only:
        link = ""
    try:
        setup_report.write_report(out, text)
    except OSError as exc:
        _err(f"setup-report: cannot write {out}: {exc.strerror or exc}; the report follows on stdout")
        sys.stdout.write(text)
        if link:
            _out(link)
        return EXIT_FAILED
    what = f"{plural(red.total, 'replacement')} of {plural(red.values, 'value')}"
    embedded = "friction log embedded" if friction.is_file() else f"no friction log found at {friction}"
    _out(f"wrote {out} ({what}; {embedded})")
    if link:
        _out(link)  # the last line: setup prompt step 3 (v5: step 5) shows it (no "next:" hint)
    return EXIT_OK


def _cmd_it_request(args: argparse.Namespace) -> int:
    """Render docs/deploy/it-request.md for this Mac into ``--out`` (0600). Exit 2 when ``--out`` is inside
    the agentsync checkout or the docs repo, 1 when the page cannot be found or the file cannot be written.
    An unreadable sources.toml is not an error: the source kinds then stay for the person to fill."""
    out = expand(args.out)
    kinds: list[str] | None = None
    paths: list[Path] = []
    roots: list[Path] = []
    checkout = it_request.source_checkout()
    if checkout is not None:
        roots.append(checkout)
    config_path: Path | None = getattr(args, "config", None)
    try:
        config = _config(args)
    except ConfigError as exc:
        if (expand(config_path) if config_path is not None else default_config_path()).exists():
            _err(f"it-request: sources.toml not read ({exc}); <arms-today> stays open")
        else:
            kinds = []  # nothing configured yet is a fact, not an open field
    else:
        kinds = [s.kind.value for s in config.sources]
        paths = [s.path for s in config.sources if s.path is not None]
        roots.append(config.docs_repo)
    inside = it_request.refused_location(out, roots)
    if inside is not None:
        _err(f"it-request: {out} is inside {inside}; write it elsewhere (e.g. {it_request.DEFAULT_OUT})")
        return EXIT_USAGE
    try:
        template = it_request.load_template()
        draft = it_request.render(template, it_request.gather_facts(kinds=kinds, source_paths=paths))
    except it_request.TemplateError as exc:
        _err(f"it-request: {exc}")
        return EXIT_FAILED
    try:
        backup = it_request.write_draft(out, draft.text)
    except OSError as exc:
        _err(f"it-request: cannot write {out}: {exc.strerror or exc}")
        return EXIT_FAILED
    _out(f"wrote {out} (mode 0600; from {template.origin}; nothing was sent)")
    if backup is not None:
        _out(f"kept the earlier draft as {backup}")
    _out(f"You fill: {', '.join(draft.you_fill) or 'nothing'}")
    _out(f"IT fills: {', '.join(draft.it_fills) or 'nothing'} (leave them)")
    for note in draft.notes:
        _out(note)
    return EXIT_OK


_CHANGE_WORDS = {"A": "ADDED", "M": "CHANGED", "D": "REMOVED"}


def _is_tombstone(page: Path) -> bool:
    try:
        with page.open(encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    return head.startswith("---\n") and "\nstatus: deleted\n" in head.split("\n---\n", 1)[0]


def _print_changes_since_checkpoint(layout: DocsLayout) -> None:
    """What the mirror gained, changed and lost since the last build session's checkpoint.  A page that
    became a tombstone reads REMOVED, and a removed page with an ``archive/`` copy names it in a third
    column; an ADDED page carries its ready-made ``sources:`` entry there instead."""
    repo = expand(layout.root)
    checkpoint = gitops.curated_checkpoint(repo) if gitops.head_sha(repo) else None
    if checkpoint is None:
        _out(
            "no build-session checkpoint yet: every mirror page is new (a sync records one once a session's "
            "topic pages are lint-clean)"
        )
        return
    sha, date = checkpoint
    changes = [
        ("D" if st == "M" and path.endswith(".md") and _is_tombstone(repo / path) else st, path)
        for st, path in gitops.changes_since(repo, sha)
    ]
    counts = {
        word: sum(1 for st, _ in changes if _CHANGE_WORDS.get(st) == word) for word in _CHANGE_WORDS.values()
    }
    for status, path in changes:
        tail = ""
        if status == "D":
            archived = archive_path(path)
            tail = f"\t{archived}" if archived and (repo / archived).is_file() else ""
        elif status == "A":
            entry = curate.source_entry(layout, path)
            tail = f"\t{entry}" if entry else ""
        _out(f"{_CHANGE_WORDS.get(status, status)}\t{path}{tail}")
    _out(
        f"since the last build session ({date}, {sha[:12]}): {counts['ADDED']} added, {counts['CHANGED']} "
        f"changed, {counts['REMOVED']} removed under mirror/"
    )


def _lost_state_stop(args: argparse.Namespace, config: Config) -> bool:
    """Print sync's ``error: state:`` line, the person's fix and the NEXT, and return True, when the state
    folder (or a folder above it) is not a folder or the manifest is lost from a Mac that has synced; else
    False. Curate's guard (B13 round 24 B89, round 25 D77), shared with ``purge`` and ``compact-history``:
    over a lost manifest the queue cannot tell a current page from a gone one, and a purge run blind erased
    current pages and their history (B13 round 26 E25; ruling 14)."""
    if doctor.state_root_not_folder(config) is not None:
        # a state folder, or a folder above it, that is not a folder: sync's line and NEXT, exit 1, never
        # the loop's "cannot read the loop state" WARNING and a "(see NEXT)" with no NEXT (B13 round 24 B89)
        root = str(expand(config.state_paths.root))
        said = _misplaced(args, NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), root))
        if said is not None:
            _err(said)
            _misplaced_next(args, said)
            return True
    db = expand(config.state_paths.db)
    lost = lost_words(db)
    if lost is not None:
        # a manifest lost from a Mac that has synced: sync's line and the person's fix, exit 1, never "0
        # findings" and a NEXT to run sync again, which stops on it (B13 round 25 D77); curate opens no
        # manifest, so its guard is here, as B89's is
        said = _misplaced(args, lost_error(db, lost))  # one errno per kind (C179)
        if said is not None:
            _err(said)
            _misplaced_next(args, said)
            return True
    return _not_a_file_stop(args, config)


def _open_state_or_raise(config: Config) -> None:
    """Open the manifest when a regular file is there, so one this account cannot open or read raises the
    error a sync stops on, which ``_main`` prints with its fix and exit code: a purge over it skipped the
    queued purges it could not judge and exited 0 with only those queued, and stopped on the open only when a
    DLP entry was queued too, while its words said it refuses (B13 round 27 P24). Judged by stat, so a fifo is
    never opened; nothing there, or not a regular file, is the guards' before it."""
    db = expand(config.state_paths.db)
    try:
        if not stat.S_ISREG(db.stat().st_mode):
            return
    except OSError:
        return
    Manifest(db).close()


def _not_a_file_state(args: argparse.Namespace) -> tuple[str, Path] | None:
    """:func:`_not_a_file_stop`'s line and path, for the error path: None when nothing is found or the
    config does not load (never a second error over the first)."""
    try:
        found = doctor.state_not_a_file(_config(args))
    except Exception:
        return None
    if found is None:
        return None
    where, result = found
    return f"error: state: {result.detail}" + (f" (fix: {result.fix})" if result.fix else ""), where


def _not_a_file_stop(
    args: argparse.Namespace, config: Config, *, manifest: bool = True, lost: bool = False
) -> bool:
    """Print status's own FAIL of a state file that is not a regular file (``doctor.state_not_a_file``: a
    folder, fifo, socket or link where the lock, heartbeat.json, a governance file, the manifest or a side of
    it belongs) as the command's ``error: state:`` line, with the NEXT (and a sync's stop record), and return
    True; else False. Judged by stat before anything opens it, so a fifo never blocks and nothing ends on a
    traceback (B13 round 26 E22, E38, E39, E40, E43, E65; ruling 13)."""
    # a damaged one too (I90); a lost purge only where the command publishes (B13 round 29 C172)
    found = doctor.state_not_a_file(config, manifest=manifest, damaged=True, lost=lost)
    if found is None:
        return False
    where, result = found
    fix = f" (fix: {result.fix})" if result.fix else ""
    said = f"error: state: {result.detail}{fix}"
    _err(said)
    _misplaced_next(args, said, where)
    return True


def _curate_findings(config: Config) -> list[LintFinding]:
    """Every whole-repo land-gate lint, TOPIC-BUDGET and the meeting citation lint's CITE-* (the curation
    findings that stay warnings), then the checkpoint blockers from the base the next sync checks
    (``loop.checkpoint_findings``): every curation lint finding and UNLISTED as an ERROR, STALE pins and
    missing sources of pages changed since that base."""
    repo, layout = config.docs_repo, config.layout
    findings: list[LintFinding] = []
    findings += lints.lint_no_symlinks(repo)
    findings += lints.lint_mirror_frontmatter(repo)
    findings += lints.lint_paths(repo)
    findings += lints.lint_no_cache_in_git(repo)
    findings += lints.lint_no_tokens(repo)
    findings += lints.lint_index_budget(repo)
    findings += [f for f in curate.generate_depends(layout)[2] if f.code == "TOPIC-BUDGET"]
    findings += curate.lint_meeting_citations(layout)
    findings += loop.checkpoint_findings(config)
    return sorted(findings, key=lambda f: (not f.blocking, f.code, f.path))


def _cmd_curate(args: argparse.Namespace) -> int:
    """KISS K09: findings, then the work rows (none during the baseline hold), then the loop's NEXT; exit 1
    on a blocking finding (or, from ``_main``, a state file this account cannot open), never because there is
    work."""
    config = _config(args)
    layout = config.layout
    if _lost_state_stop(args, config):
        return EXIT_FAILED
    findings = _curate_findings(config)
    for f in findings:
        _out(f"{'ERROR' if f.blocking else 'warn '} {f.code} {f.path}: {f.message}")
    blocking = sum(1 for f in findings if f.blocking)
    _out(f"{plural(len(findings), 'finding')}, {blocking} blocking")
    if loop.curation_held(config):
        _out("no curation rows yet: curation starts once the 'before' baseline results exist (see NEXT)")
    else:
        _print_changes_since_checkpoint(layout)
        rc, verdicts = curate.refresh_queue(layout)
        for v in verdicts:
            _out(v.line())
        if rc == 2:
            _err(f"{layout.depends_tsv}: missing or unreadable (run a sync first); listing uncovered pages")
        rows, _entities, _findings = curate.generate_depends(layout)  # live: pages written since the sync
        uncovered = curate.uncovered_mirror_pages(layout, rows)
        for rel in uncovered:
            entry = curate.source_entry(layout, rel)
            _out(f"UNCOVERED\t{rel}" + (f"\t{entry}" if entry else ""))
        _out(
            f"{plural(len(verdicts), 'refresh-queue row')}, {plural(len(uncovered), 'uncovered mirror page')}"
        )
    # A blocking finding is rule 1 here: the loop's own rules never see the land-gate lints, and rule 7 counts
    # checkpoint blockers only while something is pending or dirty, so NEXT must not send the agent past it.
    fixes = (
        [f"{plural(blocking, 'blocking finding')} above: fix every ERROR, then run `{loop.BIN} sync`"]
        if blocking
        else []
    )
    for line in loop.next_lines(config, fixes=fixes):
        _out(line)
    return EXIT_FAILED if blocking else EXIT_OK


def _cmd_curate_renamed(args: argparse.Namespace) -> int:
    """Hidden aliases (KISS K09): curate-queue, lint and refresh-queue run curate under their old names."""
    _err(f"renamed: run {loop.BIN} curate")
    return _cmd_curate(args)


def _cmd_checkpoint(args: argparse.Namespace) -> int:
    """Hidden alias (KISS K06): run an interactive sync, which records the checkpoint itself, behind sync's
    guard of a state file that is not a file (B13 round 27 P43)."""
    config = _config(args)
    if _not_a_file_stop(args, config, manifest=False, lost=True):
        return EXIT_FAILED
    return _run(config, mode=None)


def _cmd_install_skill(args: argparse.Namespace) -> int:
    """Hidden alias: write the skill every sync writes (see :mod:`agentsync.skill`); always exit 0."""
    config = _config(args)
    for path, written in skill.write_skill(expand(config.docs_repo)):
        _out(f"wrote skill {path}" if written else f"skill up to date: {path}")
    return EXIT_OK


def _cmd_adopt(args: argparse.Namespace) -> int:
    config = _config(args)
    created = curate.adopt_pages(expand(args.src_dir), config.layout, datetime.now(UTC).date().isoformat())
    for rel in created:
        _out(f"adopted {rel}")
    _out(
        f"{plural(len(created), 'page')} adopted; the next `{loop.BIN} sync` commits "
        + ("it" if len(created) == 1 else "them")
    )
    return EXIT_OK


def _cmd_migrate(args: argparse.Namespace) -> int:
    """KISS K13b: a no-op kept so older install.sh runs and scripts still exit 0; opening the manifest
    migrates it (with its pre-v<N> copy) in every command that uses it."""
    _out("migration is automatic: every agentsync command that opens the manifest migrates it")
    return EXIT_OK


def _cmd_graph(args: argparse.Namespace) -> int:
    config = _config(args)
    auth = graph_auth.MsalAuth(graph_auth.settings_from_config(config))
    action: str = args.action
    if action == "login":
        status = auth.login_device_code(_out) if args.device_code else auth.login(_out)
        _out(
            f"signed in as {status.username} (tenant {status.tenant_id}; method "
            f"{status.sign_in_method or 'unknown'}; token source {auth.last_token_source or '-'}; cache "
            f"{status.cache_backend})"
        )
    elif action == "logout":
        auth.logout()
        _out("signed out: cached Graph tokens removed")
    elif action == "whoami":
        status = auth.status()
        who = status.username if status.signed_in else "not signed in"
        _out(
            f"{who} · tenant {status.tenant_id or '-'} · method {status.sign_in_method or '-'} · "
            f"cache {status.cache_backend}"
        )
        _out(f"scopes: {' '.join(status.scopes)}")
        return EXIT_OK if status.signed_in else EXIT_REAUTH
    else:
        return _discover(config, auth, urls=tuple(args.url), toml_only=bool(args.toml))
    return EXIT_OK


def _discover(config: Config, auth: TokenProvider, *, urls: Sequence[str], toml_only: bool) -> int:
    from agentsync.graph import discover  # noqa: PLC0415
    from agentsync.graph.client import GraphClient, user_agent  # noqa: PLC0415
    from agentsync.graph.drive import DiscoveredScope  # noqa: PLC0415

    with GraphClient(
        auth,
        base_url=config.graph.base_url,
        user_agent=user_agent("agentsync", __version__),  # [graph] company is ignored (KISS K15)
        proxy=net.resolve_proxy(config.network.proxy),
    ) as client:
        if urls:
            scopes: list[DiscoveredScope] = []
            failures: list[discover.DiscoveryFailure] = []
            for url in urls:
                part = discover.resolve_url(client, url, known=config.sources)
                scopes += part.scopes
                failures += part.failures
            report = discover.DiscoveryReport(scopes=tuple(scopes), failures=tuple(failures))
        else:
            report = discover.discover_sources(client, known=config.sources)
    if not toml_only:
        for scope in report.scopes:
            where = scope.configured or "new"
            _out(
                f"{scope.kind.value}\t{scope.name}\t{where}\tdrive_id={scope.drive_id or '-'}\t"
                f"site={scope.site or '-'}\t{scope.note}"
            )
        _out("")
    _out(discover.render_sources_toml(report, known=config.sources).rstrip("\n"))
    if not report.complete:
        for f in report.failures:
            _err(f"discovery incomplete: {f.endpoint} -> HTTP {f.status} {f.code}: {f.action}")
        return EXIT_FAILED
    return EXIT_OK


def _cmd_install(args: argparse.Namespace) -> int:
    config = _config(args)
    gov = governance.load_governance(config.config_path)
    findings = _refuse_remotes(config, gov)
    if findings:  # C15 req 36: never schedule a mirror whose clones no purge can reach
        for f in findings:
            _err(f"refused: {f}")
        return EXIT_FAILED
    if any(s.kind.is_graph and s.is_live for s in config.sources) and not config.network.proxy:
        shell = net.resolve_proxy(None)
        if shell.source == "env":
            _err(
                f"refused: this shell's proxy comes from the environment ({shell.describe()}), which the "
                'LaunchAgents never see; set [network] proxy = "<url>" (or "direct") in sources.toml'
            )
            return EXIT_CONFIG
    specs = (launchd.poll_spec(config), launchd.reconcile_spec(config))  # ConfigError -> exit 78
    for line in governance.apply_time_machine_exclusions(config):
        _out(f"time machine: {line}")
    lints.remember_gitleaks()  # launchd's PATH may not reach the gitleaks this shell's does
    for spec in specs:
        path = launchd.install(spec)
        _out(f"installed {spec.label} every {spec.start_interval_s}s -> {path}")
    _out(f"launchd runs: {specs[0].program_arguments[0]}")
    prompt = launchd.tcc_prompt_text(config)
    if prompt is not None:
        _out(
            f"the first background run starts now; when macOS asks {prompt} click Allow (it grants "
            f"{launchd.launcher_identifier()} that provider only). The launcher runs only agentsync's pinned "
            "interpreter, whose user-writable uv tool environment is inside the trust boundary: prefer this "
            "grant to Full Disk Access / a PPPC SystemPolicyAllFiles payload"
        )
    _out(f"check: {loop.BIN} status (it names the File Provider target)")
    return EXIT_OK


def _cmd_uninstall(args: argparse.Namespace) -> int:
    config = _config(args)
    for suffix in ("poll", "reconcile"):
        label = f"{config.launchd_label_prefix}.{suffix}"
        _out(f"{label}: {'removed' if launchd.uninstall(label) else 'not installed'}")
    return EXIT_OK


# ---- governance (C15 section 7, requirements 36-42) ----------------------------------------------------


def _print_purge(rep: governance.PurgeReport) -> None:
    if rep.not_run:
        # the queue kept this entry on purpose (a listed file, a hold, a failure): no run counts, no "NOT
        # VERIFIED", which reads as a purge that ran and left something; its note says why it stays queued
        _out(f"purge {rep.selector} ({rep.reason.value}): not run")
        for n in rep.notes:
            _out(f"  note: {n}")
        return
    state = "dry run" if rep.dry_run else ("VERIFIED" if rep.verified else "NOT VERIFIED")
    # a dry run changed nothing: its commit count is in the note, its cache count is what would go
    rewritten = "" if rep.dry_run else f"{plural(rep.commits_rewritten, 'commit')} rewritten · "
    removed = "would be removed" if rep.dry_run else "removed"
    # "entr(ies)" read as a typo (rehearsal v10): this count's word is spelled out, singular for 1
    entries = "cache entry" if rep.cache_entries_removed == 1 else "cache entries"
    _out(
        f"purge {rep.selector} ({rep.reason.value}): {state} · {plural(len(rep.items), 'item')} · "
        f"{plural(len(rep.docs_paths), 'docs path')} · {rewritten}"
        f"{plural(rep.blobs_targeted, 'blob')} targeted · "
        f"{rep.cache_entries_removed} {entries} {removed}"
    )
    for p in rep.docs_paths:
        _out(f"  path: {p}")
    for p in rep.taken_paths:
        _out(f"  taken: {p} (another file's live page is there now: its manifest row and index lines stay)")
    for s in rep.survivors:
        _out(f"  SURVIVOR: {s}")
    if rep.unreachable_left:
        _out(f"  unreachable objects left: {rep.unreachable_left}")
    for p in rep.citing_pages:
        _out(f"  cited by (review by hand): {p}")
    _out(f"  remote: {rep.remote}")
    for n in rep.notes:
        _out(f"  note: {n}")


def _cmd_purge(args: argparse.Namespace) -> int:
    config = _config(args)
    if args.queue and args.selector:
        _err("purge: pass SELECTOR or --queue, not both")
        return EXIT_USAGE
    if (args.queue or args.selector) and _lost_state_stop(args, config):
        return EXIT_FAILED  # dry run included: it would plan erasing pages no manifest read (ruling 14)
    if args.queue or args.selector:
        _open_state_or_raise(config)  # sync's own line and exit, whatever the queue holds (P24)
    if args.forget is not None:
        if not args.queue:
            _err("purge: --forget goes with --queue")
            return EXIT_USAGE
        # the one audited way off the queue for an entry history names nothing of (B13 round 29 C173, C189)
        q = governance.forget_queued(
            config,
            governance.PurgeSelector.parse(args.forget, source_id=args.source),
            dry_run=bool(args.dry_run),
        )
        what = f"purge id={q.selector.stable_id} --source {q.selector.source_id} ({q.reason.value})"
        if args.dry_run:
            _out(f"dry run: would take off the queue {what}; nothing written")
        else:
            _out(f"taken off the queue: {what} (audited; nothing erased)")
        return EXIT_OK
    if args.queue:
        dry_run = bool(args.dry_run)
        reports = governance.run_purge_queue(config, dry_run=dry_run)
        for rep in reports:
            _print_purge(rep)
        left = governance.pending_purges(config.state_paths.root)
        ran = sum(1 for r in reports if not r.not_run)  # an entry the queue did not run stays queued
        if dry_run:
            _out(f"{plural(ran, 'queued purge')} previewed (dry run, nothing written); {len(left)} queued")
            return EXIT_OK
        # a kept upstream deletion is no failure: no run clears it while its file is listed; once the file is
        # gone it is a queued purge again, and the wait returns
        stuck, kept = governance.partition_purges(config.state_paths, left, config=config)
        if kept and all(why == governance.NEW_START_NOTE for _q, why in kept):
            # the new start's: the next sync's manifest judges them (B13 round 27 P40)
            until = "until the next sync builds the manifest"
            _out(f"{plural(ran, 'queued purge')} run; {len(left)} still queued {until}")
        elif kept and all(why.startswith(governance.UNRESOLVED_HEAD) for _q, why in kept):
            # nothing in the manifest names them and the run found no page under their id: kept until one is
            # found; its note says how to erase the page by hand (P112; B13 round 28 I93)
            until = "until its id is found in the docs history (see its note)"
            _out(f"{plural(ran, 'queued purge')} run; {len(left)} still queued {until}")
        elif kept and all(why.startswith("not judged: ") for _q, why in kept):
            # kept for the state file, not for a listed file: no manifest read judged them (E25)
            blind = "while the state file cannot be read"
            _out(f"{plural(ran, 'queued purge')} run; {len(left)} still queued {blind}")
        elif left and not stuck:
            _out(
                f"{plural(ran, 'queued purge')} run; {len(kept)} "
                + (
                    "stays queued while its file is listed"
                    if len(kept) == 1
                    else "stay queued while their file is listed"
                )
            )
        else:
            also = (
                f" ({len(kept)} of them while {'its' if len(kept) == 1 else 'their'} file is listed)"
                if kept
                else ""
            )
            _out(f"{plural(ran, 'queued purge')} run; {len(left)} still queued{also}")
        return EXIT_OK if all(r.verified or r.not_run for r in reports) and not stuck else EXIT_FAILED
    if not args.selector:
        _err("purge: SELECTOR (id=<stable_id> | path=<glob> | docs=<glob>) or --queue is required")
        return EXIT_USAGE
    selector = governance.PurgeSelector.parse(args.selector, source_id=args.source)
    rep = governance.purge(
        config,
        selector,
        reason=governance.PurgeReason(args.reason),
        dry_run=bool(args.dry_run),
        push=bool(args.push),
    )
    _print_purge(rep)
    return EXIT_OK if rep.dry_run or rep.verified else EXIT_FAILED


def _cmd_compact(args: argparse.Namespace) -> int:
    config = _config(args)
    if _lost_state_stop(args, config):
        return EXIT_FAILED  # its squash would strand the restored manifest's recovery hints (ruling 14)
    rep = governance.compact_history(config, args.keep_days, dry_run=bool(args.dry_run))
    state = "dry run" if rep.dry_run else ("VERIFIED" if rep.verified else "NOT VERIFIED")
    _out(
        f"compact-history before {rep.cutoff}: {state} · {plural(rep.squashed, 'commit')} squashed · "
        f"{rep.kept} kept" + (f" · new root {rep.new_root[:12]}" if rep.new_root else "")
    )
    for s in rep.survivors:
        _out(f"  SURVIVOR: {s}")
    if rep.dangling_recovery_hints:
        n = rep.dangling_recovery_hints
        _out(
            f"  {plural(n, 'tombstone recovery hint')} now {'points' if n == 1 else 'point'} into squashed "
            "history"
        )
    if rep.note:
        _out(f"  note: {rep.note}")
    return EXIT_OK if rep.dry_run or rep.verified or rep.squashed == 0 else EXIT_FAILED


def _cmd_hold(args: argparse.Namespace) -> int:
    config = _config(args)
    listing = args.list or (args.scope is None and not args.release)
    # a hold and its release write holds.json and the audit trail; the list only reads holds.json, whose own
    # not-a-file error _main words the same way, so a broken audit trail never hides the holds (E43)
    if not listing and _not_a_file_stop(args, config):
        return EXIT_FAILED
    root = config.state_paths.root
    if args.list or (args.scope is None and not args.release):
        holds = governance.active_holds(root, governance.load_governance(config.config_path))
        for h in holds:
            _out(h.describe())
        _out(plural(len(holds), "active hold"))
        return EXIT_OK
    if args.scope is None:
        _err("hold: SCOPE ('all' or a source id) is required")
        return EXIT_USAGE
    if not args.owner:
        _err("hold: --owner (the records/legal owner) is required")
        return EXIT_USAGE
    if args.release:
        released = governance.release_hold(root, args.scope, owner=args.owner)
        what = "released" if released else "no state hold (config holds live in sources.toml)"
        _out(f"hold {args.scope}: {what}")
        return EXIT_OK if released else EXIT_FAILED
    if not args.reason:
        _err("hold: --reason is required to set a hold")
        return EXIT_USAGE
    _out(governance.set_hold(root, args.scope, reason=args.reason, owner=args.owner).describe())
    return EXIT_OK


def _cmd_offboard(args: argparse.Namespace) -> int:
    config = _config(args)
    dry = bool(args.dry_run) or args.confirm is None
    rep = governance.offboard(config, purge_data=bool(args.purge_data), dry_run=dry, confirm=args.confirm)
    for loc in rep.locations:
        _out(f"{loc.kind}\t{'exists' if loc.exists else 'absent'}\t{loc.location}\t{loc.action}")
    for r in rep.removed:
        _out(f"removed: {r}")
    for e in rep.errors:
        _err(f"error: {e}")
    for m in rep.manual_steps:
        _out(f"manual: {m}")
    if rep.dry_run:
        _out(f"dry run: nothing removed; to remove, pass --confirm {expand(config.docs_repo)}")
    return EXIT_FAILED if rep.errors else EXIT_OK


# ---------------------------------------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------------------------------------


def _lock_holder(who: str) -> str:
    """The skip WARNING's holder: a cycle's lock label as ``another sync (its pass: poll)``, since a sync
    typed in a terminal holds the lock as ``poll`` or ``reconcile`` just as the background job does, and the
    bare word read as the job (B13 round 7 H33). Any other holder (the unrecorded and unidentified texts,
    governance's label) is named as it is: wrapping those would call install.sh's reinstall a sync. One text
    per label, so the report's Recent errors still merges repeated skips."""
    return f"another sync (its pass: {who})" if who in {m.value for m in CycleMode} else who


def _skip_holder(exc: LockHeldError) -> str:
    """The skip line's holder: ``exc.holder``, except that a lock held with an empty body is named in the wait
    line's words (``cycle.UNRECORDED_LOCK_HOLDER``) and not as ``an unrecorded holder (...)``, so the wait
    and its skip read the same (B13 round 19 W14). The skip WARNING and doctor keep ``EMPTY_BODY_HOLDER``."""
    if exc.who != EMPTY_BODY_HOLDER:
        return exc.holder
    return exc.holder.replace(EMPTY_BODY_HOLDER, UNRECORDED_LOCK_HOLDER, 1)


def _in_job(args: argparse.Namespace) -> bool:
    """Whether this run is the LaunchAgent's job, whose stderr is the err.log the setup report's Recent errors
    reads; a config that does not load counts as the job, so the WARNING is kept (B13 round 19 W13)."""
    try:
        return launchd.started_by_our_job(_config(args))
    except Exception:  # the error path: never a second error over the first
        return True


def _state_root(args: argparse.Namespace) -> Path:
    """The state folder of a command's config, or the default one when the config does not load."""
    try:
        return expand(_config(args).state_paths.root)
    except Exception:  # the error path: never a second error over the first
        return StatePaths(default_state_dir()).root


def _state_refused(args: argparse.Namespace, exc: PermissionError) -> str | None:
    """The one ``error: state:`` line for ``exc`` when its path is in the state folder, with the fix of the
    folder or file that refused it (``doctor.state_path_fix``), else None (B13 round 20 X73)."""
    root = _state_root(args)
    where = _in_folder(exc.filename, root)
    if where is None:
        return None
    fix = doctor.state_path_fix(Path(os.path.realpath(root)), Path(os.path.realpath(where)))
    why = _folder_refuses(where, fix)
    return f"error: state: {where} cannot be opened: {exc.strerror or exc}{why} (fix: {fix})"


def _folder_refuses(where: Path, fix: str) -> str:
    """The cause, when what refused ``where`` is a folder and not ``where`` itself: the path in the line is
    the one the system refused (manifest.sqlite), and its fix names the folder, so the words say which
    refused (B13 round 22 Z15; round 22 ruling 3): ``: a folder above it refuses this account a search``
    (doctor's ``_parent_fix``), ``: its folder refuses this account`` (``chmod 700`` of a folder other than
    ``where``, or the folder's ACL fixes, ``read, write and search`` and Z72's ``read-attributes, read, write
    and search``). Empty otherwise. The folder a fix names is read up to its ``;`` and compared with ``where``
    as written and as resolved: doctor names the resolved path (B13 round 23 A54: the read-attributes fix
    matched no prefix, and the ACL branch's test looked for the path followed by a space, which never
    matched)."""
    named = {doctor._shell_path(where), doctor._shell_path(os.path.realpath(where))}
    if fix.startswith("the person gives this account search access to "):
        return ": a folder above it refuses this account a search"
    for acl in ("read, write and search", "read-attributes, read, write and search"):
        head = f"the person gives this account {acl} access to the folder "
        if fix.startswith(head):
            folder = fix.removeprefix(head).split(";", 1)[0]
            return "" if folder in named else ": its folder refuses this account"
    if fix.startswith("chmod 700 "):
        return "" if fix.removeprefix("chmod 700 ") in named else ": its folder refuses this account"
    return ""


STATE_REFUSED_STEP = (
    f"this account cannot open a state file or its folder, so this sync did not run: run `{loop.BIN} status` "
    "and do what its NEXT says; its [FAIL] line names the fix"
)
"""The NEXT an interactive sync prints after its ``error: state:`` line: status, not the fix itself, since
another account's file is the person's sudo chown, which no NEXT hands the agent (B13 round 21 Y31: the sync
printed no NEXT, so the setup prompt sent the agent to the report past this account's own chmod). It promises
no owner: status's NEXT names the person only for the person's fix, and for this account's own chmod it is an
imperative, the agent's (B13 round 22 Z37: "whose NEXT says whose it is" was not kept there). "or its
folder": a folder at 0000, or one above it, refuses as often as a file does (B13 round 22 Z15)."""


STATE_MISPLACED_STEP = (
    "a state file or folder is not where or what it should be, so this sync did not run: run "
    f"`{loop.BIN} status` and do what its NEXT says; its [FAIL] line names the fix"
)
"""The NEXT a sync prints after the ``error: state:`` line of a state path that is not where or what it should
be (:func:`_misplaced`): a folder where the manifest belongs, a file where the state folder belongs. Not
:data:`STATE_REFUSED_STEP`'s "cannot open", the access framing round 20's X49 took off such a path (B13 round
22 Z51)."""
STATE_MISPLACED_COMMAND_STEP = (
    "a state file or folder is not where or what it should be, so this command stopped before it finished: "
    f"run `{loop.BIN} status` and do what its NEXT says; its [FAIL] line names the fix"
)
"""The same NEXT after any other command's ``error: state:`` line (curate, hold, accept-deletions, init,
add-source): "so this sync did not run" was false after a curate or a hold, which ran no sync (B13 round 25
D44), and "so this command did not run" after add-source and init, which add the source or write the config
before the state stop; "stopped before it finished" is true of both kinds (B13 round 26 E42)."""


def _state_next(
    args: argparse.Namespace,
    step: str = STATE_REFUSED_STEP,
    *,
    said: str = "",
    path: object = None,
    kind: str | None = None,
) -> None:
    """Print ``step`` (:data:`STATE_REFUSED_STEP`) as a NEXT line for a ``sync`` with no ``--mode`` (an
    agent's or a person's, never the LaunchAgent's), unless :data:`NO_NEXT_HINT_ENV` is 1 (install.sh names
    its own), and record the stop beside the setup log (:func:`_record_sync_stop`: ``said``, the error line,
    ``path``, the state path it names, and ``kind``, by default ``misplaced`` for
    :data:`STATE_MISPLACED_STEP` and ``refused`` for any other step)."""
    if getattr(args, "command", None) != "sync" or getattr(args, "mode", None):
        return
    if os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
        return
    _out(loop.NEXT_PREFIX + step)
    _record_sync_stop(args, said, path, kind or ("misplaced" if step == STATE_MISPLACED_STEP else "refused"))


SYNC_STOPS_LOG = setup_report._SYNC_STOPS_NAME
"""The file beside the setup log (``setup_report.default_setup_log``) where a foreground sync that stopped on
a state file records it, one line each: ``<UTC> sync-stop file=<role> rc=1 owner=<agent|person>
kind=<refused|misplaced|lost>``, no path. install.sh's own runs are in install.log, so only a sync that no
install.sh runs (the setup loop's) writes it, and never a line that starts ``<at> run=``, which the
install.log reader would take for an install run (B13 round 23 A9: a step-3 stop the agent's chmod fixed
left no trace for the report; lane RA reads it). The kind says why it stopped: a state file or folder this
account cannot open (``refused``), one not where or what it should be (``misplaced``), or a manifest missing
after earlier syncs (``lost``, ``manifest.manifest_lost``): a lost manifest's stop was recorded as
``file=state-folder`` and read as a refusal (B13 round 24 B27, B62, B82, B100; ruling 7)."""


def _record_sync_stop(args: argparse.Namespace, said: str, path: object, kind: str = "refused") -> None:
    """Append the stop's line to :data:`SYNC_STOPS_LOG`, only when the setup folder is already there; never
    raises, so a stop is never a second error. A refusal :func:`_folder_refuses` says was its folder's, not
    the file's, is the folder's role: a deny-ACL'd state folder wrote ``file=manifest`` (B13 round 24 B16,
    lane RA's reader)."""
    try:
        folder = setup_report.default_setup_log().parent
        if not folder.is_dir():
            return
        config = _config(args)
        head = said.rpartition(" (fix: ")[0]
        if head.endswith(": a folder above it refuses this account a search"):
            role = "state-folder"
        elif head.endswith(": its folder refuses this account") and isinstance(
            path, str | bytes | os.PathLike
        ):
            role = _stop_role(config, Path(os.fsdecode(path)).parent)
        else:
            role = _stop_role(config, path)
        fix = said.rpartition(" (fix: ")[2]
        owner = "agent" if fix.startswith("chmod ") else "person"
        at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{at} sync-stop file={role} rc=1 owner={owner} kind={kind}\n".encode()
        fd = os.open(folder / SYNC_STOPS_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception:  # the error path: never a second error over the first
        _log.debug("sync stop not recorded", exc_info=True)


def _stop_role(config: Config, path: object) -> str:
    """The role of ``path`` in the state folder, by name: ``manifest``, ``manifest-wal``, ``manifest-shm``,
    ``lock``, ``heartbeat``, ``holds``, ``governance`` (another file there), else ``state-folder``; with no
    path, the first state file this account is refused (``doctor.refused_state_files``)."""
    sp = config.state_paths
    if not isinstance(path, str | bytes | os.PathLike):
        refused = doctor.refused_state_files(config)
        path = refused[0] if refused else sp.root

    def real(p: object) -> Path:
        return Path(os.path.realpath(expand(Path(os.fsdecode(p)))))  # type: ignore[arg-type]

    root = real(sp.root)
    roles = {
        real(sp.db): "manifest",
        **{
            real(side): f"manifest{doctor.side_suffix(side)}" for side in doctor.manifest_sides(expand(sp.db))
        },
        real(sp.lock): "lock",
        real(sp.heartbeat): "heartbeat",
        real(governance._holds_file(root)): "holds",
    }
    where = real(path)
    if where in roles:
        return roles[where]
    # a link standing at a state file is named by where it stands, not by its target (B13 round 27 P132)
    named = expand(Path(os.fsdecode(path)))
    where = Path(os.path.realpath(named.parent)) / named.name
    if where in roles:
        return roles[where]
    gov = real(governance.governance_dir(root))
    return "governance" if where == gov or where.is_relative_to(gov) else "state-folder"


def _misplaced_check(config: Config, exc: OSError) -> doctor.CheckResult | None:
    """The status FAIL of a state path ``exc`` refused for no access matter, by the path and errno refused,
    never by the exception's class: state_dir.manifest's for something other than a file where the manifest
    belongs (EISDIR on it), state_dir's for a file where the state folder or a folder above it belongs
    (ENOTDIR, EEXIST); None for any other errno (a full disk), which no line FAILs, so no pointer names an
    [ok] line (round 21 Y43; B13 round 22 Z51)."""
    filename = exc.filename if exc.filename is not None else getattr(exc.__cause__, "filename", None)
    sp = config.state_paths
    if isinstance(exc, ManifestLostError):  # no new manifest over earlier passes (B13 round 23 A29)
        filename = expand(sp.db)
    if not isinstance(filename, str | bytes | os.PathLike):
        return None
    path = Path(os.path.realpath(os.fsdecode(filename)))
    root = Path(os.path.realpath(expand(sp.root)))
    db = Path(os.path.realpath(expand(sp.db)))
    if exc.errno == errno.EISDIR and path == db:
        name = "state_dir.manifest"
    elif isinstance(exc, ManifestLostError):
        name = "state_dir.manifest"  # lost from a Mac that has synced: no new one started (B13 round 23 A29)
    elif exc.errno in (errno.ENOENT, errno.ELOOP, errno.ENOTDIR) and path == db:
        # a link to nothing at the manifest whose folder is missing, or a symlink loop there (realpath goes
        # through the link either way): "error: <db> cannot be opened: No such file or directory" (or "Too
        # many levels of symbolic links") had no fix, no NEXT and no stop record while status FAILed it (B13
        # round 25 D43, D71); only status's own FAIL, so any other ENOENT keeps its line, and a loop above
        # the manifest is state_dir's below
        name = "state_dir.manifest"
    else:
        name = None
    if (
        name is None
        and exc.errno in (errno.ENOTDIR, errno.EEXIST, errno.ELOOP)
        and (path == root or root.is_relative_to(path) or path.is_relative_to(root))
    ):  # the folder, a folder above it, or a path under a folder that is a file (B13 round 23 A58)
        name = "state_dir"
    if name is None:
        return None
    try:
        results = doctor._check_state_dir(config)
    except Exception as err:  # a file above the folder: its stat raises; status's backstop line, the same
        results = [doctor.stopped(config, "state_dir", err)]
    found = next((r for r in results if r.name == name and not r.ok), None)
    if found is None and name == "state_dir.manifest" and exc.errno in (errno.ELOOP, errno.ENOTDIR):
        # a loop above it, or a state folder that is a file: state_dir's own FAIL (E45)
        found = next((r for r in results if r.name == "state_dir" and not r.ok), None)
    return found


def _non_folder_said(args: argparse.Namespace, exc: BaseException) -> str | None:
    """The ``error: state:`` line of :func:`_misplaced` when ``exc`` (or the error it wraps: a
    GovernanceError keeps its cause) is an ENOTDIR or ELOOP under a state folder, or a folder above it, that
    is not a folder, by the stat of those folders (``doctor._under_the_non_folder``), never by the errno
    alone, which would hide unrelated faults; else None (B13 round 24 B88, B89)."""
    try:
        config = _config(args)
        if not doctor._under_the_non_folder(config, exc):
            return None
        root = str(expand(config.state_paths.root))
    except Exception:  # the error path: never a second error over the first
        return None
    return _misplaced(args, NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), root))


def _side_said(args: argparse.Namespace) -> tuple[str, Path] | None:
    """The ``error: state:`` line of a manifest side file that is not a file, status's own state_dir.files
    result and fix (``doctor.side_not_a_file``), and that side's path; None when none is, or on any error
    (round 25 ruling 12: sync, curate and init print what status prints)."""
    try:
        found = doctor.side_not_a_file(_config(args))
    except Exception:  # the error path: never a second error over the first
        return None
    if found is None:
        return None
    side, result = found
    fix = f" (fix: {result.fix})" if result.fix else ""
    return f"error: state: {result.detail}{fix}", side


def _misplaced_next(
    args: argparse.Namespace, said: str, path: object = None, kind: str | None = None
) -> None:
    """The NEXT after :func:`_misplaced`'s line, for any command: a sync's (:data:`STATE_MISPLACED_STEP`)
    also records the stop (:func:`_state_next`, ``kind``) for ``path``, by default the state folder (a side
    file's own path records ``file=manifest-wal`` or ``-shm``: B13 round 25 D2); any other command's is
    :data:`STATE_MISPLACED_COMMAND_STEP` (D44). Curate and the governance commands printed none, so curate's
    "(see NEXT)" pointed at nothing (B13 round 24 B89). None under install.sh, which names its own."""
    if getattr(args, "command", None) == "sync":
        _state_next(args, STATE_MISPLACED_STEP, said=said, path=path or _state_root(args), kind=kind)
    elif os.environ.get(NO_NEXT_HINT_ENV, "").strip() != "1":
        _out(loop.NEXT_PREFIX + STATE_MISPLACED_COMMAND_STEP)  # no sync ran (D44)


def _misplaced(args: argparse.Namespace, exc: OSError) -> str | None:
    """The ``error: state:`` line of a state path that is not where or what it should be, in the words and
    with the fix of the status line that FAILs it (:func:`_misplaced_check`: ``<db> is a folder, not the
    state file``), else None: never a raw traceback (a sync's lock opened a state folder that is a file, B13
    round 22 Z51's verifier), and never "cannot be opened", the access words round 20's X49 took off such a
    path and the setup report reads as this account's access."""
    try:
        config = _config(args)
    except Exception:  # the error path: never a second error over the first
        return None
    result = _misplaced_check(config, exc)
    if result is None:
        return None
    fix = f" (fix: {result.fix})" if result.fix else ""
    return f"error: state: {result.detail}{fix}"


def _manifest_open_fix(args: argparse.Namespace, exc: ManifestOpenError) -> str:
    """The fix of a manifest this account cannot open, as status's state_dir lines give it: on a read-only
    volume (EROFS) ``doctor.read_only_fix`` of the file, the person's, which no chmod reaches (B13 round 22
    Z47, Z78: it got the generic owner-and-mode words); for EACCES or EPERM ``doctor.state_path_fix`` of the
    path refused; ``_ACCESS_FIX``'s generic words for any other cause or a path outside the state folder (B13
    round 21 Y31, Y69). Each adds that the file is not damaged (W65's guard against a move-aside) unless no
    file is there (:func:`_not_damaged`): :data:`MANIFEST_OPEN_FIX` is the generic words with a file there
    (B13 round 23 A60)."""
    cause = exc.__cause__
    filename = cause.filename if isinstance(cause, OSError) else None
    if not isinstance(cause, OSError) or cause.errno not in ACCESS_ERRNOS:
        return _not_damaged(_ACCESS_FIX, filename)
    if cause.errno == errno.EROFS and isinstance(filename, str | bytes | os.PathLike):
        # wherever the file is, no chmod writes on a read-only volume (lane B's reviewer)
        return _not_damaged(doctor.read_only_fix(Path(os.path.realpath(os.fsdecode(filename)))), filename)
    root = _state_root(args)
    where = _in_folder(cause.filename, root)
    if where is None:
        return _not_damaged(_ACCESS_FIX, filename)
    fix = doctor.state_path_fix(Path(os.path.realpath(root)), Path(os.path.realpath(where)))
    return _not_damaged(fix, where)


def _not_damaged(fix: str, filename: object) -> str:
    """``fix`` and that the file itself is not damaged, unless the file is known to be absent (the open that
    would create it was refused): nothing is there to be damaged or moved aside (B13 round 22 Z53). By
    ``lstat``, never ``exists()``: in a folder that refuses a search that raises on Python 3.11 (a second
    error over the first) or reads False for a file that is there, so any error but "not found" keeps the
    guard."""
    if isinstance(filename, str | bytes | os.PathLike):
        try:
            Path(os.fsdecode(filename)).lstat()
        except FileNotFoundError:
            return fix
        except OSError:
            pass
    return f"{fix}{_NOT_DAMAGED}"


def _in_folder(filename: object, root: Path) -> Path | None:
    """``filename`` (an ``OSError``'s) as a path when it is ``root`` or lies under it, else None (both read
    through their links, so /var and /private/var are one folder)."""
    if not isinstance(filename, str | bytes | os.PathLike):
        return None
    path = Path(os.fsdecode(filename))
    real, folder = Path(os.path.realpath(path)), Path(os.path.realpath(root))
    return path if real == folder or real.is_relative_to(folder) else None


def _refused_files(args: argparse.Namespace) -> str | None:
    """The ``error: state:`` line of a sqlite error that could not open or write the manifest when a file of
    it is refused to this account (``doctor.refused_state_files``): every such file, with one fix for them all
    (``doctor.refused_files_fix``); None when none is, so a folder it cannot write keeps sqlite's line."""
    try:
        config = _config(args)
        files = doctor.refused_state_files(config)
        fix = doctor.refused_files_fix(config, files) if files else ""
    except Exception:  # the error path: never a second error over the first
        return None
    if not files:
        return None
    names = ", ".join(str(p) for p in files)
    return f"error: state: {names} cannot be opened for writing by this account (fix: {fix})"


def _refused_folder(args: argparse.Namespace) -> str | None:
    """The ``error: state:`` line of a sqlite error that could not write the manifest when no file of it is
    refused but the state folder is: it is there and this account cannot write or search it (a 0500 folder,
    whose -wal sqlite cannot create), with the folder's fix as status's state_dir line gives it
    (``doctor.state_folder_fix``: another account's chown and a read-only volume before this account's
    ``chmod 700``); None when the folder allows it, so a READONLY with every path writable keeps sqlite's line
    (B13 round 23 A59: a 0500 folder ended a sync on the bare READONLY line, no fix and no NEXT)."""
    try:
        root = _state_root(args)
        if not Path(os.path.realpath(root)).is_dir() or os.access(root, os.W_OK | os.X_OK):
            return None
        fix = doctor.state_folder_fix(root)
    except Exception:  # the error path: never a second error over the first
        return None
    return _folder_unwritable_line(root, fix)


def _folder_unwritable_line(root: Path | str, fix: str) -> str:
    """:func:`_refused_folder`'s line, apart so the report's test builds its row from it (B13 round 24 B102:
    setup_report's ``_STATE_OPEN_RE`` reads these words, and a change here must reach that test)."""
    return f"error: state: the state folder {root} cannot be written by this account (fix: {fix})"


def _state_db(args: argparse.Namespace) -> Path:
    """The manifest a command's database error came from: its config's, or the default state dir's when the
    config does not load."""
    try:
        return expand(_config(args).state_paths.db)
    except Exception:  # the error path: never a second error over the first
        return StatePaths(default_state_dir()).db


class _Formatter(logging.Formatter):
    """The stderr handler's format; a live progress notice (:data:`arm_local.PROGRESS`, the inbox's settle
    wait) is a plain indented line instead, with no time, level or logger name (B13 round 17 U18)."""

    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, PROGRESS, False):
            return f"  {record.getMessage()}"
        return super().format(record)


def _setup_logging(verbosity: int) -> None:
    level = logging.WARNING if verbosity <= 0 else logging.INFO if verbosity == 1 else logging.DEBUG
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_agentsync", False):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    # UTC with a Z, as the launcher's lines beside these in the err.log and the times inside the messages are
    # (a lock holder's "since"): local time with no zone showed two clocks on one line (B13 round 8 J26). No
    # datefmt: with one, formatTime drops the milliseconds. The setup report writes older local lines this
    # same way (setup_report._utc_log_times).
    fmt = _Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fmt.converter = time.gmtime
    fmt.default_time_format = "%Y-%m-%dT%H:%M:%S"
    fmt.default_msec_format = "%s.%03dZ"
    handler.setFormatter(fmt)
    handler._agentsync = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point (console script ``agentsync``); returns the process exit code.

    Runs under umask 077 (restored on return): everything agentsync creates -- the docs repo, pages, git
    objects, cache, logs -- is owner-only, as the LaunchAgent's ``Umask = 63`` already makes it."""
    previous = os.umask(0o077)
    try:
        return _main(argv)
    finally:
        os.umask(previous)


def _main(argv: Sequence[str] | None) -> int:
    net.inject_system_trust()  # idempotent; the module-top injection already ran (C15 req 32)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on usage errors, 0 on --help/--version
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    _setup_logging(int(getattr(args, "verbose", 0) or 0))
    handler: Handler | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        return handler(args)
    except LockHeldError as exc:
        # .holder, not str(exc): that already starts "lock held: " (B13 round 8 J11, J25)
        _err(f"skipped: lock held by {_skip_holder(exc)}")
        if quiet_lock_wait():  # install.sh's first sync: the line above says it on the same stderr (H31)
            pass
        elif _in_job(args):  # the job's err.log, which Recent errors reads (N24)
            _log.warning("skipped: lock held by %s", _lock_holder(exc.who))
        else:  # a terminal's or an agent's run: the line above already said it (B13 round 19 W13, W30)
            _log.debug("skipped: lock held by %s", _lock_holder(exc.who))
        return EXIT_LOCK_HELD
    except LockNotAFileError as exc:
        # a folder, fifo, socket or link at the lock, met by a command no guard covers (exclude-empty, a
        # purge's lock): status's lock FAIL and the person's move aside, never a traceback (E38; ruling 13)
        found = _not_a_file_state(args)
        if found is None:
            raise
        _log.debug("state: %s", exc, exc_info=True)
        _err(found[0])
        _misplaced_next(args, found[0], found[1])
        return EXIT_FAILED
    except ConfigError as exc:
        _err(f"configuration error: {exc}")
        return EXIT_CONFIG
    except StateUnreadableError as exc:
        # for status (B13 round 19 W41), with the command whose read it was (B13 round 20 X54)
        doctor.record_unreadable(_state_db(args), exc.__cause__ or exc, getattr(args, "command", None))
        _err(f"state: {exc} (fix: {doctor.MANIFEST_FIX})")
        return EXIT_STATE
    except AuthBlockedError as exc:
        _err(f"sign-in blocked ({exc.state}{', ' + exc.aadsts if exc.aadsts else ''}): {exc}")
        return EXIT_REAUTH
    except AuthRequiredError as exc:
        _err(f"sign-in required: {exc} — run `{loop.BIN} graph login`")
        return EXIT_REAUTH
    except ManifestSchemaError as exc:
        _err(f"manifest: {exc}")
        return EXIT_FAILED
    except AuthError as exc:
        _err(f"sign-in failed: {exc}")
        return EXIT_FAILED
    except ManifestOpenError as exc:
        # not 65: the file is not damaged, and MANIFEST_FIX's move-aside would throw good state away; exit 1,
        # as V59 treats a file sqlite will not open (B13 round 19 W65). The fix is status's for the same
        # file, by its cause (doctor.state_path_fix: this account's chmod 600 or 700; another account's
        # chown, an ACL or a folder above that refuses a search, the person's), a read-only volume the
        # person's volume fix (B13 round 21 Y31, Y69; round 22 Z47, Z78, Z52)
        _log.debug("state: %s", exc, exc_info=True)
        fix = _manifest_open_fix(args, exc)
        # the path the line names (the manifest for curate, the folder for sync), never the cause's filename:
        # mkdir's is_dir stat reports the folder whichever the line names (B13 round 23 A54)
        named, cut, _ = str(exc).partition(" cannot be opened")
        why = (
            _folder_refuses(Path(named) if cut else _state_db(args), fix)
            if isinstance(exc.__cause__, PermissionError)
            else ""
        )
        line = f"error: state: {exc}{why} (fix: {fix})"
        _err(line)
        _state_next(args, said=line, path=Path(named) if cut else _state_db(args))
        return EXIT_FAILED
    except (ManifestPathError, FileExistsError, NotADirectoryError) as exc:
        # a state path that is not where or what it should be (a folder where the manifest belongs, a file
        # where the state folder belongs): the fix of the status line that FAILs it and a NEXT naming status;
        # a path no line FAILs (a full disk) keeps its one line, and a raw error elsewhere its traceback (B13
        # round 22 Z51: no fix and no NEXT, and a sync's lock over a state folder that is a file a traceback)
        said = _misplaced(args, exc)
        if said is None:
            if not isinstance(exc, ManifestPathError):
                raise
            _err(f"error: {exc}")
            return EXIT_FAILED
        _log.debug("state: %s", exc, exc_info=True)
        _err(said)
        where = exc.filename if exc.filename is not None else getattr(exc.__cause__, "filename", None)
        lost = isinstance(exc, ManifestLostError)
        # no filename by design (errors.py): the stop is the manifest's, as _misplaced_check names it
        if lost:
            where = _state_db(args)
        # every command's NEXT, not only a sync's: curate and hold over a folder at the manifest printed
        # none, while over a file at the state folder they did (B13 round 25 D44)
        _misplaced_next(args, said, where, kind="lost" if lost else None)
        return EXIT_FAILED
    except PermissionError as exc:
        # a state folder this account cannot look inside: the lock's open, the manifest's lstat, a hold's read
        # ended sync, poll and curate on a raw traceback, one call site each (B13 round 20 X73). Only a path
        # in the state folder: any other keeps its traceback
        said = _state_refused(args, exc)
        if said is None:
            raise
        _log.debug("state: %s", exc, exc_info=True)
        _err(said)
        _state_next(args, said=said, path=exc.filename)
        return EXIT_FAILED
    except governance.GovernanceUnreadError as exc:
        # a governance file that is not a file, met inside a command: its status words, a NEXT (E43)
        _err(f"error: state: {exc}")
        _misplaced_next(args, f"error: state: {exc}", exc.path)
        return EXIT_FAILED
    except AgentSyncError as exc:
        said = _non_folder_said(args, exc)
        if said is not None:
            # a governance file under a state folder (or a folder above it) that is not a folder: state_dir's
            # words and the person's fix, as sync says them, never "holds.json: unreadable ([Errno 20] ...);
            # fix or remove it by hand" over a path that does not exist (B13 round 24 B88, B89)
            _err(said)
            _misplaced_next(args, said)
            return EXIT_FAILED
        # a governance file in the state folder this account cannot open (holds.json): the same line
        said = _state_refused(args, exc.__cause__) if isinstance(exc.__cause__, PermissionError) else None
        _err(said or f"error: {exc}")
        if said:
            _state_next(args, said=said, path=getattr(exc.__cause__, "filename", None))
        return EXIT_FAILED
    except sqlite3.DatabaseError as exc:
        # the manifest is the one database agentsync opens: a command no _state_readable wraps (exclude-empty,
        # curate, purge) says the same one line and exits 65, as --help says (B13 round 17 U66)
        db = _state_db(args)
        if doctor.unreadable_code(exc) or doctor.manifest_unreadable(db) is not None:
            doctor.record_unreadable(db, exc, getattr(args, "command", None))
            _err(f"state: {db} cannot be read: {exc} (fix: {doctor.MANIFEST_FIX})")
            return EXIT_STATE
        side = _side_said(args) if doctor.side_may_explain(exc) else None
        if side is not None:
            # a -wal or -shm that is not a file (a folder, a socket, a link to nothing): "OperationalError:
            # unable to open database file", no fix, no NEXT and no stop record, while status FAILed it with
            # the person's move aside; its line, the misplaced NEXT, exit 1 (B13 round 25 D2, D8, D21, D70)
            said, where = side
            _log.debug("state: %s", exc, exc_info=True)
            _err(said)
            _misplaced_next(args, said, where)
            return EXIT_FAILED
        said = (_refused_files(args) or _refused_folder(args)) if doctor.cannot_open(exc) else None
        if said is not None:
            # a -wal or -shm (or the manifest) this account cannot write: sqlite's READONLY gave the bare
            # line below, no fix and no NEXT, while status named the chmod (B13 round 22 Z80); exit 1, not 65
            _log.debug("state: %s", exc, exc_info=True)
            _err(said)
            folder = said.startswith("error: state: the state folder ")
            _state_next(args, said=said, path=_state_root(args) if folder else None)
            return EXIT_FAILED
        if not doctor.environmental(exc):  # a code defect (IntegrityError, a missing column) keeps its
            raise  # traceback (U72); so does any code no one listed (B13 round 18 V59: an allowlist)
        # a locked or busy database, a full disk, a read-only or failing volume, a file that will not
        # open: one line, its traceback at -vv (B13 round 16 T12; round 18 V59: U72 had made the
        # others tracebacks)
        _log.debug("database: %s", exc, exc_info=True)
        _err(f"error: {type(exc).__name__}: {exc}")
        return EXIT_FAILED
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
