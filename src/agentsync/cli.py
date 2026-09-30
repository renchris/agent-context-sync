"""``agentsync`` command line (owner: integrator).

Subcommands: init · sync [--once] [--mode poll|reconcile|dry_run] [--dry-run] [--source ID ...] · reconcile
[--source ID ...] [--accept-deletions] · status · doctor [--network] · lint · refresh-queue · materialise
[--budget BYTES] [PATH ...] · adopt SRC_DIR · migrate · graph login|logout|whoami|discover (also as top-level
login · logout · whoami · discover; login [--device-code]; discover [--url URL ...] [--toml]) · install-agent
[--interval SECONDS] [--no-backup-exclusions] · uninstall-agent · purge SELECTOR | --queue · compact-history ·
hold · offboard [--purge-data] [--confirm DOCS_REPO] · policy show.
Every subcommand accepts ``--config PATH`` (default ~/agent-context/sources.toml) and ``-v/--verbose``.

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
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentsync import __version__, curate, gitops, governance, lints, net
from agentsync import policy as content_policy
from agentsync.config import (
    Config,
    canonical_source_root,
    default_config_text,
    load_config,
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
from agentsync.paths import default_config_path, expand
from agentsync.publish import Publisher

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
_ID_BAD = re.compile(r"[^a-z0-9-]+")
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

    def add(name: str, help_text: str, handler: Handler) -> argparse.ArgumentParser:
        p = sub.add_parser(
            name,
            help=help_text,
            description=help_text,
            parents=[common],
            epilog=_EPILOG,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
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

    p = add("sync", "run one sync cycle (what the launchd agents run)", _cmd_sync)
    p.add_argument("--once", action="store_true", help="run exactly one cycle (the default; for scripts)")
    p.add_argument(
        "--mode", choices=[m.value for m in CycleMode], default=CycleMode.POLL.value, help="default: poll"
    )
    p.add_argument("--dry-run", action="store_true", help="classify only: no fetch, no writes, no commit")
    p.add_argument(
        "--source", action="append", default=[], metavar="ID", help="only this source (repeatable)"
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
    return parser


# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------


def _config(args: argparse.Namespace) -> Config:
    path: Path | None = getattr(args, "config", None)
    return load_config(path)


def _source_id_for(path: Path, taken: set[str]) -> str:
    base = _ID_BAD.sub("-", path.name.lower()).strip("-")[:56] or "local"
    if not base[0].isalnum():
        base = "s" + base
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    taken.add(candidate)
    return candidate


def _toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _print_report(report: CycleReport) -> None:
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
        for a in s.alarms:
            _out(f"    alarm: {a}")
        for e in s.errors:
            _out(f"    error: {e}")
    for f in report.lint_findings:
        _out(f"  {'BLOCKING' if f.blocking else 'warning'} {f.code} {f.path}: {f.message}")
    if report.broke_stale_lock:
        _out("  note: a stale lock from a dead run was broken; every source ran a full pass")
    if report.auth_required:
        _out("  sign-in required: run `agentsync graph login` (errors above name any IT action)")


def _run(config: Config, **kwargs: object) -> int:
    report = run_cycle(config, **kwargs)  # type: ignore[arg-type]
    _print_report(report)
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
            sid = _source_id_for(path, taken)
            blocks.append(
                f'\n[[source]]\nid = "{sid}"\nkind = "local"\npath = {_toml_str(str(path))}\n'
                '# sentinel = "README.txt"   # recommended: a file that must always exist under path\n'
            )
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
    _out("next: agentsync doctor · agentsync sync --once · agentsync install-agent")
    return EXIT_FAILED if findings else EXIT_OK


def _cmd_sync(args: argparse.Namespace) -> int:
    config = _config(args)
    mode = CycleMode.DRY_RUN if args.dry_run else CycleMode(args.mode)
    return _run(config, mode=mode, only=tuple(args.source))


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


def _cmd_status(args: argparse.Namespace) -> int:
    config = _config(args)
    _out(f"config: {config.config_path}")
    _out(f"docs repo: {config.docs_repo}")
    lock_path = config.state_paths.lock
    held = lock_path.exists() and SingleWriterLock.is_held(lock_path)
    _out(f"lock: {SingleWriterLock.describe(lock_path) if held else 'free'}")
    gov = governance.load_governance(config.config_path)
    for h in governance.active_holds(config.state_paths.root, gov):
        _out(f"HOLD: {h.describe()} (purge and compaction suspended)")
    queued = governance.pending_purges(config.state_paths.root)
    if queued:
        _out(f"queued purges: {len(queued)} (run `agentsync purge --queue`)")
    try:
        cstate, cdetail = governance.compaction_state(config.docs_repo, gov)
    except AgentSyncError as exc:
        cstate, cdetail = "unknown", str(exc)
    _out(f"retention: {cstate}: {cdetail}")
    for event in _launcher_events(config):
        _out(f"launcher: {event}")
    db = config.state_paths.db
    if not db.exists():
        _out("manifest: none yet (run `agentsync sync --once`)")
        return EXIT_OK
    with Manifest(db) as manifest:
        runs = manifest.last_runs(5)
        _out("last runs: " + ("; ".join(f"{r} {m} {s} {(c or '-')[:12]}" for r, m, s, c in runs) or "none"))
        statuses = source_statuses(config, manifest, now=datetime.now(UTC))
    heartbeat = read_heartbeat(config.state_paths.heartbeat)
    for st in statuses:
        hb = heartbeat.get(st.source_id, {})
        _out(
            f"  {st.source_id} ({st.kind.value}, {st.state}): baseline "
            f"{'complete' if st.baseline_complete else 'INCOMPLETE'} · complete "
            f"{'yes' if st.enumeration_complete else 'no'} · live {st.live} (dataless {st.dataless}) · "
            f"quarantined {st.quarantined} · deferred {st.deferred} · breaker {st.breaker} · "
            f"auth {st.auth} · "
            f"last success {hb.get('last_success_at', 'never')}"
        )
    state_md = config.layout.state_md
    if state_md.is_file():
        _out(f"details: {state_md}")
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


def _network_checks(config: Config, *, probe: bool) -> list[doctor.CheckResult]:
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
    if probe or live_graph:
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


def _extra_checks(config: Config, *, probe: bool) -> list[doctor.CheckResult]:
    """Integrator-owned doctor checks: proxy/TLS reachability, broker, governance and content policy."""
    out: list[doctor.CheckResult] = []
    checks: tuple[tuple[str, Callable[[], list[doctor.CheckResult]]], ...] = (
        ("network", lambda: _network_checks(config, probe=probe)),
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
