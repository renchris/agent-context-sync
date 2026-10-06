#!/bin/bash
# agentsync installer for a managed Mac: no admin rights, no interactive prompts, safe to re-run.
#
# Usage: scripts/install.sh [--source-local FOLDER ...] [--confirm-install-agent [--launcher PATH]]
#                           [--report-only] [--version] [--help]
#        scripts/install.sh --list-folders
#        scripts/install.sh --log-start AGENT | --log STEP KIND WHAT FIX
#
#   --source-local FOLDER   sync this folder (e.g. one inside ~/Library/CloudStorage/OneDrive-<Org>) as a live
#                           local source; repeatable, and already-configured folders are left as they are
#   --confirm-install-agent optional background sync, the operator's choice (a setup agent never passes it):
#                           also build the signed launcher, sync once, install and start the two LaunchAgents
#                           (agentsync install-agent) and wait for the first background run (steps 3, 6-8)
#   --launcher PATH         with --confirm-install-agent: use this prebuilt, signed AgentSyncLauncher.app
#                           instead of building one
#   --report-only           only write the setup report (step 9), then exit; installs and logs nothing, except
#                           that it closes the friction log's current attempt ("<time> | end | finished") when
#                           that attempt has no end line yet
#   --version               print the commit of this checkout ("source commit: <sha> dirty <fingerprint>" when it
#                           has local changes; see the setup log), then "setup-prompt-compat N" as the last line
#                           (the line step 1 of the setup prompt checks)
#   --list-folders          only list the folders this Mac syncs, as candidates for --source-local: every
#                           folder 1 or 2 levels inside each ~/Library/CloudStorage/<provider> (names only: no
#                           file is opened; no dot folders), sorted, one full path per line, at most 200 then
#                           "(N more)"; installs nothing and writes no report, only a list-folders step in the
#                           setup log. Exit 0 listed; 3 none (OneDrive not signed in, or nothing synced yet);
#                           4 this terminal app was denied access (macOS "Operation not permitted"), or macOS
#                           is still asking; its NEXT: line says which and names the click
#   --log-start AGENT       the setup prompt's friction log (see "Friction log" below): start an attempt
#   --log STEP KIND WHAT FIX
#                           append one event to it; KIND is question, click, approval, deviation, error or prompt
#
# The config is $AGENTSYNC_CONFIG, else ~/agent-context/sources.toml.
#
# Friction log: --log-start and --log only append to $AGENTSYNC_FRICTION_LOG (default
# ~/agent-context/setup/friction.md; the directory is made 0700 and the file 0600, under umask 077), which
# `agentsync setup-report` reads. Each must be the first argument and takes no other option; they need no uv
# and no agentsync, write no install.log line, no install.out and no report, and print one confirmation line.
#   --log-start AGENT  appends "Attempt: <UTC>", "Prompt: v<SETUP_PROMPT_COMPAT>" and "Agent: AGENT" lines
#   --log STEP KIND WHAT FIX
#                      appends "<UTC> | step STEP | KIND | WHAT | FIX" ("step 2" as STEP is read as 2; a STEP
#                      that is not a number, or "-", leaves the step column out and moves a non-number into
#                      WHAT). A KIND outside the six, or a count other than four, exits 2 and still appends
#                      an "error" line that names it and keeps what was given
# --report-only closes the attempt: it appends "<UTC> | end | finished" before writing the report, only when the
# last "Attempt:" has no such line (so running it twice adds one), and prints one confirmation line.
# Arguments are written as given (printf '%s'): nothing in them is expanded or run, so a backtick, $( ) or a
# typographic ’ is logged as text; a line break inside one becomes a space (one event per line).
#
# Steps, each skipped when already done:
#   1. uv in ~/.local/bin (the official installer, without touching shell profiles) unless one is on PATH
#   2. uv tool install agentsync from the checkout holding this script (a uv-managed Python 3.11; the system
#      trust store for TLS; UV_TOOL_BIN_DIR pinned to ~/.local/bin, so the binary sits where the guides say);
#      skipped when the last install came from this same clean checkout commit
#   3. with --confirm-install-agent only: the signed launcher at ~/Applications/AgentSyncLauncher.app, built
#      with launcher/build.sh when developer tools exist (SIGN_IDENTITY passes through for a Developer ID
#      build), or copied from --launcher PATH; else a valid installed one is kept. An up-to-date one is never
#      rebuilt, because an ad-hoc rebuild is a new TCC identity and macOS would ask again (the developer
#      variable AGENTSYNC_REBUILD_LAUNCHER=1 rebuilds it anyway)
#      In the same step, with or without that option, when developer tools exist: the on-device OCR helper
#      (python -I -m agentsync.convert.ocr with the tool's interpreter; nothing else ever compiles it). It
#      prints one "OCR helper: ..." line; [convert] ocr = false builds nothing, and a helper that does not
#      build is that line, never a failed run. Without developer tools nothing is tried, and the line says so
#   4. agentsync add-source for each --source-local folder, else the flagless agentsync init: each creates
#      whatever is missing (sources.toml, the docs repo and its scaffold, the inbox, the state dir) and is
#      idempotent; opening the manifest migrates it (an upgrade may bring a newer schema)
#   5. status: agentsync status (its TCC probe may raise the one-time "wants to access files managed by"
#      prompt). Any [FAIL] line stops steps 6-8 and the run exits 1, except the launcher's own TCC_PENDING (a
#      "tcc.<source>" line, only with --confirm-install-agent), which the wait (step 8) asks the Allow for. A
#      listing macOS holds for an Allow click in this terminal is a source.<id>.listable [FAIL]: it stops them
#   6. first-sync, whenever the config has a folder to sync (a [[source]] other than the inbox) and step 5 has
#      no [FAIL] that stops it: agentsync sync --once --materialise-budget 0 (a non-zero exit fails the run;
#      75, a cycle already running, skips): no downloads, so the files already on this Mac are converted now,
#      no online-only file is downloaded here and this step's time does not grow with the folders' size; each
#      later sync downloads and converts the online-only files it deferred, within its per-run budget. Its
#      output is shown as its "converted N, deferred M online-only" line(s), each prefixed
#      "first sync: " (and logged as the step's note converted-N-deferred-M), else as all it printed. The
#      flag is passed unconditionally: sync --help hides it, and the agentsync installed here always has it
#   With --confirm-install-agent and a first sync that ran:
#   7. agent: agentsync install-agent
#   8. wait: launchctl kickstart gui/<uid>/com.agentsync.poll, then launchctl print every 3 s for up to
#      $AGENTSYNC_WAIT_SECONDS (default 180 s, 3 minutes) until the first background run is past the macOS
#      access check or has exited 0 (the runs count before and after, never a fixed sleep). Past the check:
#      the job is running and its launcher (the job's pid) has logged, since step 7 began, a CANARY_OK line
#      for every --canary path in the job's arguments and no TCC_PENDING or TCC_DENIED line; it then prints
#      "background sync: running (...)" and leaves that run converting in the background. A job without
#      canaries waits for an exit 0. While macOS waits for Allow (TCC_PENDING in the launcher log, or exit
#      79) it prints one "ACTION:" line and starts the job again after each attempt.
#   Steps 6 and 8 and the closing status (see NEXT below) print a progress line at least every 15 s, so a
#   coding tool that stops a command which has
#   printed nothing for a while does not stop this one. A whole run with --confirm-install-agent takes the
#   first sync's time plus at most the 3-minute wait: give it a 10-minute command timeout. A stopped run
#   (SIGTERM, SIGINT, SIGHUP) still logs its end (rc 143, 130, 129), writes the report and prints NEXT:.
#   9. report, at every exit after the arguments are read (failures and usage errors too) except in a dry run
#      or a --list-folders run: agentsync setup-report --out $AGENTSYNC_SETUP_REPORT (default
#      ~/agent-context/setup-report.md); when agentsync is missing or that fails, a shell report with the
#      same headings (machine facts, install.log, this run's doctor output, friction.md; the home path,
#      login name, full name, OneDrive-<org> and the --source-local folder names redacted); when the report
#      ends with its issue link (https://github.com/renchris/agent-context-sync/issues/new?template=...), one
#      line "issue link (review the report first): <link>" follows, the last line before NEXT:
# and finally one line starting "NEXT:" with the single next step and, in brackets, the report path. A run
# that ends with a folder to sync and nothing failed ends on the loop's NEXT (KISS K02): install.sh runs
# `agentsync status` once more with its NEXT line on, prints none of its output and lifts its first "NEXT:"
# line ("run ~/.local/bin/agentsync sync and follow its NEXT line" when it prints none); with
# --confirm-install-agent the line starts "background sync: running; " (or "ok; "). A [FAIL] line in that
# status is printed and the NEXT says to fix it; a listing macOS held for an Allow click in this terminal is
# the NEXT and the run exits 1. A run without a folder over an existing config ends on that status's NEXT
# too (its "no folder is synced yet" step). Nothing
# else it prints is an instruction: agentsync is always called by its full path (~/.local/bin/agentsync), so
# nothing needs adding to PATH or to a shell profile; uv's "not on your PATH ... update-shell" hint is
# filtered out of its output; every other agentsync call gets AGENTSYNC_NO_NEXT_HINT=1 (no "next:" hints of
# its own), and with --confirm-install-agent step 5 gets AGENTSYNC_AGENT_STEP_PENDING=1 (its launchd.* lines
# then say the agent step below installs the LaunchAgents instead of naming a command).
#
# Setup prompt: SETUP_PROMPT_COMPAT (below) is the N of "setup prompt vN" in README.md ("Set up on a new Mac:
# one prompt"), whose step 1 requires `install.sh --version` to print at least that number. Bump both
# whenever the prompt starts to depend on new behaviour of this installer or of agentsync, so an older
# published checkout stops at step 1 instead of failing later (tests/test_install_oneshot.py checks they
# match).
#
# Setup log: every real run (never a dry run or --report-only) appends to $AGENTSYNC_SETUP_LOG (default
# ~/agent-context/setup/install.log, directory 0700) one "start" line (compat, install.sh commit when the source
# is a checkout: "commit=<sha>" or, with local changes, "commit=<sha>-dirty tree=<fingerprint>", the
# fingerprint being the first 12 hex digits of the SHA-256 of `git diff HEAD` in the source, which reproduces
# it; the source, launchd=simulated under the test seam, the arguments), one line per step (UTC start, step, seconds,
# exit status, done / skipped / failed: uv, agentsync, launcher, config, status, first-sync, agent, wait; or
# list-folders alone), then the report step's line, then one "end" line (exit status, total seconds, the report
# included). The report is written while a provisional end line is the log's last line, so it reads a
# finished run; that line is then replaced by the report step's line and the final end line (when another
# line followed it meanwhile, the report step's line is appended instead). `agentsync setup-report` reads it
# and redacts it. git runs read-only (GIT_OPTIONAL_LOCKS=0: not even the index's stat
# cache is rewritten). Every log write also sets the setup directory to 0700 and install.log, friction.md
# and install.out in it to 0600 (files made earlier with a looser mode included).
#
# Output copy: the same runs also copy everything they print (stdout and stderr, as the agent saw it) to
# install.out next to install.log (0600): one "# run=<id> <UTC> install.sh <arguments>" line, then the output.
# Only its last 2000 lines are kept. `agentsync setup-report` may embed its (redacted) tail.
#
# Exit status: 0 when every step ran, 2 on a usage error and when a run created the config and has no folder to
# sync (its NEXT names --list-folders; a run over an existing config exits 0), 1 when a step failed (a status
# [FAIL] that stops step 6, a listing held for an Allow click, a network/proxy/TLS failure
# of uv, a failed first sync, a background run that exited 80, and --confirm-install-agent that did not
# install the LaunchAgents), 3 when the wait ran out before a background run exited 0 (exit 79 then: macOS
# still waits for Allow), 143 / 130 / 129 when stopped by a signal; --list-folders: see above. Every exit
# after argument parsing ends with one "NEXT:" line; for a background exit 80 (an earlier "Don't Allow") or
# a 79 at the timeout it names the click: turn on agentsync-launcher in System Settings > Privacy & Security
# > Files and Folders. agentsync exit codes named in messages: 0 ok, 75 lock busy, 77 sign-in required, 78
# configuration invalid, 79 TCC pending (macOS waits for Allow), 80 TCC denied. --log-start and --log:
# 0 logged, 2 a usage error (a bad KIND or argument count: see "Friction log"), 1 the friction log
# could not be written; neither prints a NEXT: line.
#
# Dry run: AGENTSYNC_INSTALL_DRY_RUN=1 prints every step that would change something and changes nothing (no
# log, no install.out, no report, no friction-log line); its NEXT: line says to re-run without it. With
# --log-start or --log it prints one "dry run:" line instead, and exits 0.
#
# Test-only seams, never for a real Mac: AGENTSYNC_SIMULATE_LAUNCHD=1 replaces install-agent, the kickstart
# and the wait with "SIMULATED" lines (no plist, no launchctl write; the wait succeeds at once; the log and
# the report record "launchd: simulated"), for sandboxed runs of the setup prompt. AGENTSYNC_LAUNCHCTL (the
# launchctl to run, default /bin/launchctl), AGENTSYNC_WAIT_POLL_SECONDS (default 3),
# AGENTSYNC_PROGRESS_SECONDS (default 15), AGENTSYNC_LIST_TIMEOUT (default 90: seconds --list-folders waits
# on one provider folder while macOS asks) and AGENTSYNC_LIST_TOTAL_SECONDS (default 100: its cap across all
# providers, under a coding tool's 2-minute default command timeout) are for the stubbed tests.
SETUP_PROMPT_COMPAT=7 # the README prompt's "setup prompt vN": bump both together (see the header)
set -euo pipefail

# ------------------------------------------------------------------------------------------------ friction log
# --log-start / --log (see "Friction log" in the header), handled before anything else: no uv, no
# git, no install.log, no install.out, no report. Every argument is written with printf '%s', never evaluated.
# The setup prompt's friction log (the path `agentsync setup-report` reads too).
friction_file() {
	local f="${AGENTSYNC_FRICTION_LOG:-$HOME/agent-context/setup/friction.md}"
	case "$f" in
	\~/*) f="$HOME/${f#\~/}" ;;
	esac
	printf '%s' "$f"
}
FRICTION_KINDS="question, click, approval, deviation, error or prompt"
# Append TEXT (whole lines) to the friction log: its directory 0700 when this creates it (or it is the default
# ~/agent-context/setup), the file 0600; a file that does not end in a newline gets one first.
friction_append() {
	local f d
	f="$(friction_file)"
	d="${f%/*}"
	[ "$d" != "$f" ] || d="."
	umask 077
	if [ ! -d "$d" ]; then
		mkdir -p "$d" || return 1
	elif [ "$d" = "$HOME/agent-context/setup" ] && [ ! -L "$d" ] && [ -O "$d" ]; then
		chmod 700 "$d" 2>/dev/null || true
	fi
	if [ -s "$f" ] && [ -n "$(tail -c 1 "$f" 2>/dev/null)" ]; then
		printf '\n' >>"$f" || return 1
	fi
	printf '%s' "$1" >>"$f" || return 1
	if [ -f "$f" ] && [ ! -L "$f" ] && [ -O "$f" ]; then chmod 600 "$f" 2>/dev/null || true; fi
}
friction_cmd() { # OPTION ARGS...: the friction-log options; their exit status
	local op="$1" now line step kind what fix note="" v a rc=0
	shift
	now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	case "$op" in
	--log-start)
		if [ $# -ne 1 ]; then
			printf 'usage error: --log-start takes one argument, your tool and model id, and no other option (see --help)\n' >&2
			return 2
		fi
		v="${1//$'\r'/ }"
		v="${v//$'\n'/ }"
		friction_append "Attempt: $now"$'\n'"Prompt: v$SETUP_PROMPT_COMPAT"$'\n'"Agent: ${v:-unknown}"$'\n' || rc=1
		[ "$rc" -ne 0 ] || printf 'friction log: attempt started in %s\n' "$(friction_file)"
		;;
	--log)
		if [ $# -ne 4 ]; then
			v=""
			for a in "$@"; do v="${v:+$v / }$a"; done
			v="${v//$'\r'/ }"
			v="${v//$'\n'/ }"
			friction_append "$now | error | install.sh --log got $# argument(s), not 4 (STEP KIND WHAT FIX, each in single quotes): ${v:--} | -"$'\n' || true
			printf 'usage error: --log takes four arguments, STEP KIND WHAT FIX, each in single quotes (see --help); logged as an error line\n' >&2
			return 2
		fi
		step="${1//$'\r'/ }"
		step="${step//$'\n'/ }"
		kind="${2//$'\r'/ }"
		kind="${kind//$'\n'/ }"
		what="${3//$'\r'/ }"
		what="${what//$'\n'/ }"
		fix="${4//$'\r'/ }"
		fix="${fix//$'\n'/ }"
		case "$step" in
		[Ss]tep\ *) step="${step#[Ss]tep }" ;;
		esac
		case "$step" in
		'' | *[!0-9]*)
			[ "$step" = "-" ] || [ -z "$step" ] || what="(step $step) $what"
			step=""
			;;
		esac
		line="$now |${step:+ step $step |}"
		case "$kind" in
		question | click | approval | deviation | error | prompt)
			line="$line $kind | $what | $fix"
			;;
		*)
			line="$line error | install.sh --log: unknown kind '$kind' (not $FRICTION_KINDS); the event was: $what | $fix"
			note="usage error: --log kind '$kind' is not $FRICTION_KINDS; logged as an error line"
			rc=2
			;;
		esac
		if ! friction_append "$line"$'\n'; then
			rc=1
		elif [ "$rc" -eq 0 ]; then
			printf 'friction log: %s logged in %s\n' "$kind" "$(friction_file)"
		fi
		[ -z "$note" ] || printf '%s (see --help)\n' "$note" >&2
		;;
	--log-end) # hidden, left out of --help: step 3 of a saved v6 prompt runs "--log-end && --report-only"
		if [ $# -ne 0 ]; then
			printf 'usage error: --log-end takes no argument and no other option (see --help)\n' >&2
			return 2
		fi
		friction_close_attempt # the same idempotent close as --report-only, so the pair adds one end line
		;;
	esac
	[ "$rc" -ne 1 ] || printf 'error: could not write the friction log %s\n' "$(friction_file)" >&2
	return "$rc"
}
# --report-only: close the friction log's current attempt with "<UTC> | end | finished", only when its last
# "Attempt:" has no end line yet (so a second --report-only adds none). No log or no attempt: nothing to close.
friction_close_attempt() {
	local f
	f="$(friction_file)"
	[ -f "$f" ] || return 0
	awk '/^Attempt:/ { open = 1; ended = 0; next } /\| end \| finished[[:space:]]*$/ { ended = 1 }
		END { exit !(open && !ended) }' "$f" || return 0
	if friction_append "$(date -u +%Y-%m-%dT%H:%M:%SZ) | end | finished"$'\n'; then
		printf 'friction log: attempt finished in %s\n' "$f"
	else
		printf 'warning: could not write the friction log %s\n' "$f" >&2
	fi
}
case "${1:-}" in
--log-start | --log | --log-end)
	if [ "${AGENTSYNC_INSTALL_DRY_RUN:-}" = "1" ]; then # the dry run changes nothing, the friction log included
		printf 'dry run: %s would write to the friction log %s; nothing is written\n' "$1" "$(friction_file)"
		exit 0
	fi
	rc=0
	friction_cmd "$@" || rc=$?
	exit "$rc"
	;;
esac

ORIG_ARGS="" # every flag given, for a re-run after a failure
BASE_ARGS="" # the same without --source-local FOLDER, for a re-run once the folders are in the config
ALL_ARGS=""  # every argument as given, for install.out
skip_next=0
for a in "$@"; do
	ALL_ARGS="$ALL_ARGS $(printf '%q' "$a")"
	if [ "$skip_next" -eq 1 ]; then
		skip_next=0
		ORIG_ARGS="$ORIG_ARGS $(printf '%q' "$a")"
		continue
	fi
	case "$a" in
	--report-only | --no-report | --list-folders | --version | -h | --help) ;; # NEXT is for the real run
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
SELF="$(printf '%q' "$here/$(basename "${BASH_SOURCE[0]}")")" # this script, absolute, for re-run commands

DRY_RUN=0
[ "${AGENTSYNC_INSTALL_DRY_RUN:-}" != "1" ] || DRY_RUN=1 # the dry run (see the header)
INSTALL_AGENT=0
REBUILD=0
[ "${AGENTSYNC_REBUILD_LAUNCHER:-}" != "1" ] || REBUILD=1 # developer only: rebuild an up-to-date launcher (step 3)
REPORT=1
REPORT_ONLY=0
LIST_FOLDERS=0
LAUNCHER_SRC=""
SOURCE=""
SOURCE_KIND="-"
SOURCE_LOCALS=()
FOLDERS=()
CONFIG="${AGENTSYNC_CONFIG:-$HOME/agent-context/sources.toml}"
APP_NAME="AgentSyncLauncher.app"
APP_DEST="$HOME/Applications/$APP_NAME"
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"
PROMPT_TEXT='“agentsync-launcher” wants to access files managed by “<your sync app, e.g. OneDrive>”'
REPORT_PATH="${AGENTSYNC_SETUP_REPORT:-$HOME/agent-context/setup-report.md}"
SIMULATE=0
[ "${AGENTSYNC_SIMULATE_LAUNCHD:-}" != "1" ] || SIMULATE=1
LAUNCHCTL="${AGENTSYNC_LAUNCHCTL:-/bin/launchctl}"
WAIT_SECONDS="${AGENTSYNC_WAIT_SECONDS:-180}"
WAIT_POLL="${AGENTSYNC_WAIT_POLL_SECONDS:-3}"
PROGRESS_SECONDS="${AGENTSYNC_PROGRESS_SECONDS:-15}" # a progress line at least this often in steps 6 and 8
LIST_TIMEOUT="${AGENTSYNC_LIST_TIMEOUT:-90}"
LIST_TOTAL="${AGENTSYNC_LIST_TOTAL_SECONDS:-100}"
POLL_LABEL="com.agentsync.poll"
ISSUE_URL="https://github.com/renchris/agent-context-sync/issues/new?template=setup-report.yml" # setup_report.ISSUE_URL
ISSUE_LINK_PREFIX="issue link (review the report first): "
OUT_MAX_LINES=2000 # install.out keeps this many lines
TEE_PIDS=""        # the two tee processes copying this run's output to install.out
# agentsync prints no "next:" hints of its own under this installer: its NEXT line is the only instruction.
export AGENTSYNC_NO_NEXT_HINT=1
# uv installs the agentsync binary into ~/.local/bin, the path every guide and NEXT line names (KISS K02).
export UV_TOOL_BIN_DIR="$HOME/.local/bin"
AGENTSYNC=""
COMMIT="-"
DIRTY="" # the SOURCE checkout's local-change fingerprint (tree_fingerprint), empty when clean or unknown
PARSED=0       # 1 once the arguments are read: from then on every exit writes the report and a NEXT line
NEXT_MSG=""    # the NEXT line the EXIT trap prints
RERUN=""       # the command a stopped run names (default: this script with the same arguments)
LAST_ERROR=""  # the last error message, for the shell report
DOCTOR_LOG=""  # this run's doctor output, for the shell report
SYNC_OUT=""    # the first sync's stdout
LOOP_OUT=""    # the closing status's output (the loop's NEXT)
END_LINE=""    # the provisional end line in install.log (set at the exit, before the report)
REPORT_LINE="" # the report step's line, written before the end line (finish_setup_log)
ACTION_SHOWN=0 # the ACTION line is printed once

say() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
fail() {
	local rc=$? # the failed command's status (every caller is `cmd || fail ...`)
	[ "$rc" -ne 0 ] || rc=1
	step_end failed "$rc"
	printf 'error: %s\n' "$*" >&2
	LAST_ERROR="$*"
	NEXT_MSG="fix the error above, then re-run: $SELF$ORIG_ARGS"
	exit 1
}
usage_error() {
	printf 'usage error: %s (see --help)\n' "$*" >&2
	LAST_ERROR="usage error: $*"
	if [ "$PARSED" -eq 1 ]; then
		log_start
		NEXT_MSG="fix the usage error above (see $SELF --help), then run the corrected command"
	fi
	exit 2
}
show_help() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }
have_devtools() {
	local d
	d="$(/usr/bin/xcode-select -p 2>/dev/null || true)"
	[ -n "$d" ] && [ -d "$d" ]
}
# git, read-only: GIT_OPTIONAL_LOCKS=0 keeps even `git diff` from rewriting the index's stat cache.
git_ro() { GIT_OPTIONAL_LOCKS=0 /usr/bin/git "$@"; }
# The first 12 hex digits of the SHA-256 of `git diff HEAD` in the checkout $1: which local changes ran.
tree_fingerprint() {
	git_ro -C "$1" diff --no-ext-diff --no-color HEAD -- 2>/dev/null | /usr/bin/shasum -a 256 | cut -c1-12
}
# "<sha12>" of the checkout $1, "<sha12> dirty <fingerprint>" with local changes; empty when git cannot say.
checkout_commit() {
	local c
	have_devtools || return 0
	c="$(git_ro -C "$1" rev-parse --short=12 HEAD 2>/dev/null || true)"
	[ -n "$c" ] || return 0
	if git_ro -C "$1" diff --quiet HEAD -- 2>/dev/null; then
		printf '%s' "$c"
	else
		printf '%s dirty %s' "$c" "$(tree_fingerprint "$1")"
	fi
}
# The commit of the checkout at $1 without running git (no developer tools: /usr/bin/git would offer to
# install them); empty when it cannot be read.
git_head_file() {
	local head ref
	head="$(cat "$1/.git/HEAD" 2>/dev/null || true)"
	case "$head" in
	"ref: "*)
		ref="${head#ref: }"
		if [ -f "$1/.git/$ref" ]; then
			head="$(cat "$1/.git/$ref")"
		else
			head="$(awk -v r="$ref" '$2 == r { print $1; exit }' "$1/.git/packed-refs" 2>/dev/null || true)"
		fi
		;;
	esac
	case "$head" in
	[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) printf '%s' "$head" | cut -c1-12 ;;
	esac
}
show_version() { # the compat line last: step 1 of the setup prompt reads the last line of its output
	local c
	if [ -e "$repo/.git" ]; then
		c="$(checkout_commit "$repo")"
		[ -n "$c" ] || c="$(git_head_file "$repo")"
		case "$c" in
		*" dirty "*) say "source commit: $c (local changes in this checkout; setup prompt step 1 keeps them on a local branch before it updates)" ;;
		*) say "source commit: ${c:-unknown}" ;;
		esac
	fi
	say "setup-prompt-compat $SETUP_PROMPT_COMPAT"
}
# What an agentsync (or launchd) exit code means, for the messages.
rc_meaning() {
	case "$1" in
	0) printf 'ok' ;;
	1) printf 'failed' ;;
	2) printf 'usage error' ;;
	3) printf 'the wait for the first background run ran out' ;;
	75) printf 'lock busy: another agentsync cycle is running' ;;
	77) printf 'sign-in required' ;;
	78) printf 'configuration invalid' ;;
	79) printf 'TCC pending: macOS is waiting for Allow' ;;
	80) printf "TCC denied: an earlier \"Don't Allow\"" ;;
	129) printf 'stopped by SIGHUP' ;;
	130) printf 'stopped by SIGINT' ;;
	143) printf 'stopped by SIGTERM (a tool timeout?)' ;;
	*) printf 'exit %s' "$1" ;;
	esac
}
# Run a command for at most $1 seconds (bash 3.2 and macOS have no timeout(1)); its status, 143 if killed.
with_timeout() {
	local t="$1" pid wd rc=0
	shift
	"$@" &
	pid=$!
	(
		sleep "$t"
		kill "$pid"
	) >/dev/null 2>&1 &
	wd=$!
	wait "$pid" || rc=$?
	kill "$wd" 2>/dev/null || true
	wait "$wd" 2>/dev/null || true
	return "$rc"
}

# Run a command, or only print it in a dry run.
run() {
	if [ "$DRY_RUN" -eq 1 ]; then
		printf '[dry-run]'
		printf ' %q' "$@"
		printf '\n'
		return 0
	fi
	"$@"
}

# Setup log (see the header): appended to, never printed; a log that cannot be written is ignored.
SETUP_LOG="${AGENTSYNC_SETUP_LOG:-$HOME/agent-context/setup/install.log}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
STEP=""
STEP_T0=0
STEP_AT=""
STARTED=0
utc_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
SETUP_DIR="$(dirname "$SETUP_LOG")"
INSTALL_OUT="$SETUP_DIR/install.out"
# The setup directory 0700 and its install.log, friction.md and install.out 0600, also when an earlier run or
# the agent made them looser; only what this user owns (never $HOME itself, never through a symlink).
tighten_setup_modes() {
	local f
	if [ -d "$SETUP_DIR" ] && [ ! -L "$SETUP_DIR" ] && [ -O "$SETUP_DIR" ] && [ "$SETUP_DIR" != "$HOME" ]; then
		chmod 700 "$SETUP_DIR"
	fi
	for f in "$SETUP_LOG" "$(friction_file)" "$INSTALL_OUT"; do
		if [ -f "$f" ] && [ ! -L "$f" ] && [ -O "$f" ]; then chmod 600 "$f"; fi
	done
}
log_at() { # STAMP TEXT...: one line "<stamp> run=<id> <text>"; nothing in a dry run or under --report-only
	local stamp="$1"
	shift
	[ "$DRY_RUN" -eq 0 ] && [ "$REPORT_ONLY" -eq 0 ] || return 0
	(
		umask 077 # a created log directory (and ~/agent-context) is 0700, the log 0600
		mkdir -p "$SETUP_DIR" && printf '%s run=%s %s\n' "$stamp" "$RUN_ID" "$*" >>"$SETUP_LOG"
		tighten_setup_modes
	) 2>/dev/null || true
}
log_start() { # the run's "start" line, once
	local sim=""
	[ "$STARTED" -eq 0 ] || return 0
	STARTED=1
	[ "$SIMULATE" -eq 0 ] || sim=" launchd=simulated"
	log_at "$(utc_now)" "start install.sh compat=$SETUP_PROMPT_COMPAT commit=$COMMIT${DIRTY:+ tree=$DIRTY}" \
		"kind=$SOURCE_KIND" \
		"source=$(printf '%q' "${SOURCE:--}")$sim args=${ORIG_ARGS# }"
}
# install.out (see the header): keep its last $OUT_MAX_LINES lines. Only while no tee writes to it (a tee holds
# the old file open, so its later lines would go to a replaced file).
trim_install_out() {
	[ -f "$INSTALL_OUT" ] && [ ! -L "$INSTALL_OUT" ] || return 0
	[ "$(wc -l <"$INSTALL_OUT" | tr -d ' ')" -gt "$OUT_MAX_LINES" ] || return 0
	(
		umask 077
		tail -n "$OUT_MAX_LINES" "$INSTALL_OUT" >"$INSTALL_OUT.$$.tmp" && mv -f "$INSTALL_OUT.$$.tmp" "$INSTALL_OUT"
	) 2>/dev/null || rm -f "$INSTALL_OUT.$$.tmp"
}
# Copy stdout and stderr (each still to where it went) to install.out, from here to the exit. The tees
# ignore INT, TERM and HUP: a tool that stops the whole process group still gets the end of the run (its
# report and NEXT line), and they end when the run closes its output (stop_capture).
start_capture() {
	[ "$DRY_RUN" -eq 0 ] && [ "$REPORT_ONLY" -eq 0 ] || return 0
	(
		umask 077
		mkdir -p "$SETUP_DIR" && touch "$INSTALL_OUT" && tighten_setup_modes
	) 2>/dev/null || return 0
	[ -f "$INSTALL_OUT" ] && [ ! -L "$INSTALL_OUT" ] && [ -w "$INSTALL_OUT" ] || return 0
	trim_install_out
	printf '# run=%s %s install.sh%s\n' "$RUN_ID" "$(utc_now)" "$ALL_ARGS" >>"$INSTALL_OUT" 2>/dev/null || return 0
	exec > >(
		trap '' INT TERM HUP
		exec tee -a "$INSTALL_OUT"
	)
	TEE_PIDS="$!"
	exec 2> >(
		trap '' INT TERM HUP
		exec tee -a "$INSTALL_OUT" >&2
	)
	TEE_PIDS="$TEE_PIDS $!"
}
# At the exit: close this run's output, give the tees up to 3 s to copy the rest (a process this run started
# and left running, such as a stopped first sync, may hold the output open longer), then trim install.out.
stop_capture() {
	local p n=0 alive
	[ -n "$TEE_PIDS" ] || return 0
	exec >/dev/null 2>&1
	while [ "$n" -lt 30 ]; do
		alive=0
		for p in $TEE_PIDS; do
			! kill -0 "$p" 2>/dev/null || alive=1
		done
		[ "$alive" -eq 1 ] || break
		sleep 0.1
		n=$((n + 1))
	done
	[ "$alive" -eq 1 ] || trim_install_out
}
step_start() {
	STEP="$1"
	STEP_T0=$SECONDS
	STEP_AT="$(utc_now)"
}
step_end() { # RESULT [RC] [NOTE]: close the open step (done | skipped | failed)
	local text
	[ -n "$STEP" ] || return 0
	text="step=$STEP seconds=$((SECONDS - STEP_T0)) rc=${2:-0} result=$1${3:+ note=$3}"
	if [ "$STEP" = "report" ] && [ -n "$END_LINE" ]; then
		REPORT_LINE="$STEP_AT run=$RUN_ID $text" # finish_setup_log puts it before the end line
	else
		log_at "$STEP_AT" "$text"
	fi
	STEP=""
}
# After the report (see "Setup log" in the header): replace the provisional end line, when it is still the
# log's last line, with the report step's line and the final end line; else append the report step's line.
finish_setup_log() { # RC
	[ -n "$REPORT_LINE" ] && [ "$DRY_RUN" -eq 0 ] && [ "$REPORT_ONLY" -eq 0 ] || return 0
	(
		umask 077
		if [ -f "$SETUP_LOG" ] && [ ! -L "$SETUP_LOG" ] && [ "$(tail -n 1 "$SETUP_LOG")" = "$END_LINE" ]; then
			{
				sed '$d' "$SETUP_LOG"
				printf '%s\n' "$REPORT_LINE" "$(utc_now) run=$RUN_ID end rc=$1 seconds=$SECONDS"
			} >"$SETUP_LOG.$$.tmp" && mv -f "$SETUP_LOG.$$.tmp" "$SETUP_LOG"
		else
			printf '%s\n' "$REPORT_LINE" >>"$SETUP_LOG"
		fi
		tighten_setup_modes
	) 2>/dev/null || rm -f "$SETUP_LOG.$$.tmp"
	REPORT_LINE=""
}

# ------------------------------------------------------------------------------------------------ report
# The agentsync that can write the report: this run's, else one a previous install left.
report_agentsync() {
	local d
	if [ -n "$AGENTSYNC" ] && [ -x "$AGENTSYNC" ]; then
		printf '%s' "$AGENTSYNC"
		return 0
	fi
	for d in "${UV_TOOL_BIN_DIR:-}" "${XDG_BIN_HOME:-}" "$HOME/.local/bin"; do
		if [ -n "$d" ] && [ -x "$d/agentsync" ]; then
			printf '%s' "$d/agentsync"
			return 0
		fi
	done
}
# "value<TAB>placeholder<TAB>w" lines (w: whole words only) for redact_stream; the %q form too, since
# install.log quotes paths that way.
redaction_pairs() {
	local cs="$HOME/Library/CloudStorage" n=0 e org f rel part name tok user
	add_pair() {
		[ "${#1}" -ge 3 ] || return 0
		printf '%s\t%s\t%s\n' "$1" "$2" "${3:-}"
		local q
		q="$(printf '%q' "$1")"
		[ "$q" = "$1" ] || printf '%s\t%s\t%s\n' "$q" "$2" "${3:-}"
	}
	if [ -n "${TMPDIR:-}" ] && [ -d "$TMPDIR" ]; then # this account's per-user temp folder links reports
		add_pair "$(cd "$TMPDIR" && pwd -P)" "<tmp>"
		add_pair "${TMPDIR%/}" "<tmp>"
	fi
	add_pair "$HOME" "~"
	if [ -d "$cs" ]; then
		for e in "$cs"/*; do
			[ -e "$e" ] || continue
			e="$(basename "$e")"
			case "$e" in
			OneDrive-Personal | OneDrive-Personal\ *) continue ;;
			OneDrive-*) org="${e#OneDrive-}" ;;
			SharedLibraries-*) org="${e#SharedLibraries-}" ;;
			*) continue ;;
			esac
			n=$((n + 1))
			add_pair "$org" "<org-$n>"
		done
	fi
	n=0
	for f in ${FOLDERS[@]+"${FOLDERS[@]}"}; do
		case "$f" in
		"$cs"/*/*) rel="${f#"$cs"/*/}" ;;
		*) continue ;;
		esac
		while [ -n "$rel" ]; do
			part="${rel%%/*}"
			[ "$part" = "$rel" ] && rel="" || rel="${rel#*/}"
			n=$((n + 1))
			add_pair "$part" "<folder-$n>"
		done
	done
	name="$(id -F 2>/dev/null || true)"
	if [ -n "$name" ]; then
		add_pair "$name" "<name>"
		for tok in $name; do
			add_pair "$tok" "<name>" w
		done
	fi
	user="$(id -un 2>/dev/null || true)"
	[ -z "$user" ] || add_pair "$user" "<user>" w
}
redact_stream() {
	REDACT_PAIRS="$(redaction_pairs)" awk '
	function lit(s, v, p, word,    out, i, n, pre, post) {
		out = ""
		n = length(v)
		while ((i = index(s, v)) > 0) {
			pre = (i > 1) ? substr(s, i - 1, 1) : substr(out, length(out), 1)
			post = substr(s, i + n, 1)
			if (word && (pre ~ /[A-Za-z0-9_]/ || post ~ /[A-Za-z0-9_]/)) {
				out = out substr(s, 1, i + n - 1)
			} else {
				out = out substr(s, 1, i - 1) p
			}
			s = substr(s, i + n)
		}
		return out s
	}
	BEGIN { np = split(ENVIRON["REDACT_PAIRS"], rows, "\n") }
	{
		line = $0
		for (k = 1; k <= np; k++) {
			if (split(rows[k], f, "\t") < 2) continue
			line = lit(line, f[1], f[2], f[3] == "w")
		}
		print line
	}'
}
launchd_field() { # TEXT KEY: the value of a top-level "KEY = value" line of `launchctl print`
	printf '%s\n' "$1" | sed -n "s/^	$2 = //p" | head -1
}
launchd_print() { with_timeout 10 "$LAUNCHCTL" print "gui/$(id -u)/$POLL_LABEL" 2>/dev/null; }
leading_int() { # "79: ..." -> 79; "(never exited)" -> empty
	case "$1" in
	[0-9]*) printf '%s' "${1%%[!0-9]*}" ;;
	esac
}
# The shell report's body (redact_stream redacts it): the same headings as `agentsync setup-report`.
fallback_body() {
	local rc="$1" why="$2" friction="$3" cs="$HOME/Library/CloudStorage" p v k n
	say "# agentsync setup report"
	say ""
	say "## Summary"
	say ""
	say "- written by: scripts/install.sh shell fallback ($why), $(utc_now)"
	say "- install.sh: setup-prompt-compat $SETUP_PROMPT_COMPAT, source commit $COMMIT${DIRTY:+ tree=$DIRTY}, exit $rc ($(rc_meaning "$rc"))"
	if [ "$SIMULATE" -eq 1 ]; then say "- launchd: simulated"; else say "- launchd: real"; fi
	[ -z "$LAST_ERROR" ] || say "- last error: $LAST_ERROR"
	say ""
	say "## Agent friction log"
	say ""
	if [ -n "$friction" ]; then
		printf '%s\n' "$friction"
	else
		say "No friction log at $(friction_file)."
	fi
	say ""
	say "## Environment"
	say ""
	say "- macOS: $(sw_vers -productVersion 2>/dev/null || echo '?') ($(sw_vers -buildVersion 2>/dev/null || echo '?'))"
	say "- arch: $(uname -m)"
	v="$(with_timeout 5 /usr/bin/profiles status -type enrollment 2>&1 </dev/null | tr '\n' ';' | sed 's/;$//; s/;/; /g' || true)"
	say "- MDM enrollment (profiles status -type enrollment): ${v:-unknown}"
	v="$(/usr/bin/xcode-select -p 2>/dev/null || true)"
	say "- developer tools (xcode-select -p): ${v:-none}"
	v="$(command -v uv 2>/dev/null || true)"
	[ -n "$v" ] || { [ ! -x "$HOME/.local/bin/uv" ] || v="$HOME/.local/bin/uv"; }
	say "- uv: ${v:-not found}"
	if have_devtools; then
		v="$(command -v python3 2>/dev/null || true)"
		say "- python3: ${v:-not found}"
	else
		say "- python3: only the /usr/bin stub (no developer tools; not run)"
	fi
	[ ! -d "$HOME/.local/share/uv/python" ] || say "- uv-managed Pythons: $(find "$HOME/.local/share/uv/python" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
	v="$(report_agentsync)"
	say "- agentsync: ${v:-not installed}"
	v=""
	for k in HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy NO_PROXY no_proxy; do
		eval "p=\${$k:-}"
		[ -z "$p" ] || v="$v $k"
	done
	if [ -n "$v" ]; then say "- proxy environment: yes (set:$v)"; else say "- proxy environment: no"; fi
	n=0
	k=0
	if [ -d "$cs" ]; then
		n="$(find "$cs" -mindepth 1 -maxdepth 1 ! -name '.*' 2>/dev/null | wc -l | tr -d ' ')"
		k="$(find "$cs" -mindepth 1 -maxdepth 1 -name 'OneDrive-*' 2>/dev/null | wc -l | tr -d ' ')"
	fi
	say "- ~/Library/CloudStorage providers: $n (OneDrive: $k)"
	say ""
	say "## Installer"
	say ""
	if [ -f "$SETUP_LOG" ]; then
		say "The last 60 lines of $SETUP_LOG:"
		say ""
		say "~~~"
		tail -60 "$SETUP_LOG"
		say "~~~"
	else
		say "No install log at $SETUP_LOG."
	fi
	say ""
	say "## Configuration"
	say ""
	if [ -f "$CONFIG" ]; then
		say "- config: $CONFIG (exists)"
		say "- [[source]] tables: $(grep -c '^[[:space:]]*\[\[source\]\]' "$CONFIG" 2>/dev/null || true)"
	else
		say "- config: $CONFIG (missing)"
	fi
	say ""
	say "## Doctor"
	say ""
	if [ -n "$DOCTOR_LOG" ] && [ -s "$DOCTOR_LOG" ]; then
		say "This run's agentsync doctor output:"
		say ""
		say "~~~"
		cat "$DOCTOR_LOG"
		say "~~~"
	else
		say "Not run ($why)."
	fi
	say ""
	say "## Status"
	say ""
	say "Not run ($why)."
	say ""
	say "## Background runs"
	say ""
	if [ "$SIMULATE" -eq 1 ]; then
		say "- launchd: simulated (AGENTSYNC_SIMULATE_LAUNCHD=1, a test seam: nothing was installed or started)"
	else
		if [ -f "$HOME/Library/LaunchAgents/$POLL_LABEL.plist" ]; then v="present"; else v="absent"; fi
		say "- $POLL_LABEL plist: $v"
		p="$(launchd_print || true)"
		if [ -n "$p" ]; then
			v="$(launchd_field "$p" "last exit code")"
			k="$(leading_int "$v")"
			say "- loaded: state $(launchd_field "$p" state), runs $(launchd_field "$p" runs), last exit code ${v:-?}${k:+ ($(rc_meaning "$k"))}"
		else
			say "- loaded: no"
		fi
	fi
	say ""
	say "## Recent errors"
	say ""
	[ -z "$LAST_ERROR" ] || say "- $LAST_ERROR"
	if [ -f "$SETUP_LOG" ] && grep -q 'result=failed' "$SETUP_LOG" 2>/dev/null; then
		say "- failed steps in the install log:"
		say ""
		say "~~~"
		grep 'result=failed' "$SETUP_LOG" | tail -10
		say "~~~"
	elif [ -z "$LAST_ERROR" ]; then
		say "None recorded."
	fi
	say ""
	say "## Redaction"
	say ""
	say "Shell fallback redaction: the home path (~), the login name (<user>), the full name (<name>), the"
	say "organisation after OneDrive- (<org-N>) and the --source-local folder names (<folder-N>). Read the report"
	say "before sending it: anything else confidential is yours to remove."
}
# The friction log for the shell report: friction.md, else what an earlier report holds under its heading.
friction_text() {
	local f
	f="$(friction_file)"
	if [ -f "$f" ]; then
		head -c 262144 "$f"
	elif [ -f "$REPORT_PATH" ]; then
		awk '/^## / { found = ($0 == "## Agent friction log"); next } found { print }' "$REPORT_PATH" |
			grep -v '^<!-- agent:' | grep -v '^No friction log at ' | sed '/./,$!d'
	fi
}
# An exclude line (the loop's WAITING ON YOU text for an empty cloud folder, which an agent may log) names
# folders below a source root, which redact_stream has never seen: the list is shown as <path>, as
# `agentsync setup-report` shows it. The second expression is a line cut inside the list.
scrub_exclude_lists() {
	sed -e 's/exclude = \[.*\] in \[\[source\]\]/exclude = [<path>] in [[source]]/' \
		-e 's/exclude = \[".*$/exclude = [<path>]/'
}
write_fallback_report() { # RC WHY
	local dir tmp friction
	dir="$(dirname "$REPORT_PATH")"
	(
		umask 077
		mkdir -p "$dir"
	) 2>/dev/null || return 1
	tmp="$dir/.setup-report.$$.tmp"
	friction="$(friction_text 2>/dev/null || true)"
	(
		umask 077
		{
			fallback_body "$1" "$2" "$friction" | redact_stream | scrub_exclude_lists
			printf '\n%s\n' "$ISSUE_URL" # the issue form, after redaction (a login may match the URL's owner)
		} >"$tmp"
	) 2>/dev/null || {
		rm -f "$tmp"
		return 1
	}
	mv -f "$tmp" "$REPORT_PATH"
}
# The one file the person copies back (field report 2026-10-05): the redacted setup report, then the fix request
# and the local work step 1 of the prompt kept, which are NOT redacted, so the person reviews it first.
write_bring_back() {
	local out="${REPORT_PATH%/*}/bring-back.md" tmp p fence="~~~~~~~~~~"
	tmp="$(mktemp "${REPORT_PATH%/*}/.bring-back.XXXXXX" 2>/dev/null)" || return 1
	{
		printf '# agentsync: the one file to bring back\n\n'
		printf 'Review before sending: section 1 is redacted; sections 2 and 3 are not.\n\n'
		printf '## 1. Setup report\n\n'
		cat "$REPORT_PATH"
		printf '\n## 2. Fix request (%s)\n\n' "$SETUP_DIR/fix-request.md"
		if [ -s "$SETUP_DIR/fix-request.md" ]; then cat "$SETUP_DIR/fix-request.md"; else printf 'none\n'; fi
		printf '\n## 3. Local work kept by setup prompt step 1 (%s)\n\n' "$SETUP_DIR/local-work"
		set -- "$SETUP_DIR"/local-work/*.patch
		if [ -e "$1" ]; then
			printf '%sdiff\n' "$fence"
			for p in "$@"; do cat "$p"; done
			printf '%s\n' "$fence"
		else
			printf 'none\n'
		fi
	} >"$tmp" 2>/dev/null || {
		rm -f "$tmp"
		return 1
	}
	chmod 600 "$tmp" && mv -f "$tmp" "$out" && say "bring back: $out (one file: report, fix request, local work)"
}
write_report() { # RC: write the setup report at $REPORT_PATH; 0 when written, 2 in a dry run
	local rc="$1" bin out src=0 why="agentsync is not installed"
	if [ "$DRY_RUN" -eq 1 ]; then
		run "${AGENTSYNC:-agentsync}" setup-report --out "$REPORT_PATH" --config "$CONFIG"
		return 2
	fi
	step_start report
	bin="$(report_agentsync || true)"
	if [ -n "$bin" ]; then
		out="$(with_timeout 120 "$bin" setup-report --out "$REPORT_PATH" --config "$CONFIG" </dev/null 2>&1)" || src=$?
		if [ "$src" -eq 0 ] && [ -s "$REPORT_PATH" ]; then
			say "report: $REPORT_PATH (agentsync setup-report)"
			step_end "done" 0 agentsync
			return 0
		fi
		if [ "$src" -eq 0 ]; then
			warn "agentsync setup-report exited 0 but wrote no $REPORT_PATH"
			why="agentsync setup-report wrote no report"
		else
			warn "agentsync setup-report failed ($(rc_meaning "$src")): $(printf '%s' "$out" | tail -1)"
			why="agentsync setup-report failed with exit $src"
		fi
	fi
	if write_fallback_report "$rc" "$why"; then
		say "report: $REPORT_PATH (shell fallback: $why)"
		step_end "done" 0 fallback
		return 0
	fi
	warn "could not write the setup report $REPORT_PATH"
	step_end failed 1
	return 1
}

on_exit() {
	local rc=$? suffix="" link secs
	trap - EXIT
	set +e
	[ "$PARSED" -eq 1 ] || exit "$rc"
	step_end failed "$rc"
	if [ "$STARTED" -eq 1 ]; then
		END_AT="$(utc_now)"
		secs=$SECONDS
		log_at "$END_AT" "end rc=$rc seconds=$secs" # provisional: the report reads a finished run
		END_LINE="$END_AT run=$RUN_ID end rc=$rc seconds=$secs"
	fi
	if [ "$REPORT" -eq 1 ]; then
		write_report "$rc"
		case $? in
		0)
			suffix=" [setup report: $REPORT_PATH]"
			write_bring_back || warn "could not write ${REPORT_PATH%/*}/bring-back.md"
			link="$(report_issue_link)"
			if [ -n "$link" ]; then
				[ "$REPORT_ONLY" -eq 0 ] ||
					NEXT_MSG="review ${REPORT_PATH%/*}/bring-back.md and copy that one file back privately, or paste the setup report into the issue the link above opens (nothing is sent for you)"
				say "$ISSUE_LINK_PREFIX$link" # the last line before NEXT (step 3 of the setup prompt names it)
			fi
			;;
		2) ;; # a dry run: printed, not written
		*)
			if [ "$REPORT_ONLY" -eq 1 ]; then
				rc=1
				NEXT_MSG="the setup report could not be written (see the warning above); send ${SETUP_LOG%/*}/friction.md instead"
			fi
			;;
		esac
	fi
	finish_setup_log "$rc"
	[ -z "$DOCTOR_LOG" ] || rm -f "$DOCTOR_LOG"
	[ -z "$SYNC_OUT" ] || rm -f "$SYNC_OUT"
	[ -z "$LOOP_OUT" ] || rm -f "$LOOP_OUT"
	[ -z "$NEXT_MSG" ] || say "NEXT: $NEXT_MSG$suffix"
	stop_capture
	exit "$rc"
}
# The issue link the setup report ends with (its last line, when it starts with $ISSUE_URL), else empty.
report_issue_link() {
	local l
	l="$(sed '/^[[:space:]]*$/d' "$REPORT_PATH" 2>/dev/null | tail -1)"
	case "$l" in
	"$ISSUE_URL"*) printf '%s' "$l" ;;
	esac
}

# A signal (a coding tool's timeout sends SIGTERM) still ends through on_exit: the log's end line, the report
# and a NEXT line that says to run the same command again.
on_signal() {
	LAST_ERROR="install.sh was stopped by a signal ($(rc_meaning "$1")) before it finished"
	printf 'error: %s\n' "$LAST_ERROR" >&2
	NEXT_MSG="this run was stopped before it finished ($(rc_meaning "$1")); it is safe to re-run: ${RERUN:-$SELF$ORIG_ARGS}"
	exit "$1"
}
# What macOS calls the app behind a ~/Library/CloudStorage entry ("OneDrive-Contoso" -> OneDrive).
provider_label() {
	case "$1" in
	OneDrive* | SharedLibraries*) printf 'OneDrive' ;;
	GoogleDrive*) printf 'Google Drive' ;;
	*) printf '%s' "${1%%-*}" ;;
	esac
}
# This terminal app's name, as System Settings lists it, when the terminal says (else empty).
terminal_app() {
	case "${TERM_PROGRAM:-}" in
	Apple_Terminal) printf 'Terminal' ;;
	iTerm.app) printf 'iTerm' ;;
	vscode) printf 'Visual Studio Code' ;;
	WarpTerminal) printf 'Warp' ;;
	ghostty) printf 'Ghostty' ;;
	*) printf '%s' "${TERM_PROGRAM:-}" ;;
	esac
}
# --list-folders: the folders 1-2 levels inside each ~/Library/CloudStorage/<provider>, names only (find reads
# directory entries and their metadata; no file is opened, so nothing is downloaded). Sets NEXT_MSG; returns
# 0 listed, 3 none, 4 denied or still asking.
list_folders() {
	local cs="$HOME/Library/CloudStorage" tmp e label rc total providers=0 denied="" asking="" term max=200
	local deadline=$((SECONDS + LIST_TOTAL)) t
	step_start list-folders
	tmp="$(mktemp -d)"
	: >"$tmp/list"
	if [ -d "$cs" ] && ! ls "$cs" >/dev/null 2>"$tmp/err"; then
		grep -q 'Operation not permitted' "$tmp/err" && denied="OneDrive"
	fi
	for e in "$cs"/*; do
		[ -d "$e" ] || continue
		providers=$((providers + 1))
		label="$(provider_label "$(basename "$e")")"
		rc=0
		: >"$tmp/err"
		# at most $LIST_TIMEOUT s per provider and $LIST_TOTAL s for all: a provider left without time counts
		# as still asking (the likely reason the ones before it were slow)
		t=$((deadline - SECONDS))
		[ "$t" -le "$LIST_TIMEOUT" ] || t="$LIST_TIMEOUT"
		if [ "$t" -le 0 ]; then
			rc=143
		else
			with_timeout "$t" find "$e" -mindepth 1 -maxdepth 2 -name '.*' -prune -o -type d -print \
				>>"$tmp/list" 2>"$tmp/err" || rc=$?
		fi
		if grep -q 'Operation not permitted' "$tmp/err"; then
			case ", $denied," in *", $label,"*) ;; *) denied="${denied:+$denied, }$label" ;; esac
		elif [ "$rc" -eq 143 ]; then
			case ", $asking," in *", $label,"*) ;; *) asking="${asking:+$asking, }$label" ;; esac
		elif [ -s "$tmp/err" ]; then
			warn "listing $e: $(tail -1 "$tmp/err")"
		fi
	done
	total="$(wc -l <"$tmp/list" | tr -d ' ')"
	sort "$tmp/list" | awk -v m="$max" 'NR <= m' # awk reads it all: no SIGPIPE for sort under pipefail
	[ "$total" -le "$max" ] || say "($((total - max)) more)"
	rm -rf "$tmp"
	term="$(terminal_app)"
	term="this terminal app${term:+ ($term)}"
	rc=0
	if [ -n "$denied" ]; then
		rc=4
		NEXT_MSG="$term was denied access to files managed by $denied: allow it in System Settings > Privacy & Security > Files and Folders (turn on $denied under $term; a click, not a command), then re-run: $RERUN"
		step_end failed "$rc" denied
	elif [ -n "$asking" ]; then
		rc=4
		NEXT_MSG="macOS is asking whether $term may access files managed by $asking: click Allow (or turn it on in System Settings > Privacy & Security > Files and Folders), then re-run: $RERUN"
		step_end failed "$rc" tcc-pending
	elif [ "$providers" -eq 0 ]; then
		rc=3
		NEXT_MSG="OneDrive is not signed in on this Mac: sign in to OneDrive, then re-run: $RERUN"
		step_end failed "$rc" not-signed-in
	elif [ "$total" -eq 0 ]; then
		rc=3
		NEXT_MSG="no folders are synced yet in $cs: sign in to OneDrive (or let it finish setting up), then re-run: $RERUN"
		step_end failed "$rc" no-folders
	else
		NEXT_MSG="choose the folders to sync from the list above (project folders rather than a whole library), then run: $SELF --source-local \"<folder>\" (one --source-local per folder)"
		step_end "done" 0 "listed-$total"
	fi
	return "$rc"
}

while [ $# -gt 0 ]; do
	case "$1" in
	--confirm-install-agent) INSTALL_AGENT=1 ;;
	--report-only) REPORT_ONLY=1 ;;
	--list-folders) LIST_FOLDERS=1 ;;
	--no-report) ;; # hidden, ignored: the old baseline prompt ran "install.sh --no-report && ... install-skill"
	--log-start | --log | --log-end) usage_error "$1 must be the first argument and takes no other option" ;;
	--launcher)
		[ $# -ge 2 ] || usage_error "--launcher needs a path"
		LAUNCHER_SRC="$2"
		shift
		;;
	--source-local)
		if [ $# -lt 2 ] || [ -z "$2" ]; then
			usage_error "--source-local needs a folder"
		fi
		SOURCE_LOCALS+=("$2")
		shift
		;;
	--version)
		show_version
		exit 0
		;;
	-h | --help)
		show_help
		exit 0
		;;
	-*) usage_error "unknown option $1" ;;
	*) # SOURCE, a test seam left out of --help: the checkout or wheel to install instead of this checkout
		[ -z "$SOURCE" ] || usage_error "only one SOURCE is accepted"
		SOURCE="$1"
		;;
	esac
	shift
done

# From here on every exit logs its end, writes the report (not in a dry run or --list-folders) and prints NEXT.
PARSED=1
trap 'on_exit' EXIT
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
start_capture # a copy of this run's output in install.out (not in a dry run or for --report-only)
case "$CONFIG" in
/*) ;;
*) CONFIG="$PWD/$CONFIG" ;;
esac
if [ "$LIST_FOLDERS" -eq 1 ]; then # names only: no install, no report, one list-folders step in the log
	REPORT=0
	if [ -n "$ORIG_ARGS" ] || [ "$REPORT_ONLY" -eq 1 ]; then
		usage_error "--list-folders takes no other option or SOURCE"
	fi
	case "$LIST_TIMEOUT" in
	'' | 0 | *[!0-9]*) usage_error "AGENTSYNC_LIST_TIMEOUT must be a whole number of seconds: $LIST_TIMEOUT" ;;
	esac
	case "$LIST_TOTAL" in
	'' | 0 | *[!0-9]*) usage_error "AGENTSYNC_LIST_TOTAL_SECONDS must be a whole number of seconds: $LIST_TOTAL" ;;
	esac
	RERUN="$SELF --list-folders"
	ORIG_ARGS=" --list-folders" # for the log's start line
	log_start
	rc=0
	list_folders || rc=$?
	exit "$rc"
fi
if [ "$REPORT_ONLY" -eq 1 ]; then # the attempt's end line, then step 9 alone (the EXIT trap writes it)
	NEXT_MSG="review ${REPORT_PATH%/*}/bring-back.md and copy that one file back privately (nothing is sent for you)"
	if [ "$DRY_RUN" -eq 1 ]; then
		NEXT_MSG="re-run without AGENTSYNC_INSTALL_DRY_RUN=1 to write the report"
	else
		friction_close_attempt
	fi
	exit 0
fi
case "$WAIT_SECONDS" in
'' | *[!0-9]*) usage_error "AGENTSYNC_WAIT_SECONDS must be a whole number of seconds: $WAIT_SECONDS" ;;
esac
case "$PROGRESS_SECONDS" in
'' | 0 | *[!0-9]*) usage_error "AGENTSYNC_PROGRESS_SECONDS must be a whole number of seconds: $PROGRESS_SECONDS" ;;
esac
# $SECONDS counts whole seconds and a check comes once per tick (0.2 s) or poll: printing a little early keeps
# every gap under $PROGRESS_SECONDS.
PROGRESS_EVERY=$((PROGRESS_SECONDS - 1))
wp="${WAIT_POLL%%.*}"
case "$wp" in '' | *[!0-9]*) wp=0 ;; esac
WAIT_EVERY=$((PROGRESS_EVERY - wp))
[ "$PROGRESS_EVERY" -ge 1 ] || PROGRESS_EVERY=1
[ "$WAIT_EVERY" -ge 1 ] || WAIT_EVERY=1

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
	[ "$INSTALL_AGENT" -eq 1 ] || usage_error "--launcher only applies with --confirm-install-agent (the launcher is for background sync)"
	[ -d "$LAUNCHER_SRC" ] || usage_error "--launcher is not an app bundle directory: $LAUNCHER_SRC"
	LAUNCHER_SRC="$(cd "$LAUNCHER_SRC" && pwd)"
fi
# Each --source-local folder: absolute (a quoted "~/..." expanded), and an existing directory, checked before
# anything is installed. agentsync resolves symlinks and derives the source id.
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

if [ "$SOURCE_KIND" = "checkout" ] && [ "$DRY_RUN" -eq 0 ] && [ -e "$SOURCE/.git" ]; then
	c="$(checkout_commit "$SOURCE")"
	case "$c" in
	"") ;;
	*" dirty "*)
		COMMIT="${c%% *}-dirty" # "-dirty" keeps a changed tree from ever matching the install stamp
		DIRTY="${c##* }"
		;;
	*) COMMIT="$c" ;;
	esac
fi
log_start

[ "$DRY_RUN" -eq 1 ] && say "dry run: nothing below is changed"
say "source: $SOURCE ($SOURCE_KIND)"
say "config: $CONFIG"
for f in ${FOLDERS[@]+"${FOLDERS[@]}"}; do
	say "source-local: $f"
done
[ "$SIMULATE" -eq 0 ] || say "SIMULATED: AGENTSYNC_SIMULATE_LAUNCHD=1 (test only): install-agent, the kickstart and the wait are simulated"

# Corporate TLS inspection: let uv trust the macOS keychain (a no-op where the network is not inspected).
if [ -z "${UV_SYSTEM_CERTS:-}" ] && [ -z "${UV_NATIVE_TLS:-}" ]; then
	export UV_SYSTEM_CERTS=1
fi

# ------------------------------------------------------------------------------------------------ 1. uv
step_start uv
UV=""
if command -v uv >/dev/null 2>&1; then
	UV="$(command -v uv)"
elif [ -x "$HOME/.local/bin/uv" ]; then
	UV="$HOME/.local/bin/uv"
fi
if [ -n "$UV" ]; then
	say "uv: $UV (present)"
	step_end skipped 0 present
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
	step_end "done" 0 installed
fi

# ------------------------------------------------------------------------------------------------ 2. agentsync
# uv tool install, without uv's "`~/.local/bin` is not on your PATH ... uv tool update-shell" hint: this setup
# calls agentsync by its full path and must not send anyone to edit a shell profile (the final note says so).
uv_filter() { sed -e '/is not on your PATH/d' -e '/update-shell/d'; }
uv_tool_install() {
	local args=(tool install --force --reinstall-package agentsync --python 3.11 "$SOURCE")
	if [ "$DRY_RUN" -eq 1 ]; then
		run "$UV" "${args[@]}"
		return 0
	fi
	{ "$UV" "${args[@]}" </dev/null 2>&1 1>&3 3>&- | uv_filter >&2; } 3>&1 # uv's status (pipefail)
}
step_start agentsync
BIN_DIR="$HOME/.local/bin"
TOOL_DIR="$HOME/.local/share/uv/tools"
if [ -x "$UV" ]; then
	BIN_DIR="$("$UV" tool dir --bin 2>/dev/null || printf '%s' "$HOME/.local/bin")"
	TOOL_DIR="$("$UV" tool dir 2>/dev/null || printf '%s' "$TOOL_DIR")"
fi
AGENTSYNC="$BIN_DIR/agentsync"
# The last install's source, kept inside the tool environment (a reinstall replaces it): a clean checkout at
# the same commit is the same code, so it is not installed again.
STAMP="$TOOL_DIR/agentsync/.agentsync-install-source"
STAMP_WANT="commit=$COMMIT source=$SOURCE"
if [ "$DRY_RUN" -eq 0 ] && [ "$SOURCE_KIND" = "checkout" ] && [ "$COMMIT" != "-" ] &&
	[ "${COMMIT%-dirty}" = "$COMMIT" ] && [ -x "$AGENTSYNC" ] && [ -f "$STAMP" ] &&
	[ "$(cat "$STAMP" 2>/dev/null)" = "$STAMP_WANT" ]; then
	say "agentsync: $AGENTSYNC (current: installed from commit $COMMIT)"
	step_end skipped 0 current
else
	uv_tool_install ||
		fail "uv tool install failed (network, proxy or TLS inspection? set HTTPS_PROXY / UV_SYSTEM_CERTS=1)"
	if [ "$DRY_RUN" -eq 0 ] && [ ! -x "$AGENTSYNC" ]; then
		fail "uv tool install finished but $AGENTSYNC is missing"
	fi
	if [ "$DRY_RUN" -eq 0 ] && [ -d "$TOOL_DIR/agentsync" ]; then
		if [ "$SOURCE_KIND" = "checkout" ] && [ "$COMMIT" != "-" ] && [ "${COMMIT%-dirty}" = "$COMMIT" ]; then
			printf '%s\n' "$STAMP_WANT" >"$STAMP" 2>/dev/null || true
		else
			rm -f "$STAMP" 2>/dev/null || true
		fi
	fi
	say "agentsync: $AGENTSYNC"
	step_end "done"
fi
# The one interpreter the launcher may start (baked into its sealed Info.plist; see launcher/build.sh).
TOOL_PY="$TOOL_DIR/agentsync/bin/python"

# ------------------------------------------------------------------------------------------------ 3. launcher
step_start launcher
LAUNCHER_RESULT="done"
LAUNCHER_NOTE=""
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

# Only for background sync (KISS K11b): a run without --confirm-install-agent builds, copies and checks nothing.
LAUNCHER_STATE="missing"
if [ "$INSTALL_AGENT" -eq 0 ]; then
	LAUNCHER_RESULT="skipped" LAUNCHER_NOTE="not-requested" LAUNCHER_STATE="not-requested"
elif [ -n "$LAUNCHER_SRC" ]; then
	launcher_valid "$LAUNCHER_SRC" || fail "--launcher $LAUNCHER_SRC does not pass codesign --verify --strict"
	if [ -d "$APP_DEST" ] && launcher_valid "$APP_DEST" && [ "$(cdhash_of "$APP_DEST")" = "$(cdhash_of "$LAUNCHER_SRC")" ]; then
		say "launcher: $APP_DEST is up to date (same cdhash as $LAUNCHER_SRC)"
		LAUNCHER_RESULT="skipped" LAUNCHER_NOTE="up-to-date"
	else
		install_app "$LAUNCHER_SRC"
	fi
	LAUNCHER_STATE="installed"
elif [ "$SOURCE_KIND" = "checkout" ] && [ -x "$SOURCE/launcher/build.sh" ] && have_devtools; then
	want="$(ALLOWED_PROGRAM="$TOOL_PY" "$SOURCE/launcher/build.sh" --print-source-sha256)"
	if [ "$REBUILD" -eq 0 ] && [ -d "$APP_DEST" ] && launcher_valid "$APP_DEST" && [ "$(installed_launcher_sha)" = "$want" ]; then
		say "launcher: $APP_DEST is up to date (sources $want)"
		LAUNCHER_RESULT="skipped" LAUNCHER_NOTE="up-to-date"
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
elif [ -d "$APP_DEST" ] && launcher_valid "$APP_DEST"; then
	say "launcher: keeping $APP_DEST (no developer tools to rebuild it, and no --launcher PATH)"
	LAUNCHER_RESULT="skipped" LAUNCHER_NOTE="kept"
	LAUNCHER_STATE="installed"
else
	warn "no launcher: no developer tools (xcode-select -p) and no --launcher PATH; sources under" \
		"$HOME/Library/CloudStorage cannot run from launchd until one is installed"
	LAUNCHER_RESULT="skipped" LAUNCHER_NOTE="no-launcher"
fi
# The on-device OCR helper (agentsync.convert.ocr), with or without background sync: this is the one place it
# is compiled, so status, a sync and the LaunchAgent never start a compiler. The module reads [convert] ocr
# itself (off builds nothing) and prints one line. OCR is optional: a helper that does not build is that
# line, never a failed step. Without developer tools nothing is tried, and the line says so: status sends the
# reader here for the helper, and a run that said nothing would send them round again.
# -I: with -m alone Python puts the folder this script is run from first on its import path, so a random.py
# or types.py lying there would be imported in place of the standard library's.
if have_devtools; then
	if [ "$DRY_RUN" -eq 1 ]; then
		run "$TOOL_PY" -I -m agentsync.convert.ocr
	elif [ -x "$TOOL_PY" ]; then
		ocr_line="$(AGENTSYNC_CONFIG="$CONFIG" "$TOOL_PY" -I -m agentsync.convert.ocr </dev/null 2>/dev/null | head -n 1)" || true
		say "${ocr_line:-OCR helper: not built (the build did not run)}"
	fi
else
	say "OCR helper: not built (no Xcode or Command Line Tools)"
fi
step_end "$LAUNCHER_RESULT" 0 "$LAUNCHER_NOTE"
if [ "$LAUNCHER_STATE" = "installed" ] && [ "$DRY_RUN" -eq 0 ]; then
	say "launcher: $(/usr/bin/codesign -d -r- "$APP_DEST" 2>&1 | sed -n 's/^#* *designated => /designated requirement: /p')"
fi

# ------------------------------------------------------------------------------------------------ 4. config
step_start config
# add-source and the flagless init each create whatever is missing (sources.toml, the docs repo and its
# scaffold, the inbox, the state dir) and are idempotent (KISS K14). Opening the manifest migrates it, so an
# upgrade (step 2) needs no separate migrate call.
if [ -f "$CONFIG" ]; then
	CONFIG_STATE="present"
else
	CONFIG_STATE="created"
fi
if [ "${#FOLDERS[@]}" -gt 0 ]; then
	for f in "${FOLDERS[@]}"; do
		run "$AGENTSYNC" add-source "$f" --config "$CONFIG" </dev/null ||
			fail "agentsync add-source $f failed (see the error above)"
	done
else
	run "$AGENTSYNC" init --config "$CONFIG" </dev/null || fail "agentsync init failed (see the error above)"
	[ "$CONFIG_STATE" = "created" ] || say "config: $CONFIG exists (inbox ensured)"
fi
if [ "$CONFIG_STATE" = "created" ]; then
	step_end "done" 0 created
elif [ "${#FOLDERS[@]}" -gt 0 ]; then
	step_end "done" 0 add-source
else
	step_end skipped 0 exists
fi
# Sources other than the inbox: init and add-source always add the inbox (KISS K05), so a config holding only
# the inbox still has no folder to sync and gets "choose a folder to sync".
non_inbox_sources() { # $1: sources.toml; prints the number of [[source]] tables whose kind is not "inbox"
	awk '
		/^[[:space:]]*\[/ { if (t && !ib) n++; t = ($0 ~ /^[[:space:]]*\[\[source\]\][[:space:]]*(#.*)?$/); ib = 0; next }
		t && /^[[:space:]]*kind[[:space:]]*=[[:space:]]*("inbox"|\047inbox\047)[[:space:]]*(#.*)?$/ { ib = 1 }
		END { if (t && !ib) n++; print n + 0 }
	' "$1" 2>/dev/null || echo 0
}
HAVE_SOURCES=0
if [ "${#FOLDERS[@]}" -gt 0 ] || [ "$(non_inbox_sources "$CONFIG")" -gt 0 ]; then
	HAVE_SOURCES=1
fi

# The sync app named in macOS's prompt ("... files managed by OneDrive"), from the folders being synced.
provider_of_paths() { # stdin: paths; the first CloudStorage provider folder's name up to its "-"
	local cs="$HOME/Library/CloudStorage" f
	while IFS= read -r f; do
		case "$f" in
		\~/*) f="$HOME/${f#\~/}" ;;
		esac
		case "$f" in
		"$cs"/*)
			f="${f#"$cs"/}"
			f="${f%%/*}"
			printf '%s' "${f%%-*}"
			return 0
			;;
		esac
	done
}
provider_name() {
	local p f
	p="$({
		for f in ${FOLDERS[@]+"${FOLDERS[@]}"}; do printf '%s\n' "$f"; done
		sed -n 's/^[[:space:]]*path[[:space:]]*=[[:space:]]*"\(.*\)".*/\1/p' "$CONFIG" 2>/dev/null || true
	} | provider_of_paths)"
	case "$p" in
	'' | OneDrive | SharedLibraries) printf 'OneDrive' ;;
	GoogleDrive) printf 'Google Drive' ;;
	*) printf '%s' "$p" ;;
	esac
}
action_once() {
	[ "$ACTION_SHOWN" -eq 0 ] || return 0
	ACTION_SHOWN=1
	say "ACTION: macOS is asking whether agentsync-launcher may access files managed by $(provider_name). Click Allow."
}

# ------------------------------------------------------------------------------------------------ 5. status
# The [FAIL] lines of status output $1 that stop the run: all but the launcher's own TCC_PENDING (a
# "tcc.<source>" check, which the wait asks the Allow for). A listing macOS holds for an Allow click in this
# terminal is a source.<id>.listable [FAIL], so it stops the run (field N8b).
blocking_fails() {
	grep '^\[FAIL' "$1" 2>/dev/null | grep -Ev '^\[FAIL\] tcc\.[^ ]+ +— TCC_PENDING: ' || true
}
step_start status
DOCTOR_RC=0
DOCTOR_TCC_ONLY=0 # every [FAIL] line is the launcher's TCC_PENDING: the wait (step 8) asks for the Allow instead
if [ "$DRY_RUN" -eq 1 ]; then
	run "$AGENTSYNC" status --config "$CONFIG"
	step_end "done"
else
	DOCTOR_LOG="$(mktemp)"
	# With --confirm-install-agent and a source, step 7 installs the LaunchAgents: status's launchd.* lines say
	# so instead of naming `agentsync install-agent` (a second instruction next to NEXT:). This step only: the
	# report written at the exit describes what is installed by then. Under AGENTSYNC_NO_NEXT_HINT=1 status
	# prints no NEXT, detail or policy lines (no label names in install.out).
	if [ "$INSTALL_AGENT" -eq 1 ] && [ "$HAVE_SOURCES" -eq 1 ]; then
		export AGENTSYNC_AGENT_STEP_PENDING=1
	fi
	"$AGENTSYNC" status --config "$CONFIG" </dev/null | tee "$DOCTOR_LOG" || DOCTOR_RC=$?
	unset AGENTSYNC_AGENT_STEP_PENDING
	if [ "$DOCTOR_RC" -ne 0 ] && grep -q '^\[FAIL' "$DOCTOR_LOG" && [ -z "$(blocking_fails "$DOCTOR_LOG")" ]; then
		DOCTOR_TCC_ONLY=1
	fi
	if [ "$DOCTOR_TCC_ONLY" -eq 1 ]; then
		step_end "done" "$DOCTOR_RC" tcc-pending
	else
		step_end "done" "$DOCTOR_RC" "$([ "$DOCTOR_RC" -eq 0 ] || printf '%s' fail-lines)"
	fi
fi
DOCTOR_BLOCKS=0
if [ "$DOCTOR_RC" -ne 0 ] && [ "$DOCTOR_TCC_ONLY" -eq 0 ]; then
	DOCTOR_BLOCKS=1
fi

# ------------------------------------------------------------------------------------------------ 6. first sync
# Run a command, printing "LABEL: still running, <N>s" every $PROGRESS_SECONDS while it runs (a coding tool
# may stop a command that prints nothing for a while); its exit status.
with_progress() {
	local label="$1" pid rc=0 t0=$SECONDS last=$SECONDS
	shift
	"$@" </dev/null &
	pid=$!
	while kill -0 "$pid" 2>/dev/null; do
		if [ $((SECONDS - last)) -ge "$PROGRESS_EVERY" ]; then
			last=$SECONDS
			say "$label: still running, $((SECONDS - t0))s"
		fi
		sleep 0.2
	done
	wait "$pid" || rc=$?
	return "$rc"
}
# Step 6 runs with a folder to sync and a status with no [FAIL] that stops it (SYNC_GO, KISS K02); steps 7-8
# also need --confirm-install-agent (GO). A dry run prints them all.
SYNC_GO=0
SYNC_SKIP_NOTE=""
if [ "$DRY_RUN" -eq 1 ]; then
	SYNC_GO=1
elif [ "$HAVE_SOURCES" -eq 0 ]; then
	SYNC_SKIP_NOTE="no-sources"
elif [ "$DOCTOR_BLOCKS" -eq 1 ]; then
	SYNC_SKIP_NOTE="status-failed"
else
	SYNC_GO=1
fi
GO=0
SKIP_NOTE="not-requested"
if [ "$INSTALL_AGENT" -eq 1 ]; then
	if [ "$SYNC_GO" -eq 1 ]; then
		GO=1
	else
		SKIP_NOTE="$SYNC_SKIP_NOTE"
		[ "$DOCTOR_BLOCKS" -eq 0 ] || warn "not installing the LaunchAgents: agentsync status reported failures"
	fi
fi
[ "$DOCTOR_TCC_ONLY" -eq 0 ] || [ "$GO" -eq 0 ] || action_once

# The first sync downloads nothing (--materialise-budget 0): the files already on this Mac are converted, and
# the one install command is not bounded by the folders' size; each later sync downloads and converts the
# online-only files it deferred.
FIRST_SYNC=(sync --once --materialise-budget 0)
FIRST_SYNC_NOTE=""
# What this step's lines call it: on a Mac whose sources.toml already existed it is one more sync, not the
# first. The install.log step stays "first-sync" either way (setup-report reads that name).
SYNC_LABEL="first sync"
[ "$CONFIG_STATE" = "created" ] || SYNC_LABEL="sync"
SYNC_SUMMARY_RE='converted [0-9]+, deferred [0-9]+ online-only'
first_sync_run() { "$AGENTSYNC" "${FIRST_SYNC[@]}" --config "$CONFIG" >"$SYNC_OUT"; } # stderr as it comes
# What the first sync printed: its "converted N, deferred M online-only" line(s) after a success, else all of
# it (a failure's detail, or an agentsync that prints no such line).
show_first_sync() { # RC
	local summary
	summary="$(grep -E "$SYNC_SUMMARY_RE" "$SYNC_OUT" 2>/dev/null || true)"
	if [ "$1" -eq 0 ] && [ -n "$summary" ]; then
		printf '%s\n' "$summary" | sed "s/^[[:space:]]*/$SYNC_LABEL: /"
		[ -n "$FIRST_SYNC_NOTE" ] || FIRST_SYNC_NOTE="$(printf '%s\n' "$summary" | awk '
			{ for (i = 1; i < NF; i++) { if ($i == "converted") c += $(i + 1); if ($i == "deferred") d += $(i + 1) } }
			END { printf "converted-%d-deferred-%d", c, d }')"
	else
		cat "$SYNC_OUT" 2>/dev/null || true
	fi
}
step_start first-sync
SYNC_RC=0
if [ "$SYNC_GO" -eq 0 ]; then
	step_end skipped 0 "$SYNC_SKIP_NOTE"
elif [ "$DRY_RUN" -eq 1 ]; then
	run "$AGENTSYNC" "${FIRST_SYNC[@]}" --config "$CONFIG"
	step_end "done"
else
	say "$SYNC_LABEL: $AGENTSYNC sync --once --materialise-budget 0 (downloads nothing: the files already on this Mac are converted now; each later sync downloads and converts the online-only ones)"
	SYNC_OUT="$(mktemp)"
	with_progress "$SYNC_LABEL" first_sync_run || SYNC_RC=$?
	show_first_sync "$SYNC_RC"
	if [ "$SYNC_RC" -eq 0 ]; then
		step_end "done" 0 "$FIRST_SYNC_NOTE"
	elif [ "$SYNC_RC" -eq 80 ]; then
		step_end failed 80 tcc-denied
		LAST_ERROR="the first sync (agentsync sync --once) exited 80 ($(rc_meaning 80)): this terminal app may not read the folders"
		printf 'error: %s\n' "$LAST_ERROR" >&2
		term="$(terminal_app)"
		term="this terminal app${term:+ ($term)}"
		NEXT_MSG="$term was denied access to files managed by $(provider_name): allow it in System Settings > Privacy & Security > Files and Folders (turn on $(provider_name) under $term; a click, not a command), then re-run: $SELF$ORIG_ARGS"
		exit 1
	elif [ "$SYNC_RC" -eq 75 ]; then
		say "$SYNC_LABEL: skipped, $(rc_meaning 75)"
		step_end skipped 75 lock-busy
	else
		step_end failed "$SYNC_RC"
		LAST_ERROR="the first sync (agentsync sync --once) exited $SYNC_RC ($(rc_meaning "$SYNC_RC"))"
		printf 'error: %s\n' "$LAST_ERROR" >&2
		NEXT_MSG="fix what the first sync printed above ($(rc_meaning "$SYNC_RC")), then re-run: $SELF$ORIG_ARGS"
		exit 1
	fi
fi

# ------------------------------------------------------------------------------------------------ 7. agent
step_start agent
AGENT_RC=0
WAIT_SINCE="$STEP_AT" # the wait reads the launcher lines logged from here on (the RunAtLoad run's too)
if [ "$GO" -eq 0 ]; then
	if [ "$INSTALL_AGENT" -eq 1 ]; then AGENT_RC=1; fi
	# Not asked for, but an earlier install left the LaunchAgents: say so, or the run reads as "no background
	# sync" on a Mac where it runs. Only the plist is checked (no launchctl call in a run that was not asked
	# for the agent step), so the line claims no more than "installed".
	if [ "$INSTALL_AGENT" -eq 0 ] && [ -e "$HOME/Library/LaunchAgents/$POLL_LABEL.plist" ]; then
		say "background sync: already installed by an earlier run ($POLL_LABEL); this run left it as it is, and $AGENTSYNC install-agent refreshes it"
	fi
	step_end skipped "$AGENT_RC" "$SKIP_NOTE"
elif [ "$SIMULATE" -eq 1 ]; then
	say "SIMULATED: $AGENTSYNC install-agent --config $CONFIG (no plist written, no launchctl call)"
	step_end "done" 0 simulated
elif [ "$DRY_RUN" -eq 1 ]; then
	run "$AGENTSYNC" install-agent --config "$CONFIG"
	step_end "done"
else
	"$AGENTSYNC" install-agent --config "$CONFIG" </dev/null || AGENT_RC=$?
	if [ "$AGENT_RC" -eq 0 ]; then
		say "the first background run starts now; if macOS asks $PROMPT_TEXT, click Allow"
		step_end "done"
	else
		LAST_ERROR="agentsync install-agent exited $AGENT_RC ($(rc_meaning "$AGENT_RC"))"
		step_end failed "$AGENT_RC"
	fi
fi

# ------------------------------------------------------------------------------------------------ 8. wait
step_start wait
WAIT_RESULT="skipped" # ok | running | tcc | timeout | failed | skipped
WAIT_CODE=""          # the last background run's exit code
WAIT_STATE=""
WAIT_ERRLOG=""
WAIT_CANARIES="" # the job's --canary paths, one per line
kickstart() { "$LAUNCHCTL" kickstart "gui/$(id -u)/$POLL_LABEL" >/dev/null 2>&1 || true; }
tcc_lines() { # TCC_PENDING lines in the job's stderr log (the launcher's log)
	if [ -n "$WAIT_ERRLOG" ] && [ -f "$WAIT_ERRLOG" ]; then
		grep -c 'TCC_PENDING' "$WAIT_ERRLOG" 2>/dev/null || true
	else
		printf '0'
	fi
}
# The --canary paths of the job's launcher: the "arguments = {" block of `launchctl print`, one per line.
job_canaries() {
	printf '%s\n' "$1" | awk '
		/^\targuments = \{$/ { inside = 1; next }
		inside && /^\t\}$/ { exit }
		inside { sub(/^[\t ]+/, ""); if (want) { print; want = 0 } else if ($0 == "--canary") want = 1 }'
}
# Whether the running job's launcher (PID) is past the macOS access check: since WAIT_SINCE it logged
# CANARY_OK for every canary and no TCC_PENDING or TCC_DENIED (the launcher's quoting: \ and " escaped).
canaries_passed() { # PID
	local lines c q bs="\\" dq='"'
	[ -n "$1" ] && [ -n "$WAIT_CANARIES" ] && [ -n "$WAIT_ERRLOG" ] && [ -f "$WAIT_ERRLOG" ] || return 1
	lines="$(awk -v t0="$WAIT_SINCE" -v tag="agentsync-launcher[$1]: " '$1 >= t0 && index($0, tag)' "$WAIT_ERRLOG" 2>/dev/null || true)"
	[ -n "$lines" ] || return 1
	case "$lines" in
	*"]: TCC_PENDING"* | *"]: TCC_DENIED"*) return 1 ;;
	esac
	while IFS= read -r c; do
		[ -n "$c" ] || continue
		q=${c//"$bs"/$bs$bs} # unquoted: bash 3.2 keeps quotes in a quoted replacement
		q=${q//"$dq"/$bs$dq}
		case "$lines" in
		*"]: CANARY_OK path=$dq$q$dq"*) ;;
		*) return 1 ;;
		esac
	done <<EOF
$WAIT_CANARIES
EOF
}
if [ "$GO" -eq 0 ] || [ "$AGENT_RC" -ne 0 ]; then
	step_end skipped 0 "$([ "$AGENT_RC" -eq 0 ] && printf '%s' "$SKIP_NOTE" || printf '%s' agent-failed)"
elif [ "$SIMULATE" -eq 1 ]; then
	say "SIMULATED: $LAUNCHCTL kickstart gui/$(id -u)/$POLL_LABEL (no launchctl call)"
	say "SIMULATED: the first background run exited 0 ($(rc_meaning 0))"
	WAIT_RESULT="ok"
	WAIT_CODE=0
	step_end "done" 0 simulated
elif [ "$DRY_RUN" -eq 1 ]; then
	run "$LAUNCHCTL" kickstart "gui/$(id -u)/$POLL_LABEL"
	say "[dry-run] then: $LAUNCHCTL print gui/$(id -u)/$POLL_LABEL every ${WAIT_POLL}s for up to ${WAIT_SECONDS}s"
	step_end "done"
else
	p="$(launchd_print || true)"
	runs="$(launchd_field "$p" runs)"
	case "$runs" in '' | *[!0-9]*) runs=0 ;; esac
	WAIT_ERRLOG="$(launchd_field "$p" "stderr path")"
	WAIT_CANARIES="$(job_canaries "$p")"
	tcc0="$(tcc_lines)"
	target=$((runs + 1))
	if [ "$(launchd_field "$p" state)" = "running" ]; then
		target=$runs # the run install-agent started (RunAtLoad) is the one to wait for
	fi
	kickstart
	say "wait: started $POLL_LABEL; waiting up to ${WAIT_SECONDS}s (AGENTSYNC_WAIT_SECONDS, default 180) for its first run to pass the macOS access check or exit 0, with a progress line every ${PROGRESS_SECONDS}s"
	t0=$SECONDS
	last=$SECONDS
	while :; do
		p="$(launchd_print || true)"
		WAIT_STATE="$(launchd_field "$p" state)"
		runs="$(launchd_field "$p" runs)"
		case "$runs" in '' | *[!0-9]*) runs=0 ;; esac
		code="$(leading_int "$(launchd_field "$p" "last exit code")")"
		if [ "$(tcc_lines)" -gt "$tcc0" ]; then
			action_once
		fi
		if [ -z "$WAIT_ERRLOG" ]; then # the job was not loaded yet before the kickstart
			WAIT_ERRLOG="$(launchd_field "$p" "stderr path")"
			tcc0="$(tcc_lines)"
		fi
		[ -n "$WAIT_CANARIES" ] || WAIT_CANARIES="$(job_canaries "$p")"
		if [ "$WAIT_STATE" = "running" ] && canaries_passed "$(launchd_field "$p" pid)"; then
			WAIT_RESULT="running"
			break
		fi
		if [ -n "$p" ] && [ "$WAIT_STATE" != "running" ] && [ "$runs" -ge "$target" ] && [ -n "$code" ]; then
			WAIT_CODE="$code"
			if [ "$code" -eq 0 ]; then
				WAIT_RESULT="ok"
				break
			elif [ "$code" -eq 79 ] || [ "$code" -eq 75 ]; then
				[ "$code" -ne 79 ] || action_once
				say "wait: background run $runs exited $code ($(rc_meaning "$code")); starting it again"
				target=$((runs + 1))
				kickstart
			else
				WAIT_RESULT="failed"
				break
			fi
		fi
		if [ $((SECONDS - last)) -ge "$WAIT_EVERY" ]; then
			last=$SECONDS
			if [ "$ACTION_SHOWN" -eq 1 ]; then
				what="waiting for Allow on the macOS prompt"
			elif [ "$WAIT_STATE" = "running" ]; then
				what="the background run is running"
			else
				what="waiting for the background run"
			fi
			say "wait: $((SECONDS - t0))s of ${WAIT_SECONDS}s, $what (runs: $runs, last exit: ${code:-none})"
		fi
		if [ $((SECONDS - t0)) -ge "$WAIT_SECONDS" ]; then
			if [ "$WAIT_CODE" = "79" ] || { [ -z "$WAIT_CODE" ] && [ "$ACTION_SHOWN" -eq 1 ]; }; then
				WAIT_RESULT="tcc"
			else
				WAIT_RESULT="timeout"
			fi
			break
		fi
		sleep "$WAIT_POLL"
	done
	case "$WAIT_RESULT" in
	ok)
		say "wait: the first background run exited 0 ($(rc_meaning 0))"
		step_end "done" 0
		;;
	running)
		say "wait: the first background run is past the macOS access check (CANARY_OK for every protected folder)"
		say "background sync: running (first run converts online-only files; it continues after this command)"
		step_end "done" 0 running
		;;
	failed)
		LAST_ERROR="the first background run exited $WAIT_CODE ($(rc_meaning "$WAIT_CODE"))"
		printf 'error: %s\n' "$LAST_ERROR" >&2
		step_end failed "$WAIT_CODE"
		;;
	*)
		LAST_ERROR="no background run exited 0 within ${WAIT_SECONDS}s (last exit: ${WAIT_CODE:-none}$([ -z "$WAIT_CODE" ] || printf ', %s' "$(rc_meaning "$WAIT_CODE")"); state: ${WAIT_STATE:-not loaded})"
		printf 'error: %s\n' "$LAST_ERROR" >&2
		step_end failed "${WAIT_CODE:-3}" "$WAIT_RESULT"
		;;
	esac
fi

case ":$PATH:" in
*":$BIN_DIR:"*) ;;
*) say "note: $BIN_DIR is not on PATH; nothing to change: this setup and its NEXT lines use the full path $AGENTSYNC" ;;
esac

# ------------------------------------------------------------------------------------------------ next step
# The loop's NEXT (KISS K02): status once more, with its NEXT line on (AGENTSYNC_NO_NEXT_HINT unset). Only its
# [FAIL] and WAITING ON YOU lines are printed, above the one NEXT: its detail and policy lines would put label
# names into install.out. WAITING lines name source ids, which setup-report redacts, and one names folders
# in an exclude line (an empty cloud folder), which setup-report and the shell report show as <path>. The
# status step's own output (DOCTOR_LOG) never has that line: its heartbeat check names no folder.
loop_status_run() { /usr/bin/env -u AGENTSYNC_NO_NEXT_HINT "$AGENTSYNC" status --config "$CONFIG" >"$LOOP_OUT" 2>/dev/null; }
loop_next() { # sets next, and EXIT_RC to 1 when that status found something that stops the loop
	local fails blocking held waits
	LOOP_OUT="$(mktemp)"
	with_progress "status" loop_status_run || true # its exit status is its [FAIL] lines
	fails="$(grep '^\[FAIL' "$LOOP_OUT" 2>/dev/null || true)"
	blocking="$(blocking_fails "$LOOP_OUT")" # the launcher's TCC_PENDING alone does not stop the loop
	held="$(awk '/^WAITING ON YOU: macOS held the listing / { sub(/^WAITING ON YOU: /, ""); print; exit }' "$LOOP_OUT")"
	waits="$(grep '^WAITING ON YOU: ' "$LOOP_OUT" 2>/dev/null || true)"
	next="$(awk '/^NEXT: / { sub(/^NEXT: /, ""); print; exit }' "$LOOP_OUT")"
	[ -z "$fails" ] || printf '%s\n' "$fails"
	[ -z "$blocking" ] || EXIT_RC=1
	[ -z "$held" ] || EXIT_RC=1 # a listing macOS holds for an Allow click in this terminal (field N8b)
	if [ -n "$blocking" ] || { [ -n "$fails" ] && [ -z "$held" ]; }; then # its NEXT points at a [FAIL] line "below"
		next="fix the [FAIL] lines above (each names its fix), then run $AGENTSYNC sync and follow its NEXT line"
	elif [ -n "$held" ]; then # the held listing is the NEXT, so its WAITING line is not printed twice
		next="$held"
		waits="$(printf '%s\n' "$waits" | grep -vxF "WAITING ON YOU: $held" || true)"
	elif [ -z "$next" ]; then
		next="run $AGENTSYNC sync and follow its NEXT line"
	fi
	if [ -n "$waits" ]; then # printed above the NEXT, which status puts first
		printf '%s\n' "$waits"
		next="${next//WAITING ON YOU below/WAITING ON YOU above}"
	fi
}
EXIT_RC=0
TCC_CLICK="turn on agentsync-launcher in System Settings > Privacy & Security > Files and Folders" # a click, not a command
if [ "$DRY_RUN" -eq 1 ]; then
	next="re-run without AGENTSYNC_INSTALL_DRY_RUN=1 to apply the steps above"
elif [ "$CONFIG_STATE" = "created" ] && [ "$HAVE_SOURCES" -eq 0 ]; then
	EXIT_RC=2 # a first run with no folder: an existing config (inbox-only too) ends on status's NEXT below
	next="no folder to sync yet: list them with $SELF --list-folders, choose the folders to sync, then run: $SELF$BASE_ARGS --source-local \"<folder>\" (one --source-local per folder)"
elif [ "$DOCTOR_BLOCKS" -eq 1 ]; then
	EXIT_RC=1
	next="fix the [FAIL] lines above (each names its fix), then re-run: $SELF$ORIG_ARGS"
elif [ "$INSTALL_AGENT" -eq 1 ] && [ "$WAIT_RESULT" != "ok" ] && [ "$WAIT_RESULT" != "running" ]; then
	EXIT_RC=1 # --confirm-install-agent asked for background sync: anything short of it is a failure
	if [ "$HAVE_SOURCES" -eq 0 ]; then
		next="choose a folder to sync, then re-run: $SELF$BASE_ARGS --source-local \"<folder>\""
	elif [ "$AGENT_RC" -ne 0 ]; then
		next="read the install-agent error above, then re-run: $SELF$ORIG_ARGS"
	elif [ "$WAIT_RESULT" = "tcc" ]; then
		EXIT_RC=3
		next="macOS is still waiting for Allow for agentsync-launcher: click Allow if the prompt is showing, or $TCC_CLICK, then re-run: $SELF$ORIG_ARGS"
	elif [ "$WAIT_CODE" = "80" ]; then
		next="macOS denied agentsync-launcher access to files managed by $(provider_name) (an earlier \"Don't Allow\"): $TCC_CLICK, then re-run: $SELF$ORIG_ARGS"
	elif [ "$WAIT_RESULT" = "timeout" ]; then
		EXIT_RC=3
		next="the first background run did not exit 0 within ${WAIT_SECONDS}s (last exit: ${WAIT_CODE:-none}); check $AGENTSYNC status, then re-run: $SELF$ORIG_ARGS"
	else
		next="the first background run exited $WAIT_CODE ($(rc_meaning "$WAIT_CODE")); fix what $AGENTSYNC status and $WAIT_ERRLOG show, then re-run: $SELF$ORIG_ARGS"
	fi
else # the loop's NEXT, after the background-sync result when it was asked for
	loop_next
	if [ "$INSTALL_AGENT" -eq 1 ] && [ "$SIMULATE" -eq 1 ]; then
		next="background sync: simulated (AGENTSYNC_SIMULATE_LAUNCHD=1, no job runs); $next"
	elif [ "$INSTALL_AGENT" -eq 1 ]; then
		next="background sync: $WAIT_RESULT; $next"
	fi
fi
NEXT_MSG="$next"
[ "$EXIT_RC" -eq 0 ] || exit "$EXIT_RC" # the EXIT trap writes the report and prints NEXT
