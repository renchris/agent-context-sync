#!/bin/bash
# agentsync installer for a managed Mac: no admin rights, no interactive prompts, safe to re-run.
#
# Usage: scripts/install.sh [--dry-run] [--source-local FOLDER ...] [--confirm-install-agent] [--launcher PATH]
#                           [--rebuild-launcher] [--config PATH] [--help] [SOURCE]
#
#   SOURCE                  a local agentsync checkout (default: the checkout holding this script) or a wheel
#   --dry-run               print every step that would change something; change nothing
#   --source-local FOLDER   sync this folder (e.g. one inside ~/Library/CloudStorage/OneDrive-<Org>) as a live
#                           local source; repeatable, and already-configured folders are left as they are
#   --confirm-install-agent also install and start the two LaunchAgents (agentsync install-agent)
#   --launcher PATH         use this prebuilt, signed AgentSyncLauncher.app instead of building one
#   --rebuild-launcher      rebuild the launcher even when the installed one matches its sources
#   --config PATH           sources.toml (default: $AGENTSYNC_CONFIG or ~/agent-context/sources.toml)
#
# Steps, each skipped when already done:
#   1. uv in ~/.local/bin (the official installer, without touching shell profiles) unless one is on PATH
#   2. uv tool install agentsync from SOURCE (a uv-managed Python 3.11; the system trust store for TLS)
#   3. the signed launcher at ~/Applications/AgentSyncLauncher.app: built with launcher/build.sh when
#      developer tools exist (SIGN_IDENTITY passes through for a Developer ID build), else --launcher PATH or
#      a prebuilt AgentSyncLauncher.app next to the wheel; an up-to-date one is never rebuilt, because an
#      ad-hoc rebuild is a new TCC identity and macOS would ask again
#   4. agentsync init when the config does not exist (with every --source-local folder), else
#      agentsync add-source for each --source-local folder (idempotent)
#   5. agentsync doctor (its TCC probe may raise the one-time "wants to access files managed by" prompt)
#   6. with --confirm-install-agent: agentsync install-agent
# and finally one line starting "NEXT:" with the single next step.
#
# Exit status: 0 when every step ran (doctor findings do not fail the script), 2 on a usage error, 1 when a
# step failed (including a network/proxy/TLS failure of uv, and --confirm-install-agent that did not install
# the LaunchAgents). Every exit after argument parsing ends with one "NEXT:" line.
set -euo pipefail

ORIG_ARGS="" # every flag given, for a re-run after a failure
BASE_ARGS="" # the same without --source-local FOLDER, for a re-run once the folders are in the config
skip_next=0
for a in "$@"; do
	if [ "$skip_next" -eq 1 ]; then
		skip_next=0
		ORIG_ARGS="$ORIG_ARGS $(printf '%q' "$a")"
		continue
	fi
	case "$a" in
	--dry-run) ;; # the NEXT line is for the real run
	--source-local)
		skip_next=1
		ORIG_ARGS="$ORIG_ARGS $(printf '%q' "$a")"
		;;
	*)
		ORIG_ARGS="$ORIG_ARGS $(printf '%q' "$a")"
		BASE_ARGS="$BASE_ARGS $(printf '%q' "$a")"
		;;
	esac
done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"

DRY_RUN=0
INSTALL_AGENT=0
REBUILD=0
LAUNCHER_SRC=""
SOURCE=""
SOURCE_LOCALS=()
CONFIG="${AGENTSYNC_CONFIG:-$HOME/agent-context/sources.toml}"
APP_NAME="AgentSyncLauncher.app"
APP_DEST="$HOME/Applications/$APP_NAME"
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"
PROMPT_TEXT='“agentsync-launcher” wants to access files managed by “<your sync app, e.g. OneDrive>”'

say() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
fail() {
	printf 'error: %s\n' "$*" >&2
	say "NEXT: fix the error above, then re-run scripts/install.sh$ORIG_ARGS"
	exit 1
}
usage_error() {
	printf 'usage error: %s (see --help)\n' "$*" >&2
	exit 2
}
show_help() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

# Run a command, or only print it under --dry-run.
run() {
	if [ "$DRY_RUN" -eq 1 ]; then
		printf '[dry-run]'
		printf ' %q' "$@"
		printf '\n'
		return 0
	fi
	"$@"
}

while [ $# -gt 0 ]; do
	case "$1" in
	--dry-run) DRY_RUN=1 ;;
	--confirm-install-agent) INSTALL_AGENT=1 ;;
	--rebuild-launcher) REBUILD=1 ;;
	--launcher)
		[ $# -ge 2 ] || usage_error "--launcher needs a path"
		LAUNCHER_SRC="$2"
		shift
		;;
	--config)
		[ $# -ge 2 ] || usage_error "--config needs a path"
		CONFIG="$2"
		shift
		;;
	--source-local)
		[ $# -ge 2 ] && [ -n "$2" ] || usage_error "--source-local needs a folder"
		SOURCE_LOCALS+=("$2")
		shift
		;;
	-h | --help)
		show_help
		exit 0
		;;
	-*) usage_error "unknown option $1" ;;
	*)
		[ -z "$SOURCE" ] || usage_error "only one SOURCE is accepted"
		SOURCE="$1"
		;;
	esac
	shift
done

if [ -z "$SOURCE" ]; then
	SOURCE="$repo"
fi
case "$SOURCE" in
*.whl)
	[ -f "$SOURCE" ] || usage_error "wheel not found: $SOURCE"
	SOURCE="$(cd "$(dirname "$SOURCE")" && pwd)/$(basename "$SOURCE")"
	SOURCE_KIND="wheel"
	;;
*)
	[ -f "$SOURCE/pyproject.toml" ] || usage_error "not an agentsync checkout (no pyproject.toml): $SOURCE"
	SOURCE="$(cd "$SOURCE" && pwd)"
	SOURCE_KIND="checkout"
	;;
esac
if [ -n "$LAUNCHER_SRC" ]; then
	[ -d "$LAUNCHER_SRC" ] || usage_error "--launcher is not an app bundle directory: $LAUNCHER_SRC"
	LAUNCHER_SRC="$(cd "$LAUNCHER_SRC" && pwd)"
fi
case "$CONFIG" in
/*) ;;
*) CONFIG="$PWD/$CONFIG" ;;
esac
# Each --source-local folder: absolute (a quoted "~/..." expanded), and an existing directory, checked before
# anything is installed. agentsync resolves symlinks and derives the source id.
FOLDERS=()
for f in ${SOURCE_LOCALS[@]+"${SOURCE_LOCALS[@]}"}; do
	case "$f" in
	\~) f="$HOME" ;;
	\~/*) f="$HOME/${f#\~/}" ;;
	/*) ;;
	*) f="$PWD/$f" ;;
	esac
	[ -d "$f" ] || usage_error "--source-local is not an existing folder: $f"
	FOLDERS+=("$f")
done

[ "$DRY_RUN" -eq 1 ] && say "dry run: nothing below is changed"
say "source: $SOURCE ($SOURCE_KIND)"
say "config: $CONFIG"
for f in ${FOLDERS[@]+"${FOLDERS[@]}"}; do
	say "source-local: $f"
done

# Corporate TLS inspection: let uv trust the macOS keychain (a no-op where the network is not inspected).
if [ -z "${UV_SYSTEM_CERTS:-}" ] && [ -z "${UV_NATIVE_TLS:-}" ]; then
	export UV_SYSTEM_CERTS=1
fi

# ------------------------------------------------------------------------------------------------ 1. uv
UV=""
if command -v uv >/dev/null 2>&1; then
	UV="$(command -v uv)"
elif [ -x "$HOME/.local/bin/uv" ]; then
	UV="$HOME/.local/bin/uv"
fi
if [ -n "$UV" ]; then
	say "uv: $UV (present)"
else
	say "uv: not found; installing into $HOME/.local/bin with the official installer"
	if [ "$DRY_RUN" -eq 1 ]; then
		run /usr/bin/curl -LsSf "$UV_INSTALLER_URL" -o "<tmp>/uv-install.sh"
		run env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 /bin/sh "<tmp>/uv-install.sh"
	else
		tmp="$(mktemp -d)"
		/usr/bin/curl -LsSf --retry 3 "$UV_INSTALLER_URL" -o "$tmp/uv-install.sh" ||
			fail "could not download $UV_INSTALLER_URL (proxy or TLS inspection?); install uv by hand"
		env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 /bin/sh "$tmp/uv-install.sh" </dev/null ||
			fail "the uv installer failed (network, proxy or TLS inspection?)"
		rm -rf "$tmp"
		[ -x "$HOME/.local/bin/uv" ] || fail "the uv installer did not create $HOME/.local/bin/uv"
	fi
	UV="$HOME/.local/bin/uv"
fi

# ------------------------------------------------------------------------------------------------ 2. agentsync
run "$UV" tool install --force --reinstall-package agentsync --python 3.11 "$SOURCE" </dev/null ||
	fail "uv tool install failed (network, proxy or TLS inspection? set HTTPS_PROXY / UV_SYSTEM_CERTS=1)"
BIN_DIR="$HOME/.local/bin"
if [ -x "$UV" ]; then
	BIN_DIR="$("$UV" tool dir --bin 2>/dev/null || printf '%s' "$HOME/.local/bin")"
fi
AGENTSYNC="$BIN_DIR/agentsync"
if [ "$DRY_RUN" -eq 0 ] && [ ! -x "$AGENTSYNC" ]; then
	fail "uv tool install finished but $AGENTSYNC is missing"
fi
say "agentsync: $AGENTSYNC"
# The one interpreter the launcher may start (baked into its sealed Info.plist; see launcher/build.sh).
TOOL_DIR="$HOME/.local/share/uv/tools"
if [ -x "$UV" ]; then
	TOOL_DIR="$("$UV" tool dir 2>/dev/null || printf '%s' "$TOOL_DIR")"
fi
TOOL_PY="$TOOL_DIR/agentsync/bin/python"

# ------------------------------------------------------------------------------------------------ 3. launcher
have_devtools() {
	local d
	d="$(/usr/bin/xcode-select -p 2>/dev/null || true)"
	[ -n "$d" ] && [ -d "$d" ]
}
installed_launcher_sha() {
	/usr/libexec/PlistBuddy -c "Print :AgentSyncSourceSHA256" "$APP_DEST/Contents/Info.plist" 2>/dev/null || true
}
launcher_valid() { /usr/bin/codesign --verify --strict "$1" >/dev/null 2>&1; }
cdhash_of() { /usr/bin/codesign -dv "$1" 2>&1 | sed -n 's/^CDHash=//p'; }
install_app() {
	# Replace APP_DEST with the bundle $1 (ditto keeps the signature intact).
	run mkdir -p "$HOME/Applications"
	if [ -e "$APP_DEST" ]; then
		say "launcher: replacing $APP_DEST (a new ad-hoc signature is a new TCC identity: macOS asks again)"
		run rm -rf "$APP_DEST"
	fi
	run /usr/bin/ditto "$1" "$APP_DEST"
}

LAUNCHER_STATE="missing"
if [ -n "$LAUNCHER_SRC" ]; then
	launcher_valid "$LAUNCHER_SRC" || fail "--launcher $LAUNCHER_SRC does not pass codesign --verify --strict"
	if [ -d "$APP_DEST" ] && launcher_valid "$APP_DEST" && [ "$(cdhash_of "$APP_DEST")" = "$(cdhash_of "$LAUNCHER_SRC")" ]; then
		say "launcher: $APP_DEST is up to date (same cdhash as $LAUNCHER_SRC)"
	else
		install_app "$LAUNCHER_SRC"
	fi
	LAUNCHER_STATE="installed"
elif [ "$SOURCE_KIND" = "checkout" ] && [ -x "$SOURCE/launcher/build.sh" ] && have_devtools; then
	want="$(ALLOWED_PROGRAM="$TOOL_PY" "$SOURCE/launcher/build.sh" --print-source-sha256)"
	if [ "$REBUILD" -eq 0 ] && [ -d "$APP_DEST" ] && launcher_valid "$APP_DEST" && [ "$(installed_launcher_sha)" = "$want" ]; then
		say "launcher: $APP_DEST is up to date (sources $want)"
	elif [ "$DRY_RUN" -eq 1 ]; then
		run env OUT_DIR="<tmp>" ALLOWED_PROGRAM="$TOOL_PY" "$SOURCE/launcher/build.sh"
		install_app "<tmp>/$APP_NAME"
	else
		out="$(mktemp -d)"
		OUT_DIR="$out" ALLOWED_PROGRAM="$TOOL_PY" "$SOURCE/launcher/build.sh" || fail "launcher/build.sh failed"
		install_app "$out/$APP_NAME"
		rm -rf "$out"
	fi
	LAUNCHER_STATE="installed"
else
	prebuilt=""
	if [ "$SOURCE_KIND" = "wheel" ] && [ -d "$(dirname "$SOURCE")/$APP_NAME" ]; then
		prebuilt="$(dirname "$SOURCE")/$APP_NAME"
	elif [ -d "$repo/launcher/prebuilt/$APP_NAME" ]; then
		prebuilt="$repo/launcher/prebuilt/$APP_NAME"
	fi
	if [ -n "$prebuilt" ] && launcher_valid "$prebuilt"; then
		if [ -d "$APP_DEST" ] && [ "$(cdhash_of "$APP_DEST")" = "$(cdhash_of "$prebuilt")" ]; then
			say "launcher: $APP_DEST is up to date (prebuilt $prebuilt)"
		else
			install_app "$prebuilt"
		fi
		LAUNCHER_STATE="installed"
	elif [ -d "$APP_DEST" ] && launcher_valid "$APP_DEST"; then
		say "launcher: keeping $APP_DEST (no developer tools and no prebuilt launcher to update it from)"
		LAUNCHER_STATE="installed"
	else
		warn "no launcher: no developer tools (xcode-select -p) and no prebuilt $APP_NAME; sources under" \
			"$HOME/Library/CloudStorage cannot run from launchd until one is installed"
	fi
fi
if [ "$LAUNCHER_STATE" = "installed" ] && [ "$DRY_RUN" -eq 0 ]; then
	say "launcher: $(/usr/bin/codesign -d -r- "$APP_DEST" 2>&1 | sed -n 's/^#* *designated => /designated requirement: /p')"
fi

# ------------------------------------------------------------------------------------------------ 4. config
if [ -f "$CONFIG" ]; then
	if [ "${#FOLDERS[@]}" -eq 0 ]; then
		say "config: $CONFIG exists (not touched)"
	fi
	for f in ${FOLDERS[@]+"${FOLDERS[@]}"}; do
		run "$AGENTSYNC" add-source "$f" --config "$CONFIG" </dev/null ||
			fail "agentsync add-source $f failed (see the error above)"
	done
	CONFIG_STATE="present"
else
	init_args=(init --config "$CONFIG")
	for f in ${FOLDERS[@]+"${FOLDERS[@]}"}; do
		init_args+=(--source-local "$f")
	done
	run "$AGENTSYNC" "${init_args[@]}" </dev/null || fail "agentsync init failed (see the error above)"
	CONFIG_STATE="created"
fi

# ------------------------------------------------------------------------------------------------ 5. doctor
DOCTOR_RC=0
if [ "$DRY_RUN" -eq 1 ]; then
	run "$AGENTSYNC" doctor --config "$CONFIG"
else
	"$AGENTSYNC" doctor --config "$CONFIG" </dev/null || DOCTOR_RC=$?
fi

# ------------------------------------------------------------------------------------------------ 6. agent
AGENT_RC=0
if [ "$INSTALL_AGENT" -eq 1 ]; then
	if [ "$DRY_RUN" -eq 1 ]; then
		run "$AGENTSYNC" install-agent --config "$CONFIG"
	elif [ "$DOCTOR_RC" -ne 0 ]; then
		warn "not installing the LaunchAgents: agentsync doctor reported failures"
		AGENT_RC=1
	else
		"$AGENTSYNC" install-agent --config "$CONFIG" </dev/null || AGENT_RC=$?
		if [ "$AGENT_RC" -eq 0 ]; then
			say "the first background run starts now; if macOS asks $PROMPT_TEXT, click Allow"
		fi
	fi
fi

case ":$PATH:" in
*":$BIN_DIR:"*) ;;
*) say "note: $BIN_DIR is not on PATH; add it to your shell profile to type 'agentsync'" ;;
esac

# ------------------------------------------------------------------------------------------------ next step
agent_plist="$HOME/Library/LaunchAgents/com.agentsync.poll.plist"
CONFIG_FLAG=""
if [ "$CONFIG" != "$HOME/agent-context/sources.toml" ]; then
	CONFIG_FLAG=" --config $(printf '%q' "$CONFIG")"
fi
if [ "$DRY_RUN" -eq 1 ]; then
	next="re-run without --dry-run to apply the steps above"
elif [ "$LAUNCHER_STATE" != "installed" ]; then
	next="get a signed $APP_NAME (ask IT, or install the Xcode Command Line Tools) and re-run with --launcher PATH"
elif [ "$CONFIG_STATE" = "created" ] && [ "${#FOLDERS[@]}" -eq 0 ]; then
	next="add your sources to $CONFIG, then re-run scripts/install.sh$ORIG_ARGS"
elif [ "$DOCTOR_RC" -ne 0 ]; then
	next="fix the [FAIL] lines above (each names its fix), then re-run scripts/install.sh$ORIG_ARGS"
elif [ "$AGENT_RC" -ne 0 ]; then
	next="read the install-agent error above, then re-run scripts/install.sh$ORIG_ARGS"
elif [ "$INSTALL_AGENT" -eq 0 ] && [ ! -f "$agent_plist" ] && [ "${#FOLDERS[@]}" -gt 0 ]; then
	next="check a first cycle by hand with: agentsync sync --once$CONFIG_FLAG; then re-run scripts/install.sh$BASE_ARGS --confirm-install-agent to start background sync"
elif [ "$INSTALL_AGENT" -eq 0 ] && [ ! -f "$agent_plist" ]; then
	next="re-run scripts/install.sh$BASE_ARGS --confirm-install-agent to start background sync"
else
	next="if macOS asks $PROMPT_TEXT, click Allow; then check progress with: agentsync status$CONFIG_FLAG"
fi
say "NEXT: $next"
if [ "$INSTALL_AGENT" -eq 1 ] && [ "$DRY_RUN" -eq 0 ] && [ "$AGENT_RC" -ne 0 ]; then
	exit 1 # --confirm-install-agent asked for the LaunchAgents and they were not installed
fi
