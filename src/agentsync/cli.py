"""``agentsync`` command line (owner: integrator).

Subcommands: init · sync [--once] [--mode poll|reconcile|dry_run] [--dry-run] [--source ID ...]
[--materialise-budget BYTES] · reconcile [--source ID ...] [--accept-deletions] · status · doctor [--network]
· lint · refresh-queue · materialise [--budget BYTES] [PATH ...] · adopt SRC_DIR · migrate · graph
login|logout|whoami|discover (also as top-level login · logout · whoami · discover; login [--device-code];
discover [--url URL ...] [--toml]) · install-agent [--interval SECONDS] [--no-backup-exclusions] ·
uninstall-agent · purge SELECTOR | --queue · compact-history · hold · offboard [--purge-data] [--confirm
DOCS_REPO] · policy show · add-source PATH [--id ID] · setup-report [--out PATH] [--friction PATH]
[--no-redact] · it-request --out PATH.
Every subcommand accepts ``--config PATH`` (default ~/agent-context/sources.toml) and ``-v/--verbose``.
``AGENTSYNC_NO_NEXT_HINT=1`` (scripts/install.sh sets it) drops the ``next:`` hint of init and add-source;
``setup-report --out`` ends with the issue link, never a hint.

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
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentsync import __version__, curate, gitops, governance, it_request, lints, net, setup_report, skill
from agentsync import policy as content_policy
from agentsync.config import (
    SOURCE_ID_RE,
    Config,
    append_to_config,
    canonical_source_root,
    default_config_text,
    derive_source_id,
    inbox_source_table,
    load_config,
    local_source_table,
    parse_config,
    parse_size,
)
from agentsync.cycle import run_cycle, source_statuses
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
from agentsync.manifest import Manifest
from agentsync.model import CycleMode, CycleReport, LintFinding
from agentsync.ops import doctor, launchd
from agentsync.ops.lock import SingleWriterLock, read_heartbeat
from agentsync.paths import default_config_path, expand, is_under
from agentsync.publish import Publisher, archive_path

EXIT_OK = 0
EXIT_FAILED = 1  # a source failed, a blocking lint fired, or refresh-queue found rows
EXIT_USAGE = 2
EXIT_LOCK_HELD = 75  # EX_TEMPFAIL: another cycle holds the lock; logged "skipped: lock held"
EXIT_REAUTH = 77  # EX_NOPERM: auth REAUTH_REQUIRED
EXIT_CONFIG = 78  # EX_CONFIG: sources.toml invalid
EXIT_TCC_PENDING = launchd.EXIT_TCC_PENDING  # 79: only from the signed launcher that wraps a LaunchAgent run

_EPILOG = """\
exit codes:
  0   ok
  1   failed: a source failed, a blocking lint fired, the refresh queue has rows, a doctor check failed,
      a purge/compaction was not verified, or discovery was incomplete
  2   usage error (bad arguments), or refresh-queue could not read DEPENDS.tsv
  75  skipped: another agentsync cycle holds the single-writer lock (EX_TEMPFAIL; launchd retries later)
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


NO_NEXT_HINT_ENV = "AGENTSYNC_NO_NEXT_HINT"
"""Set to 1 (scripts/install.sh does) and init, add-source and setup-report print no ``next:`` hint: the
caller prints its own single next step."""


def _next_hint(text: str) -> None:
    """Print ``next: <text>`` unless :data:`NO_NEXT_HINT_ENV` is 1."""
    if os.environ.get(NO_NEXT_HINT_ENV, "").strip() != "1":
        _out(f"next: {text}")


# ---------------------------------------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------------------------------------


def _common(sub_default: object) -> argparse.ArgumentParser:
    """--config / -v, accepted before or after the subcommand."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--config",
        type=Path,
        default=sub_default,
        metavar="PATH",
        help="sources.toml (default ~/agent-context/sources.toml)",
    )
    p.add_argument(
        "-v", "--verbose", action="count", default=sub_default, help="more logging (-v info, -vv debug)"
    )
    return p


def _graph_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--device-code",
        action="store_true",
        help="login: go straight to device-code sign-in (refused unless [graph] allow_device_code = true)",
    )
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

    p = add("init", "write sources.toml, create the docs repo (no remote) and its scaffold", _cmd_init)
    p.add_argument(
        "--docs-repo", type=Path, metavar="PATH", help="docs git repo (default ~/agent-context/docs)"
    )
    p.add_argument(
        "--source-local",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="add a live local source for this folder (repeatable)",
    )
    p.add_argument("--force", action="store_true", help="overwrite an existing sources.toml")

    p = add(
        "add-source",
        "append a live local source for a folder to an existing sources.toml (idempotent)",
        _cmd_add_source,
    )
    p.add_argument(
        "path",
        type=Path,
        nargs="?",
        metavar="PATH",
        help="the folder to sync (it must exist; with --inbox it is created, default <docs repo>/../inbox)",
    )
    p.add_argument(
        "--inbox",
        action="store_true",
        help="add a drop folder for files you save by hand (e.g. .eml dragged out of Outlook) instead",
    )
    p.add_argument(
        "--id", metavar="ID", help="source id (default: derived from the folder name, made unique)"
    )

    p = add("sync", "run one sync cycle (what the launchd agents run)", _cmd_sync)
    p.add_argument("--once", action="store_true", help="run exactly one cycle (the default; for scripts)")
    p.add_argument(
        "--mode", choices=[m.value for m in CycleMode], default=CycleMode.POLL.value, help="default: poll"
    )
    p.add_argument("--dry-run", action="store_true", help="classify only: no fetch, no writes, no commit")
    p.add_argument(
        "--source", action="append", default=[], metavar="ID", help="only this source (repeatable)"
    )
    p.add_argument(
        "--materialise-budget",
        metavar="BYTES",
        help='download budget per source for this run only, e.g. "200MB" (default: each source\'s '
        "max_materialise_bytes). Only online-only files are charged: files already on this Mac are always "
        "read and converted. 0 = no downloads this run: every local file is converted, and every changed "
        "online-only file is left for a later run",
    )

    p = add("reconcile", "run one full-enumeration cycle (every source re-listed)", _cmd_reconcile)
    p.add_argument(
        "--source", action="append", default=[], metavar="ID", help="only this source (repeatable)"
    )
    p.add_argument(
        "--accept-deletions",
        action="store_true",
        help="clear the deletion breaker of the selected sources and apply their removals (needs --source)",
    )

    add(
        "status", "show sources, last runs, lock, heartbeat, holds and queued purges (read-only)", _cmd_status
    )
    p = add("doctor", "preflight checks, each failure with its fix", _cmd_doctor)
    p.add_argument(
        "--network",
        action="store_true",
        help="also probe the Graph host through the resolved proxy (automatic with live Graph sources)",
    )
    add("lint", "run every land-gate lint over the whole docs repo, plus the curation lints", _cmd_lint)
    add("refresh-queue", "print curated pages whose pinned sources changed (rc 1 when any)", _cmd_refresh)
    add(
        "curate-queue",
        "the curation work list: mirror changes since the last checkpoint, the refresh queue, then "
        "UNCOVERED mirror pages no curated page cites (rc 1 when any refresh or uncovered row)",
        _cmd_curate_queue,
    )
    add(
        "checkpoint",
        "end a build session: tag the committed docs repo so the next curate-queue lists changes since here",
        _cmd_checkpoint,
    )
    add(
        "install-skill",
        "write the Claude Code skill now (every sync already writes it)",
        _cmd_install_skill,
        hidden=True,
    )

    p = add("materialise", "hydrate + convert named files (or pending work) within a byte budget", _cmd_mat)
    p.add_argument(
        "--budget", metavar="BYTES", help='byte budget for this run, e.g. "500MB" (default: config)'
    )
    p.add_argument("paths", nargs="*", type=Path, metavar="PATH", help="files inside a local/inbox source")

    p = add("adopt", "copy an existing hand-made docs tree into topics/ as hand-written pages", _cmd_adopt)
    p.add_argument("src_dir", type=Path, metavar="PATH")

    add("migrate", "bring the manifest to this build's schema (after an upgrade)", _cmd_migrate)

    p = add("graph", "Microsoft Graph sign-in: login | logout | whoami | discover", _cmd_graph)
    p.add_argument("action", choices=["login", "logout", "whoami", "discover"])
    _graph_options(p)
    for name, text in (
        ("login", "sign in to Microsoft Graph: broker, then browser (PKCE), then device code if allowed"),
        ("logout", "remove the cached Graph sign-in (same as `graph logout`)"),
        ("whoami", "show the cached Graph account and sign-in method, offline (same as `graph whoami`)"),
        ("discover", "propose sources.toml tables for every drive, site, channel, chat and mail folder"),
    ):
        _graph_options(add(name, text, _cmd_graph))
        sub.choices[name].set_defaults(action=name)

    p = add("install-agent", "install the poll + reconcile LaunchAgents (no admin rights)", _cmd_install)
    p.add_argument("--interval", type=int, metavar="SECONDS", help="poll interval (default: config, 300)")
    p.add_argument(
        "--reconcile-interval", type=int, metavar="SECONDS", help="reconcile interval (default: config, 3600)"
    )
    p.add_argument(
        "--no-backup-exclusions",
        action="store_true",
        help="skip `tmutil addexclusion` of mirror/, the cache and the manifest (C15 req 42)",
    )
    add("uninstall-agent", "unload and remove both LaunchAgents", _cmd_uninstall)

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
    p.add_argument("--dry-run", action="store_true", help="report what would be purged; change nothing")
    p.add_argument("--push", action="store_true", help="force-push the rewrite to an allowed tenant remote")

    p = add("compact-history", "squash mirror history older than [governance] history_days", _cmd_compact)
    p.add_argument("--keep-days", type=int, metavar="N", help="default: [governance] history_days")
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

    p = add("policy", "content policy (sensitivity labels): show the effective policy", _cmd_policy)
    p.add_argument("action", choices=["show"])

    p = add(
        "setup-report",
        "write a redacted Markdown report of this Mac's setup (a summary, the agent's friction log, "
        "environment, installer, doctor, status, background runs, recent errors) for the setup feedback "
        f"loop; read-only, no network, under {setup_report.TIME_BUDGET_S:.0f} s",
        _cmd_setup_report,
    )
    p.add_argument("--out", type=Path, metavar="PATH", help="write the report here (default: stdout)")
    p.add_argument(
        "--friction",
        type=Path,
        metavar="PATH",
        help="the setup prompt's friction log to embed, redacted (default: "
        f"${setup_report.FRICTION_ENV}, else ~/agent-context/setup/friction.md)",
    )
    p.add_argument(
        "--no-redact",
        action="store_true",
        help="keep names, organisation, folders, emails and ids (the report says so at the top)",
    )

    p = add(
        "it-request",
        "write the IT request (docs/deploy/it-request.md) as an email draft with this Mac's values filled in "
        "and the fields still open listed first; never sends anything",
        _cmd_it_request,
    )
    p.add_argument(
        "--out",
        type=Path,
        required=True,
        metavar="PATH",
        help=f"write the draft here, mode 0600 (e.g. {it_request.DEFAULT_OUT}); never inside the agentsync "
        "checkout or the docs repo",
    )
    return parser


# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------


def _config(args: argparse.Namespace) -> Config:
    path: Path | None = getattr(args, "config", None)
    return load_config(path)


def _source_id_for(path: Path, taken: set[str]) -> str:
    candidate = derive_source_id(path, taken)
    taken.add(candidate)
    return candidate


def _toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


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
    converted = sum(s.converted for s in report.sources)
    online = sum(s.deferred_online_only for s in report.sources)
    _out(f"converted {converted}, deferred {online} online-only")


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


def _cmd_init(args: argparse.Namespace) -> int:
    cfg_path = expand(args.config or default_config_path())
    if cfg_path.exists() and not args.force:
        if args.source_local or args.docs_repo:
            _err(f"{cfg_path} exists: edit it, or pass --force to overwrite it")
            return EXIT_USAGE
        _out(f"using existing {cfg_path}")
        config = load_config(cfg_path)
    else:
        text = default_config_text()
        if args.docs_repo is not None:
            line = 'docs_repo = "~/agent-context/docs"'
            if line not in text:
                raise ConfigError("sources.toml template has no docs_repo line to replace")
            text = text.replace(line, f"docs_repo = {_toml_str(str(expand(args.docs_repo)))}", 1)
        taken: set[str] = set()
        blocks = []
        for raw in args.source_local:
            path = canonical_source_root(expand(raw))  # a symlinked cloud root stays protected
            if not path.is_dir():
                _err(f"--source-local {raw}: not a directory")
                return EXIT_USAGE
            blocks.append(local_source_table(_source_id_for(path, taken), path))
        text = text.rstrip("\n") + "\n" + "".join(blocks)
        config = parse_config(text, config_path=cfg_path)  # validate before writing anything
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(text, encoding="utf-8")
        cfg_path.chmod(0o600)
        _out(f"wrote {cfg_path}")
    gov = governance.load_governance(config.config_path)
    created = gitops.ensure_repo(config.docs_repo)
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for d in _owner_only_dirs(config):
        mode = d.stat().st_mode & 0o777
        if mode & 0o077:
            d.chmod(0o700)
            _out(f"tightened {d} to 0700 (was {mode:04o}): it holds tenant data")
    with Manifest(config.state_paths.db) as manifest:
        written = Publisher(config, manifest).ensure_scaffold()
    _out(
        f"docs repo {config.docs_repo} ({'created' if created else 'exists'}, no remote; "
        f"{len(written)} scaffold file(s))"
    )
    findings = _refuse_remotes(config, gov)
    for f in findings:
        _err(f"governance: {f}")
    for line in governance.ensure_time_machine_exclusions(config):
        _out(f"time machine: {line}")
    _out(f"sources: {', '.join(s.id for s in config.sources) or 'none yet (edit sources.toml)'}")
    _next_hint("agentsync doctor · agentsync sync --once · agentsync install-agent")
    return EXIT_FAILED if findings else EXIT_OK


def _cmd_add_source(args: argparse.Namespace) -> int:
    config = _config(args)  # a missing or invalid sources.toml exits 78 before anything is written
    if args.path is None and not args.inbox:
        _err("add-source: PATH is required (or use --inbox for a drop folder)")
        return EXIT_USAGE
    raw: Path = args.path if args.path is not None else expand(config.docs_repo).parent / "inbox"
    if args.inbox:
        expand(raw).mkdir(mode=0o700, parents=True, exist_ok=True)
    path = canonical_source_root(expand(raw))  # a symlinked cloud root stays protected, as in init
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
    for s in config.sources:
        if s.path is not None and s.path == path:
            _out(
                f"already configured: source {s.id!r} ({s.kind.value}, {s.state.value}) has path {path} "
                f"in {config.config_path}"
            )
            return EXIT_OK
    taken = {s.id for s in config.sources}
    if args.id is not None:
        sid = str(args.id)
        if not SOURCE_ID_RE.match(sid):
            _err(f"add-source --id {sid!r}: must match {SOURCE_ID_RE.pattern} (lowercase, digits, '-')")
            return EXIT_USAGE
        if sid in taken:
            _err(f"add-source --id {sid!r}: another source in {config.config_path} already has this id")
            return EXIT_USAGE
    else:
        sid = derive_source_id(path, taken)
    table = inbox_source_table(sid, path) if args.inbox else local_source_table(sid, path)
    try:
        append_to_config(config.config_path, table)  # validated before anything is written
    except ConfigError as exc:
        _err(f"add-source {raw}: refused, sources.toml would not load: {exc}")
        return EXIT_USAGE
    _out(f"added source {sid!r} to {config.config_path}:")
    _out(table.strip("\n"))
    _next_hint("agentsync doctor · agentsync sync --once")
    return EXIT_OK


def _cmd_sync(args: argparse.Namespace) -> int:
    config = _config(args)
    mode = CycleMode.DRY_RUN if args.dry_run else CycleMode(args.mode)
    if args.materialise_budget is None:
        return _run(config, mode=mode, only=tuple(args.source))
    budget = parse_size(args.materialise_budget, where="--materialise-budget")
    return _run(config, mode=mode, only=tuple(args.source), budget_bytes=budget)


def _cmd_reconcile(args: argparse.Namespace) -> int:
    config = _config(args)
    if args.accept_deletions and not args.source:
        _err("--accept-deletions needs --source ID (it is an operator assertion about one source)")
        return EXIT_USAGE
    accept = tuple(args.source) if args.accept_deletions else ()
    return _run(config, mode=CycleMode.RECONCILE, only=tuple(args.source), accept_deletions=accept)


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


def _launcher_events(config: Config, *, now: datetime | None = None) -> list[str]:
    """Each job's last TCC_PENDING / TCC_DENIED launcher line, with its age, when no later CANARY_OK or
    CHILD_EXIT line shows a run got past it (a stale event is not a current problem)."""
    moment = now or datetime.now(UTC)
    out: list[str] = []
    for suffix in ("poll", "reconcile"):
        log_path = expand(config.log_dir) / f"{config.launchd_label_prefix}.{suffix}.err.log"
        try:
            lines = _log_tail(log_path)
        except OSError:
            continue
        last_hit = max((n for n, ln in enumerate(lines) if any(t in ln for t in _TCC_TOKENS)), default=None)
        if last_hit is None:
            continue
        if any(any(t in ln for t in _CLEARED_TOKENS) for ln in lines[last_hit + 1 :]):
            continue
        line = lines[last_hit].strip()
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
        out.append(f"queued purges: {len(queued)} (run `agentsync purge --queue`)")
    try:
        cstate, cdetail = governance.compaction_state(config.docs_repo, gov)
    except AgentSyncError as exc:
        cstate, cdetail = "unknown", str(exc)
    out.append(f"retention: {cstate}: {cdetail}")
    for event in _launcher_events(config):
        out.append(f"launcher: {event}")
    db = config.state_paths.db
    if not db.exists():
        out.append("manifest: none yet (run `agentsync sync --once`)")
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


def _cmd_status(args: argparse.Namespace) -> int:
    for line in _status_lines(_config(args)):
        _out(line)
    return EXIT_OK


# ---- doctor additions (C15 sections 1, 5 and 7; the ops checks live in ops/doctor.py) ------------------


def _check(
    name: str, ok: bool, detail: str, severity: doctor.Severity, fix: str | None = None
) -> doctor.CheckResult:
    return doctor.CheckResult(name, ok, detail, doctor.Severity.INFO if ok else severity, fix)


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


def _network_checks(config: Config, *, probe: bool, offline: bool = False) -> list[doctor.CheckResult]:
    live_graph = any(s.kind.is_graph and s.is_live for s in config.sources)
    system = net.system_proxy()
    proxy = net.resolve_proxy(config.network.proxy, system=system)
    diag = net.proxy_diagnostics(proxy)
    out: list[doctor.CheckResult] = []
    # The background job resolves the proxy from ITS environment, not this shell's (review deploy-ops).
    job = net.resolve_proxy(config.network.proxy, environ=_job_environment(config), system=system)
    if (job.url, job.policy_error) != (proxy.url, proxy.policy_error):
        out.append(
            _check(
                "network.proxy.job",
                False,
                f"the LaunchAgent resolves {job.describe()} but this shell resolves {proxy.describe()}",
                doctor.Severity.ERROR if live_graph else doctor.Severity.WARN,
                fix='set [network] proxy = "<url>" (or "direct") in sources.toml: both then use it',
            )
        )
    if proxy.policy_error:
        out.append(
            _check(
                "network.proxy",
                False,
                proxy.policy_error,
                doctor.Severity.ERROR if live_graph else doctor.Severity.WARN,
                fix='set [network] proxy = "http://<proxy>:<port>" (PAC files are not evaluated)',
            )
        )
    else:
        out.append(_check("network.proxy", True, diag[0], doctor.Severity.INFO))
    out += [_check("network.proxy", False, w, doctor.Severity.WARN) for w in diag[1:]]
    if offline:  # setup-report: no network, ever
        if probe or live_graph:
            out.append(
                _check(
                    "network.graph",
                    False,
                    "not probed (setup-report makes no network calls; run `agentsync doctor --network`)",
                    doctor.Severity.INFO,
                )
            )
    elif probe or live_graph:
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
                    fix="agentsync install-agent (or: tmutil addexclusion "
                    + " ".join(map(str, missing))
                    + ")",
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
    fix = None if state == "ok" else "agentsync reconcile (or agentsync compact-history); release any hold"
    out.append(_check("governance.compaction", state == "ok", f"{state}: {detail}", severity, fix=fix))
    queued = governance.pending_purges(config.state_paths.root)
    if queued:
        out.append(
            _check(
                "governance.purge_queue",
                False,
                f"{len(queued)} purge request(s) queued (confirmed upstream deletions / label escalations)",
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


def _extra_checks(config: Config, *, probe: bool, offline: bool = False) -> list[doctor.CheckResult]:
    """Integrator-owned doctor checks: proxy/TLS reachability, broker, governance and content policy
    (``offline``: never the Graph reachability probe, whatever ``probe`` and the sources say)."""
    out: list[doctor.CheckResult] = []
    checks: tuple[tuple[str, Callable[[], list[doctor.CheckResult]]], ...] = (
        ("network", lambda: _network_checks(config, probe=probe, offline=offline)),
        ("graph.broker", lambda: _broker_check(config)),
        ("governance", lambda: _governance_checks(config)),
        ("policy", lambda: _policy_check(config)),
    )
    for name, fn in checks:
        try:
            out += fn()
        except Exception as exc:  # one broken check must not hide the others
            out.append(
                _check(name, False, f"check crashed: {type(exc).__name__}: {exc}", doctor.Severity.ERROR)
            )
    return out


def _cmd_doctor(args: argparse.Namespace) -> int:
    config = _config(args)
    results = doctor.run_checks(config) + _extra_checks(config, probe=bool(args.network))
    _out(doctor.format_results(results))
    failed = [r for r in results if not r.ok and r.severity is doctor.Severity.ERROR]
    return EXIT_FAILED if failed else EXIT_OK


def _doctor_lines_offline(config: Config) -> list[str]:
    """Every ``agentsync doctor`` line without the Graph network probe (what setup-report embeds)."""
    results = doctor.run_checks(config) + _extra_checks(config, probe=False, offline=True)
    return doctor.format_results(results).splitlines()


def _cmd_setup_report(args: argparse.Namespace) -> int:
    """Never fails hard: every section records its own error; exit 1 only when --out cannot be written
    (the report then goes to stdout instead)."""
    out: Path | None = expand(args.out) if args.out is not None else None
    friction: Path = (
        expand(args.friction) if args.friction is not None else setup_report.default_friction_path()
    )
    text, red = setup_report.build_report(
        getattr(args, "config", None),
        redact=not args.no_redact,
        hooks=setup_report.ReportHooks(doctor=_doctor_lines_offline, status=_status_lines),
        friction_path=friction,
    )
    if out is None:
        sys.stdout.write(text)  # the report's own last line is the bare issue link
        return EXIT_OK
    link = f"{setup_report.ISSUE_LINK_LABEL} {setup_report.issue_link(text) or setup_report.ISSUE_URL}"
    try:
        setup_report.write_report(out, text)
    except OSError as exc:
        _err(f"setup-report: cannot write {out}: {exc.strerror or exc}; the report follows on stdout")
        sys.stdout.write(text)
        _out(link)
        return EXIT_FAILED
    what = (
        f"{red.total} replacement(s) of {red.values} value(s)"
        if red.enabled
        else "NOT redacted (--no-redact)"
    )
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


def _cmd_lint(args: argparse.Namespace) -> int:
    config = _config(args)
    repo, layout = config.docs_repo, config.layout
    findings: list[LintFinding] = []
    findings += lints.lint_no_symlinks(repo)
    findings += lints.lint_mirror_frontmatter(repo)
    findings += lints.lint_paths(repo)
    findings += lints.lint_no_cache_in_git(repo)
    findings += lints.lint_no_tokens(repo)
    findings += lints.lint_index_budget(repo)
    rows, _entities, curate_findings = curate.generate_depends(layout)
    findings += curate_findings
    findings += [dataclasses.replace(f, blocking=True) for f in curate.lint_unlisted_pages(layout, rows)]
    for f in sorted(findings, key=lambda f: (not f.blocking, f.code, f.path)):
        _out(f"{'ERROR' if f.blocking else 'warn '} {f.code} {f.path}: {f.message}")
    blocking = sum(1 for f in findings if f.blocking)
    _out(f"{len(findings)} finding(s), {blocking} blocking")
    return EXIT_FAILED if blocking else EXIT_OK


def _cmd_refresh(args: argparse.Namespace) -> int:
    config = _config(args)
    rc, verdicts = curate.refresh_queue(config.layout)
    for v in verdicts:
        _out(v.line())
    if rc == 2:
        _err(f"{config.layout.depends_tsv}: missing or unreadable (run a sync first)")
    return rc


_CHANGE_WORDS = {"A": "ADDED", "M": "CHANGED", "D": "REMOVED"}


def _is_tombstone(page: Path) -> bool:
    try:
        with page.open(encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    return head.startswith("---\n") and "\nstatus: deleted\n" in head.split("\n---\n", 1)[0]


def _print_changes_since_checkpoint(repo: Path) -> None:
    """What the mirror gained, changed and lost since the last build session's checkpoint.  A page that
    became a tombstone reads REMOVED, and a removed page with an ``archive/`` copy names it in a third
    column."""
    checkpoint = gitops.curated_checkpoint(repo) if gitops.head_sha(repo) else None
    if checkpoint is None:
        _out(
            "no build-session checkpoint yet: every mirror page is new (agentsync checkpoint ends a session)"
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
        archived = archive_path(path) if status == "D" else None
        tail = f"\t{archived}" if archived and (repo / archived).is_file() else ""
        _out(f"{_CHANGE_WORDS.get(status, status)}\t{path}{tail}")
    _out(
        f"since the last build session ({date}, {sha[:12]}): {counts['ADDED']} added, {counts['CHANGED']} "
        f"changed, {counts['REMOVED']} removed under mirror/"
    )


def _cmd_curate_queue(args: argparse.Namespace) -> int:
    config = _config(args)
    layout = config.layout
    _print_changes_since_checkpoint(config.docs_repo)
    rc, verdicts = curate.refresh_queue(layout)
    for v in verdicts:
        _out(v.line())
    if rc == 2:
        _err(f"{layout.depends_tsv}: missing or unreadable (run a sync first); listing uncovered pages only")
    rows, _entities, _findings = curate.generate_depends(layout)  # live: pages written since the last sync
    uncovered = curate.uncovered_mirror_pages(layout, rows)
    for rel in uncovered:
        _out(f"UNCOVERED\t{rel}")
    _out(f"{len(verdicts)} refresh-queue row(s), {len(uncovered)} uncovered mirror page(s)")
    return EXIT_FAILED if verdicts or uncovered else EXIT_OK


def _cmd_checkpoint(args: argparse.Namespace) -> int:
    config = _config(args)
    repo, layout = config.docs_repo, config.layout
    head = gitops.head_sha(repo)
    if head is None:
        _err("checkpoint: the docs repo has no commit yet (run agentsync sync --once first)")
        return EXIT_FAILED
    if gitops.has_changes(repo):
        _err("checkpoint: the docs repo has uncommitted pages; run agentsync sync --once, then checkpoint")
        return EXIT_FAILED
    previous = gitops.curated_checkpoint(repo)
    gitops.tag_curated(repo, head)
    snapshot = (
        gitops.tag_snapshot(repo, head, datetime.now(UTC))
        if governance.load_governance(config.config_path).archive
        else None
    )
    _rc, verdicts = curate.refresh_queue(layout)
    rows, _entities, _findings = curate.generate_depends(layout)
    left = len(verdicts) + len(curate.uncovered_mirror_pages(layout, rows))
    since = f"previous {previous[0][:12]} ({previous[1]})" if previous else "the first checkpoint"
    _out(f"checkpoint {head[:12]}: the next curate-queue lists changes since here; {since}")
    if snapshot is not None:
        _out(f"snapshot {snapshot}: a permanent tag; read a past page with git show {snapshot}:<path>")
    _out(f"{left} item(s) still in the curate queue")
    return EXIT_OK


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
    config = _config(args)
    applied = Manifest.migrate(config.state_paths.db)
    _out(f"manifest {config.state_paths.db}: " + (f"applied {applied}" if applied else "already current"))
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
        user_agent=user_agent(config.graph.company, __version__),
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
    if args.interval is not None:
        config = dataclasses.replace(config, poll_interval_s=args.interval)
    if args.reconcile_interval is not None:
        config = dataclasses.replace(config, reconcile_interval_s=args.reconcile_interval)
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
    if not args.no_backup_exclusions:
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
    _out("check: agentsync doctor (it names the File Provider target)")
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
        reports = governance.run_purge_queue(config)
        for rep in reports:
            _print_purge(rep)
        left = governance.pending_purges(config.state_paths.root)
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


def _cmd_policy(args: argparse.Namespace) -> int:
    config = _config(args)
    pol = content_policy.load_policy(config)  # ConfigError -> exit 78: a broken policy never means "allow"
    sidecar = config.config_path.parent / content_policy.POLICY_FILE_NAME
    _out(f"sources: {config.config_path} [policy]" + (f" + {sidecar}" if sidecar.is_file() else ""))
    _out(f"labels_active: {'true' if pol.labels_active else 'false'}")
    _out(f"exclude_label_ids: {', '.join(pol.exclude_label_ids) or '-'}")
    _out(f"exclude_label_names: {', '.join(pol.exclude_label_names) or '-'}")
    _out(f"refuse_unlabelled: {'true' if pol.refuse_unlabelled else 'false'}")
    _out(f"fingerprint: {pol.fingerprint()}")
    _out("always: encrypted Office (EncryptedPackage) and encrypted PDFs become UNREADABLE stubs")
    return EXIT_OK


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
