#!/bin/bash
# Usage: scripts/tenant-probes.sh [options]
#
# Measure the target tenant with read-only probes and write a dated report.
#
# Options:
#   --config <FILE>           sources.toml to read (default: $AGENTSYNC_CONFIG,
#                             then ~/agent-context/sources.toml)
#   --dry-run                 print what would be requested and exit; no
#                             sign-in, no network, no writes
#   -h, --help                print this help and exit, even with --version
#   --json                    one JSON document: the result on stdout, or one
#                             error line on stderr (not with --selftest)
#   --max-bytes <NUMBER>      largest file probe 4b downloads, in bytes
#                             (default: 20000000)
#   --max-downloads <NUMBER>  probe 4b downloads up to this many files twice
#                             each; 0 skips probe 4b (default: 6)
#   --max-pages <NUMBER>      delta pages probe 1 reads per drive
#                             (default: 10, about 2,000 items)
#   -o, --output <DIR>        where the report goes, mode 0700
#                             (default: <state_dir>/probes)
#   --python <PATH>           the interpreter that has agentsync installed
#                             (default: $AGENTSYNC_PYTHON, then the uv tool
#                             environment, then the .venv of this checkout)
#   --selftest                check the offline helpers and exit
#   --version                 print the commit of this checkout, or unknown,
#                             and exit
#   --                        end the options
#
# Examples:
#   scripts/tenant-probes.sh --dry-run
#   scripts/tenant-probes.sh
#
# Notes:
#   Run `agentsync graph login` first: it is the only interactive step.
#   The probes never prompt, never open a browser and never write to the
#   tenant. They use the cached token silently and only GET, plus two
#   downloads per probe-4b file into a temporary folder that is deleted.
#   An option that takes a value also takes the --name=VALUE form, which is
#   how to pass a value that is itself an option; a repeat takes the last.
#   Probes 1, 2, 4b and 9 run; probe 9 needs re-runs at +30, +60 and +90 days.
#   Exit status: 0 report written, every probe finished; 1 a probe could
#   not finish (stderr counts them; a 401, 403, 404 or probe-9 410 is a
#   measurement and finishes it) or any other failure; 2 usage error;
#   77 not signed in or sign-in blocked (report written); 78 sources.toml
#   invalid or no [graph] client_id; 130, 129 or 143 stopped by INT, HUP or
#   TERM (a partial report once probing began).
#
# Learn more: docs/deploy/tenant-probes.md
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"

DRY_RUN=0
SELFTEST=0
SHOW_VERSION=0
CONFIG="${AGENTSYNC_CONFIG:-$HOME/agent-context/sources.toml}"
OUT=""
OUT_ALIAS=0
PY="${AGENTSYNC_PYTHON:-}"
MAX_PAGES=10
MAX_DOWNLOADS=6
MAX_BYTES=20000000
JSON=0
# --json is read before the parse loop: a usage error or --help can fire before its arm
for arg in "$@"; do
	case "$arg" in
	--) break ;;
	--json) JSON=1 ;;
	esac
done

# $1 as the body of a JSON string: controls dropped, backslash and quote escaped
json_text() { printf '%s' "$1" | LC_ALL=C tr -d '\000-\037\177' | LC_ALL=C sed 's/\\/\\\\/g; s/"/\\"/g'; }
json_error() { printf '{"status":"error","reason":"%s","message":"%s"}\n' "$1" "$(json_text "$2")" >&2; }
usage_error() {
	if [ "$JSON" = 1 ]; then
		json_error usage "$*"
	else
		printf 'usage error: %s (see --help)\n' "$*" >&2
	fi
	exit 2
}
# An error line: the mark on a terminal, the word Error: in a log or a pipe.
error_line() {
	if [ "$JSON" = 1 ]; then
		json_error failed "$*"
	elif [ -t 2 ]; then
		printf '✗ %s\n' "$*" >&2
	else
		printf 'Error: %s\n' "$*" >&2
	fi
}
show_help() {
	if [ "$JSON" != 1 ]; then
		awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
		return
	fi
	# one document from the same text: the usage line, each option as Options lists it, and the text whole
	local tty=0
	[ ! -t 1 ] || tty=1
	awk -v tty="$tty" '
	function esc(s) { gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); return s }
	NR > 1 && /^#/ {
		sub(/^# ?/, "")
		if ($0 == "") sec = ""
		else if ($0 ~ /^[A-Z][a-z]*:$/) sec = $0
		else if (sec == "Options:" && $0 ~ /^  -/) {
			o = substr($0, 3)
			sub(/  .*/, "", o)
			opts = opts (n++ ? (tty ? ",\n    " : ",") : "") "\"" esc(o) "\""
		}
		if ($0 ~ /^Usage: /) usage = substr($0, 8)
		text = text esc($0) "\\n"
		next
	}
	NR > 1 { exit }
	END {
		if (tty) printf "{\n  \"usage\": \"%s\",\n  \"options\": [\n    %s\n  ],\n  \"text\": \"%s\"\n}\n", esc(usage), opts, text
		else printf "{\"usage\":\"%s\",\"options\":[%s],\"text\":\"%s\"}\n", esc(usage), opts, text
	}' "$0"
}
# The option a separated value must not be: --output --dry-run would set OUT and leave DRY_RUN=0, a live run
# for someone asking for a dry one.
need_value() {
	[ "$1" -ge 2 ] || usage_error "$2 needs a value"
	case "${3:-}" in
	-h | --help | --version | --dry-run | --selftest | --json | -- | --config | -o | --output | --out | --python | --max-pages | --max-downloads | --max-bytes | --config=* | --output=* | --out=* | --python=* | --max-pages=* | --max-downloads=* | --max-bytes=*)
		usage_error "$2 needs a value, got the option $3 (write ${2/#-o/--output}=$3 if that is the value)"
		;;
	esac
}
is_uint() { case "$1" in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac }
set_value() {
	[ -n "$2" ] || usage_error "$1 needs a value"
	case "$1" in
	--config) CONFIG="$2" ;;
	-o | --output)
		# the folder also holds deltalinks.json and the login files, so stdout cannot stand in for it
		case "$2" in
		-) usage_error "$1 needs a folder, not -: it also holds deltalinks.json and the login files" ;;
		json | yaml | table | text) usage_error "$1 takes a folder, not a format: the report is written into it; --json gives JSON output" ;;
		esac
		OUT="$2"
		;;
	--out)
		OUT="$2"
		OUT_ALIAS=1
		;;
	--python) PY="$2" ;;
	*)
		is_uint "$2" || usage_error "$1 needs a non-negative integer, got '$2'"
		case "$1" in
		--max-pages) MAX_PAGES="$2" ;;
		--max-downloads) MAX_DOWNLOADS="$2" ;;
		*) MAX_BYTES="$2" ;;
		esac
		;;
	esac
}
# The commit of the checkout at $1 without running git: /usr/bin/git can offer to install developer tools, and
# this script promises no prompts. Empty when it cannot be read. As git_head_file in install.sh, and it also
# follows the .git file of a linked worktree, whose branches live in the common directory.
git_head_file() {
	local git_dir="$1/.git" common head ref
	if [ -f "$git_dir" ]; then
		git_dir="$(sed -n 's/^gitdir: //p' "$git_dir" 2>/dev/null || true)"
		[ -n "$git_dir" ] || return 0
		case "$git_dir" in
		/*) ;;
		*) git_dir="$1/$git_dir" ;;
		esac
	fi
	common="$git_dir"
	if [ -f "$git_dir/commondir" ]; then
		common="$(cat "$git_dir/commondir" 2>/dev/null || true)"
		case "$common" in
		/*) ;;
		*) common="$git_dir/$common" ;;
		esac
	fi
	head="$(cat "$git_dir/HEAD" 2>/dev/null || true)"
	case "$head" in
	"ref: "*)
		ref="${head#ref: }"
		if [ -f "$common/$ref" ]; then
			head="$(cat "$common/$ref")"
		else
			head="$(awk -v r="$ref" '$2 == r { print $1; exit }' "$common/packed-refs" 2>/dev/null || true)"
		fi
		;;
	esac
	case "$head" in
	[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) printf '%s' "$head" | cut -c1-12 ;;
	esac
}

while [ $# -gt 0 ]; do
	case "$1" in
	--dry-run) DRY_RUN=1 ;;
	--selftest) SELFTEST=1 ;;
	--version) SHOW_VERSION=1 ;;
	--json) JSON=1 ;;
	--config | -o | --output | --out | --python | --max-pages | --max-downloads | --max-bytes)
		need_value $# "$1" "${2:-}"
		set_value "$1" "$2"
		shift
		;;
	--config=* | --output=* | --out=* | --python=* | --max-pages=* | --max-downloads=* | --max-bytes=*) set_value "${1%%=*}" "${1#*=}" ;;
	-h | --help | help)
		show_help
		exit 0
		;;
	--)
		shift
		[ $# -eq 0 ] || usage_error "Unexpected positional argument $1 for tenant-probes.sh."
		break
		;;
	-*) usage_error "Unknown option $1 for tenant-probes.sh." ;;
	*) usage_error "Unexpected positional argument $1 for tenant-probes.sh." ;;
	esac
	shift
done
[ "$SELFTEST$JSON" != 11 ] || usage_error "--selftest has no JSON output; drop --json"

if [ "$SHOW_VERSION" = 1 ]; then
	commit="$(git_head_file "$repo")"
	if [ "$JSON" = 0 ]; then
		printf '%s\n' "${commit:-unknown}"
	elif [ -t 1 ]; then
		printf '{\n  "version": "%s"\n}\n' "${commit:-unknown}"
	else
		printf '{"version":"%s"}\n' "${commit:-unknown}"
	fi
	exit 0
fi
# under --json the warning goes into the document instead
[ "$OUT_ALIAS" = 0 ] || [ "$JSON" = 1 ] || printf 'warning: --out is deprecated; use --output <DIR>\n' >&2

case "$CONFIG" in
/*) ;;
*) CONFIG="$PWD/$CONFIG" ;;
esac
if [ -n "$OUT" ]; then
	case "$OUT" in
	/*) ;;
	*) OUT="$PWD/$OUT" ;;
	esac
fi

# The interpreter: the one `agentsync graph login` used, so the Keychain item's ACL matches and no
# "python wants to use your confidential information" dialog can appear.
if [ -z "$PY" ]; then
	tool_dir=""
	if command -v uv >/dev/null 2>&1; then
		tool_dir="$(uv tool dir 2>/dev/null || true)"
	elif [ -x "$HOME/.local/bin/uv" ]; then
		tool_dir="$("$HOME/.local/bin/uv" tool dir 2>/dev/null || true)"
	fi
	for cand in "${tool_dir:+$tool_dir/agentsync/bin/python}" "$HOME/.local/share/uv/tools/agentsync/bin/python" \
		"$repo/.venv/bin/python"; do
		if [ -n "$cand" ] && [ -x "$cand" ]; then
			PY="$cand"
			break
		fi
	done
fi
if [ -z "$PY" ]; then
	error_line "Can't find a Python with agentsync installed. Run scripts/install.sh, or pass --python <PATH>."
	exit 1
fi
if [ ! -f "$PY" ] || [ ! -x "$PY" ]; then
	error_line "Can't run $PY: it isn't executable. Check --python or AGENTSYNC_PYTHON."
	exit 1
fi

# exec, so a signal sent to this pid reaches Python, which cleans up; nothing after the heredoc runs
exec "$PY" -I -X utf8 - "$CONFIG" "$OUT" "$DRY_RUN" "$SELFTEST" "$MAX_PAGES" "$MAX_DOWNLOADS" "$MAX_BYTES" "$JSON" "$OUT_ALIAS" <<'PYTHON'
import json
import os
import signal
import sys

JSON = sys.argv[8:9] == ["1"]
WARNINGS: list[str] = ["--out is deprecated; use --output <DIR>"] if JSON and sys.argv[9:10] == ["1"] else []
# every reason an error document can carry
REASONS = frozenset(("usage", "not_signed_in", "sign_in_blocked", "config_invalid", "client_id_unset",
                     "network_policy", "failed", "canceled", "internal_error"))


def fail_line(text: str) -> None:
    """An error line on stderr: the mark on a terminal, the word Error: in a log or a pipe."""
    print(("✗ " if sys.stderr.isatty() else "Error: ") + text, file=sys.stderr)


def warn(text: str) -> None:
    """A warning: a stderr line, or under --json an entry of the document's warnings."""
    if JSON:
        WARNINGS.append(text)
    else:
        print(f"warning: {text}", file=sys.stderr)


def error_doc(reason: str, message: str, report: object = None, data: dict[str, object] | None = None) -> bytes:
    """The one stderr line of a failure under --json, non-ASCII escaped."""
    assert reason in REASONS, reason
    doc: dict[str, object] = {"status": "error", "reason": reason, "message": message}
    if report is not None:
        doc["report"] = {"path": str(report)}
    if data:
        doc["data"] = data
    if WARNINGS:
        doc["warnings"] = [{"message": w} for w in WARNINGS]
    return (json.dumps(doc, separators=(",", ":")) + "\n").encode()


def json_error(reason: str, message: str, report: object = None, data: dict[str, object] | None = None) -> bool:
    """Under --json, the error document on stderr and True, so the caller skips its human line."""
    if JSON:
        sys.stdout.flush()
        sys.stderr.buffer.write(error_doc(reason, message, report, data))
        sys.stderr.flush()
    return JSON


def json_out(doc: dict[str, object]) -> None:
    """The one result document on stdout: indented by two on a terminal, one compact line otherwise."""
    if WARNINGS:
        doc["warnings"] = [{"message": w} for w in WARNINGS]
    tty = sys.stdout.isatty()
    print(json.dumps(doc, indent=2) if tty else json.dumps(doc, separators=(",", ":")))


def no_module(exc: ImportError) -> None:
    message = (f"Can't import {exc.name} with {sys.executable}. Pass --python with the interpreter that has "
               "agentsync installed, or run scripts/install.sh.")
    if not json_error("failed", message):
        fail_line(message)
    sys.exit(1)


PROBING = False  # set just before the temporary folder exists: from then on a stop writes a partial report
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class _Stopped(BaseException):
    """INT, TERM or HUP arrived while probing; past every except arm, to the cleanup and the partial report."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def stopped_line(text: str) -> bytes:
    return error_doc("canceled", text) if JSON else f"{text}\n".encode()


def on_signal(signum: int, _frame: object) -> None:
    if not PROBING:
        os.write(2, stopped_line("Stopped. Nothing was downloaded; no report written."))
        os._exit(128 + signum)
    raise _Stopped(signum)


# a signal ignored at start stays ignored, or `nohup` would die at the hangup it was started to survive
HANDLED = [s for s in STOP_SIGNALS if signal.getsignal(s) is not signal.SIG_IGN]
for _sig in HANDLED:
    signal.signal(_sig, on_signal)
SOURCE = (lambda: None).__code__.co_filename  # <stdin> for this heredoc


def bug_report(kind: type[BaseException], exc: BaseException, tb: object) -> None:
    """D34: an unexpected error is a short bug report (its type and line, never its message, which can quote a
    path or a link). __AGENTSYNC_RAISE=1 prints the full trace instead."""
    if isinstance(exc, _Stopped):
        os.write(2, stopped_line("Stopped. Temporary downloads may remain; no report written."))
        os._exit(128 + exc.signum)
    if issubclass(kind, KeyboardInterrupt) or os.environ.get("__AGENTSYNC_RAISE"):
        sys.__excepthook__(kind, exc, tb)  # type: ignore[arg-type]
        return
    import platform
    import traceback

    frames = [f for f in traceback.extract_tb(tb) if f.filename == SOURCE]  # type: ignore[arg-type]
    site = f"{kind.__name__} at {SOURCE}:{frames[-1].lineno}" if frames else kind.__name__
    try:
        from agentsync import __version__ as version
    except Exception:
        version = "unknown"
    try:
        from agentsync.it_request import REPO_URL as repo_url
    except Exception:
        repo_url = "https://github.com/renchris/agent-context-sync"
    if JSON:
        partial = None
        if "write_report" in globals() and "out_dir" in globals():
            try:
                emit(f"**failed: unexpected error**: {site}")
                partial = write_report(out_dir, or_exit=False)
            except BaseException:
                pass
        json_error("internal_error", f"Unexpected error in tenant-probes.sh: {site}. This is a bug in agentsync "
                   f"{version}; report it at {repo_url}/issues/new", partial)
        return
    fail_line("Unexpected error in tenant-probes.sh. This is a bug in agentsync.")
    host = f"macOS {platform.mac_ver()[0]}" if platform.mac_ver()[0] else platform.system()
    print(f"  Error: {site}", file=sys.stderr)
    print(f"  Version: agentsync {version}, Python {platform.python_version()}, {host}", file=sys.stderr)
    print(f"  Report: {repo_url}/issues/new", file=sys.stderr)
    if "write_report" in globals() and "out_dir" in globals():
        try:
            emit(f"**failed: unexpected error**: {site}")
            print(f"  Partial report: {write_report(out_dir)}", file=sys.stderr)
        except BaseException:
            pass


sys.excepthook = bug_report

try:
    import truststore  # the macOS trust store before anything imports ssl users (C15 section 9 item 32)
except ImportError as exc:
    no_module(exc)

truststore.inject_into_ssl()

import base64  # noqa: E402
import datetime as dt  # noqa: E402
import fcntl  # noqa: E402
import json  # noqa: E402
import platform  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import tempfile  # noqa: E402
import unicodedata  # noqa: E402
from pathlib import Path  # noqa: E402

CONFIG, OUT, DRY, SELFTEST = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1", sys.argv[4] == "1"
MAX_PAGES, MAX_DOWNLOADS, MAX_BYTES = int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7])
MILESTONES = (30, 60, 90)
PROBE_EXTS = (".docx", ".xlsx", ".pptx", ".pdf")
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
# CSI and terminated OSC sequences, whole, as agentsync.text has them; the categories below take the rest
ESCAPE_RE = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]|(?:\x1b\]|\x9d)[^\x07\x1b\x9c\x9d]*(?:\x07|\x1b\\|\x9c)")
DROPPED = frozenset(("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"))


def clean(text: object) -> str:
    """Remote text (a Graph code or message, a file name) as one printable line: no escape sequence, control,
    bidi or zero-width character; a newline, return or tab shows as two characters. Local rather than
    agentsync.text.safe_text, because the interpreter may hold an older agentsync build."""
    shown = ESCAPE_RE.sub("", str(text)).replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return "".join(c for c in shown if unicodedata.category(c) not in DROPPED)


def cell(text: object, limit: int | None = 160) -> str:
    """``clean`` for a report table cell: ``|`` becomes ``/``, and past ``limit`` it is cut with an ellipsis."""
    shown = clean(text).replace("|", "/")
    return shown if limit is None or len(shown) <= limit else shown[: limit - 1] + "…"


def quickxor(path: Path) -> str:
    """OneDrive QuickXorHash (160-bit, shift 11): byte i lands at bit (11*i) mod 160, so fold 160-byte rows."""
    fold, length = 0, 0
    with path.open("rb") as fh:
        while chunk := fh.read(160 * 8192):
            length += len(chunk)
            for i in range(0, len(chunk), 160):
                fold ^= int.from_bytes(chunk[i : i + 160], "little")
    mask, h = (1 << 160) - 1, 0
    for j in range(160):
        byte = (fold >> (8 * j)) & 0xFF
        if byte:
            v = byte << ((11 * j) % 160)
            h ^= (v & mask) | (v >> 160)
    out = bytearray(h.to_bytes(20, "little"))
    for i, b in enumerate(length.to_bytes(8, "little")):
        out[12 + i] ^= b
    return base64.b64encode(bytes(out)).decode()


def claims(token: str) -> dict:
    """The few access-token claims the report needs (never the token); {} when the token is opaque."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        body = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        return {}
    return {
        "scp": sorted(str(body.get("scp", "")).split()),
        "amr": list(body.get("amr", [])),
        "device_claim": "deviceid" in body,
        "tid": body.get("tid"),
        "appid": body.get("appid") or body.get("azp"),
        "minutes_left": int((int(body.get("exp", 0)) - NOW.timestamp()) // 60) if body.get("exp") else None,
    }


def selftest() -> int:
    vectors = [(b"", "AAAAAAAAAAAAAAAAAAAAAAAAAAA="), (base64.b64decode("Sg=="), "SgAAAAAAAAAAAAAAAQAAAAAAAAA=")]
    vectors += [(base64.b64decode("vPgoDjOfO6fm71RxLw=="), "vMAHChwwg0/s4BTmdQcV4vACAAA=")]
    vectors += [(base64.b64decode("NbYXsp5/K6mR+NmHwExjvWeWDJFnXTKWVlzYHoesp2E="), "wjuAuWDiq04qDt1R8hHWDDcwVoQ=")]
    with tempfile.TemporaryDirectory() as tmp:
        for n, (data, want) in enumerate(vectors):
            p = Path(tmp) / f"v{n}"
            p.write_bytes(data)
            got = quickxor(p)
            if got != want:
                print(f"selftest FAILED: quickxor vector {n}: {got} != {want}", file=sys.stderr)
                return 1
        big = Path(tmp) / "big"  # the row fold must equal the byte-by-byte definition across rows
        data = bytes((i * 7 + 3) % 251 for i in range(1000))
        big.write_bytes(data)
        h, mask = 0, (1 << 160) - 1
        for i, b in enumerate(data):
            v = b << ((11 * i) % 160)
            h ^= (v & mask) | (v >> 160)
        ref = bytearray(h.to_bytes(20, "little"))
        for i, b in enumerate(len(data).to_bytes(8, "little")):
            ref[12 + i] ^= b
        if quickxor(big) != base64.b64encode(bytes(ref)).decode():
            print("selftest FAILED: row fold differs from the byte-by-byte QuickXorHash", file=sys.stderr)
            return 1
    body = base64.urlsafe_b64encode(json.dumps({"scp": "User.Read Files.Read.All", "deviceid": "x"}).encode())
    c = claims("h." + body.decode().rstrip("=") + ".s")
    if c.get("scp") != ["Files.Read.All", "User.Read"] or c.get("device_claim") is not True or claims("opaque"):
        print(f"selftest FAILED: claims {c}", file=sys.stderr)
        return 1
    got = cell("\x1b[31mred\x1b[0m \u202eflip|pipe\nnext\x1b]8;;x\x07")
    if got != "red flip/pipe\\nnext" or cell("x" * 160) != "x" * 160 or cell("x" * 161) != "x" * 159 + "…":
        print(f"selftest FAILED: cell {got!r}", file=sys.stderr)
        return 1
    print("selftest ok: 4 QuickXorHash vectors (rclone), row fold, claim decoding")
    return 0


if SELFTEST:
    sys.exit(selftest())

try:
    from agentsync import __version__, net  # noqa: E402
    from agentsync.config import load_config  # noqa: E402
    from agentsync.errors import AgentSyncError, AuthError, AuthRequiredError, ConfigError, GraphError  # noqa: E402
    from agentsync.model import SourceKind  # noqa: E402
except ImportError as exc:
    no_module(exc)

lines: list[str] = []
loose_warned = False


def emit(text: str = "") -> None:
    lines.append(text)


class StoreError(AgentSyncError):
    """deltalinks.json can't be read or has the wrong shape: rc 1, and the probes before it still reach the report."""


def atomic_write(path: Path, text: str) -> None:
    """``text`` to ``path`` (0600) through a temporary file in the same folder, so a stop or a full disk keeps
    the old bytes. The temporary name never matches ``login-*.txt`` or ``tenant-probes-*.md``."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        Path(tmp).unlink(missing_ok=True)
        raise
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def ensure_out_dir(out_dir: Path) -> None:
    """The report folder, 0700. The default one is tightened if it is looser (the documented ``mkdir -p``
    makes it 0755); a folder given with --output is the operator's, so it is only warned about."""
    global loose_warned
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not OUT:
        os.chmod(out_dir, 0o700)
    elif out_dir.stat().st_mode & 0o077 and not loose_warned:
        loose_warned = True
        mode = stat.S_IMODE(out_dir.stat().st_mode)
        warn(f"{out_dir} is mode {mode:04o}, open to other users; the files in it stay 0600")


def write_report(out_dir: Path, *, or_exit: bool = True) -> Path:
    path = out_dir / f"tenant-probes-{NOW.strftime('%Y%m%dT%H%M%SZ')}.md"
    try:
        ensure_out_dir(out_dir)
        atomic_write(path, "\n".join(lines).rstrip() + "\n")
    except OSError as exc:
        if not or_exit:
            raise
        message = f"Can't write the report under {out_dir}: {exc.strerror or exc}. Pass --output with a folder you can write."
        if not json_error("failed", message):
            fail_line(message)
        sys.exit(1)
    return path


def load_store(store: Path) -> dict:
    """The probe-9 store, checked before anything writes it, so a damaged one is never truncated. A message names
    the place, never the bytes: a link holds a token."""
    try:
        mode = store.stat().st_mode
    except FileNotFoundError:
        return {"version": 1, "links": []}
    except OSError as exc:
        raise StoreError(f"{store}: can't read it: {exc.strerror}") from None
    if not stat.S_ISREG(mode):
        raise StoreError(f"{store}: not a regular file")
    try:
        data = json.loads(store.read_bytes().decode("utf-8"))
    except OSError as exc:
        raise StoreError(f"{store}: can't read it: {exc.strerror}") from None
    except UnicodeDecodeError as exc:
        raise StoreError(f"{store}: not UTF-8 at byte {exc.start}") from None
    except json.JSONDecodeError as exc:
        raise StoreError(f"{store}: line {exc.lineno} column {exc.colno}: {exc.msg}") from None
    if not isinstance(data, dict) or "version" not in data:
        raise StoreError(f"{store}: damaged: no version")
    version = data["version"]
    if version != 1:
        newer = isinstance(version, int) and not isinstance(version, bool) and version > 1
        raise StoreError(f"{store}: written by a newer build (version {version}); this script reads version 1"
                         if newer else f"{store}: damaged: version is not 1")
    links = data.get("links")
    if not isinstance(links, list):
        raise StoreError(f"{store}: damaged: links is not a list")
    want = {"drive": (str,), "obtained_at": (str,), "milestone_days": (int,), "link": (str, type(None)),
            "tested_at": (str, type(None)), "outcome": (str, type(None)), "age_days": (int, type(None))}
    for i, entry in enumerate(links):
        if not isinstance(entry, dict):
            raise StoreError(f"{store}: damaged: links[{i}] is not an object")
        for key, types in want.items():
            if not isinstance(entry.get(key), types) or isinstance(entry.get(key), bool):
                raise StoreError(f"{store}: damaged: links[{i}].{key} is missing or of the wrong type")
        try:
            aware = dt.datetime.fromisoformat(entry["obtained_at"]).tzinfo is not None
        except ValueError:
            aware = False
        if not aware:
            raise StoreError(f"{store}: damaged: links[{i}].obtained_at is not a UTC time")
    return data


try:
    config = load_config(CONFIG)
except ConfigError as exc:
    if not json_error("config_invalid", f"Configuration invalid: {exc}"):
        fail_line(f"Configuration invalid: {exc}")
    sys.exit(78)

out_dir = Path(OUT) if OUT else config.state_paths.root / "probes"
graph_sources = [s for s in config.sources if s.kind is SourceKind.GRAPH_DRIVE]
emit(f"# agentsync tenant probes, {NOW.isoformat().replace('+00:00', 'Z')}")
emit()
emit(f"- agentsync {__version__}; macOS {platform.mac_ver()[0]} {platform.machine()}; Python {platform.python_version()}")
emit(f"- config `{CONFIG}`; Graph `{config.graph.base_url}`; tenant `{config.graph.tenant}`")
emit("- read-only: GETs and downloads into a deleted temporary directory; nothing is written to the tenant")
emit()

if not config.graph.client_id:
    if DRY:
        if not json_error("client_id_unset", "Can't run the probes. [graph] client_id is unset. The dry run wrote nothing."):
            fail_line("Can't run the probes. [graph] client_id is unset. The dry run wrote nothing.")
        sys.exit(78)
    emit("**Not run:** `[graph] client_id` is unset (the IT app registration is pending; docs/deploy/it-request.md).")
    report = write_report(out_dir)
    if not json_error("client_id_unset", "Can't run the probes. [graph] client_id is unset.", report):
        fail_line("Can't run the probes. [graph] client_id is unset.")
        print(f"Report: {report}", file=sys.stderr)
    sys.exit(78)

try:
    from agentsync.graph.auth import MsalAuth, settings_from_config  # noqa: E402 - msal after truststore
    from agentsync.graph.client import GraphClient, user_agent  # noqa: E402
    from agentsync.graph.drive import DRIVE_SELECT, resolve_drive_id  # noqa: E402
    from agentsync.graph.errors import AuthBlockedError, NetworkPolicyError  # noqa: E402
    from agentsync.policy import read_labels  # noqa: E402
except ImportError as exc:
    no_module(exc)

try:
    settings = settings_from_config(config)
except ConfigError as exc:
    if not json_error("config_invalid", f"Configuration invalid: {exc}"):
        fail_line(f"Configuration invalid: {exc}")
    sys.exit(78)

drives_planned = ["me (/me/drive)", "root-site (/sites/root/drive)"] + [f"{s.id} ({s.drive_id or s.site})" for s in graph_sources]
PROBE2_READS = ["/me", "/me/drive", "/sites/root", "/me/followedSites", "/me/mailFolders", "/me/joinedTeams",
                "/teams/{first}/channels", "/teams/{first}/channels/{first}/messages", "/me/chats"]
if DRY and JSON:
    json_out({
        "report": {"path": f"{out_dir}/tenant-probes-<UTC timestamp>.md"},
        "outcome": "planned",
        "probes": [
            {"id": "2", "requests": PROBE2_READS},
            {"id": "1", "requests": ["/drives/{id}/root/delta"], "maxPages": MAX_PAGES, "drives": drives_planned},
            {"id": "4b", "requests": ["/drives/{id}/items/{id}/content"], "maxDownloads": MAX_DOWNLOADS,
             "maxBytes": MAX_BYTES, "types": list(PROBE_EXTS)},
            {"id": "9", "requests": ["/me/drive/root/delta"], "store": f"{out_dir}/deltalinks.json",
             "milestones": list(MILESTONES)},
        ],
        "authority": settings.authority,
        "requestedScopes": list(config.graph_scopes()),
        "interpreter": sys.executable,
    })
    sys.exit(0)
if DRY:
    print(f"dry run: nothing is signed in, requested or written\ninterpreter: {sys.executable}")
    print(f"authority: {settings.authority}\nrequested scopes: {' '.join(config.graph_scopes())}")
    print("probe 2: silent token (no prompt); GET /me, /me/drive, /sites/root, /me/followedSites, "
          "/me/mailFolders, /me/joinedTeams, /teams/{first}/channels, /teams/{first}/channels/{first}/messages, /me/chats")
    print(f"probe 1: GET /drives/{{id}}/root/delta?$select=<drive arm>, at most {MAX_PAGES} pages, on: {', '.join(drives_planned)}")
    print(f"probe 4b: at most {MAX_DOWNLOADS} of {'/'.join(PROBE_EXTS)} <= {MAX_BYTES} bytes, each downloaded twice, then deleted")
    print(f"probe 9: <out>/deltalinks.json (0600): 3 token=latest links per run set, tested once at {MILESTONES} days")
    print(f"report: {out_dir}/tenant-probes-<UTC timestamp>.md (0600)")
    sys.exit(0)

auth = MsalAuth(settings)
status = auth.status()
emit("## Probe 2: sign-in and consent outcome")
emit()
emit("| item | value |")
emit("|---|---|")
emit(f"| cached account | {'yes' if status.signed_in else 'no'}; domain `{(status.username or '-').rpartition('@')[2]}`; tenant `{status.tenant_id or '-'}` |")
emit(f"| sign-in method (last `agentsync graph login`) | `{status.sign_in_method or '-'}` |")
emit(f"| token cache | `{status.cache_backend}` |")
emit(f"| requested scopes | `{' '.join(config.graph_scopes())}` |")
# the --json record of each probe, collected beside its rows; the username only as its domain
username = status.username or ""
probe2: dict[str, object] = {
    "id": "2", "signedIn": bool(status.signed_in), "domain": username.rpartition("@")[2] if "@" in username else None,
    "tenantId": status.tenant_id, "signInMethod": status.sign_in_method, "tokenCache": status.cache_backend,
    "requestedScopes": list(config.graph_scopes()),
}
records: list[dict[str, object]] = [probe2]
logins =sorted(out_dir.glob("login-*.txt")) if out_dir.is_dir() else []
if logins:
    tail = [ln for ln in logins[-1].read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()][-3:]
    emit(f"| `{logins[-1].name}` (last lines) | {cell(' / '.join(tail))} |")
try:
    token = auth.get_token()
except AuthBlockedError as exc:
    emit(f"| silent token | **{exc.state}** (`{cell(exc.aadsts or '-')}`): {cell(exc, None)} |")
    report = write_report(out_dir)
    if not json_error("sign_in_blocked", f"sign-in blocked ({exc.state}): {cell(exc, None)}", report):
        print(f"sign-in blocked ({exc.state}): {cell(exc, None)}; report {report}", file=sys.stderr)
    sys.exit(77)
except AuthRequiredError as exc:
    emit(f"| silent token | **reauth-required**: {cell(exc, None)}. Run `agentsync graph login 2>&1 | tee \"{out_dir}/login-$(date +%Y%m%d).txt\"`, then re-run |")
    report = write_report(out_dir)
    if not json_error("not_signed_in", "not signed in; run `agentsync graph login`, then re-run", report):
        print(f"not signed in; run `agentsync graph login`, then re-run; report {report}", file=sys.stderr)
    sys.exit(77)
except AuthError as exc:  # a PAC-only network or a refresh that failed: not a sign-in state, so exit 1
    emit(f"| silent token | **failed**: {cell(exc, None)} |")
    report = write_report(out_dir)
    if not json_error("failed", f"Can't get a token silently: {cell(exc, None)}", report):
        fail_line(f"Can't get a token silently: {cell(exc, None)}; report {report}")
    sys.exit(1)
c = claims(token)
del token
emit(f"| token source (MSAL `token_source`) | `{auth.last_token_source or '-'}` |")
probe2["tokenSource"] = auth.last_token_source
if c:
    granted = {s.lower() for s in c["scp"]}
    missing = sorted(s for s in config.graph_scopes() if s.lower() not in granted)
    emit(f"| granted scopes (`scp`) | `{' '.join(c['scp'])}` |")
    emit(f"| requested but not granted | `{' '.join(missing) or 'none'}` |")
    emit(f"| device identity claim (`deviceid`) | {'present (broker / device-bound sign-in)' if c['device_claim'] else 'absent'} |")
    emit(f"| authentication methods (`amr`) | `{' '.join(c['amr']) or '-'}` |")
    emit(f"| app id matches `[graph] client_id` | {c['appid'] == settings.client_id} |")
    probe2.update(grantedScopes=c["scp"], missingScopes=missing, deviceClaim=c["device_claim"], authMethods=c["amr"],
                  appIdMatches=c["appid"] == settings.client_id)
else:
    emit("| token claims | opaque token (not a JWT); scopes not readable |")
    probe2["opaqueToken"] = True
emit()

client = GraphClient(
    auth,
    base_url=config.graph.base_url,
    user_agent=user_agent("agentsync", __version__),  # [graph] company is ignored (KISS K15)
    proxy=net.resolve_proxy(config.network.proxy),
    max_retries=3,
)


def graph_result(exc: GraphError | None = None) -> dict[str, object]:
    """A read's outcome for the --json record: the status, and the Graph code of a failure."""
    return {"status": 200} if exc is None else {"status": exc.status, "code": cell(exc.code)}


last_result: dict[str, object] = {}
# what could not finish (a 0, a 429, a 5xx, a failed download, a delta page with no deltaLink); a 401, 403 or
# 404, and a 410 on probe 9, is the tenant's answer and so a measurement
incomplete: list[str] = []


def measured(exc: BaseException, *, gone_counts: bool = False) -> bool:
    return isinstance(exc, GraphError) and (exc.status in (401, 403, 404) or (gone_counts and exc.status == 410))


def status_of(path: str, params: dict | None = None, label: str | None = None) -> tuple[str, dict]:
    """A read's result cell and body; ``label`` names it in the incomplete list when the path holds ids."""
    global last_result
    try:
        body = client.get_json(path, params=params)
        last_result = graph_result()
        return "200", body
    except NetworkPolicyError:
        raise
    except AuthRequiredError as exc:
        last_result = {"status": 401, "message": cell(exc, None)}
        return f"401 {cell(exc, None)}", {}
    except GraphError as exc:
        last_result = graph_result(exc)
        if not measured(exc):
            incomplete.append(f"`GET {label or path}`: {exc.status}")
        return f"{exc.status} `{cell(exc.code)}`", {}


rc = 0
reason = failure = ""
tmp_root: Path | None = None
try:
    PROBING = True
    tmp_root = Path(tempfile.mkdtemp(prefix="agentsync-probes-"))
    emit("| read | permission it needs | result |")
    emit("|---|---|---|")
    checks = [
        ("/me", {"$select": "id"}, "User.Read"),
        ("/me/drive", {"$select": "id,driveType"}, "Files.Read(.All)"),
        ("/sites/root", {"$select": "id"}, "Sites.Read.All"),
        ("/me/followedSites", {"$select": "id"}, "Sites.Read.All"),
        ("/me/mailFolders", {"$select": "id", "$top": "1"}, "Mail.ReadBasic / Mail.Read"),
        ("/me/chats", {"$select": "id", "$top": "1"}, "Chat.ReadBasic"),
    ]
    reads: list[dict[str, object]] = []
    probe2["reads"] = reads
    for path, params, perm in checks:
        emit(f"| `GET {path}` | `{perm}` | {status_of(path, params)[0]} |")
        reads.append({"path": path, "permission": perm, **last_result})
    st, teams = status_of("/me/joinedTeams")
    emit(f"| `GET /me/joinedTeams` | `Team.ReadBasic.All` | {st} |")
    reads.append({"path": "/me/joinedTeams", "permission": "Team.ReadBasic.All", **last_result})
    team_ids = sorted(str(t.get("id")) for t in teams.get("value", []))
    # a read never made still gets its row, saying why
    skipped = "" if team_ids else f"skipped: joinedTeams returned {st}" if st != "200" else "skipped: no joined teams"
    chan_ids: list[str] = []
    if team_ids:
        st, chans = status_of(f"/teams/{team_ids[0]}/channels", {"$select": "id"}, "/teams/{first}/channels")
        reads.append({"path": "/teams/{first}/channels", "permission": "Channel.ReadBasic.All", **last_result})
        chan_ids = sorted(str(ch.get("id")) for ch in chans.get("value", []))
    else:
        reads.append({"path": "/teams/{first}/channels", "permission": "Channel.ReadBasic.All", "skipped": skipped})
    emit(f"| `GET /teams/{{first}}/channels` | `Channel.ReadBasic.All` | {st if team_ids else skipped} |")
    if team_ids and not chan_ids:
        skipped = f"skipped: channels returned {st}" if st != "200" else "skipped: no channels"
    if chan_ids:
        st, _ = status_of(f"/teams/{team_ids[0]}/channels/{chan_ids[0]}/messages", {"$top": "1"},
                          "/teams/{first}/channels/{first}/messages")
        reads.append({"path": "/teams/{first}/channels/{first}/messages", "permission": "ChannelMessage.Read.All",
                      **last_result})
    else:
        reads.append({"path": "/teams/{first}/channels/{first}/messages", "permission": "ChannelMessage.Read.All",
                      "skipped": skipped})
    emit(f"| `GET /teams/{{first}}/channels/{{first}}/messages?$top=1` (status only) | `ChannelMessage.Read.All` | {st if chan_ids else skipped} |")
    emit()

    emit("## Probe 1: hashes and cTag coverage (drive delta, first pages only)")
    emit()
    emit("| drive | items | files | quickXorHash | sha256Hash | no hash | cTag on files | zero-size | OneNote packages | listing |")
    emit("|---|---|---|---|---|---|---|---|---|---|")
    targets: list[tuple[str, str | None, str, dict[str, object]]] = []
    st, me = status_of("/me/drive", {"$select": "id"})
    targets.append(("me", me.get("id"), st, last_result))
    st, root = status_of("/sites/root/drive", {"$select": "id"})
    targets.append(("root-site", root.get("id"), st, last_result))
    for src in graph_sources:
        try:
            targets.append((src.id, resolve_drive_id(client, src), "200", graph_result()))
        except NetworkPolicyError:
            raise
        except GraphError as exc:
            targets.append((src.id, None, f"{exc.status} `{cell(exc.code)}`", graph_result(exc)))
            if not measured(exc):
                incomplete.append(f"drive `{cell(src.id, 40)}`: {exc.status}")
    listing_cut = False  # a drive unreadable or stopped: "none" below would claim more than was read
    candidates: list[tuple[str, str, str, int, str | None]] = []
    nohash_ext: dict[str, int] = {}
    seen_drives: set[str] = set()
    drives: list[dict[str, object]] = []  # counts only: never a file name, a drive id or a link
    records.append({"id": "1", "drives": drives})
    for name, drive_id, st, result in targets:
        if not drive_id:
            listing_cut = True
            emit(f"| {name} | - | - | - | - | - | - | - | - | not readable: {st} |")
            drives.append({"name": name, "listing": "not-readable", "error": result})
            continue
        if drive_id in seen_drives:
            emit(f"| {name} | same drive as above | | | | | | | | |")
            drives.append({"name": name, "listing": "same-drive"})
            continue
        seen_drives.add(drive_id)
        n = files = qx = sha = none = ctag = zero = onenote = pages = 0
        listing = "complete"
        drive: dict[str, object] = {"name": name, "listing": "complete"}
        try:
            for page in client.iter_pages(f"/drives/{drive_id}/root/delta", params={"$select": DRIVE_SELECT}):
                pages += 1
                for item in page.value:
                    n += 1
                    if item.get("package", {}).get("type") == "oneNote":
                        onenote += 1
                    f = item.get("file")
                    if f is None or item.get("deleted"):
                        continue
                    files += 1
                    hashes = f.get("hashes") or {}
                    qx += "quickXorHash" in hashes
                    sha += "sha256Hash" in hashes
                    ctag += "cTag" in item
                    zero += int(item.get("size") or 0) == 0
                    ext = os.path.splitext(str(item.get("name", "")))[1].lower() or "(none)"
                    if not hashes:
                        none += 1
                        nohash_ext[ext] = nohash_ext.get(ext, 0) + 1
                    size = int(item.get("size") or 0)
                    if ext in PROBE_EXTS and 0 < size <= MAX_BYTES:
                        candidates.append((ext, drive_id, str(item["id"]), size, hashes.get("quickXorHash")))
                if pages >= MAX_PAGES and page.next_link:
                    listing = f"sample: first {pages} pages"
                    drive["listing"] = "sample"
                    break
        except NetworkPolicyError:
            raise
        except GraphError as exc:
            listing = f"stopped: {exc.status} `{cell(exc.code)}`"
            drive.update(listing="stopped", error=graph_result(exc))
            listing_cut = True
            if not measured(exc):
                incomplete.append(f"drive `{cell(name, 40)}` listing: {exc.status}")
        emit(f"| {name} | {n} | {files} | {qx} | {sha} | {none} | {ctag} | {zero} | {onenote} | {listing} |")
        drive.update(pages=pages, items=n, files=files, quickXorHash=qx, sha256Hash=sha, noHash=none, cTagOnFiles=ctag,
                     zeroSize=zero, oneNotePackages=onenote)
        drives.append(drive)
    emit()
    unread = "none in what was read; a drive listing did not complete (see listing)"
    emit("Files without any hash, by extension: " + (", ".join(f"`{cell(k, 32)}` {v}" for k, v in sorted(nohash_ext.items())) or (unread if listing_cut else "none")) + ".")
    emit("Not probed here: whether `cTag` lags right after an upload (needs a write; do it by hand in a scratch library).")
    emit()

    emit("## Probe 4b: download twice, compare (label and hash stability)")
    emit()
    emit("| # | type | size | labels in the file | download 1 == download 2 (sha256) | served bytes == quickXorHash |")
    emit("|---|---|---|---|---|---|")
    picked: list[tuple[str, str, str, int, str | None]] = []
    by_ext: dict[str, list] = {}
    for cand in sorted(candidates, key=lambda c: (c[0], c[3], c[2])):
        by_ext.setdefault(cand[0], []).append(cand)
    while len(picked) < MAX_DOWNLOADS and any(by_ext.values()):
        for ext in sorted(by_ext):
            if by_ext[ext] and len(picked) < MAX_DOWNLOADS:
                picked.append(by_ext[ext].pop(0))
    if MAX_DOWNLOADS == 0:
        emit("| - | skipped: --max-downloads 0 | | | | |")
    elif not picked and listing_cut:
        emit("| - | no Office/PDF file in what was read: a drive listing did not complete | | | | |")
    elif not picked:
        emit("| - | no Office/PDF file within the size limit in the sampled pages | | | | |")
    downloads: list[dict[str, object]] = []
    records.append({"id": "4b", "files": downloads})
    for n, (ext, drive_id, item_id, size, qxh) in enumerate(picked, 1):
        a, b = tmp_root / f"{n}a{ext}", tmp_root / f"{n}b{ext}"
        try:
            _, sha_a = client.download(f"/drives/{drive_id}/items/{item_id}/content", a, max_bytes=MAX_BYTES)
            _, sha_b = client.download(f"/drives/{drive_id}/items/{item_id}/content", b, max_bytes=MAX_BYTES)
        except NetworkPolicyError:
            raise
        except (GraphError, AgentSyncError) as exc:
            emit(f"| {n} | `{ext}` | {size} | - | download failed: {cell(exc, 80)} | - |")
            downloads.append({"n": n, "type": ext, "size": size, "error": f"download failed: {cell(exc, 80)}"})
            if not measured(exc):
                incomplete.append(f"probe 4b file {n}: download failed")
            continue
        labels_record: dict[str, object]
        try:
            labels = read_labels(a, name=f"probe{ext}")
            lab = "error: " + cell(labels.error, 60) if labels.error else (
                f"{len(labels.labels)} ({', '.join(sorted({lb.origin for lb in labels.labels}))})" if labels.labels else "none")
            labels_record = {"error": cell(labels.error, 60)} if labels.error else {
                "count": len(labels.labels), "origins": sorted({lb.origin for lb in labels.labels})}
        except Exception as exc:  # a reader defect is this file's cell, not the end of the run
            lab = "error: " + cell(type(exc).__name__, 60)
            labels_record = {"error": cell(type(exc).__name__, 60)}
        matches = None if not qxh else quickxor(a) == qxh
        served = "n/a (no quickXorHash)" if matches is None else str(matches)
        emit(f"| {n} | `{ext}` | {size} | {lab} | {sha_a == sha_b} | {served} |")
        downloads.append({"n": n, "type": ext, "size": size, "labels": labels_record, "downloadsEqual": sha_a == sha_b,
                          "servedMatchesHash": matches})
        a.unlink(missing_ok=True)
        b.unlink(missing_ok=True)
    emit()

    emit("## Probe 9: deltaLink idle lifetime (one link per milestone, each used once)")
    emit()
    store = out_dir / "deltalinks.json"
    ensure_out_dir(out_dir)
    # one run at a time holds the store; never wait for the lock, since this section makes network calls
    lock_fd = os.open(out_dir / ".deltalinks.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        lock_fd = -1
    if lock_fd < 0:
        emit("**Skipped:** another tenant-probes.sh run holds `.deltalinks.lock`; re-run probe 9 once it ends.")
        rc = 1
        reason = "failed"
        failure = f"failed: probe 9 skipped: another run holds {out_dir}/.deltalinks.lock"
    else:
        probe9: dict[str, object] = {"id": "9"}  # ages and outcomes, never a link
        unfinished: dict[int, str] = {}  # this run's cell for a test that could not finish; the link is kept
        try:
            data = load_store(store)
            try:
                for i, entry in enumerate(data["links"]):
                    obtained = dt.datetime.fromisoformat(entry["obtained_at"])
                    age = (NOW - obtained).days
                    if entry.get("link") and age >= entry["milestone_days"]:
                        try:
                            client.get_json(entry["link"])
                            entry["outcome"] = "200: still valid"
                        except NetworkPolicyError:
                            raise
                        except GraphError as exc:
                            if not measured(exc, gone_counts=True):  # no answer about the link: it stays usable
                                unfinished[i] = f"not completed: {exc.status} {cell(exc.code, None)}; the link is kept for the next run"
                                incomplete.append(f"probe 9 {entry['milestone_days']}-day test: {exc.status}")
                                continue
                            entry["outcome"] = f"{exc.status} {cell(exc.code, None)}" + (": expired, full re-enumeration" if exc.status == 410 else "")
                        entry["tested_at"], entry["age_days"], entry["link"] = NOW.isoformat(), age, None
                if not any(e.get("link") for e in data["links"]):
                    for days in MILESTONES:
                        try:
                            page = client.get_json("/me/drive/root/delta", params={"token": "latest", "$select": "id"})
                        except NetworkPolicyError:
                            raise
                        except GraphError as exc:
                            emit(f"Couldn't get a deltaLink: {exc.status} `{cell(exc.code)}`.")
                            probe9["linkError"] = graph_result(exc)
                            if not measured(exc):
                                incomplete.append(f"probe 9 new links: {exc.status}")
                            break
                        link = page.get("@odata.deltaLink")
                        if not link:
                            emit("not stored: response carried no deltaLink")
                            probe9["linkError"] = {"status": 200, "message": "not stored: response carried no deltaLink"}
                            incomplete.append("probe 9 new links: no deltaLink")
                            break
                        data["links"].append({"drive": "me", "obtained_at": NOW.isoformat(), "milestone_days": days,
                                              "link": link, "tested_at": None, "outcome": None, "age_days": None})
            finally:  # a link already tested is spent: keep its outcome even when the loop stopped early
                atomic_write(store, json.dumps(data, indent=1, sort_keys=True))
        finally:
            os.close(lock_fd)
        emit("| drive | obtained | test due | tested | age (days) | outcome |")
        emit("|---|---|---|---|---|---|")
        milestones: list[dict[str, object]] = []
        for i, e in enumerate(data["links"]):
            due = (dt.datetime.fromisoformat(e["obtained_at"]) + dt.timedelta(days=e["milestone_days"])).date()
            outcome = cell(unfinished.get(i) or e.get("outcome") or "pending")
            emit(f"| {e['drive']} | {e['obtained_at'][:10]} | {due} | {(e.get('tested_at') or '-')[:10]} | {e.get('age_days') if e.get('age_days') is not None else '-'} | {outcome} |")
            milestones.append({"drive": cell(e["drive"]), "obtained": e["obtained_at"][:10], "testDue": due.isoformat(),
                               "tested": (e.get("tested_at") or "")[:10] or None, "ageDays": e.get("age_days"),
                               "outcome": outcome})
        probe9["milestones"] = milestones
        records.append(probe9)
        emit()
        emit("A 410 is not an outage: the drive arm falls back to a full enumeration. The result sets the reconcile floor.")
except _Stopped as exc:
    name = signal.Signals(exc.signum).name
    emit(f"**stopped: {name}**: the run was stopped before it finished; this report holds what ran before it.")
    rc = 128 + exc.signum
    reason = "canceled"
    failure = "Stopped waiting. Nothing was written to the tenant; temporary downloads removed."
except NetworkPolicyError as exc:
    emit(f"**failed: network-policy ({exc.policy})**: {cell(exc, None)}. See docs/deploy/README.md (TLS inspection / proxy).")
    rc = 1
    reason = "network_policy"
    failure = f"failed: network-policy ({exc.policy}); see docs/deploy/README.md (TLS inspection / proxy)"
except AuthRequiredError as exc:
    emit(f"**stopped: sign-in required mid-run**: {cell(exc, None)}. Run `agentsync graph login`, then re-run.")
    rc = 77
    reason = "not_signed_in"
    failure = "stopped: sign-in required mid-run; run `agentsync graph login`, then re-run"
except AgentSyncError as exc:
    emit(f"**failed**: {cell(exc, None)}")
    rc = 1
    reason = "failed"
    if isinstance(exc, GraphError):
        failure = f"failed: {exc.status} {cell(exc.code)}"
    else:  # a store message is the repair, so it is never cut
        failure = f"failed: {cell(exc, None if isinstance(exc, StoreError) else 160)}"
except OSError as exc:
    where = f" ({exc.filename})" if exc.filename else ""
    emit(f"**failed**: {cell(exc.strerror or exc, None)}{cell(where, None)}")
    rc = 1
    reason = "failed"
    failure = f"failed: {cell(exc.strerror or exc, None)}{cell(where, None)}"
finally:
    # the downloads go first, past a second signal; then a second signal ends the run at once
    for _sig in HANDLED:
        signal.signal(_sig, signal.SIG_IGN)
    if tmp_root is not None:
        shutil.rmtree(tmp_root, ignore_errors=True)
    for _sig in HANDLED:
        signal.signal(_sig, signal.SIG_DFL)
    client.close()

if incomplete:
    probes_word = f"{len(incomplete)} probe{'s' if len(incomplete) != 1 else ''} incomplete"
    emit()
    emit(f"**{probes_word}** (re-run once the cause clears): {'; '.join(incomplete)}.")
emit()
emit("## Not probed by this script")
emit()
emit("- Device-code block state (design §9 probe 2 extension): needs an interactive `agentsync graph login --device-code`, "
     "and only when IT has set `allow_device_code`; expect `blocked: policy` (AADSTS53003).")
emit("- Token Protection, Teams channel delta, shared-mailbox delta, launchd matrix: C15 §8 probes 4, 6, 7 (manual).")
report = write_report(out_dir)
only_incomplete = rc == 0 and bool(incomplete)
if only_incomplete:  # D33: a probe that could not finish is exit 1; a measured refusal is not
    rc, reason, failure = 1, "failed", f"{probes_word}; see report"
if rc and json_error(reason, failure, report, {"incomplete": len(incomplete)} if incomplete else None):
    pass
elif only_incomplete:
    print(f"{failure} {report}", file=sys.stderr)
elif rc > 128:
    print(f"{failure} Partial report: {report}", file=sys.stderr)
elif rc:
    print(f"{failure}; report {report}", file=sys.stderr)
elif JSON:
    json_out({"report": {"path": str(report)}, "outcome": "created", "probes": records})
else:
    print(f"report: {report}")
sys.exit(rc)
PYTHON
