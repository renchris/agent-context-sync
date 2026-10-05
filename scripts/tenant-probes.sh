#!/bin/bash
# agentsync tenant probes: the open design §9 probes that need the target tenant, read-only and zero-prompt.
#
# Usage: scripts/tenant-probes.sh [--dry-run] [--config PATH] [--out DIR] [--python PATH]
#                                 [--max-pages N] [--max-downloads N] [--max-bytes BYTES] [--selftest]
#
#   --dry-run          load the config and print what would be requested; no sign-in, no network, no writes
#   --config PATH      sources.toml (default: $AGENTSYNC_CONFIG or ~/agent-context/sources.toml)
#   --out DIR          where the dated report goes (default: <state_dir>/probes, mode 0700)
#   --python PATH      the interpreter that has agentsync installed (default: $AGENTSYNC_PYTHON, then the
#                      uv tool environment `agentsync graph login` ran in, then this checkout's .venv)
#   --max-pages N      delta pages read per drive for probe 1 (default 10, about 2,000 items)
#   --max-downloads N  files downloaded twice for probe 4b (default 6)
#   --max-bytes BYTES  largest file probe 4b downloads (default 20000000)
#   --selftest         check the offline helpers (QuickXorHash vectors, claim decoding) and exit
#
# Run it after `agentsync graph login` (the only interactive step, done by the operator). It never opens a
# browser, never prompts and never writes to the tenant: it uses the cached token silently (exit 77 when
# there is none) and only GETs, plus two downloads per probe-4b file into a temporary directory it deletes.
# Probes (design §9 numbering):
#   1   hashes/cTag coverage: GET /drives/{id}/root/delta ($select as the drive arm) on /me/drive, the root
#       site's library and every configured graph_drive source; counts only, no names
#   2   consent outcome: granted scopes (the token's scp claim), token source, device claim, and the HTTP status
#       of one read per permission; the newest <out>/login-*.txt (tee'd `agentsync graph login` output) is quoted
#   4b  label/hash stability: up to N Office/PDF files downloaded twice; sha256 equal? served bytes ==
#       quickXorHash? labels present in the file? (the files are deleted at once)
#   9   deltaLink idle lifetime: stores three `token=latest` links (mode 0600, never in the report) and tests
#       each exactly once, at 30, 60 and 90 days; re-run the script on or after those dates
#
# Exit status: 0 report written; 2 usage error; 77 not signed in / sign-in blocked (report written, names
# the state); 78 configuration invalid or no [graph] client_id; 1 any other failure (network policy included).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"

DRY_RUN=0
SELFTEST=0
CONFIG="${AGENTSYNC_CONFIG:-$HOME/agent-context/sources.toml}"
OUT=""
PY="${AGENTSYNC_PYTHON:-}"
MAX_PAGES=10
MAX_DOWNLOADS=6
MAX_BYTES=20000000

usage_error() {
	printf 'usage error: %s (see --help)\n' "$*" >&2
	exit 2
}
show_help() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }
need_value() { [ "$1" -ge 2 ] || usage_error "$2 needs a value"; }
is_uint() { case "$1" in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac }

while [ $# -gt 0 ]; do
	case "$1" in
	--dry-run) DRY_RUN=1 ;;
	--selftest) SELFTEST=1 ;;
	--config)
		need_value $# "$1"
		CONFIG="$2"
		shift
		;;
	--out)
		need_value $# "$1"
		OUT="$2"
		shift
		;;
	--python)
		need_value $# "$1"
		PY="$2"
		shift
		;;
	--max-pages | --max-downloads | --max-bytes)
		need_value $# "$1"
		is_uint "$2" || usage_error "$1 needs a non-negative integer, got '$2'"
		case "$1" in
		--max-pages) MAX_PAGES="$2" ;;
		--max-downloads) MAX_DOWNLOADS="$2" ;;
		*) MAX_BYTES="$2" ;;
		esac
		shift
		;;
	-h | --help)
		show_help
		exit 0
		;;
	*) usage_error "unknown argument $1" ;;
	esac
	shift
done

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
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
	printf 'error: no interpreter with agentsync installed (run scripts/install.sh, or pass --python)\n' >&2
	exit 1
fi

rc=0
"$PY" -I -X utf8 - "$CONFIG" "$OUT" "$DRY_RUN" "$SELFTEST" "$MAX_PAGES" "$MAX_DOWNLOADS" "$MAX_BYTES" <<'PYTHON' || rc=$?
import truststore  # the macOS trust store before anything imports ssl users (C15 section 9 item 32)

truststore.inject_into_ssl()

import base64  # noqa: E402
import datetime as dt  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import platform  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

CONFIG, OUT, DRY, SELFTEST = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1", sys.argv[4] == "1"
MAX_PAGES, MAX_DOWNLOADS, MAX_BYTES = int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7])
MILESTONES = (30, 60, 90)
PROBE_EXTS = (".docx", ".xlsx", ".pptx", ".pdf")
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


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
                print(f"selftest FAILED: quickxor vector {n}: {got} != {want}")
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
            print("selftest FAILED: row fold differs from the byte-by-byte QuickXorHash")
            return 1
    body = base64.urlsafe_b64encode(json.dumps({"scp": "User.Read Files.Read.All", "deviceid": "x"}).encode())
    c = claims("h." + body.decode().rstrip("=") + ".s")
    if c.get("scp") != ["Files.Read.All", "User.Read"] or c.get("device_claim") is not True or claims("opaque"):
        print(f"selftest FAILED: claims {c}")
        return 1
    print("selftest ok: 4 QuickXorHash vectors (rclone), row fold, claim decoding")
    return 0


if SELFTEST:
    sys.exit(selftest())

from agentsync import __version__, net  # noqa: E402
from agentsync.config import load_config  # noqa: E402
from agentsync.errors import AgentSyncError, AuthRequiredError, ConfigError, GraphError  # noqa: E402
from agentsync.model import SourceKind  # noqa: E402

lines: list[str] = []


def emit(text: str = "") -> None:
    lines.append(text)


def write_report(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = out_dir / f"tenant-probes-{NOW.strftime('%Y%m%dT%H%M%SZ')}.md"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")
    return path


def cell(text: object) -> str:
    return str(text).replace("|", "/").replace("\n", " ")[:160]


try:
    config = load_config(CONFIG)
except ConfigError as exc:
    print(f"configuration invalid: {exc}", file=sys.stderr)
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
    emit("**Not run:** `[graph] client_id` is unset (the IT app registration is pending; docs/deploy/it-request.md).")
    print(f"not run: [graph] client_id is unset; report {write_report(out_dir)}", file=sys.stderr)
    sys.exit(78)

from agentsync.graph.auth import MsalAuth, settings_from_config  # noqa: E402 - msal after truststore
from agentsync.graph.client import GraphClient, user_agent  # noqa: E402
from agentsync.graph.drive import DRIVE_SELECT, resolve_drive_id  # noqa: E402
from agentsync.graph.errors import AuthBlockedError, NetworkPolicyError  # noqa: E402
from agentsync.policy import read_labels  # noqa: E402

try:
    settings = settings_from_config(config)
except ConfigError as exc:
    print(f"configuration invalid: {exc}", file=sys.stderr)
    sys.exit(78)

drives_planned = ["me (/me/drive)", "root-site (/sites/root/drive)"] + [f"{s.id} ({s.drive_id or s.site})" for s in graph_sources]
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
logins = sorted(out_dir.glob("login-*.txt")) if out_dir.is_dir() else []
if logins:
    tail = [ln for ln in logins[-1].read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()][-3:]
    emit(f"| `{logins[-1].name}` (last lines) | {cell(' / '.join(tail))} |")
try:
    token = auth.get_token()
except AuthBlockedError as exc:
    emit(f"| silent token | **{exc.state}** (`{exc.aadsts or '-'}`): {cell(exc)} |")
    print(f"sign-in blocked ({exc.state}); report {write_report(out_dir)}", file=sys.stderr)
    sys.exit(77)
except AuthRequiredError as exc:
    emit(f"| silent token | **reauth-required**: {cell(exc)}. Run `agentsync graph login 2>&1 | tee {out_dir}/login-$(date +%Y%m%d).txt`, then re-run |")
    print(f"not signed in; report {write_report(out_dir)}", file=sys.stderr)
    sys.exit(77)
c = claims(token)
del token
emit(f"| token source (MSAL `token_source`) | `{auth.last_token_source or '-'}` |")
if c:
    granted = {s.lower() for s in c["scp"]}
    missing = sorted(s for s in config.graph_scopes() if s.lower() not in granted)
    emit(f"| granted scopes (`scp`) | `{' '.join(c['scp'])}` |")
    emit(f"| requested but not granted | `{' '.join(missing) or 'none'}` |")
    emit(f"| device identity claim (`deviceid`) | {'present (broker / device-bound sign-in)' if c['device_claim'] else 'absent'} |")
    emit(f"| authentication methods (`amr`) | `{' '.join(c['amr']) or '-'}` |")
    emit(f"| app id matches `[graph] client_id` | {c['appid'] == settings.client_id} |")
else:
    emit("| token claims | opaque token (not a JWT); scopes not readable |")
emit()

client = GraphClient(
    auth,
    base_url=config.graph.base_url,
    user_agent=user_agent("agentsync", __version__),  # [graph] company is ignored (KISS K15)
    proxy=net.resolve_proxy(config.network.proxy),
    max_retries=3,
)


def status_of(path: str, params: dict | None = None) -> tuple[str, dict]:
    try:
        return "200", client.get_json(path, params=params)
    except NetworkPolicyError:
        raise
    except AuthRequiredError as exc:
        return f"401 {cell(exc)[:60]}", {}
    except GraphError as exc:
        return f"{exc.status} `{exc.code}`", {}


rc = 0
tmp_root = Path(tempfile.mkdtemp(prefix="agentsync-probes-"))
try:
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
    for path, params, perm in checks:
        emit(f"| `GET {path}` | `{perm}` | {status_of(path, params)[0]} |")
    st, teams = status_of("/me/joinedTeams")
    emit(f"| `GET /me/joinedTeams` | `Team.ReadBasic.All` | {st} |")
    team_ids = sorted(str(t.get("id")) for t in teams.get("value", []))
    if team_ids:
        st, chans = status_of(f"/teams/{team_ids[0]}/channels", {"$select": "id"})
        emit(f"| `GET /teams/{{first}}/channels` | `Channel.ReadBasic.All` | {st} |")
        chan_ids = sorted(str(ch.get("id")) for ch in chans.get("value", []))
        if chan_ids:
            st, _ = status_of(f"/teams/{team_ids[0]}/channels/{chan_ids[0]}/messages", {"$top": "1"})
            emit(f"| `GET /teams/{{first}}/channels/{{first}}/messages?$top=1` (status only) | `ChannelMessage.Read.All` | {st} |")
    emit()

    emit("## Probe 1: hashes and cTag coverage (drive delta, first pages only)")
    emit()
    emit("| drive | items | files | quickXorHash | sha256Hash | no hash | cTag on files | zero-size | OneNote packages | listing |")
    emit("|---|---|---|---|---|---|---|---|---|---|")
    targets: list[tuple[str, str | None, str]] = []
    st, me = status_of("/me/drive", {"$select": "id"})
    targets.append(("me", me.get("id"), st))
    st, root = status_of("/sites/root/drive", {"$select": "id"})
    targets.append(("root-site", root.get("id"), st))
    for src in graph_sources:
        try:
            targets.append((src.id, resolve_drive_id(client, src), "200"))
        except GraphError as exc:
            targets.append((src.id, None, f"{exc.status} `{exc.code}`"))
    candidates: list[tuple[str, str, str, int, str | None]] = []
    nohash_ext: dict[str, int] = {}
    seen_drives: set[str] = set()
    for name, drive_id, st in targets:
        if not drive_id:
            emit(f"| {name} | - | - | - | - | - | - | - | - | not readable: {st} |")
            continue
        if drive_id in seen_drives:
            emit(f"| {name} | same drive as above | | | | | | | | |")
            continue
        seen_drives.add(drive_id)
        n = files = qx = sha = none = ctag = zero = onenote = pages = 0
        listing = "complete"
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
                    break
        except GraphError as exc:
            listing = f"stopped: {exc.status} `{exc.code}`"
        emit(f"| {name} | {n} | {files} | {qx} | {sha} | {none} | {ctag} | {zero} | {onenote} | {listing} |")
    emit()
    emit("Files without any hash, by extension: " + (", ".join(f"`{k}` {v}" for k, v in sorted(nohash_ext.items())) or "none") + ".")
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
    if not picked:
        emit("| - | no Office/PDF file within the size limit in the sampled pages | | | | |")
    for n, (ext, drive_id, item_id, size, qxh) in enumerate(picked, 1):
        a, b = tmp_root / f"{n}a{ext}", tmp_root / f"{n}b{ext}"
        try:
            _, sha_a = client.download(f"/drives/{drive_id}/items/{item_id}/content", a, max_bytes=MAX_BYTES)
            _, sha_b = client.download(f"/drives/{drive_id}/items/{item_id}/content", b, max_bytes=MAX_BYTES)
        except (GraphError, AgentSyncError) as exc:
            emit(f"| {n} | `{ext}` | {size} | - | download failed: {cell(exc)[:80]} | - |")
            continue
        labels = read_labels(a, name=f"probe{ext}")
        lab = "error: " + cell(labels.error)[:60] if labels.error else (
            f"{len(labels.labels)} ({', '.join(sorted({lb.origin for lb in labels.labels}))})" if labels.labels else "none")
        served = "n/a (no quickXorHash)" if not qxh else str(quickxor(a) == qxh)
        emit(f"| {n} | `{ext}` | {size} | {lab} | {sha_a == sha_b} | {served} |")
        a.unlink(missing_ok=True)
        b.unlink(missing_ok=True)
    emit()

    emit("## Probe 9: deltaLink idle lifetime (one link per milestone, each used once)")
    emit()
    store = out_dir / "deltalinks.json"
    data = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else {"version": 1, "links": []}
    for entry in data["links"]:
        obtained = dt.datetime.fromisoformat(entry["obtained_at"])
        age = (NOW - obtained).days
        if entry.get("link") and age >= entry["milestone_days"]:
            try:
                client.get_json(entry["link"])
                entry["outcome"] = "200: still valid"
            except NetworkPolicyError:
                raise
            except GraphError as exc:
                entry["outcome"] = f"{exc.status} {exc.code}" + (": expired, full re-enumeration" if exc.status == 410 else "")
            entry["tested_at"], entry["age_days"], entry["link"] = NOW.isoformat(), age, None
    if not any(e.get("link") for e in data["links"]):
        for days in MILESTONES:
            try:
                page = client.get_json("/me/drive/root/delta", params={"token": "latest", "$select": "id"})
            except GraphError as exc:
                emit(f"Could not obtain a deltaLink: {exc.status} `{exc.code}`.")
                break
            link = page.get("@odata.deltaLink")
            if link:
                data["links"].append({"drive": "me", "obtained_at": NOW.isoformat(), "milestone_days": days,
                                      "link": link, "tested_at": None, "outcome": None, "age_days": None})
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(store, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    emit("| drive | obtained | test due | tested | age (days) | outcome |")
    emit("|---|---|---|---|---|---|")
    for e in data["links"]:
        due = (dt.datetime.fromisoformat(e["obtained_at"]) + dt.timedelta(days=e["milestone_days"])).date()
        emit(f"| {e['drive']} | {e['obtained_at'][:10]} | {due} | {(e.get('tested_at') or '-')[:10]} | {e.get('age_days') if e.get('age_days') is not None else '-'} | {e.get('outcome') or 'pending'} |")
    emit()
    emit("A 410 is not an outage: the drive arm falls back to a full enumeration. The result sets the reconcile floor.")
except NetworkPolicyError as exc:
    emit(f"**failed: network-policy ({exc.policy})**: {cell(exc)}. See docs/deploy/README.md (TLS inspection / proxy).")
    rc = 1
except AuthRequiredError as exc:
    emit(f"**stopped: sign-in required mid-run**: {cell(exc)}. Run `agentsync graph login`, then re-run.")
    rc = 77
except AgentSyncError as exc:
    emit(f"**failed**: {cell(exc)}")
    rc = 1
finally:
    client.close()
    shutil.rmtree(tmp_root, ignore_errors=True)

emit()
emit("## Not probed by this script")
emit()
emit("- Device-code block state (design §9 probe 2 extension): needs an interactive `agentsync login --device-code`, "
     "and only when IT has set `allow_device_code`; expect `blocked: policy` (AADSTS53003).")
emit("- Token Protection, Teams channel delta, shared-mailbox delta, launchd matrix: C15 §8 probes 4, 6, 7 (manual).")
report = write_report(out_dir)
print(f"report: {report}")
sys.exit(rc)
PYTHON
exit "$rc"
