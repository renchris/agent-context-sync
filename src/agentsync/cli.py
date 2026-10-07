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
import dataclasses
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
from agentsync.config import (
    Config,
    append_to_config,
    canonical_source_root,
    default_config_text,
    derive_source_id,
    ensure_inbox,
    load_config,
    local_source_table,
    parse_config,
    parse_size,
)
from agentsync.cycle import _tighten_own_paths, run_cycle, source_statuses
from agentsync.errors import (
    AgentSyncError,
    AuthError,
    AuthRequiredError,
    ConfigError,
    LockHeldError,
    ManifestSchemaError,
)
from agentsync.graph.auth import TokenProvider
from agentsync.graph.errors import AuthBlockedError
from agentsync.loop import NO_NEXT_HINT_ENV
from agentsync.manifest import Manifest
from agentsync.model import CycleMode, CycleReport, LintFinding, SourceKind
from agentsync.ops import doctor, launchd
from agentsync.ops.lock import SingleWriterLock, read_heartbeat
from agentsync.paths import DocsLayout, default_config_path, expand, is_under
from agentsync.publish import Publisher, archive_path
from agentsync.slug import collision_key

EXIT_OK = 0
EXIT_FAILED = 1  # a source failed, or a blocking lint or curate finding fired
EXIT_USAGE = 2
EXIT_LOCK_HELD = 75  # EX_TEMPFAIL: another cycle holds the lock; logged "skipped: lock held"
EXIT_REAUTH = 77  # EX_NOPERM: auth REAUTH_REQUIRED
EXIT_CONFIG = 78  # EX_CONFIG: sources.toml invalid
EXIT_TCC_PENDING = launchd.EXIT_TCC_PENDING  # 79: only from the signed launcher that wraps a LaunchAgent run

_EPILOG = """\
exit codes:
  0   ok
  1   failed: a source failed, a blocking lint fired, curate found a blocking finding, a status check
      failed, a purge/compaction was not verified, or discovery was incomplete (curate rows are not a failure)
  2   usage error (bad arguments)
  75  skipped: another agentsync cycle holds the single-writer lock (EX_TEMPFAIL; launchd retries later);
      `sync` (not a LaunchAgent's run) first waits up to 10 minutes for it
  77  sign-in required: a Graph source needs `agentsync graph login` (auth REAUTH_REQUIRED), or Entra
      blocked sign-in (blocked: device|policy|consent|assignment, config-invalid; the message names the fix)
  78  configuration invalid: sources.toml or policy.toml is missing or wrong (the message names the key)
  79  (LaunchAgent runs only) TCC_PENDING: the signed launcher's canary or watchdog timed out waiting for a
      macOS privacy prompt; the cycle never ran or was stopped (see the job's .err.log, `agentsync status`)

files: ~/agent-context/sources.toml (config) · ~/agent-context/docs (the docs git repo) ·
~/Library/Application Support/agentsync (manifest, lock, heartbeat, governance/) · ~/Library/Caches/agentsync
"""

_log = logging.getLogger("agentsync.cli")
_TCC_TOKENS = ("TCC_PENDING", "TCC_DENIED")

Handler = Callable[[argparse.Namespace], int]


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
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
        "the deletion is real: clear SOURCE's tripped deletion breaker and apply its removals",
        _cmd_accept_deletions,
    )
    p.add_argument("source", metavar="SOURCE", help="the source id the breaker tripped on")

    p = add(
        "reconcile",
        "run one full-enumeration cycle (agentsync sync runs one when due)",
        _cmd_reconcile,
        hidden=True,
    )
    p.add_argument(
        "--source", action="append", default=[], metavar="ID", help="only this source (repeatable)"
    )
    p.add_argument(
        "--accept-deletions",
        action="store_true",
        help="old spelling of agentsync accept-deletions SOURCE (needs --source)",
    )

    add(
        "status",
        "the single read-only check: NEXT, the loop line, every preflight check with its fix (rc 1 on any "
        "FAIL), then policy, holds, queued purges and each source",
        _cmd_status,
    )
    add("doctor", "renamed: run agentsync status", _cmd_status_renamed, hidden=True)
    add(
        "curate",
        "the curation work list: every lint finding, mirror changes since the last checkpoint, the refresh "
        "queue, then UNCOVERED mirror pages no curated page cites, then NEXT (rc 1 only on a blocking "
        "finding)",
        _cmd_curate,
    )
    for old_name in ("curate-queue", "lint", "refresh-queue"):
        add(old_name, "renamed: run agentsync curate", _cmd_curate_renamed, hidden=True)
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

    p = add("policy", "renamed: run agentsync status", _cmd_status_renamed, hidden=True)
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


def _print_report(report: CycleReport, *, run_budget: int | None = None) -> None:
    """Print a cycle's report, ending with one line for the whole run, ``converted N, deferred M online-only``
    (N files read and converted, M online-only files left for a later run's download budget). With
    ``run_budget`` (``sync --materialise-budget``) the per-file "exceeds the per-cycle budget" alarms are one
    count per source: at 0 every changed online-only file would otherwise get one."""
    commit = report.commit_sha[:12] if report.commit_sha else "none (no content change)"
    _out(
        f"run {report.run_id} · mode {report.mode.value} · commit {commit} · {len(report.changes)} change(s)"
    )
    for s in report.sources:
        counts = ", ".join(f"{k.value}={v}" for k, v in sorted(s.counts.items(), key=lambda kv: kv[0].value))
        state = s.pass_kind.value if s.pass_kind else f"skipped ({s.skipped_reason or '-'})"
        bits = [state, "complete" if s.enumeration_complete else "INCOMPLETE"]
        if s.deferred:
            bits.append(f"{s.deferred} deferred")
        if s.breaker_tripped:
            bits.append("BREAKER TRIPPED")
        bits.append("cursor advanced" if s.cursor_advanced else "cursor held")
        _out(f"  {s.source_id}: {' · '.join(bits)}" + (f" · {counts}" if counts else ""))
        over = [a for a in s.alarms if run_budget is not None and _OVER_BUDGET in a]
        for a in s.alarms:
            if a not in over:
                _out(f"    alarm: {a}")
        if over:
            _out(
                f"    {len(over)} online-only file(s) left for a later run (over this run's "
                f"--materialise-budget of {run_budget} bytes)"
            )
        for e in s.errors:
            _out(f"    error: {e}")
    for f in report.lint_findings:
        _out(f"  {'BLOCKING' if f.blocking else 'warning'} {f.code} {f.path}: {f.message}")
    if report.broke_stale_lock:
        _out("  note: a stale lock from a dead run was broken; every source ran a full pass")
    if report.auth_required:
        _out("  sign-in required: run `agentsync graph login` (errors above name any IT action)")
    _print_checkpoint(report)
    converted = sum(s.converted for s in report.sources)
    online = sum(s.deferred_online_only for s in report.sources)
    _out(f"converted {converted}, deferred {online} online-only")


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
        _out(
            f"checkpoint held: {len(report.checkpoint_blockers)} curation error(s); fix them, then "
            "sync again (every sync retries it)"
        )
        for f in report.checkpoint_blockers:
            _out(f"  ERROR {f.code} {f.path}: {f.message}")
    elif report.checkpoint == "failed":
        _out(
            f"checkpoint not recorded (the sync itself landed; the next sync retries it): "
            f"{report.checkpoint_detail}"
        )


def _run(config: Config, **kwargs: object) -> int:
    report = run_cycle(config, **kwargs)  # type: ignore[arg-type]
    budget = kwargs.get("budget_bytes")
    _print_report(report, run_budget=budget if isinstance(budget, int) else None)
    if kwargs.get("mode") is not CycleMode.DRY_RUN and report.exit_code in (EXIT_OK, EXIT_FAILED):
        # C15 req 42 outside install-agent: exclude mirror/, .git, the cache and the manifest from Time
        # Machine once they exist (a cheap xattr check; tmutil runs only for a path still missing it).
        try:
            for line in governance.ensure_time_machine_exclusions(config):
                _log.warning("time machine: %s", line)
        except (OSError, AgentSyncError) as exc:
            _log.warning("time machine exclusions not applied: %s", exc)
    return report.exit_code


def _owner_only_dirs(config: Config) -> list[Path]:
    """Directories that hold tenant data and must not be group/world readable (existing ones)."""
    candidates = [
        expand(config.docs_repo),
        expand(config.docs_repo) / ".git",
        expand(config.cache_dir),
        expand(config.log_dir),
        expand(config.state_dir),
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
        _out(f"tightened {inside} path(s) inside the docs repo, the cache or the logs: they hold tenant data")
    with Manifest(config.state_paths.db) as manifest:
        written = Publisher(config, manifest).ensure_scaffold()
    # The count carries its verb: install.sh calls add-source once per folder, and a bare "0 scaffold
    # file(s)" on the second call read as the first call's files undone.
    scaffold = f"{len(written)} scaffold file(s) written" if written else "scaffold up to date"
    _out(f"docs repo {config.docs_repo} ({'created' if created else 'exists'}, no remote; {scaffold})")
    findings = _refuse_remotes(config, gov)
    for f in findings:
        _err(f"governance: {f}")
    for line in governance.ensure_time_machine_exclusions(config):
        _out(f"time machine: {line}")
    if any(s.kind is not SourceKind.INBOX for s in config.sources):
        _out(f"sources: {', '.join(s.id for s in config.sources)}")
    else:  # the inbox alone is not a source to sync (KISS K05): point at the one verb that adds one
        besides = " besides the inbox" if config.sources else ""
        _out(f"sources: none yet{besides} (run agentsync add-source <folder>)")
    return EXIT_FAILED if findings else EXIT_OK


def _cmd_init(args: argparse.Namespace) -> int:
    """Hidden since KISS K14 (add-source does the same and adds a folder): idempotent, no options."""
    cfg_path = expand(args.config or default_config_path())
    if cfg_path.exists():
        _out(f"using existing {cfg_path}")
        config = load_config(cfg_path)
    else:
        text = default_config_text()
        config = parse_config(text, config_path=cfg_path)  # validate before writing anything
        _write_config(cfg_path, text)
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
        _out(f"added inbox source {added.id!r} ({added.path}): drop files you save by hand here")
    return config


def _same_folder(configured: Path, path: Path) -> bool:
    """Whether ``path`` (an existing folder) is the folder a source already has as ``configured``. macOS
    takes a name in any case and in either Unicode form for the same folder, so a path typed back in lower
    case, or with a composed é where the disk holds e and an accent, is not a second folder. The key is the
    one the manifest and the docs repo use for "the same path" (:func:`agentsync.slug.collision_key`); the
    file system then confirms it, so on a case-sensitive volume two folders that differ only in case stay
    two (and a configured folder that is gone is not the one that exists)."""
    if configured == path:
        return True
    if collision_key(str(configured)) != collision_key(str(path)):
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
    if known is not None:
        _out(
            f"already configured: source {known.id!r} ({known.kind.value}, {known.state.value}) has path "
            f"{known.path} in {config.config_path}"
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
        _out(f"added source {sid!r} to {config.config_path}:")
        _out(table.strip("\n"))
    return _ensure_setup(config)


def _cmd_sync(args: argparse.Namespace) -> int:
    """One cycle; without ``--mode`` (an agent's or a person's run, never a LaunchAgent's) the report ends
    with the loop's NEXT and WAITING ON YOU lines, unless :data:`NO_NEXT_HINT_ENV` is 1 (KISS K01)."""
    config = _config(args)
    mode = CycleMode(args.mode) if args.mode else None
    if args.materialise_budget is None:
        rc = _run(config, mode=mode)
    else:
        budget = parse_size(args.materialise_budget, where="--materialise-budget")
        rc = _run(config, mode=mode, budget_bytes=budget)
    if mode is None and os.environ.get(NO_NEXT_HINT_ENV, "").strip() != "1":
        for line in loop.next_lines(config):
            _out(line)
    return rc


def _cmd_reconcile(args: argparse.Namespace) -> int:
    config = _config(args)
    if args.accept_deletions and not args.source:
        _err("--accept-deletions needs a source: run agentsync accept-deletions SOURCE")
        return EXIT_USAGE
    accept = tuple(args.source) if args.accept_deletions else ()
    return _run(config, mode=CycleMode.RECONCILE, only=tuple(args.source), accept_deletions=accept)


def _cmd_accept_deletions(args: argparse.Namespace) -> int:
    """The operator asserts SOURCE's absent files really were deleted: a RECONCILE of that source alone that
    clears its breaker and applies the held removals (the breaker itself is unchanged).  Like interactive
    sync it waits for a running cycle's lock: launchd never re-runs an operator's assertion after exit 75."""
    config = _config(args)
    return _run(
        config,
        mode=CycleMode.RECONCILE,
        only=(args.source,),
        accept_deletions=(args.source,),
        wait_for_lock=True,
    )


def _cmd_mat(args: argparse.Namespace) -> int:
    config = _config(args)
    budget = parse_size(args.budget, where="--budget") if args.budget is not None else None
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


def _status_lines(config: Config) -> list[str]:
    """What ``agentsync status`` prints (read-only); ``setup-report`` embeds the same lines."""
    out: list[str] = [f"config: {config.config_path}", f"docs repo: {config.docs_repo}"]
    lock_path = config.state_paths.lock
    held = lock_path.exists() and SingleWriterLock.is_held(lock_path)
    out.append(f"lock: {SingleWriterLock.describe(lock_path) if held else 'free'}")
    gov = governance.load_governance(config.config_path)
    for h in governance.active_holds(config.state_paths.root, gov):
        out.append(f"HOLD: {h.describe()} (purge and compaction suspended)")
    queued = governance.pending_purges(config.state_paths.root)
    if queued:
        out.append(f"queued purges: {len(queued)} (run `{loop.BIN} purge --queue`)")
    try:
        cstate, cdetail = governance.compaction_state(config.docs_repo, gov)
    except AgentSyncError as exc:
        cstate, cdetail = "unknown", str(exc)
    out.append(f"retention: {cstate}: {cdetail}")
    for event in _launcher_events(config):
        out.append(f"launcher: {event}")
    db = config.state_paths.db
    if not db.exists():
        out.append("manifest: none yet (no sync has run)")
        return out
    with Manifest(db) as manifest:
        runs = manifest.last_runs(5)
        out.append(
            "last runs: " + ("; ".join(f"{r} {m} {s} {(c or '-')[:12]}" for r, m, s, c in runs) or "none")
        )
        statuses = source_statuses(config, manifest, now=datetime.now(UTC))
    heartbeat = read_heartbeat(config.state_paths.heartbeat)
    for st in statuses:
        hb = heartbeat.get(st.source_id, {})
        out.append(
            f"  {st.source_id} ({st.kind.value}, {st.state}): baseline "
            f"{'complete' if st.baseline_complete else 'INCOMPLETE'} · complete "
            f"{'yes' if st.enumeration_complete else 'no'} · live {st.live} (dataless {st.dataless}) · "
            f"quarantined {st.quarantined} · deferred {st.deferred} · breaker {st.breaker} · "
            f"auth {st.auth} · "
            f"last success {hb.get('last_success_at', 'never')}"
        )
    state_md = config.layout.state_md
    if state_md.is_file():
        out.append(f"details: {state_md}")
    return out


def _policy_lines(config: Config) -> list[str]:
    """The effective content policy (what ``policy show`` printed; a broken policy is the ``policy`` FAIL)."""
    try:
        pol = content_policy.load_policy(config)
    except ConfigError:
        return ["policy: invalid (see the [FAIL] policy line)"]
    sidecar = config.config_path.parent / content_policy.POLICY_FILE_NAME
    return [
        f"policy: {config.config_path} [policy]" + (f" + {sidecar}" if sidecar.is_file() else ""),
        f"  labels_active: {'true' if pol.labels_active else 'false'}",
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
    keeps those out of NEXT) or a bare ``agentsync`` that is not on PATH; the [FAIL] line carries it."""
    if result.fix:
        return f"the {result.name} check failed: do what the fix on its [FAIL] line below says"
    return f"the {result.name} check failed: its [FAIL] line below says why"


def _guarded(name: str, fn: Callable[[], list[str]]) -> list[str]:
    """``fn()``, or one line naming the error: a part of status that cannot be read never hides the checks
    (the matching FAIL, e.g. governance.config or a manifest check, explains it)."""
    try:
        return fn()
    except Exception as exc:
        return [f"{name}: cannot read the state: {type(exc).__name__}: {exc}"]


def _status_checks(config: Config, *, offline: bool = False) -> list[doctor.CheckResult]:
    """Every check of ``status``. ``offline`` (setup-report): no Graph probe and no TCC canary (it can raise
    a privacy prompt)."""
    checks = doctor.run_checks(config, tcc_canary=not offline and _tcc_canary_due(config))
    return checks + _extra_checks(config, offline=offline)


def _loop_line(config: Config) -> str:
    try:
        return loop.status_line(config)
    except Exception as exc:
        return f"loop: cannot read the state: {type(exc).__name__}: {exc}"


def _build_status(config: Config, *, brief: bool = False) -> _Status:
    """``status``'s output. ``brief`` (the ``doctor`` alias, or AGENTSYNC_NO_NEXT_HINT=1, both install.sh's):
    no detail and no policy lines, so install.sh's output (and setup-report's tail of it) holds what the old
    doctor printed and no label names."""
    checks = _status_checks(config)
    fixes = [_fail_step(r) for r in checks if not r.ok and r.severity is doctor.Severity.ERROR]
    hint_off = os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1"
    next_lines: list[str] = []
    if not hint_off:
        next_lines = _guarded("NEXT", lambda: loop.next_lines(config, fixes=fixes))
    detail: list[str] = []
    if not (brief or hint_off):
        detail = _guarded("status", lambda: _status_lines(config))
        detail += _guarded("policy", lambda: _policy_lines(config))
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
        _err("renamed: run agentsync status")
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
                    "not probed (setup-report makes no network calls; `agentsync status` probes it)",
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
    from agentsync.graph import auth  # noqa: PLC0415 - imports pymsalruntime only when Graph is configured

    reason = auth.broker_unavailable_reason()
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


def _governance_checks(config: Config) -> list[doctor.CheckResult]:
    try:
        gov = governance.load_governance(config.config_path)
    except ConfigError as exc:
        return [_check("governance.config", False, str(exc), doctor.Severity.ERROR, fix="fix [governance]")]
    out: list[doctor.CheckResult] = []
    findings = _refuse_remotes(config, gov)
    if findings:
        out += [
            _check("governance.remote", False, f, doctor.Severity.ERROR, fix="remove the remote (C15 req 36)")
            for f in findings
        ]
    else:
        out.append(
            _check("governance.remote", True, "no disallowed remote on the docs repo", doctor.Severity.INFO)
        )
    holds = governance.active_holds(config.state_paths.root, gov)
    out += [_check("governance.hold", True, h.describe(), doctor.Severity.INFO) for h in holds]
    if sys.platform == "darwin" and os.environ.get("AGENTSYNC_TM_EXCLUDE", "1") != "0":
        status = governance.time_machine_status(config)
        missing = [p for p, ok in status if ok is False and not p.name.endswith(("-wal", "-shm"))]
        if missing:
            out.append(
                _check(
                    "governance.time_machine",
                    False,
                    "not excluded from Time Machine: " + ", ".join(str(p) for p in missing),
                    doctor.Severity.WARN,
                    fix="agentsync sync (or: tmutil addexclusion " + " ".join(map(str, missing)) + ")",
                )
            )
        else:
            out.append(
                _check("governance.time_machine", True, "tenant-data paths excluded", doctor.Severity.INFO)
            )
    try:
        state, detail = governance.compaction_state(config.docs_repo, gov)
    except AgentSyncError as exc:
        state, detail = "overdue", f"cannot read the docs repo history: {exc}"
    severity = {"ok": doctor.Severity.INFO, "due": doctor.Severity.WARN}.get(state, doctor.Severity.ERROR)
    compact_fix = "agentsync sync (its next full pass compacts); release any hold"
    fix = None if state == "ok" else compact_fix
    out.append(_check("governance.compaction", state == "ok", f"{state}: {detail}", severity, fix=fix))
    queued = governance.pending_purges(config.state_paths.root)
    if queued:
        detail = f"{len(queued)} purge request(s) queued (confirmed upstream deletions / label escalations)"
        if os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
            # install.sh prints the loop's WAITING ON YOU line for the queue: no second instruction here.
            out.append(
                doctor.CheckResult(
                    "governance.purge_queue", False, detail, doctor.Severity.WARN, note=_PURGE_YOURS_NOTE
                )
            )
        else:
            out.append(
                _check(
                    "governance.purge_queue",
                    False,
                    detail,
                    doctor.Severity.WARN,
                    fix="agentsync purge --queue",
                )
            )
    return out


def _policy_check(config: Config) -> list[doctor.CheckResult]:
    try:
        pol = content_policy.load_policy(config)
    except ConfigError as exc:
        return [_check("policy", False, str(exc), doctor.Severity.ERROR, fix="fix [policy] / policy.toml")]
    if not pol.labels_active:
        detail = "no label rules (encrypted Office/PDF files are still stubbed, never converted)"
    else:
        detail = (
            f"{len(pol.exclude_label_ids)} excluded label id(s), {len(pol.exclude_label_names)} name(s), "
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
        try:
            out += fn()
        except Exception as exc:  # one broken check must not hide the others
            out.append(
                _check(name, False, f"check crashed: {type(exc).__name__}: {exc}", doctor.Severity.ERROR)
            )
    return out


def _report_hooks() -> setup_report.ReportHooks:
    """setup-report's hooks, split along status's cost: the Doctor section runs the offline checks once (under
    the rest of the report's budget), the Status section only the cheap loop line and detail lines (under its
    own 4 s). No policy detail (label names stay out of a report meant for sharing). The Summary's Loop line
    (KISS K16b) takes the loop's NEXT from ``loop_next``, with the doctor checks' FAILs as rule 1's fixes and
    without rule 9's mirror walk; the Status section still prints no NEXT line."""
    fixes: list[str] = []

    def checks(config: Config) -> list[str]:
        results = _status_checks(config, offline=True)
        fixes[:] = [_fail_step(r) for r in results if not r.ok and r.severity is doctor.Severity.ERROR]
        return doctor.format_results(results).splitlines()

    return setup_report.ReportHooks(
        doctor=checks,
        status=lambda config: [_loop_line(config), *_guarded("status", lambda: _status_lines(config))],
        loop_next=lambda config: loop.next_lines(config, fixes=fixes, count_queue=False),
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
    try:
        setup_report.write_report(out, text)
    except OSError as exc:
        _err(f"setup-report: cannot write {out}: {exc.strerror or exc}; the report follows on stdout")
        sys.stdout.write(text)
        _out(link)
        return EXIT_FAILED
    what = f"{red.total} replacement(s) of {red.values} value(s)"
    embedded = "friction log embedded" if friction.is_file() else f"no friction log found at {friction}"
    _out(f"wrote {out} ({what}; {embedded})")
    _out(link)  # always the last line: setup prompt step 3 (v5: step 5) shows it (no "next:" hint)
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


def _curate_findings(config: Config) -> list[LintFinding]:
    """Every whole-repo land-gate lint, TOPIC-BUDGET (the one curation finding that stays a warning), then
    the checkpoint blockers from the base the next sync checks (``loop.checkpoint_findings``): every curation
    lint finding and UNLISTED as an ERROR, STALE pins and missing sources of pages changed since that base."""
    repo, layout = config.docs_repo, config.layout
    findings: list[LintFinding] = []
    findings += lints.lint_no_symlinks(repo)
    findings += lints.lint_mirror_frontmatter(repo)
    findings += lints.lint_paths(repo)
    findings += lints.lint_no_cache_in_git(repo)
    findings += lints.lint_no_tokens(repo)
    findings += lints.lint_index_budget(repo)
    findings += [f for f in curate.generate_depends(layout)[2] if f.code == "TOPIC-BUDGET"]
    findings += loop.checkpoint_findings(config)
    return sorted(findings, key=lambda f: (not f.blocking, f.code, f.path))


def _cmd_curate(args: argparse.Namespace) -> int:
    """KISS K09: findings, then the work rows (none during the baseline hold), then the loop's NEXT; exit 1
    only on a blocking finding, never because there is work."""
    config = _config(args)
    layout = config.layout
    findings = _curate_findings(config)
    for f in findings:
        _out(f"{'ERROR' if f.blocking else 'warn '} {f.code} {f.path}: {f.message}")
    blocking = sum(1 for f in findings if f.blocking)
    _out(f"{len(findings)} finding(s), {blocking} blocking")
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
        _out(f"{len(verdicts)} refresh-queue row(s), {len(uncovered)} uncovered mirror page(s)")
    # A blocking finding is rule 1 here: the loop's own rules never see the land-gate lints, and rule 7 counts
    # checkpoint blockers only while something is pending or dirty, so NEXT must not send the agent past it.
    fixes = (
        [f"{blocking} blocking finding(s) above: fix every ERROR, then run `{loop.BIN} sync`"]
        if blocking
        else []
    )
    for line in loop.next_lines(config, fixes=fixes):
        _out(line)
    return EXIT_FAILED if blocking else EXIT_OK


def _cmd_curate_renamed(args: argparse.Namespace) -> int:
    """Hidden aliases (KISS K09): curate-queue, lint and refresh-queue run curate under their old names."""
    _err("renamed: run agentsync curate")
    return _cmd_curate(args)


def _cmd_checkpoint(args: argparse.Namespace) -> int:
    """Hidden alias (KISS K06): run an interactive sync, which records the checkpoint itself."""
    return _run(_config(args), mode=None)


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
    _out(f"{len(created)} page(s) adopted; the next `agentsync sync` commits them")
    return EXIT_OK


def _cmd_migrate(args: argparse.Namespace) -> int:
    """KISS K13b: a no-op kept so older install.sh runs and scripts still exit 0; opening the manifest
    migrates it (with its pre-v<N> copy) in every command that uses it."""
    _out("migration is automatic: every agentsync command that opens the manifest migrates it")
    return EXIT_OK


def _cmd_graph(args: argparse.Namespace) -> int:
    from agentsync.graph.auth import MsalAuth, settings_from_config  # noqa: PLC0415 - msal only when used

    config = _config(args)
    auth = MsalAuth(settings_from_config(config))
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
    _out("check: agentsync status (it names the File Provider target)")
    return EXIT_OK


def _cmd_uninstall(args: argparse.Namespace) -> int:
    config = _config(args)
    for suffix in ("poll", "reconcile"):
        label = f"{config.launchd_label_prefix}.{suffix}"
        _out(f"{label}: {'removed' if launchd.uninstall(label) else 'not installed'}")
    return EXIT_OK


# ---- governance (C15 section 7, requirements 36-42) ----------------------------------------------------


def _print_purge(rep: governance.PurgeReport) -> None:
    state = "dry run" if rep.dry_run else ("VERIFIED" if rep.verified else "NOT VERIFIED")
    _out(
        f"purge {rep.selector} ({rep.reason.value}): {state} · {len(rep.items)} item(s) · "
        f"{len(rep.docs_paths)} docs path(s) · {rep.commits_rewritten} commit(s) rewritten · "
        f"{rep.blobs_targeted} blob(s) targeted · {rep.cache_entries_removed} cache entr(ies) removed"
    )
    for p in rep.docs_paths:
        _out(f"  path: {p}")
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
    if args.queue:
        if args.selector:
            _err("purge: pass SELECTOR or --queue, not both")
            return EXIT_USAGE
        dry_run = bool(args.dry_run)
        reports = governance.run_purge_queue(config, dry_run=dry_run)
        for rep in reports:
            _print_purge(rep)
        left = governance.pending_purges(config.state_paths.root)
        if dry_run:
            _out(f"{len(reports)} queued purge(s) previewed (dry run, nothing written); {len(left)} queued")
            return EXIT_OK
        _out(f"{len(reports)} queued purge(s) run; {len(left)} still queued")
        return EXIT_OK if all(r.verified for r in reports) and not left else EXIT_FAILED
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
    rep = governance.compact_history(config, args.keep_days, dry_run=bool(args.dry_run))
    state = "dry run" if rep.dry_run else ("VERIFIED" if rep.verified else "NOT VERIFIED")
    _out(
        f"compact-history before {rep.cutoff}: {state} · {rep.squashed} commit(s) squashed · {rep.kept} kept"
        + (f" · new root {rep.new_root[:12]}" if rep.new_root else "")
    )
    for s in rep.survivors:
        _out(f"  SURVIVOR: {s}")
    if rep.dangling_recovery_hints:
        _out(f"  {rep.dangling_recovery_hints} tombstone recovery hint(s) now point into squashed history")
    if rep.note:
        _out(f"  note: {rep.note}")
    return EXIT_OK if rep.dry_run or rep.verified or rep.squashed == 0 else EXIT_FAILED


def _cmd_hold(args: argparse.Namespace) -> int:
    config = _config(args)
    root = config.state_paths.root
    if args.list or (args.scope is None and not args.release):
        holds = governance.active_holds(root, governance.load_governance(config.config_path))
        for h in holds:
            _out(h.describe())
        _out(f"{len(holds)} active hold(s)")
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


def _setup_logging(verbosity: int) -> None:
    level = logging.WARNING if verbosity <= 0 else logging.INFO if verbosity == 1 else logging.DEBUG
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_agentsync", False):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
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
        _err(f"skipped: lock held ({exc})")
        _log.warning("skipped: lock held")
        return EXIT_LOCK_HELD
    except ConfigError as exc:
        _err(f"configuration error: {exc}")
        return EXIT_CONFIG
    except AuthBlockedError as exc:
        _err(f"sign-in blocked ({exc.state}{', ' + exc.aadsts if exc.aadsts else ''}): {exc}")
        return EXIT_REAUTH
    except AuthRequiredError as exc:
        _err(f"sign-in required: {exc} — run `agentsync graph login`")
        return EXIT_REAUTH
    except ManifestSchemaError as exc:
        _err(f"manifest: {exc}")
        return EXIT_FAILED
    except AuthError as exc:
        _err(f"sign-in failed: {exc}")
        return EXIT_FAILED
    except AgentSyncError as exc:
        _err(f"error: {exc}")
        return EXIT_FAILED
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
