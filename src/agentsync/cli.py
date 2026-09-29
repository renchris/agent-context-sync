"""``agentsync`` command line (owner: integrator).

Subcommands: init · sync [--once] [--mode poll|reconcile|dry_run] [--dry-run] [--source ID ...] · reconcile
[--source ID ...] [--accept-deletions] · status · doctor · lint · refresh-queue · materialise [--budget BYTES]
[PATH ...] · adopt SRC_DIR · migrate · graph login|logout|whoami|discover (also as top-level login · logout ·
whoami · discover) · install-agent [--interval SECONDS] · uninstall-agent.
Every subcommand accepts ``--config PATH`` (default ~/agent-context/sources.toml) and ``-v/--verbose``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import re
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from agentsync import __version__, curate, gitops, lints
from agentsync.config import Config, default_config_text, load_config, parse_config, parse_size
from agentsync.cycle import run_cycle, source_statuses
from agentsync.errors import (
    AgentSyncError,
    AuthError,
    AuthRequiredError,
    ConfigError,
    LockHeldError,
    ManifestSchemaError,
)
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

_EPILOG = """\
exit codes:
  0   ok
  1   failed: a source failed, a blocking lint fired, the refresh queue has rows, or a doctor check failed
  2   usage error (bad arguments), or refresh-queue could not read DEPENDS.tsv
  75  skipped: another agentsync cycle holds the single-writer lock (EX_TEMPFAIL; launchd retries later)
  77  sign-in required: a Graph source needs `agentsync graph login` (auth REAUTH_REQUIRED)
  78  configuration invalid: sources.toml is missing or wrong (the message names the key)

files: ~/agent-context/sources.toml (config) · ~/agent-context/docs (the docs git repo) ·
~/Library/Application Support/agentsync (manifest, lock, heartbeat) · ~/Library/Caches/agentsync (cache)
"""

_log = logging.getLogger("agentsync.cli")
_ID_BAD = re.compile(r"[^a-z0-9-]+")

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

    p = add("init", "write sources.toml, create the docs repo and its scaffold", _cmd_init)
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

    add("status", "show sources, last runs, lock and heartbeat (read-only)", _cmd_status)
    add("doctor", "preflight checks, each failure with its fix", _cmd_doctor)
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
    for name, text in (
        ("login", "device-code sign-in to Microsoft Graph (same as `graph login`)"),
        ("logout", "remove the cached Graph sign-in (same as `graph logout`)"),
        ("whoami", "show the cached Graph account, offline (same as `graph whoami`)"),
        ("discover", "list drives/libraries this account can see (same as `graph discover`)"),
    ):
        add(name, text, _cmd_graph).set_defaults(action=name)

    p = add("install-agent", "install the poll + reconcile LaunchAgents (no admin rights)", _cmd_install)
    p.add_argument("--interval", type=int, metavar="SECONDS", help="poll interval (default: config, 300)")
    p.add_argument(
        "--reconcile-interval", type=int, metavar="SECONDS", help="reconcile interval (default: config, 3600)"
    )
    add("uninstall-agent", "unload and remove both LaunchAgents", _cmd_uninstall)
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
        _out("  sign-in required: run `agentsync graph login`")


def _run(config: Config, **kwargs: object) -> int:
    report = run_cycle(config, **kwargs)  # type: ignore[arg-type]
    _print_report(report)
    return report.exit_code


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
            path = expand(raw)
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
    created = gitops.ensure_repo(config.docs_repo)
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with Manifest(config.state_paths.db) as manifest:
        written = Publisher(config, manifest).ensure_scaffold()
    _out(
        f"docs repo {config.docs_repo} ({'created' if created else 'exists'}; "
        f"{len(written)} scaffold file(s))"
    )
    _out(f"sources: {', '.join(s.id for s in config.sources) or 'none yet (edit sources.toml)'}")
    _out("next: agentsync doctor · agentsync sync --once · agentsync install-agent")
    return EXIT_OK


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


def _cmd_status(args: argparse.Namespace) -> int:
    config = _config(args)
    _out(f"config: {config.config_path}")
    _out(f"docs repo: {config.docs_repo}")
    lock_path = config.state_paths.lock
    held = lock_path.exists() and SingleWriterLock.is_held(lock_path)
    _out(f"lock: {SingleWriterLock.describe(lock_path) if held else 'free'}")
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


def _cmd_doctor(args: argparse.Namespace) -> int:
    config = _config(args)
    results = doctor.run_checks(config)
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
        status = auth.login_device_code(_out)
        _out(f"signed in as {status.username} (tenant {status.tenant_id}; cache {status.cache_backend})")
    elif action == "logout":
        auth.logout()
        _out("signed out: cached Graph tokens removed")
    elif action == "whoami":
        status = auth.status()
        who = status.username if status.signed_in else "not signed in"
        _out(f"{who} · tenant {status.tenant_id or '-'} · cache {status.cache_backend}")
        _out(f"scopes: {' '.join(status.scopes)}")
        return EXIT_OK if status.signed_in else EXIT_REAUTH
    else:
        from agentsync.graph.client import GraphClient, user_agent  # noqa: PLC0415
        from agentsync.graph.drive import discover  # noqa: PLC0415

        with GraphClient(
            auth, base_url=config.graph.base_url, user_agent=user_agent(config.graph.company, __version__)
        ) as client:
            for scope in discover(client):
                _out(
                    f"{scope.kind.value}\t{scope.name}\tdrive_id={scope.drive_id or '-'}\t"
                    f"site={scope.site or '-'}\t{scope.note}"
                )
    return EXIT_OK


def _cmd_install(args: argparse.Namespace) -> int:
    config = _config(args)
    if args.interval is not None:
        config = dataclasses.replace(config, poll_interval_s=args.interval)
    if args.reconcile_interval is not None:
        config = dataclasses.replace(config, reconcile_interval_s=args.reconcile_interval)
    for spec in (launchd.poll_spec(config), launchd.reconcile_spec(config)):
        path = launchd.install(spec)
        _out(f"installed {spec.label} every {spec.start_interval_s}s -> {path}")
    _out(f"interpreter: {launchd.program_arguments(config, CycleMode.POLL.value)[0]}")
    _out("check: agentsync doctor (it names the Full Disk Access target for ~/Library/CloudStorage)")
    return EXIT_OK


def _cmd_uninstall(args: argparse.Namespace) -> int:
    config = _config(args)
    for suffix in ("poll", "reconcile"):
        label = f"{config.launchd_label_prefix}.{suffix}"
        _out(f"{label}: {'removed' if launchd.uninstall(label) else 'not installed'}")
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
    """Entry point (console script ``agentsync``); returns the process exit code."""
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
