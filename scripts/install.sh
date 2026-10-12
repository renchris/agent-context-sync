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
#                           that attempt has no end line yet, and that it leaves the setup folder's contents
#                           owner-only, as every run does (see "Setup log"). When --log-start stopped that
#                           attempt (a copy of the prompt that is not this installer's), its NEXT: says not
#                           to bring this report back and what to do instead (the fix that stop logged), and
#                           the run writes no bring-back.md and prints no "bring back:" or issue link line
#   --version               print the commit of this checkout ("source commit: <sha> dirty <fingerprint>" when it
#                           has local changes; see the setup log), then "setup-prompt-compat N" as the last line
#                           (the line step 1 of the setup prompt checks)
#   --list-folders          only list the folders this Mac syncs, as candidates for --source-local: every
#                           folder 1 or 2 levels inside each ~/Library/CloudStorage/<provider> (names only: no
#                           file is opened; no dot folders), sorted, one full path per line, at most 200 then
#                           "(N more)"; installs nothing and writes no report, only a list-folders step in the
#                           setup log. Exit 0 listed; 3 none (OneDrive not signed in, or nothing synced yet);
#                           4 this terminal app was denied access (macOS "Operation not permitted"), or macOS
#                           is still asking; its NEXT: line says which and names the click. On a Mac whose
#                           config already syncs folders (see "Folders already synced" below) the list starts
#                           with "already synced on this Mac: N folder(s) ..." and each of them has
#                           "[synced] " before its path (one the list does not reach is printed first, as
#                           the config names it); a listed folder inside one of them has "[inside a
#                           synced folder] " there, and one that holds one "[contains a synced folder] "; its
#                           NEXT: names the command that keeps them. A list that exits 4 has that line and
#                           the marks too, for what it could list, and still ends on the click. With nothing
#                           to list, such a Mac gets that line, its synced folders and exit 0, not 3
#   --log-start AGENT       the setup prompt's friction log (see "Friction log" below): start an attempt;
#                           AGENT starts with the pasted prompt's version, and a copy of the prompt that is
#                           not this installer's is told to stop
#   --log STEP KIND WHAT FIX
#                           append one event to it; KIND is question, click, approval, deviation, error or prompt
#
# The config is $AGENTSYNC_CONFIG, else ~/agent-context/sources.toml.
#
# Folders already synced: a re-run on a Mac that is set up asks for no folder. The person chose them once, and
# they are in the config: the folder of each live local source (the inbox is agentsync's own folder and is
# not one of them). The installed agentsync reads them, with its own rule for a folder reached through a
# link (config.canonical_source_root), so --list-folders marks exactly the folders a sync reads: each of
# them, and every listed folder inside one (a sync reads the whole tree under a source; an exclude list is
# not read here). A listed folder that holds one has a mark of its own: adding it would read that tree twice.
# So an unmarked folder is one no sync reads, in whole or in part. A synced folder the list does not reach
# is printed first, so each one is named, also when nothing else is listed (OneDrive signed out) or a
# provider waits for a click. Without a
# config, or with one that holds no such source, --list-folders prints what it always did. A config the
# installed agentsync cannot read (or no installed agentsync) is a warning, and no folder is marked. A run
# with no --source-local over such a config keeps every source, updates agentsync, and runs status and a sync
# (steps 4 to 6); --source-local adds to them. Step 4 says which it was, in counts: one line, "folders: kept
# the N already synced (none added)", "folders: M added to the N already synced" or "folders: M added (none
# was synced before)", and its log line ends with kept=N added=M.
#
# Friction log: --log-start and --log only append to $AGENTSYNC_FRICTION_LOG (default
# ~/agent-context/setup/friction.md; the directory is made 0700 and the file 0600, under umask 077, and
# whatever else is in that default directory is made owner-only: see "Setup log"), which
# `agentsync setup-report` reads. Each must be the first argument and takes no other option; they need no uv
# and no agentsync, write no install.log line, no install.out and no report, and print one confirmation line.
#   --log-start AGENT  AGENT is 'prompt vN, TOOL': N is the version in the first line of the prompt the
#                      agent was given, TOOL its tool and model id. Appends "Attempt: <UTC>", "Prompt: vN"
#                      and "Agent: TOOL" lines. The version leads the value; its letters may be capitals,
#                      and a space, colon, semicolon or dash may stand where the comma does ("Prompt v8:
#                      TOOL" is read as the prompt writes it). When N is not this installer's
#                      SETUP_PROMPT_COMPAT the pasted copy is not the README's: it also appends a "step 1
#                      | error" line and the attempt's "end | finished" line, prints what to do (stop; the
#                      person copies the prompt again from README.md on the main branch, or, for a copy
#                      newer than this checkout that does not follow that branch, points the checkout at
#                      it: git, read-only, tells the two apart) and exits 2, so
#                      step 1's command stops before --list-folders. An AGENT that does not start with a
#                      version is a copy from before v8 and is logged as "Prompt: v7 or older". The
#                      message names no value that passes; its last sentence is for a current copy whose
#                      agent changed the value (run step 1's command again as the prompt writes it)
#   --log STEP KIND WHAT FIX
#                      appends "<UTC> | step STEP | KIND | WHAT | FIX" ("step 2" as STEP is read as 2; a STEP
#                      that is not a number, or "-", leaves the step column out and moves a non-number into
#                      WHAT). A KIND outside the six, or a count other than four, exits 2 and still appends
#                      an "error" line that names it and keeps what was given
# --report-only closes the attempt: it appends "<UTC> | end | finished" before writing the report, only when the
# last "Attempt:" has no such line (so running it twice adds one, and none after an attempt --log-start
# stopped and closed), and prints one confirmation line.
# Arguments are written as given (printf '%s'): nothing in them is expanded or run, so a backtick, $( ) or a
# typographic ’ is logged as text; a line break inside one becomes a space (one event per line).
#
# Steps, each skipped when already done:
#   1. uv in ~/.local/bin (the official installer, without touching shell profiles) unless one is on PATH
#   2. uv tool install agentsync from the checkout holding this script (a uv-managed Python 3.11; the system
#      trust store for TLS; UV_TOOL_BIN_DIR pinned to ~/.local/bin, so the binary sits where the guides say);
#      skipped when the last install came from this same clean checkout commit. With --force, uv rebuilds the
#      tool environment the background sync runs from: the package, its interpreter and the bundled pandoc
#      are missing for a moment, and a cycle already running meets a new build. So the reinstall holds that
#      sync's single-writer lock: <state_dir>/agentsync.lock of the config, read by the tool being replaced,
#      held with /usr/bin/lockf, which writes nothing in it.
#      It holds the lock of this run's config and of the config each LaunchAgent's plist names after --config
#      (they can differ).
#      It waits for a running sync, at most $AGENTSYNC_REINSTALL_WAIT_SECONDS (default 120 s, for all its locks
#      together; 0 never waits), with "agentsync: reinstalling after the background sync ends, still running, <N>s" lines.
#      A background sync that starts meanwhile and reaches its lock is skipped (exit 75); a launchd tick that
#      starts while the environment is being recreated ends in the launcher's SPAWN_FAILED (exit 71) or an
#      import error instead, and uv itself may fail on the half-removed environment, so a failed locked
#      install whose interpreter is gone runs once more. launchd starts the next sync at its interval. A sync
#      still running after the wait, or a lock lockf cannot take, leaves the reinstall as it was: not waited
#      for. A run killed with SIGKILL during the wait leaves lockf to run the reinstall once the lock frees,
#      still under the lock. install.log's step=agentsync line notes what the script saw:
#      waited-for-a-background-sync, wait-ran-out-after-<N>s or lock-not-taken-rc<N> (a sync that starts
#      after the probe is waited for too, with no note)
#   3. with --confirm-install-agent only: the signed launcher at ~/Applications/AgentSyncLauncher.app, built
#      with launcher/build.sh when developer tools exist (SIGN_IDENTITY passes through for a Developer ID
#      build), or copied from --launcher PATH; else a valid installed one is kept. An up-to-date one is never
#      rebuilt, because an ad-hoc rebuild is a new TCC identity and macOS would ask again (the developer
#      variable AGENTSYNC_REBUILD_LAUNCHER=1 rebuilds it anyway)
#      Then, with or without that option, a step of its own, logged `helpers` (done when the modules ran;
#      skipped, note no-devtools, without developer tools). When developer tools exist: the on-device OCR
#      helper (python -I -m agentsync.convert.ocr with the tool's interpreter; nothing else ever compiles it).
#      It prints one "OCR helper: ..." line; [convert] ocr = false builds nothing, and a helper that does not
#      build is that line, never a failed run. Without developer tools nothing is tried, and the line says so.
#      The media helper that reads recordings follows the same way (python -I -m agentsync.convert.media, one
#      "media helper: ..." line; [convert] recordings = false builds nothing), then the speech helper (python
#      -I -m agentsync.convert.speech, one "speech: ..." line; it fetches FluidAudio from github.com and may
#      take up to 20 minutes; the model folders are placed by hand, see the README)
#   4. agentsync add-source for each --source-local folder, else the flagless agentsync init: each creates
#      whatever is missing (sources.toml, the docs repo and its scaffold, the inbox, the state dir) and is
#      idempotent; opening the manifest migrates it (an upgrade may bring a newer schema). Each also makes
#      agentsync's own paths owner-only, as every sync does (what a coding agent wrote into the docs repo
#      under its own umask, the cache, the logs), so step 5 does not stop on a docs_repo.permissions [FAIL]
#      that step 6's sync would have cleared (field 2026-10-07: that run ended on a chmod no agent may run).
#      With several folders the two lines every add-source call ends on are printed once: "docs repo ..."
#      from the first call and "sources: ..." from the last
#   5. status: first the pandoc that status checks ([convert] pandoc_path, else the one bundled with the
#      tool) is started once, as "pandoc --version", outside that check's 60 s. The bundled one is an Intel
#      program: on Apple silicon macOS prepares each new copy at its first start (10 to 67 s measured, under
#      1 s after), and step 2 writes a new copy at every update, so the check ran out of time there and the
#      run exited 1 (rehearsal 2026-10-07). A slow start prints "pandoc: still running, <N>s (...)" lines,
#      then "pandoc: started after <N>s"; a quick one prints nothing; one with no answer in 300 s is stopped
#      and left to status. Then agentsync status (its TCC probe may raise the one-time "wants to access
#      files managed by" prompt). Any [FAIL] line stops steps 6-8 and the run exits 1, except the launcher's
#      own TCC_PENDING (a "tcc.<source>" line, only with --confirm-install-agent), which the wait (step 8)
#      asks the Allow for. A listing macOS holds for an Allow click in this terminal is a
#      source.<id>.listable [FAIL]: it stops them. Every [FAIL] line names its fix; for a check that ran out
#      of time the fix is this same command again, then what to do if the same line comes back
#   6. first-sync, whenever the config has a folder to sync (a [[source]] other than the inbox) and step 5 has
#      no [FAIL] that stops it: agentsync sync --once --materialise-budget 0 (a non-zero exit fails the run;
#      75, a cycle already running, skips): no downloads, so the files already on this Mac are converted now,
#      no online-only file is downloaded here and this step's time does not grow with the folders' size; each
#      later sync downloads and converts the online-only files it deferred, within its per-run budget. Its
#      output is shown as its "converted N, deferred M online-only[, read again R]" line(s), each prefixed
#      "first sync: " (and logged as the step's note converted-N-deferred-M, with -reread-R when R > 0 and
#      -waited-for-the-lock when another sync held the config's lock as it started), else as all it printed. The flag is passed unconditionally: sync --help hides it, and the agentsync
#      installed here always has it
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
#   Step 2's reinstall under a background sync's lock, the helper builds (step 3: "helpers: building the
#   <OCR|media|speech> helper, still running, <N>s"), steps 5, 6 and 8 and the closing status (see NEXT below)
#   print a progress line at least every 15 s, so a coding tool that stops a command which has printed nothing
#   for a while does not stop this one. Step 5's two waits keep one clock for that: a start of pandoc too
#   quick to print a line is counted in the first interval of status, which follows it (the one part with no
#   line of its own is reading the config for the pandoc path, 0.1 s as a rule and stopped at 20 s). A whole
#   run with --confirm-install-agent takes the first sync's time plus at most the 3-minute wait: give it a
#   10-minute command timeout (30 minutes while the speech helper is not built: its build alone can take up to
#   20 minutes). A re-run may spend up to 2 minutes of the first sync's time in step 2, waiting for a
#   background sync the first sync would otherwise wait for. A stopped run (SIGTERM, SIGINT, SIGHUP) still logs
#   its end (rc 143, 130, 129), writes the report and prints NEXT:. A stop of the whole process group during
#   step 3 also stops the helper builds' compilers within seconds.
#   9. report, at every exit after the arguments are read (failures and usage errors too) except in a dry run
#      or a --list-folders run: agentsync setup-report --out $AGENTSYNC_SETUP_REPORT (default
#      ~/agent-context/setup-report.md); when agentsync is missing or that fails, a shell report with the
#      same headings (machine facts, install.log, this run's doctor output, friction.md; the home path,
#      login name, full name, the organisation after a CloudStorage folder's app name (OneDrive-<org>) and the
#      --source-local folder names redacted, a $'...' word as <path>); when the report ends with its issue link
#      (https://github.com/renchris/agent-context-sync/issues/new?template=...), one line "issue link (review
#      the report first): <link>" follows, the last line before NEXT: (not for the --report-only of an
#      attempt --log-start stopped, see --report-only)
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
# then say the agent step below installs the LaunchAgents instead of naming a command). Step 9's setup-report
# gets AGENTSYNC_REPORT_RUN=<this run's id>: the run's NEXT: line comes after the report, which counts it as
# still to come.
#
# Setup prompt: SETUP_PROMPT_COMPAT (below) is the N of "setup prompt vN" in README.md ("Set up on a new Mac:
# one prompt"), whose step 1 requires `install.sh --version` to print exactly that number and hands it to
# --log-start. Bump both on ANY change to the prompt's text, a reworded sentence included, so that a copy
# is always known by its version: a saved copy that is no longer the README's stops at step 1 (--log-start
# above) instead of running old wording against a new installer, and the setup report says which version
# an attempt used. A prompt that starts to depend on new behaviour of this installer or of agentsync is
# such a change too. tests/test_install_oneshot.py checks that the two numbers match, and
# tests/test_deploy_pack.py holds the prompt's text to its version (a changed text with the old number
# fails there).
#
# Setup log: every real run (never a dry run or --report-only) appends to $AGENTSYNC_SETUP_LOG (default
# ~/agent-context/setup/install.log, directory 0700) one "start" line (compat, install.sh commit when the source
# is a checkout: "commit=<sha>" or, with local changes, "commit=<sha>-dirty tree=<fingerprint>", the
# fingerprint being the first 12 hex digits of the SHA-256 of `git diff HEAD` in the source, which reproduces
# it; the source, launchd=simulated under the test seam, the arguments), one line per step (UTC start, step, seconds,
# exit status, done / skipped / failed: uv, agentsync, launcher, helpers, config, status, first-sync, agent,
# wait; or list-folders alone, whose line ends with synced=N when it read the config: the folders already
# synced, 0 included; the config line ends with kept=N added=M when agentsync could count them: the folders
# synced before the step and the ones it added), then the report step's line, then one "end" line (exit
# status, total seconds, the report included). The report is written while a provisional end line is the log's
# last line, so it reads a
# finished run; that line is then replaced by the report step's line and the final end line (when another
# line followed it meanwhile, the report step's line is appended instead). `agentsync setup-report` reads it
# and redacts it. git runs read-only (GIT_OPTIONAL_LOCKS=0: not even the index's stat
# cache is rewritten). Every log write also sets the setup directory to 0700 and install.log, friction.md
# and install.out in it to 0600 (files made earlier with a looser mode included). The coding agent writes
# into that directory too, under its own umask: fix-request.md, and local-work/ with the patch of step 1's
# keep command. So as its last act every run but a dry run, --list-folders and --report-only included,
# clears group and other access on every file and folder in the default ~/agent-context/setup, and so do
# --log-start and --log (tighten_setup_tree: this user's regular files and folders only, no symlink
# followed; a directory $AGENTSYNC_SETUP_LOG names elsewhere is not walked).
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
# configuration invalid, 79 TCC pending (macOS waits for Allow), 80 TCC denied, and for a background run
# 64: the launcher refused the job (its program is not the one the launcher starts). --log-start and --log:
# 0 logged, 2 a usage error (a bad KIND or argument count, or a --log-start from a copy of the prompt
# that is not this installer's: see "Friction log"), 1 the friction log could not be written; neither
# prints a NEXT: line.
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
SETUP_PROMPT_COMPAT=14 # the README prompt's "setup prompt vN": bump both on any change to its text (see the header)
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
PROMPT_UNSTATED=7 # the last setup prompt whose --log-start named no version: a copy that names none is that or older
CANON_REPO="https://github.com/renchris/agent-context-sync"
PROMPT_SOURCE="README.md on the main branch of $CANON_REPO"
PROMPT_STOPPED="install.sh --log-start: the pasted setup prompt is" # how the error line of a stopped attempt starts
# The default setup folder 0700 and everything in it owner-only: DIR is touched only when it is
# ~/agent-context/setup, a real folder (not a symlink) that this user owns. The coding agent writes there
# under its own umask (fix-request.md with its file tool, local-work/ and its patch with step 1's git
# command), so those were readable by group and other inside a folder that was not (field 2026-10-07). Only
# group and other bits are cleared, on regular files and folders this user owns: no mode is widened, and a
# symlink is neither changed nor followed, so nothing outside the folder is reached. A setup log somewhere
# else ($AGENTSYNC_SETUP_LOG) is in a folder agentsync did not make: that one is not walked.
tighten_setup_tree() { # DIR
	[ "$1" = "$HOME/agent-context/setup" ] && [ -d "$1" ] && [ ! -L "$1" ] && [ -O "$1" ] || return 0
	chmod 700 "$1" 2>/dev/null || true
	/usr/bin/find "$1" -mindepth 1 \( -type f -o -type d \) -user "$EUID" -perm +077 \
		-exec chmod go-rwx {} + 2>/dev/null || true
}
# Append TEXT (whole lines) to the friction log: its directory 0700 when this creates it (or it is the default
# ~/agent-context/setup, whose contents are then made owner-only too), the file 0600; a file that does not
# end in a newline gets one first.
friction_append() {
	local f d
	f="$(friction_file)"
	d="${f%/*}"
	[ "$d" != "$f" ] || d="."
	umask 077
	if [ ! -d "$d" ]; then
		mkdir -p "$d" || return 1
	else
		tighten_setup_tree "$d"
	fi
	if [ -s "$f" ] && [ -n "$(tail -c 1 "$f" 2>/dev/null)" ]; then
		printf '\n' >>"$f" || return 1
	fi
	printf '%s' "$1" >>"$f" || return 1
	if [ -f "$f" ] && [ ! -L "$f" ] && [ -O "$f" ]; then chmod 600 "$f" 2>/dev/null || true; fi
}
# What to tell the person when the pasted copy is newer than this installer (B13 round 5 F17): step 1's
# git pull --ff-only has just run, so the checkout did not update to the README's version. Either the copy
# came from another branch or a fork, and copying it again from the README fixes it, or the checkout does not
# follow main of CANON_REPO (a fork or a stale mirror, another branch), and a fresh copy stops here again. This
# script's own checkout says which: its upstream branch and that remote's URL (origin's when there is no
# upstream), read with git, read-only, only with the developer tools (else /usr/bin/git offers to install
# them). Sets TELL, a clause that ends the sentence "Tell the person to ...", for stderr, and FIX, the same
# without the checkout's path or remote URL, for the friction line's fix column and the stopped NEXT, which
# reach the report (a fork owner's login or a company mirror's host is nothing the redaction knows). No "|"
# in either. Mutates nothing.
newer_copy_fix() {
	local src shown url="" u="" r=origin d canon=0 follows
	src="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)" || src=""
	shown="$src"
	# shellcheck disable=SC2088 # the ~ is printed
	case "$shown" in "$HOME"/*) shown='~/'"${shown#"$HOME"/}" ;; esac
	d="$(/usr/bin/xcode-select -p 2>/dev/null || true)"
	if [ -n "$src" ] && [ -n "$d" ] && [ -d "$d" ]; then
		u="$(GIT_OPTIONAL_LOCKS=0 /usr/bin/git -C "$src" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
		[ -z "$u" ] || r="${u%%/*}"
		url="$(GIT_OPTIONAL_LOCKS=0 /usr/bin/git -C "$src" remote get-url "$r" 2>/dev/null || true)"
	fi
	url="$(printf '%s' "$url" | LC_ALL=C sed -E 's#^([A-Za-z+]+://)[^/@]*@#\1#')" # no user or token
	case "$(printf '%s' "${url%/}" | LC_ALL=C tr '[:upper:]' '[:lower:]')" in
	"${CANON_REPO#https://}" | "$CANON_REPO" | "$CANON_REPO.git" | git@github.com:renchris/agent-context-sync | \
		git@github.com:renchris/agent-context-sync.git | ssh://git@github.com/renchris/agent-context-sync | \
		ssh://git@github.com/renchris/agent-context-sync.git) canon=1 ;;
	esac
	if [ "$canon" -eq 1 ] && [ -n "$u" ] && [ "${u#*/}" = main ]; then
		TELL="copy the prompt again from $PROMPT_SOURCE (\"Set up on a new Mac: one prompt\") and paste it into a new session: this Mac's checkout follows that branch, so a copy newer than it came from another branch or a fork"
		FIX="$TELL"
	elif [ -n "$url" ] && { [ "$canon" -eq 0 ] || [ -n "$u" ]; }; then
		# a remote that is not the repository, or a branch of it that is not main: the checkout is the cause
		if [ -n "$u" ]; then follows="$u of $url"; else follows="$url (no upstream branch)"; fi
		FIX="point this Mac's checkout at main of $CANON_REPO (it follows another remote or branch, so a fresh copy of the prompt would stop here again), or move the checkout aside so step 1 clones it, then paste the same prompt into a new session"
		TELL="point this Mac's checkout ($shown) at main of $CANON_REPO (it follows $follows, so a fresh copy of the prompt would stop here again), or move the checkout aside so step 1 clones it, then paste the same prompt into a new session"
	else
		follows="does not follow main of $CANON_REPO (git could not say which branch it follows): point it there, or move it aside so step 1 clones it, then paste the prompt again"
		FIX="copy the prompt again from $PROMPT_SOURCE (\"Set up on a new Mac: one prompt\") and paste it into a new session; if that copy stops here too, this Mac's checkout $follows"
		TELL="copy the prompt again from $PROMPT_SOURCE (\"Set up on a new Mac: one prompt\") and paste it into a new session; if that copy stops here too, this Mac's checkout ($shown) $follows"
	fi
}
friction_cmd() { # OPTION ARGS...: the friction-log options; their exit status
	local op="$1" now line step kind what fix note="" v a p rest said rc=0 TELL FIX
	shift
	now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	case "$op" in
	--log-start)
		if [ $# -ne 1 ]; then
			printf "usage error: --log-start takes one argument, 'prompt vN, <your tool and model id>', and no other option (see --help)\n" >&2
			return 2
		fi
		v="${1//$'\r'/ }"
		v="${v//$'\n'/ }"
		p=""
		case "$v" in
		[Pp][Rr][Oo][Mm][Pp][Tt]\ [Vv][0-9]*)
			# 'prompt vN, TOOL': the version of the copy of the prompt the agent was given. The version
			# leads. An agent that wrote "Prompt v8: TOOL" or "prompt v8 - TOOL" gave it all the same.
			rest="${v:8}"
			p="${rest%%[!0-9]*}"
			rest="${rest#"$p"}"
			case "$rest" in
			'' | [\ ,:\;-]*) ;; # the number ends the value, or a separator follows it
			*) p="" ;;           # "prompt v8x": no version
			esac
			[ "${#p}" -le 3 ] || p="" # four digits: no version
			if [ -n "$p" ]; then
				while :; do
					case "$rest" in
					[\ ,:\;-]*) rest="${rest#?}" ;;
					*) break ;;
					esac
				done
				v="$rest"
			fi
			;;
		esac
		if [ "$p" = "$SETUP_PROMPT_COMPAT" ]; then
			friction_append "Attempt: $now"$'\n'"Prompt: v$SETUP_PROMPT_COMPAT"$'\n'"Agent: ${v:-unknown}"$'\n' || rc=1
			[ "$rc" -ne 0 ] || printf 'friction log: attempt started in %s\n' "$(friction_file)"
		else
			# Not this installer's prompt. The attempt is logged with the version the copy gave, so the
			# setup report says so, and the exit status stops step 1's command before it lists the folders.
			said="v${p:-$PROMPT_UNSTATED or older}"
			what="the pasted copy is not the current one"
			[ -z "$p" ] || [ "$p" -lt "$SETUP_PROMPT_COMPAT" ] || what="this checkout is older than the prompt"
			what="$said and this installer is for setup prompt v$SETUP_PROMPT_COMPAT: $what"
			fix="copy the prompt again from $PROMPT_SOURCE"
			TELL="$fix (\"Set up on a new Mac: one prompt\") and paste it into a new session"
			if [ -n "$p" ] && [ "$p" -gt "$SETUP_PROMPT_COMPAT" ]; then
				newer_copy_fix
				fix="$FIX"
			fi
			# The attempt is closed here: the agent is told to run no other step, so an agent that obeys runs
			# no --report-only (an older copy's step 3 may still run one, and that report reads this closed
			# attempt), and a later session's line must not join an attempt left open.
			friction_append "Attempt: $now"$'\n'"Prompt: $said"$'\n'"Agent: ${v:-unknown}"$'\n'"$now | step 1 | error | $PROMPT_STOPPED $what; setup stopped (exit 2) | $fix"$'\n'"$now | end | finished"$'\n' || true
			# The last sentence is for a current copy whose agent changed the value. It names no value that
			# passes: an old copy's agent that read one here used it, and ran its old wording after all.
			printf 'error: the pasted setup prompt is %s. Stop here and run no other step of that prompt. Tell the person to %s. If the first line of your prompt says "setup prompt v%s", the --log-start value was changed: run step 1'\''s command again exactly as the prompt writes it.\n' "$what" "$TELL" "$SETUP_PROMPT_COMPAT" >&2
			rc=2
		fi
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
# Whether the attempt the setup report judges is one --log-start stopped (a copy of the prompt that is not
# this installer's): the friction log's last attempt holds that error line. The attempt ends where
# `agentsync setup-report` ends it (parse_friction), so this NEXT and the report's Summary are of the same
# attempt: a step 1 error dated more than NEW_SESSION_GAP seconds after its end line is another session's,
# whose step 1 stopped before --log-start, and that one is not a stopped copy. Any other line after the end
# line is a late line of the stopped session (an old copy logs the failed command, then writes its report).
# When it is, it prints the fix that stop logged (its line's last column), for the NEXT (B13 round 5 F17).
NEW_SESSION_GAP=600 # setup_report._NEW_SESSION_GAP, in seconds
# awk: secs(T), "2026-10-06T16:42:00Z" as seconds on a day count (to subtract two of them), and the start of
# a step 1 error line, which after an end line may open a header-less attempt (parse_friction). track(LINE),
# called on every line first, keeps the open attempt's prompt number PV, agent AG and latest time LT, and is 1
# for a step 1 error that states "prompt vN, <agent>:" words another session's: after an Attempt: or Prompt:
# header (HD) always, since a prefixed line is written only before --log-start, so after a header it is
# always another session's (B13 round 5 F23); after a header-less attempt, a version or agent that differs
# from its words, or a time more than gap after its latest, end line or not (B13 round 4 Q21).
# restate(LINE) gives an attempt a step 1 error opened the words that line states.
FRICTION_AWK='
		function secs(t,    y, m) { # "2026-10-06T16:42:00Z": seconds on a day count, to subtract two of them
			y = substr(t, 1, 4) + 0; m = substr(t, 6, 2) + 0
			if (m <= 2) { y--; m += 12 }
			return (365 * y + int(y / 4) - int(y / 100) + int(y / 400) + int((153 * (m - 3) + 2) / 5) + substr(t, 9, 2)) * 86400 \
				+ substr(t, 12, 2) * 3600 + substr(t, 15, 2) * 60 + substr(t, 18, 2)
		}
		function agent_key(a) { # setup_report._agent_key: ASCII lower case, quotes dropped (curly ones too), blanks collapsed
			gsub("\342\200[\230\231\234\235]", " ", a)
			a = tolower(a); gsub("[\"\047*]", " ", a); gsub("[ \t]+", " ", a); sub("^ ", "", a); sub(" $", "", a)
			return a
		}
		function stated(line,    w, a, c) { # sets SV and SA from "prompt vN, <agent>:" at the start of the what-text
			w = line; sub("^[^|]*[|][^|]*[|][^|]*[|][ \t]*", "", w)
			while (match(w, "^([\"\047 \t]|\342\200[\230\231])")) w = substr(w, RLENGTH + 1)
			if (tolower(substr(w, 1, 8)) != "prompt v" || !match(substr(w, 9), "^[0-9][0-9]?[0-9]?")) return 0
			SV = substr(w, 9, RLENGTH) + 0; w = substr(w, 9 + RLENGTH)
			if (!match(w, "^[ ,:;-]+")) return 0
			w = substr(w, RLENGTH + 1)
			if (!match(w, "^[^: \t][^:]*:")) return 0
			a = substr(w, 1, RLENGTH - 1); sub("[ \t]+$", "", a)
			# _STEP1_STATED_RE reads at most 100 characters of agent: UTF-8 ones, so continuation bytes do not count
			c = a; gsub("[\200-\277]", "", c)
			if (length(c) > 100) return 0
			SA = agent_key(a); return 1
		}
		function restate(line) { HD = 0; PV = ""; AG = ""; if (stated(line)) { PV = SV; AG = SA } }
		function track(line,    a, new) {
			if (line ~ /^Attempt:/) { SEEN = 1; HD = 1; PV = ""; AG = ""; LT = substr(line, 10, 20); return 0 }
			if (line ~ /^[-* ]*Prompt:/) { SEEN = 1; HD = 1; if (PV == "" && match(line, "[0-9]+")) PV = substr(line, RSTART, RLENGTH) + 0; return 0 }
			if (line ~ /^[-* ]*Agent:/) { # a no-break space around it goes, as parse_friction strips the value
				SEEN = 1; a = line; sub("^[-* ]*Agent:", "", a); gsub("^([ \t]|\302\240)+|([ \t]|\302\240)+$", "", a)
				if (AG == "") AG = agent_key(a); return 0
			}
			if (line !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z [|]/) return 0
			new = 0
			if (!SEEN) { SEEN = 1; if (line ~ s1) restate(line) }
			else if (line ~ s1 && (PV != "" || AG != "") && stated(line)) {
				if (HD || (PV != "" && SV != PV) || (AG != "" && SA != AG) || (LT != "" && secs(line) - secs(LT) > gap)) new = 1
			}
			if (new) { HD = 0; PV = SV; AG = SA; LT = substr(line, 1, 20) }
			else if (LT == "" || secs(line) > secs(LT)) LT = substr(line, 1, 20)
			return new
		}
'
STEP1_ERROR_RE='^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z [|] step 1 [|] error [|]'
friction_attempt_stopped() {
	local f
	f="$(friction_file)"
	[ -f "$f" ] || return 1
	LC_ALL=C awk -v mark="| step 1 | error | $PROMPT_STOPPED " -v gap="$NEW_SESSION_GAP" -v s1="$STEP1_ERROR_RE" "$FRICTION_AWK"'
		{ q = track($0) }
		/^Attempt:/ { stopped = 0; ended = ""; next }
		q { stopped = 0; ended = ""; next }
		/\| end \| finished[[:space:]]*$/ { ended = substr($0, 1, 20); next }
		ended != "" && $0 ~ s1 && secs($0) - secs(ended) > gap { stopped = 0; ended = ""; restate($0); next }
		index($0, mark) { stopped = 1; fx = $0; sub(/.* [|] /, "", fx) }
		END { if (stopped) print fx; exit !stopped }' "$f" 2>/dev/null
}
# --report-only: close the friction log's current attempt with "<UTC> | end | finished", only when its last
# "Attempt:" has no end line yet (so a second --report-only adds none). No log or no attempt: nothing to close.
friction_close_attempt() {
	local f
	f="$(friction_file)"
	[ -f "$f" ] || return 0
	LC_ALL=C awk '/^Attempt:/ { open = 1; ended = 0; next } /\| end \| finished[[:space:]]*$/ { ended = 1 }
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
# This script, for the commands NEXT and re-run lines name: ~/... under this home, as the README's approval
# rules write it (Bash(~/src/agent-context-sync/scripts/install.sh *)), else absolute. The ~ stays outside
# %q, which would write it as \~, a path that never expands (B13 round 4 Q7), and so does the slash after it:
# bash expands a ~ only before an unquoted /, and %q writes a name with a byte it finds unprintable (an accent
# under C, a curly apostrophe) as $'..', which would hold the slash (B13 round 5 F53).
SELF="$here/$(basename "${BASH_SOURCE[0]}")"
# shellcheck disable=SC2088 # the ~ is printed, for the reader's shell to expand
case "$SELF" in
"$HOME"/*) SELF='~/'"$(printf '%q' "${SELF#"$HOME"/}")" ;;
*) SELF="$(printf '%q' "$SELF")" ;;
esac

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
REINSTALL_WAIT="${AGENTSYNC_REINSTALL_WAIT_SECONDS:-120}" # step 2's wait for a running background sync
LOCKF="/usr/bin/lockf"
# a progress line at least this often in step 2's wait, the helper builds and steps 5, 6 and 8
PROGRESS_SECONDS="${AGENTSYNC_PROGRESS_SECONDS:-15}"
LIST_TIMEOUT="${AGENTSYNC_LIST_TIMEOUT:-90}"
LIST_TOTAL="${AGENTSYNC_LIST_TOTAL_SECONDS:-100}"
POLL_LABEL="com.agentsync.poll"
RECONCILE_LABEL="com.agentsync.reconcile"
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
# COMMIT and DIRTY from the checkout $1; both stay as they are when git cannot say.
read_commit() {
	local c
	c="$(checkout_commit "$1")"
	case "$c" in
	"") ;;
	*" dirty "*)
		COMMIT="${c%% *}-dirty" # "-dirty" keeps a changed tree from ever matching the install stamp
		DIRTY="${c##* }"
		;;
	*) COMMIT="$c" ;;
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
# A signal's exit (128 + N) that no line above names reads "stopped by SIG<NAME> (signal N)", the phrase
# ocr._ended gives a converter that a signal stopped (B13 round 3 R81).
rc_meaning() {
	local n=""
	case "$1" in
	0) printf 'ok' ;;
	1) printf 'failed' ;;
	2) printf 'usage error' ;;
	3) printf 'the wait for the first background run ran out' ;;
	64) printf 'the launcher refused the job: its program is not the one it starts, or an option is wrong' ;;
	75) printf 'lock busy: another agentsync cycle is running' ;;
	77) printf 'sign-in required' ;;
	78) printf 'configuration invalid' ;;
	79) printf 'TCC pending: macOS is waiting for Allow' ;;
	80) printf "TCC denied: an earlier \"Don't Allow\"" ;;
	129) printf 'stopped by SIGHUP' ;;
	130) printf 'stopped by SIGINT' ;;
	143) printf 'stopped by SIGTERM (a tool timeout?)' ;;
	*)
		case "$1" in 129 | 1[3-8][0-9] | 19[0-2]) n="$(kill -l "$(($1 - 128))" 2>/dev/null || true)" ;; esac
		if [ -n "$n" ]; then printf 'stopped by SIG%s (signal %s)' "${n#SIG}" "$(($1 - 128))"; else printf 'exit %s' "$1"; fi
		;;
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
# Run a command, printing "LABEL: still running, <N>s" every $PROGRESS_SECONDS while it runs (a coding tool
# may stop a command that prints nothing for a while); its exit status. A wait that may print nothing and
# is followed by this one (start_pandoc_once, before status) leaves the time of its last line, or of its
# start, in $PROGRESS_SINCE: the first interval here is counted from it, once, so the two are never silent
# for an interval each.
PROGRESS_SINCE=""
with_progress() {
	local label="$1" pid rc=0 t0=$SECONDS last="${PROGRESS_SINCE:-$SECONDS}"
	PROGRESS_SINCE=""
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
# Print "LINE, still running, <N>s" every $PROGRESS_EVERY s until killed, for the command the caller runs in
# the foreground meanwhile (a stopped run still lets the one under way end before it exits, so a re-run never
# meets a second build in the same tree, or a second reinstall). A helper build's LINE names the helper
# ("helpers: building the speech helper"): the speech helper's first build takes minutes after the OCR and
# media lines said ready, and ends in "speech: off" while its models are not placed. TICKER is its pid;
# on_exit kills it too.
# The sleep holds no output open, so a killed ticker never keeps a reader of this run's output waiting.
TICKER=""
ticker() { # LINE
	local t0=$SECONDS
	while sleep "$PROGRESS_EVERY" >/dev/null 2>&1 && kill -0 "$$" 2>/dev/null; do
		say "$1, still running, $((SECONDS - t0))s"
	done
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
# TOKEN: whether a part of the full name is a two-letter name to register alone: a capital first (a particle
# such as "de" names no one), no word of setup_report._SHORT_WORDS in any case, and no _SHORT_CAPS abbreviation
# written in upper case ("Ai" is a name, "AI" is not).
short_name() {
	local low
	case "$1" in
	[[:upper:]][[:alpha:]]) ;;
	*) return 1 ;;
	esac
	low="$(printf '%s' "$1" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
	case "$low" in
	am | an | as | at | be | by | do | go | he | hi | if | in | is | it | me | my | no | of | ok | on | or | so | to | up | us | we | cc | co | re | dr | jr | mr | ms | sr) return 1 ;;
	esac
	case "$1" in
	[[:upper:]][[:upper:]])
		case "$low" in
		ad | ai | ca | ci | db | eu | gb | hr | id | io | ip | kb | mb | os | ou | pc | pm | pr | qa | rc | tb | tz | uk | ui | uv | ux | vm | vs | ii | iv | vi) return 1 ;;
		esac
		;;
	esac
}
# WORD (lower case): whether a full name's parts run together spell a word the report prints,
# setup_report._JOINED_WORDS (a test keeps them equal): name_forms then registers that join with a CamelCase
# boundary only ("HoMe"), never as the word in one case ("home", "HOME"; B13 round 4 Q72).
joined_word() {
	case "$1" in
	above | added | adds | after | again | agent | alias | allow | alone | alpha | also | back | bare | before | below | beyond | budget | build | built | bytes | canary | cannot | case | change | check | class | click | clicks | client | cloud | code | column | commit | config | copy | could | count | counts | cycle | data | detail | differ | docs | doctor | does | domain | done | draft | each | edit | else | email | empty | ended | ends | engine | entry | error | event | events | every | exists | exit | exited | fail | failed | fails | field | fields | file | files | first | fixed | folder | form | found | from | full | fully | gave | github | gives | gone | graph | group | hash | have | head | header | held | helper | here | holds | home | host | human | image | inbox | inside | into | issue | item | items | itself | keeps | kept | kind | kinds | label | last | later | latest | layer | leaves | left | legacy | letter | like | limit | line | lines | link | list | listed | live | loaded | local | logged | login | logs | long | looked | loop | lower | made | main | many | media | mirror | mode | module | more | most | name | named | names | never | newest | next | none | note | number | older | once | ones | online | only | open | option | order | origin | other | output | over | page | pages | part | parts | past | path | paths | person | policy | prints | probe | prompt | proxy | purge | queue | queued | read | reads | ready | real | reason | record | report | reread | result | round | rows | rule | runs | same | says | scan | second | select | serial | setup | shape | shell | short | shown | shows | signal | since | source | stable | stage | start | starts | state | stated | status | step | steps | still | stub | such | suffix | sync | synced | table | text | than | that | their | them | then | there | these | they | this | those | time | turns | type | types | under | used | user | value | values | wait | waits | walk | warn | were | what | when | where | which | whole | whose | with | word | words | worked | would | write | writes | wrote) return 0 ;;
	esac
	return 1
}
# A folder name every OneDrive or Google Drive has: setup_report._GENERIC_FOLDERS (a test keeps them equal). A
# configured one is still redacted, as the Python report does, but only as a whole word.
generic_folder() {
	case "$1" in
	Apps | Attachments | Desktop | Documents | Downloads | "Microsoft Copilot Chat Files") return 0 ;;
	"Microsoft Teams Chat Files" | Music | "My Drive" | Notebooks | "Personal Vault" | Pictures) return 0 ;;
	Recordings | Shared | "Shared Documents" | "Shared drives" | Videos) return 0 ;;
	esac
	return 1
}
# A proxy host with no dot that names no one and that the report prints as a word of its own (HTTPS_PROXY,
# network.proxy): setup_report._GENERIC_PROXY_HOSTS (a test keeps them equal). Matched only where a URL names it.
generic_proxy_host() {
	case "$(printf '%s' "$1" | LC_ALL=C tr '[:upper:]' '[:lower:]')" in
	proxy | webproxy | wpad | localhost | gateway | firewall | squid | cache | http | https) return 0 ;;
	direct | none | off | network | host) return 0 ;;
	esac
	return 1
}
# The proxy values in the config files git reads, one a line: the files setup_report._git_proxy_urls reads,
# the global ones and the repository config of two checkouts (checkout_git_configs, a linked worktree too):
# the one holding this script, which agentsync is installed from, and the one the prompt clones. Read as files: the logged git error names git's proxy, and step 1 may have stopped
# with no git. An empty "proxy =" (git's off switch) prints nothing.
git_proxy_values() {
	local g
	{
		printf '%s\n' "$HOME/.gitconfig" "${XDG_CONFIG_HOME:-$HOME/.config}/git/config" /etc/gitconfig
		[ -z "${repo:-}" ] || checkout_git_configs "$repo"
		checkout_git_configs "$HOME/src/agent-context-sync"
	} | while IFS= read -r g; do
		# An unreadable file is skipped: under set -e a failed sed would end the caller's whole list (the
		# LaunchAgent's proxies after it went unread).
		[ ! -f "$g" ] ||
			LC_ALL=C sed -n 's/^[[:space:]]*[Pp][Rr][Oo][Xx][Yy][[:space:]]*=[[:space:]]*"\{0,1\}\([^"[:space:];#][^"[:space:];#]*\).*/\1/p' "$g" ||
			true
	done 2>/dev/null
}
# Source ids that are agentsync's own words (setup_report._GENERIC_IDS; a test holds the two equal).
GENERIC_SOURCE_IDS="agent agentsync archive calendar chats config docs drive files graph inbox local mail mirror notes"
GENERIC_SOURCE_IDS="$GENERIC_SOURCE_IDS onedrive sharepoint source sources status sync teams"
# "value<TAB>placeholder<TAB>w" lines (w: whole words only; t: not followed by a word character, a dot or a
# hyphen) for redact_stream; the %q form too, since install.log quotes paths that way.
redaction_pairs() {
	local cs="$HOME/Library/CloudStorage" n=0 e org f rel part name core tok user mdm line w take u h v pac seen i whole orgs_seen c kept
	local port k
	local -a known=(${FOLDERS[@]+"${FOLDERS[@]}"}) words toks segs loose=()
	# --report-only names no folder: the ones earlier runs added are in sources.toml and in install.log's args
	# (written with printf %q, which read without -r undoes; a $'..' form is unquoted by the loop after it).
	# LC_ALL=C: under a UTF-8 locale BSD sed stops at the first byte that is not UTF-8 and every later line is
	# lost. Both install.log loops hand each argument, unquoted, to logged_source_local. An argument is placed
	# as that run placed it ("~/..." is the home); install.log has no working directory, so a relative one is
	# placed only when it names CloudStorage or one of its entries, else it is shown as <path>.
	logged_source_local() {
		local rel part
		case "$1" in
		\~) known+=("$HOME") ;;
		\~/*) known+=("$HOME/${1#\~/}") ;;
		/*) known+=("$1") ;;
		*Library/CloudStorage/*) known+=("$cs/${1#*Library/CloudStorage/}") ;;
		*)
			rel="${1#./}" # the word as logged stays the loose one: install.log shows "./Name"
			part="${rel%%/*}"
			# "$cs/.." always exists, so a "." or ".." first part ("../docs", "././Notes") would be placed in
			# CloudStorage and its bare last word registered: it is loose, shown as <path>.
			case "$part" in
			"" | . | ..) loose+=("$1") ;;
			*) if [ -e "$cs/$part" ]; then known+=("$cs/$rel"); else loose+=("$1"); fi ;;
			esac
			;;
		esac
	}
	if [ -f "$CONFIG" ]; then
		while IFS= read -r line; do
			known+=("$line")
		done < <(LC_ALL=C sed -n 's/^[[:space:]]*path[[:space:]]*=[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$CONFIG" 2>/dev/null)
	fi
	if [ -f "$SETUP_LOG" ]; then
		while IFS= read -r line; do
			# shellcheck disable=SC2162 # backslashes are the %q escapes to undo
			read -a words <<<"$line" || true
			take=0
			for w in ${words[@]+"${words[@]}"}; do
				if [ "$take" -eq 1 ]; then
					case "$w" in
					\$\'*) ;; # read split it at its spaces, still quoted: the loop after this one unquotes it whole
					*) logged_source_local "$w" ;;
					esac
				fi
				[ "$w" = --source-local ] && take=1 || take=0
			done
		done < <(LC_ALL=C sed -n 's/.* start install\.sh .* args=\(.*--source-local.*\)$/\1/p' "$SETUP_LOG" 2>/dev/null | LC_ALL=C sort -u)
		# A $'..' word, which read splits and leaves quoted: its bytes. bash 3.2's %q writes one for a byte it
		# finds unprintable (under UTF-8 a curly apostrophe, a dash, an NFD accent, most CJK; under C any
		# non-ASCII name). sed undoes the escaped quote, which %b keeps; %b undoes the rest.
		while IFS= read -r line; do
			line="${line#--source-local \$\'}"
			logged_source_local "$(printf '%b' "${line%\'}")"
		done < <(LC_ALL=C sed -n 's/.* start install\.sh .* args=\(.*--source-local.*\)$/\1/p' "$SETUP_LOG" 2>/dev/null |
			LC_ALL=C grep -oE -e "--source-local \\\$'([^'\\\\]|\\\\.)*'" | LC_ALL=C sed "s/\\\\'/'/g" | LC_ALL=C sort -u)
	fi
	emit_pair() { # VALUE PLACEHOLDER [w] [utf8]: the pair and its printf %q form; with utf8, %q in UTF-8 too
		printf '%s\t%s\t%s\n' "$1" "$2" "${3:-}"
		local q u
		q="$(printf '%q' "$1")"
		[ "$q" = "$1" ] || printf '%s\t%s\t%s\n' "$q" "$2" "${3:-}"
		# install.log's %q is the logging run's locale: under UTF-8 a non-ASCII word is plain ("Café\ Notes"),
		# under C a $'..' word, which redact_stream makes <path> first. So a report written under C needs the
		# UTF-8 form too (B13 R111). bash 3.2 ignores a prefix assignment for a builtin: set inside the $(..).
		[ -n "${4:-}" ] || return 0
		u="$(LC_ALL=en_US.UTF-8 && printf '%q' "$1")"
		[ "$u" = "$1" ] || [ "$u" = "$q" ] || printf '%s\t%s\t%s\n' "$u" "$2" "${3:-}"
	}
	add_pair() { # VALUE PLACEHOLDER [w]: add_forms for a value of 3 or more characters
		[ "${#1}" -ge 3 ] || return 0
		add_forms "$@"
	}
	add_forms() { # VALUE PLACEHOLDER [w]: emit_pair for the value as written, in NFC and in NFD, as the Python
		# report matches it: a Finder-made name is NFD on disk and an agent types it NFC, or the reverse.
		# iconv's UTF-8-MAC is macOS's decomposed form; a value that is not UTF-8 adds no other form. Printable
		# ASCII has one form: listed, not a range, which a UTF-8 locale would collate (no iconv run for it).
		# Any length: only a two-letter name part, which short_name screens, comes here without add_pair.
		local v nfc="" nfd="" bare ascii='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
		ascii+=' !"#$%&'\''()*+,./:;<=>?@[\]^_`{|}~-'
		case "$1" in *[!"$ascii"]*) ;; *) emit_pair "$@" && return 0 ;; esac
		emit_pair "$1" "$2" "${3:-}" utf8
		nfd="$(printf '%s' "$1" | LC_ALL=C /usr/bin/iconv -f UTF-8 -t UTF-8-MAC 2>/dev/null)" || nfd=""
		nfc="$(printf '%s' "$1" | LC_ALL=C /usr/bin/iconv -f UTF-8-MAC -t UTF-8 2>/dev/null)" || nfc=""
		# And without its accents ("Cafe Plans" for "Café Plans", B13 R115): the NFD form without its combining
		# marks, U+0300-U+036F (bytes CC 80-BF, CD 80-AF). Text that mixes NFC and NFD letters is the Python
		# report's alone: a value with k accents would need 2^k pairs here.
		bare="$(printf '%s' "$nfd" | LC_ALL=C sed "s/$(printf '\314')[$(printf '\200')-$(printf '\277')]//g;s/$(printf '\315')[$(printf '\200')-$(printf '\257')]//g")"
		[ "$bare" != "$nfd" ] || bare=""
		[ "$nfc" != "$nfd" ] || nfc=""
		for v in "$nfd" "$nfc" "$bare"; do
			[ -z "$v" ] || [ "$v" = "$1" ] || emit_pair "$v" "$2" "${3:-}" utf8
		done
	}
	recase() { # upper|lower TEXT, as Python's str.upper and str.lower write it ("ÅL" for "Ål"): tr in UTF-8,
		# byte-wise for TEXT that is not UTF-8 (where tr in UTF-8 stops). bash 3.2 has no ${x^^}.
		local from='[:lower:]' to='[:upper:]' out
		[ "$1" = upper ] || { from='[:upper:]' && to='[:lower:]'; }
		out="$(printf '%s' "$2" | LC_ALL=en_US.UTF-8 tr "$from" "$to" 2>/dev/null)" && printf '%s' "$out" && return 0
		printf '%s' "$2" | LC_ALL=C tr "$from" "$to"
	}
	capital() { # WORD: its first letter as written, the rest lower case ("Doe" for "DOE"), as str.capitalize
		printf '%s%s' "${1:0:1}" "$(recase lower "${1:1}")"
	}
	name_forms() { # $@: words, joined by a space, -, _ or nothing; as written, lower and upper case, and each
		# word capitalised ("Jane Li" for "LI, JANE", as the Python report's case-insensitive forms match): <name>
		local s v c x
		local -a caps=()
		for x in "$@"; do caps+=("$(capital "$x")"); done
		for s in ' ' - _ ''; do
			v=""
			c=""
			for x in "$@"; do v="${v:+$v$s}$x"; done
			for x in "${caps[@]}"; do c="${c:+$c$s}$x"; done
			if [ -z "$s" ] && [ "$#" -ge 2 ] && joined_word "$(recase lower "$v")"; then
				# run together they spell a report word: only a CamelCase join is the name (B13 round 4 Q72)
				for x in "$v" "$c"; do
					case "$x" in *[[:lower:]][[:upper:]]*) add_pair "$x" "<name>" w ;; esac
				done
				continue
			fi
			add_pair "$v" "<name>" w
			add_pair "$(recase lower "$v")" "<name>" w
			add_pair "$(recase upper "$v")" "<name>" w
			add_pair "$c" "<name>" w
		done
	}
	if [ -n "${TMPDIR:-}" ] && [ -d "$TMPDIR" ]; then # this account's per-user temp folder links reports
		add_pair "$(cd "$TMPDIR" && pwd -P)" "<tmp>"
		add_pair "${TMPDIR%/}" "<tmp>"
	fi
	add_pair "$HOME" "~"
	for w in ${loose[@]+"${loose[@]}"}; do # only as the argument: a short relative name is also a common word
		v="$(printf '%q' "$w")"
		printf '%s\t%s\t\n' "--source-local $v" "--source-local <path>"
		u="$(LC_ALL=en_US.UTF-8 && printf '%q' "$w")" # as a UTF-8 run logged it (emit_pair, B13 R111)
		[ "$u" = "$v" ] || printf '%s\t%s\t\n' "--source-local $u" "--source-local <path>"
	done
	if [ -d "$cs" ]; then
		# The organisation, team or account after the app's name ("<app>-<domain>", as setup_report._org_of
		# reads it): not a bare app name, Personal, Business or Work, nor a name repeating the app's (Box-Box).
		# An account's email address too (GoogleDrive-<address>): the shell has no email pattern. An organisation
		# already registered takes no second number (OneDrive-X and OneDrive-SharedLibraries-X; B13 round 3
		# R44). Only the same bytes: the matcher is case-sensitive, so another spelling keeps its own pair.
		orgs_seen=$'\n'
		for e in "$cs"/*; do
			[ -e "$e" ] || continue
			e="$(basename "$e")"
			case "$e" in
			*-*) ;;
			*) continue ;;
			esac
			org="${e#*-}"
			org="${org#SharedLibraries-}"
			case "$org" in
			"${e%%-*}" | "" | [Pp]ersonal | [Pp]ersonal\ * | [Bb]usiness | [Ww]ork) continue ;;
			esac
			case "$orgs_seen" in *$'\n'"$org"$'\n'*) continue ;; esac
			orgs_seen+="$org"$'\n'
			n=$((n + 1))
			add_pair "$org" "<org-$n>"
		done
	fi
	n=0
	for f in ${known[@]+"${known[@]}"}; do
		case "$f" in
		"$cs"/*/*) rel="${f#"$cs"/*/}" ;;
		*) continue ;;
		esac
		while [ -n "$rel" ]; do
			part="${rel%%/*}"
			[ "$part" = "$rel" ] && rel="" || rel="${rel#*/}"
			n=$((n + 1))
			whole=""
			if generic_folder "$part"; then whole=w; fi # "Documents" is kept inside "MyDocuments"
			add_pair "$part" "<folder-$n>" "$whole"
		done
	done
	# Every configured source id, as <source-N> by its [[source]] table's place (B13 round 5 F6): an id is its
	# folder's name in slug form ("project-beta---general"), and the empty-folder wait an agent quotes names it.
	# A whole word, where "-" is no word character, so "exclude-empty <id>" is matched. An id that is one of
	# agentsync's own words is kept unless its source is a cloud folder (setup_report._GENERIC_IDS), so "the
	# docs repo" and "inbox" stay readable. Only ids inside [[source]] tables: the graph and policy ones are not.
	if [ -f "$CONFIG" ]; then
		while IFS=$'\t' read -r i v f; do
			case " $GENERIC_SOURCE_IDS " in
			*" $v "*) case "$f" in "$cs"/* | \~/Library/CloudStorage/*) ;; *) continue ;; esac ;;
			esac
			add_pair "$v" "<source-$i>" w
		done < <(LC_ALL=C awk '
			function flush() { if (inside && id != "") print n "\t" id "\t" path; id = ""; path = "" }
			/^[[:space:]]*\[/ {
				flush()
				inside = ($0 ~ /^[[:space:]]*\[\[[[:space:]]*source[[:space:]]*\]\]/)
				if (inside) n++
				next
			}
			inside && /^[[:space:]]*id[[:space:]]*=[[:space:]]*"/ { v = $0; sub(/^[^"]*"/, "", v); sub(/".*$/, "", v); id = v; next }
			inside && /^[[:space:]]*path[[:space:]]*=[[:space:]]*"/ { v = $0; sub(/^[^"]*"/, "", v); sub(/"[^"]*$/, "", v); path = v; next }
			END { flush() }' "$CONFIG" 2>/dev/null)
	fi
	# The login before the name's joined forms: of two equal values the first pair wins, and a login that is the
	# name run together ("janedoe" for "Jane Doe") is <user>, as in the Python report.
	user="$(id -un 2>/dev/null || true)"
	[ -z "$user" ] || add_pair "$user" "<user>" w
	name="$(id -F 2>/dev/null || true)"
	if [ -n "$name" ]; then
		add_pair "$name" "<name>"
		# Every word as written ("(JJ)" too) and without its brackets ("Doe, Jane (Contoso)"): macOS keeps the
		# comma. read, not an unquoted for: a part with * ? or [ would glob.
		# A hyphenated part in pieces ("Jean", "Luc"; the loop after this one keeps "Jean-Luc"), each in its
		# upper case and, all capitals ("DOE"), capitalised ("Doe"), as setup_report does (B13 R71).
		read -r -a toks <<<"${name//[,-]/ }" || true
		for tok in ${toks[@]+"${toks[@]}"}; do
			add_pair "$tok" "<name>" w
			tok="${tok%.}"
			u="$(recase upper "$tok")"
			c=""
			[ "$u" != "$tok" ] || c="$(capital "$tok")"
			if [ "${#tok}" -ge 3 ]; then
				[ "$u" = "$tok" ] || add_pair "$u" "<name>" w
				add_pair "$c" "<name>" w
			elif [ "${#tok}" -eq 2 ] && case "$tok" in [[:upper:]][[:alpha:]]) true ;; *) false ;; esac; then
				# A two-letter part ("Li", "LI") too, each form that is no word of the report's (short_name), as
				# setup_report does: add_forms has no 3-character floor (B13 R72). A part written in lower case
				# is a particle ("de"): alone it names no one, in no case.
				kept=" "
				for v in "$tok" "$u" "$c"; do
					case "$kept" in *" $v "*) continue ;; esac
					kept="$kept$v "
					[ -z "$v" ] || ! short_name "$v" || add_forms "$v" "<name>" w
				done
			fi
		done
		read -r -a toks <<<"$(printf '%s' "$name" | LC_ALL=C sed 'y/,()[]{}/       /')"
		for tok in ${toks[@]+"${toks[@]}"}; do
			tok="${tok%.}"
			tok="${tok#.}"
			add_pair "$tok" "<name>" w
			u="$(recase upper "$tok")"
			[ "$u" = "$tok" ] || add_pair "$u" "<name>" w
			[ "$u" != "$tok" ] || add_pair "$(capital "$tok")" "<name>" w
		done
		# Without its bracketed part, in the order the name is said ("Jane Q. Doe" for "Doe, Jane Q."): the
		# words joined, and every two words in either order, the later of 2 or more characters and the earlier
		# the first word or of 2 or more, so a suffix ("Jane Doe, Jr.") or a title ("Dr. Jane Doe") hides no
		# pair; each as written, lower and upper case and joined by a space, -, _ or nothing ("JaneDoe").
		core="$(printf '%s' "$name" | LC_ALL=C sed -e ':a' -e 's/[[:space:]]*[([{][^]()[{}]*[])}]//' -e 'ta')" # innermost first (R73)
		IFS=, read -r -a segs <<<"$core"
		[ "${#segs[@]}" -lt 2 ] || segs=("${segs[1]}" "${segs[0]}" ${segs[2]+"${segs[@]:2}"})
		read -r -a toks <<<"${segs[*]-}"
		words=()
		for tok in ${toks[@]+"${toks[@]}"}; do
			tok="${tok%.}"
			[ -z "${tok#.}" ] || words+=("${tok#.}")
		done
		if [ "${#words[@]}" -ge 2 ]; then
			name_forms "${words[@]}"
			for ((i = 0; i < ${#words[@]} - 1; i++)); do
				[ "$i" -eq 0 ] || [ "${#words[i]}" -ge 2 ] || continue
				for tok in "${words[@]:i+1}"; do
					[ "${#tok}" -lt 2 ] || { name_forms "${words[i]}" "$tok" && name_forms "$tok" "${words[i]}"; }
				done
			done
		fi
	fi
	# A hosted MDM's host can name the company, spelled unlike the OneDrive organisation.
	mdm="$(with_timeout 5 /usr/bin/profiles status -type enrollment 2>/dev/null </dev/null |
		sed -n 's|.*MDM server: [A-Za-z]*://\([^/@]*@\)\{0,1\}\([^/:;?#@[:space:]]*\).*|\2|p' | head -n 1 || true)"
	case "$mdm" in *.*) add_pair "$mdm" "<mdmhost>" ;; esac
	# Proxy values, as the Python report registers them (B13 H19): the system PAC URL after its scheme (a cloud
	# proxy's path can name the customer: /<domain>/proxy.pac), and the host of it and of every proxy URL:
	# scutil's, the proxy environment variables, [network] proxy and git's config files (read as files: the
	# logged git error names git's proxy, and step 1 may have stopped with no git). status's network.proxy lines
	# print them. scutil by name (on every Mac's PATH), so a test can stand in for it.
	n=0
	seen=" "
	while IFS= read -r u; do
		pac=""
		case "$u" in pac=*)
			u="${u#pac=}"
			pac="${u#*://}"
			# A PAC URL that is only a generic host ("http://wpad") is that host, matched inside a URL only.
			case "$pac" in */*) ;; *) ! generic_proxy_host "${pac%%:*}" || pac="" ;; esac
			;;
		esac
		h="${u#*://}"
		h="${h%%[/?#]*}"
		h="${h##*@}"
		port=""
		case "$h" in *:*) port="${h#*:}" ;; esac
		h="${h%%:*}"
		for v in "$pac" "$h"; do
			[ -n "$v" ] || continue
			case "$seen" in *" $v "*) continue ;; esac
			seen="$seen$v "
			n=$((n + 1))
			case "$v" in
			"$pac" | *.*) add_pair "$v" "<proxy-$n>" ;;
			*)
				# A host with no dot (B13 R42, R67): a generic word (proxy, wpad) only where a URL names it, so
				# the report's HTTPS_PROXY and network.proxy stay; any other (contosogw) can name the company and
				# is a whole word, as git and curl print it ("Could not resolve proxy: contosogw").
				if generic_proxy_host "$v"; then
					add_pair "//$v" "//<proxy-$n>" t
					add_pair "@$v" "@<proxy-$n>" t
					[ -z "$port" ] || add_pair "$v:$port" "<proxy-$n>:$port" w
				else
					add_pair "$v" "<proxy-$n>" w
				fi
				;;
			esac
		done
	done < <(
		{
			with_timeout 5 "$(command -v scutil || printf /usr/sbin/scutil)" --proxy 2>/dev/null </dev/null |
				LC_ALL=C sed -n -e 's/^[[:space:]]*ProxyAutoConfigURLString : /pac=/p' \
					-e 's/^[[:space:]]*HTTPS\{0,1\}Proxy : //p' || true
			printf '%s\n' "${HTTPS_PROXY:-}" "${https_proxy:-}" "${ALL_PROXY:-}" "${all_proxy:-}" \
				"${HTTP_PROXY:-}" "${http_proxy:-}"
			[ ! -f "$CONFIG" ] || LC_ALL=C sed -n 's/^[[:space:]]*proxy[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$CONFIG"
			git_proxy_values
			# The LaunchAgent environment (launchctl setenv) that the doctor network.proxy.job line prints (B13 R68); a
			# read, as launchd_print is, and none when launchd is simulated (no launchctl call at all).
			[ "$SIMULATE" -eq 1 ] || for k in HTTPS_PROXY https_proxy ALL_PROXY all_proxy HTTP_PROXY http_proxy; do
				with_timeout 5 "$LAUNCHCTL" getenv "$k" </dev/null || true
			done
		} 2>/dev/null
	)
}
# The repository config files git reads for checkout DIR, one a line, as setup_report._checkout_git_configs
# reads them: <common dir>/config and <git dir>/config.worktree, where .git is the git dir or a file naming it
# ("gitdir: <path>", a linked worktree's or a submodule's; relative to DIR) and the git dir's commondir file
# names the common dir (relative to the git dir). No git process: step 1 may have stopped without the tools.
checkout_git_configs() { # DIR
	local dot="$1/.git" gd c=""
	if [ -d "$dot" ]; then
		gd="$dot"
	elif [ -f "$dot" ]; then
		gd="$(LC_ALL=C sed -n '1s/^gitdir:[[:space:]]*//p' "$dot" 2>/dev/null)"
		[ -n "$gd" ] || return 0
		case "$gd" in /*) ;; *) gd="$1/$gd" ;; esac
	else
		return 0
	fi
	[ ! -f "$gd/commondir" ] || c="$(LC_ALL=C sed -n '1p' "$gd/commondir" 2>/dev/null)"
	case "$c" in "") c="$gd" ;; /*) ;; *) c="$gd/$c" ;; esac
	printf '%s\n' "$c/config" "$gd/config.worktree"
}
repo_slug() { # owner/repo of ISSUE_URL: redact_stream holds this repository's own locator
	local s="${ISSUE_URL#https://github.com/}"
	printf '%s' "${s%%/issues/*}"
}
redact_stream() {
	# Longest value first, as the Python report does: "Plans Archive" before "Plans", a folder before its org.
	# Every stage byte-wise: under a UTF-8 locale macOS cut and sort stop at the first pair that is not UTF-8
	# (printf %q writes one for a curly apostrophe or an NFD accent) and every pair after it is lost. A $'..'
	# word (install.log's args, a re-run line in the friction log) is <path> first: its octal bytes match no
	# pair, so it would show the escaped folder name. awk too: macOS awk's index and substr are byte-wise, but a
	# regex match (the placeholder match, the word test) under UTF-8 exits at a byte that is not UTF-8 on its
	# own ("Jane" + U+2019), and the placeholder match runs on every line.
	LC_ALL=C sed -E "s/\\\$'([^'\\\\]|\\\\.)*'?/<path>/g" |
		REDACT_PAIRS="$(redaction_pairs | LC_ALL=C awk -F '\t' '{ print length($1) "\t" $0 }' |
			LC_ALL=C sort -t "$(printf '\t')" -k1,1nr -s | LC_ALL=C cut -f 2-)" REPO_SLUG="$(repo_slug)" LC_ALL=C awk '
	# The line is kept as pieces, plain text or a placeholder (held): a later pair never matches inside a
	# placeholder an earlier one wrote ("<name>" stays whole beside a folder named "name"), as in the Python
	# report, which replaces in one pass. A held piece ends in ">" or "~", or is followed by no word character,
	# as the line it stood in did. A placeholder already in the text (the friction log of an earlier report; an
	# agent may copy one from either report or from it-request.md, whose <org>, <Org>, <team> and <TEAMID>
	# are template fields) is held from the start: only these names, since "<jdoe>" may be the login itself.
	# So is the locator of this repository (github.com/ or github.com: and the owner/repo of ISSUE_URL, any case,
	# not followed by a word character): on a Mac whose full name run together is its public owner, the name
	# pairs cut the issue link and the source URL to github.com/<name>/..., as setup_report.Redactor holds it.
	# A heading of the report itself is never redacted, as in the Python report.
	function keep(text, isheld) { if (text != "") { m++; tx[m] = text; th[m] = isheld } }
	function repo_at(s, low, best, k, p, t, i, off, n) { # where the locator starts in s (0: nowhere); RL its length
		best = 0
		for (k = 1; k <= 2; k++) {
			p = "github.com" (k == 1 ? "/" : ":") slug; t = low = tolower(s); off = 0
			while (slug != "" && (i = index(t, p)) > 0) {
				n = length(p)
				if (substr(t, i + n, 4) == ".git" && substr(t, i + n + 4, 1) !~ /[A-Za-z0-9_-]/) n += 4
				if (substr(t, i + n, 1) !~ /[A-Za-z0-9_-]/) {
					if (!best || off + i < best) { best = off + i; RL = n }
					break
				}
				off += i; t = substr(t, i + 1)
			}
		}
		return best
	}
	BEGIN {
		slug = tolower(ENVIRON["REPO_SLUG"])
		np = split(ENVIRON["REDACT_PAIRS"], rows, "\n")
		nh = split("# agentsync setup report|## Summary|## Agent friction log|## Environment|## Installer|" \
			"## Configuration|## Doctor|## Status|## Background runs|## Recent errors", hd, "|")
		for (k = 1; k <= nh; k++) heading[hd[k]] = 1
	}
	$0 in heading { print; next }
	{
		m = 0; s = $0
		while (1) {
			if (!match(s, /<(name|user|host|mdmhost|tmp|path|serial|org|Org|team|TEAMID|(org|folder|library|source|email|guid|hash|commit|proxy)-([0-9]+|N))>/)) RSTART = 0
			if ((r = repo_at(s)) && (!RSTART || r < RSTART)) { RSTART = r; RLENGTH = RL }
			if (!RSTART) break
			keep(substr(s, 1, RSTART - 1), 0)
			keep(substr(s, RSTART, RLENGTH), 1)
			s = substr(s, RSTART + RLENGTH)
		}
		keep(s, 0)
		ns = m
		for (j = 1; j <= m; j++) { seg[j] = tx[j]; held[j] = th[j] }
		for (k = 1; k <= np; k++) {
			if (split(rows[k], f, "\t") < 2) continue
			v = f[1]
			if (!index($0, v)) continue # every plain piece is a piece of the input line
			n = length(v); word = (f[3] == "w"); tail = (f[3] == "t"); m = 0
			for (j = 1; j <= ns; j++) {
				if (held[j]) { keep(seg[j], 1); continue }
				s = seg[j]; acc = ""
				while ((i = index(s, v)) > 0) {
					pre = (i > 1) ? substr(s, i - 1, 1) : substr(acc, length(acc), 1)
					post = substr(s, i + n, 1)
					if ((word && (pre ~ /[A-Za-z0-9_]/ || post ~ /[A-Za-z0-9_]/)) || (tail && post ~ /[A-Za-z0-9_.-]/)) {
						acc = acc substr(s, 1, i + n - 1)
					} else {
						keep(acc substr(s, 1, i - 1), 0); acc = ""
						keep(f[2], 1)
					}
					s = substr(s, i + n)
				}
				keep(acc s, 0)
			}
			ns = m
			for (j = 1; j <= m; j++) { seg[j] = tx[j]; held[j] = th[j] }
		}
		line = ""
		for (j = 1; j <= ns; j++) line = line seg[j]
		print line
	}'
}
launchd_field() { # TEXT KEY: the value of a top-level "KEY = value" line of `launchctl print`
	printf '%s\n' "$1" | sed -n "s/^	$2 = //p" | head -1
}
launchd_print() { with_timeout 10 "$LAUNCHCTL" print "gui/$(id -u)/${1:-$POLL_LABEL}" 2>/dev/null; } # [LABEL]
# `launchctl print` reads the uid's domain, not this HOME's: the job it shows is another install's when the
# plist it was loaded from is outside this home folder, or its program or an argument is in another user's or
# a temporary folder (setup_report._background's rule, _under_home and _foreign_paths; B13 round 3 R6, R16).
# A job loaded from this home's plist path whose file is gone stays this setup's.
launchd_foreign() { # PRINT-TEXT: exit 0 when the loaded job is another install's
	local hp
	hp="$(cd "$HOME" 2>/dev/null && pwd -P || true)"
	printf '%s\n' "$1" | LC_ALL=C awk -v h="$HOME" -v hp="${hp:-$HOME}" '
		function mine(v) { return v == h || v == hp || index(v, h "/") == 1 || index(v, hp "/") == 1 }
		function homelike(v) { return v ~ /^\/(Users|private\/var\/folders|var\/folders|private\/tmp|tmp|Volumes)\// }
		/^\targuments = \{$/ { inside = 1; next }
		inside && /^\t\}$/ { inside = 0; next }
		inside { sub(/^[\t ]+/, ""); if (homelike($0) && !mine($0)) bad = 1; next }
		/^\tpath = / { v = substr($0, 9); if (v != "" && !mine(v)) bad = 1 }
		/^\tprogram = / { v = substr($0, 12); if (homelike(v) && !mine(v)) bad = 1 }
		END { exit !bad }'
}
leading_int() { # "79: ..." -> 79; "(never exited)" -> empty
	case "$1" in
	[0-9]*) printf '%s' "${1%%[!0-9]*}" ;;
	esac
}
# The last install run in install.log, as setup_report._install_line reads it: by run id, skipping a
# --list-folders run. Prints "<start time> <rc>", the rc empty when the run has no end line; nothing when there
# is no such run.
last_install_run() {
	[ -f "$SETUP_LOG" ] || return 0
	LC_ALL=C awk '
		$2 !~ /^run=/ { next }
		{ id = substr($2, 5) }
		!(id in at) { order[++n] = id; at[id] = $1 }
		index($0 " ", " step=list-folders ") { lists[id] = 1 }
		$3 == "end" && match($0, / rc=[0-9]+/) { rc[id] = substr($0, RSTART + 4, RLENGTH - 4) }
		END {
			for (i = n; i > 0; i--) if (!(order[i] in lists)) { print at[order[i]] " " rc[order[i]]; exit }
		}' "$SETUP_LOG" 2>/dev/null || true
}
# When the friction log's latest attempt began, as parse_friction draws it (Attempt.begin): its Attempt: time,
# else its first timed line's; an attempt opens at an Attempt: line, at a step 1 error track() reads as another
# session's, and at a step 1 error dated more than NEW_SESSION_GAP after an end line. Nothing when there is no
# friction log or no time.
friction_attempt_begin() {
	local f
	f="$(friction_file)"
	[ -f "$f" ] || return 0
	LC_ALL=C awk -v gap="$NEW_SESSION_GAP" -v s1="$STEP1_ERROR_RE" "$FRICTION_AWK"'
		BEGIN { stamp = "^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z" }
		{ q = track($0) }
		/^Attempt:/ { b = substr($0, 10, 20); if (b !~ (stamp "$")) b = ""; ended = ""; next }
		q { b = substr($0, 1, 20); ended = ""; next }
		ended != "" && $0 ~ s1 && secs($0) - secs(ended) > gap { b = substr($0, 1, 20); ended = ""; restate($0); next }
		$0 !~ (stamp " [|]") { next }
		b == "" { b = substr($0, 1, 20) }
		/\| end \| finished[[:space:]]*$/ { ended = substr($0, 1, 20) }
		END { print b }' "$f" 2>/dev/null || true
}
# The Summary's install.sh line. A --report-only run only writes the report, so its own exit 0 says nothing of
# the setup: it gives the last install run's instead, dated, so an earlier run's is not read as this
# attempt's (B13 round 4 Q23). A run that started before the friction log's latest attempt began is said to be
# an earlier one, as setup_report._install_line scopes runs to the attempt (B13 round 5 F30); no install log is
# not "no install run in the install log" (F29).
fallback_install_line() { # RC
	local head="- install.sh: setup-prompt-compat $SETUP_PROMPT_COMPAT, source commit $COMMIT${DIRTY:+ tree=$DIRTY}" last at rc
	local begin what
	if [ "$REPORT_ONLY" -eq 0 ]; then
		say "$head, exit $1 ($(rc_meaning "$1"))"
		return 0
	fi
	head="$head; this --report-only run only writes the report"
	if [ ! -f "$SETUP_LOG" ]; then
		say "$head; no install log (no install run on this Mac)"
		return 0
	fi
	last="$(last_install_run)"
	at="${last%% *}"
	rc="${last#* }"
	if [ -z "$last" ]; then
		say "$head; no install run in the install log"
		return 0
	fi
	if [ -z "$rc" ]; then what="has no end line (stopped or still running)"; else what="exit $rc ($(rc_meaning "$rc"))"; fi
	begin="$(utc_stamp "$(friction_attempt_begin)")"
	if [ -n "$begin" ] && [ -n "$(utc_stamp "$at")" ] && [[ "$at" < "$begin" ]]; then
		say "$head; no install run during this attempt (began $begin); the last one, started $at, $what"
	else
		say "$head; the last install run (started $at) $what"
	fi
}
# The shell report's body (redact_stream redacts it): the same headings as `agentsync setup-report`.
fallback_body() {
	local rc="$1" why="$2" friction="$3" cs="$HOME/Library/CloudStorage" p v k n l o
	say "# agentsync setup report"
	say ""
	say "## Summary"
	say ""
	say "- written by: scripts/install.sh shell fallback ($why), $(utc_now)"
	fallback_install_line "$rc"
	# The mode, and whether this home holds each job's plist: "not simulated" alone read as "installed" on a setup
	# that installed none (B13 round 4 Q6). Background runs has each job's loaded state.
	if [ "$SIMULATE" -eq 1 ]; then
		say "- launchd: simulated"
	else
		v=""
		for l in "$POLL_LABEL" "$RECONCILE_LABEL"; do
			if [ -f "$HOME/Library/LaunchAgents/$l.plist" ]; then k="present"; else k="absent"; fi
			v="$v${v:+, }$l plist $k"
		done
		say "- launchd: not simulated (the real launchctl); $v (Background runs has each job's state)"
	fi
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
	# The gitleaks lints.gitleaks_lookup uses (B13 round 4 Q81): the path remember_gitleaks recorded in the
	# default state folder, when it is still an absolute executable file (B13 round 5 F58), then PATH, then the
	# fixed places; the builtin patterns always run. read trims the blanks around it, as .strip() does.
	v=""
	p=""
	{ read -r p || [ -n "$p" ]; } <"$HOME/Library/Application Support/agentsync/gitleaks-path" 2>/dev/null || p=""
	case "$p" in /*) [ ! -f "$p" ] || [ ! -x "$p" ] || v="$p" ;; esac
	[ -n "$v" ] || v="$(command -v gitleaks 2>/dev/null || true)"
	for c in /opt/homebrew/bin/gitleaks /usr/local/bin/gitleaks /opt/local/bin/gitleaks "$HOME/.local/bin/gitleaks" "$HOME/go/bin/gitleaks"; do
		[ -n "$v" ] || [ ! -x "$c" ] || v="$c"
	done
	say "- gitleaks: ${v:-not found (builtin patterns only)}"
	if have_devtools; then
		v="$(command -v python3 2>/dev/null || true)"
		say "- python3: ${v:-not found}"
	else
		say "- python3: only the /usr/bin stub (no developer tools; not run)"
	fi
	[ ! -d "$HOME/.local/share/uv/python" ] || say "- uv-managed Pythons: $(find "$HOME/.local/share/uv/python" -mindepth 1 -maxdepth 1 -type d ! -name '.*' 2>/dev/null | wc -l | tr -d ' ')"
	v="$(report_agentsync)"
	say "- agentsync: ${v:-not installed}"
	v=""
	for k in HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy NO_PROXY no_proxy; do
		eval "p=\${$k:-}"
		[ -z "$p" ] || v="$v $k"
	done
	if [ -n "$v" ]; then say "- proxy environment: yes (set:$v)"; else say "- proxy environment: no"; fi
	# The system proxy by its Enable flags, as net.py reads them (a host key can stay while its flag is 0), and
	# whether git's config names a proxy: yes or no only, never a host (B13 R4, R27).
	if p="$(with_timeout 5 "$(command -v scutil || printf /usr/sbin/scutil)" --proxy 2>/dev/null </dev/null)" && [ -n "$p" ]; then
		v="no"
		if LC_ALL=C grep -q '^[[:space:]]*HTTPSEnable : 1$' <<<"$p" &&
			LC_ALL=C grep -q '^[[:space:]]*HTTPSProxy : [^[:space:]]' <<<"$p"; then v="yes"; fi
		k="no"
		! LC_ALL=C grep -q '^[[:space:]]*ProxyAutoConfigEnable : 1$' <<<"$p" || k="yes"
		n="no"
		! LC_ALL=C grep -q '^[[:space:]]*ProxyAutoDiscoveryEnable : 1$' <<<"$p" || n="yes"
		say "- system proxy (scutil --proxy): manual HTTPS $v · PAC $k · WPAD $n"
	else
		say "- system proxy (scutil --proxy): unknown"
	fi
	if [ -n "$(git_proxy_values)" ]; then
		say "- proxy in git's config: set (git uses it before HTTPS_PROXY)"
	else
		say "- proxy in git's config: not set"
	fi
	# Folders only, as setup_report.cloud counts them since B13 round 4 Q33: a symlink and any other entry are
	# counted apart, and named only when there is one (B13 round 5 F56). BSD find does not follow a link (-P).
	n=0
	k=0
	l=0
	o=0
	if [ -d "$cs" ]; then
		n="$(find "$cs" -mindepth 1 -maxdepth 1 ! -name '.*' -type d 2>/dev/null | wc -l | tr -d ' ')"
		k="$(find "$cs" -mindepth 1 -maxdepth 1 -name 'OneDrive-*' -type d 2>/dev/null | wc -l | tr -d ' ')"
		l="$(find "$cs" -mindepth 1 -maxdepth 1 ! -name '.*' -type l 2>/dev/null | wc -l | tr -d ' ')"
		o="$(find "$cs" -mindepth 1 -maxdepth 1 ! -name '.*' ! -type d ! -type l 2>/dev/null | wc -l | tr -d ' ')"
	fi
	v="$n (OneDrive: $k)"
	[ "$l" -eq 0 ] || v="$v and $l symlink(s)"
	[ "$o" -eq 0 ] || v="$v and $o other entry(ies)"
	say "- ~/Library/CloudStorage providers: $v"
	say ""
	say "## Installer"
	say ""
	if [ -f "$SETUP_LOG" ]; then
		say "The last 60 lines of $SETUP_LOG (run ids without their process id):"
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
		# One line a job, each starting with its label, as setup_report._background writes them (B13 round 4 Q5).
		for l in "$POLL_LABEL" "$RECONCILE_LABEL"; do
			if [ -f "$HOME/Library/LaunchAgents/$l.plist" ]; then v="present"; else v="absent"; fi
			p="$(launchd_print "$l" || true)"
			if [ -z "$p" ]; then
				say "- $l: plist $v · loaded: no"
			elif launchd_foreign "$p"; then
				say "- $l: loaded, but the job belongs to another install (its plist or ProgramArguments are outside this home folder); its runs and exit codes are not this setup's result"
			else
				n="$(launchd_field "$p" "last exit code")"
				k="$(leading_int "$n")"
				n="state $(launchd_field "$p" state), runs $(launchd_field "$p" runs), last exit code ${n:-?}"
				[ -z "$k" ] || n="$n ($(rc_meaning "$k"))"
				if [ "$v" = present ]; then
					say "- $l: plist present · loaded: $n"
				else
					say "- $l: loaded, but the plist is absent (removed without launchctl bootout; agentsync install-agent repairs it): $n"
				fi
			fi
		done
	fi
	say ""
	say "## Recent errors"
	say ""
	[ -z "$LAST_ERROR" ] || say "- $LAST_ERROR"
	if [ -f "$SETUP_LOG" ] && LC_ALL=C grep -q 'result=failed' "$SETUP_LOG" 2>/dev/null; then
		say "- failed steps in the install log:"
		say ""
		say "~~~"
		LC_ALL=C grep 'result=failed' "$SETUP_LOG" | tail -10
		say "~~~"
	elif [ -z "$LAST_ERROR" ]; then
		say "None recorded."
	fi
	say ""
}
# The shell report's Redaction section: fixed text, written after redact_stream (as the issue link is), so no
# value can replace a word of it ("the login name" with a folder named "name").
fallback_redaction() {
	say "## Redaction"
	say ""
	say "Shell fallback redaction: the home path (~), this account's temporary folder (\$TMPDIR: <tmp>), the login name"
	say "(<user>), the full name and its joined forms (<name>), the organisation or team after the app's name in a"
	say "CloudStorage folder (OneDrive-<org-N>, Dropbox-<org-N>; an account's email address too), the MDM server's host"
	say "(<mdmhost>), proxy hosts (one named like proxy or wpad only inside a URL) and the system PAC URL after its"
	say "scheme (<proxy-N>), the names of the folders under CloudStorage that --source-local, sources.toml or install.log"
	say "name (<folder-N>; a name every OneDrive or Google Drive has, such as Documents, only where it stands as a whole"
	say "word), a configured source id (<source-N>; one of agentsync's own words, such as docs or inbox, only for a cloud"
	say "folder), a --source-local argument in install.log that names no CloudStorage folder (<path>), and every word"
	say "bash wrote in its dollar-quote form, which install.log uses for a name with a curly apostrophe, an en dash or an"
	say "accent (<path>). Read the report before sending it: anything else confidential is yours to remove."
}
# The friction log for the shell report: friction.md, else what an earlier report holds under its heading
# (byte-wise: that report can hold a byte that is not UTF-8, and macOS awk's regex match exits there).
friction_text() {
	local f
	f="$(friction_file)"
	if [ -f "$f" ]; then
		head -c 262144 "$f"
	elif [ -f "$REPORT_PATH" ]; then
		LC_ALL=C awk '/^## / { found = ($0 == "## Agent friction log"); next } found { print }' "$REPORT_PATH" |
			LC_ALL=C grep -v '^<!-- agent:' | LC_ALL=C grep -v '^No friction log at ' | LC_ALL=C sed '/./,$!d'
	fi
}
# The globs agentsync itself ships (setup_report._SHIPPED_EXCLUDES: config.DEFAULT_EXCLUDES, INBOX_IGNORES
# and OS_JUNK_EXCLUDES), each as an exclude line writes it (json.dumps): Icon\r is a backslash and an r. A test
# holds it to the Python set.
SHIPPED_EXCLUDES='"~$*" "*.tmp" ".~lock.*#" ".DS_Store" "Icon\r" "._*" "*.crdownload" "*.part" "*.partial" "*.download"'
# An exclude line (the loop's WAITING ON YOU text for an empty cloud folder, which an agent may log) names
# folders below a source root, which redact_stream has never seen. As `agentsync setup-report` shows it
# (setup_report._exclude_placeholder, B13 round 3 R8; here since round 4 Q80): in a whole list, every entry
# that is exactly one of SHIPPED_EXCLUDES is shown as written, so a reviewer can check the advice kept them,
# and every other one is "<path>", or "/<path>/" for a root-anchored folder. A list whose entries do not parse
# as "...", "..." (a backslash escapes the next byte; blanks around a comma as JSON allows them) is
# exclude = [<path>] in [[source]], and a line cut inside the list is exclude = [<path>]. Byte-wise (LC_ALL=C, index and substr), and the set is read from
# ENVIRON: awk -v would turn its \r into a carriage return.
scrub_exclude_lists() {
	SHIPPED="$SHIPPED_EXCLUDES" LC_ALL=C awk '
		BEGIN {
			n = split(ENVIRON["SHIPPED"], a, " ")
			for (i = 1; i <= n; i++) shipped[a[i]] = 1
			op = "exclude = ["
			cl = "] in [[source]]"
		}
		function entry(t, inner) {
			if (t in shipped) return t
			inner = substr(t, 2, length(t) - 2)
			if (length(inner) > 2 && substr(inner, 1, 1) == "/" && substr(inner, length(inner), 1) == "/") return "\"/<path>/\""
			return "\"<path>\""
		}
		function blanks(body, i) {
			while (i <= length(body) && index(" \t\r\n", substr(body, i, 1)) > 0) i++
			return i
		}
		function entries(body, out, i, j, m) { # sets parsed: 0 when the body does not parse
			parsed = 0
			m = length(body)
			i = blanks(body, 1)
			out = ""
			if (i > m) {
				parsed = 1
				return ""
			}
			while (1) {
				if (substr(body, i, 1) != "\"") return ""
				for (j = i + 1; j <= m && substr(body, j, 1) != "\""; j++) if (substr(body, j, 1) == "\\") j++
				if (j > m) return ""
				out = out (out == "" ? "" : ", ") entry(substr(body, i, j - i + 1))
				i = blanks(body, j + 1)
				if (i > m) {
					parsed = 1
					return out
				}
				if (substr(body, i, 1) != ",") return ""
				i = blanks(body, i + 1)
			}
		}
		{
			out = ""
			rest = $0
			while ((i = index(rest, op)) > 0) {
				out = out substr(rest, 1, i - 1)
				rest = substr(rest, i + length(op))
				j = index(rest, cl)
				if (j == 0) {
					out = out "exclude = [<path>]"
					rest = ""
					break
				}
				shown = entries(substr(rest, 1, j - 1))
				out = out op (parsed ? shown : "<path>") cl
				rest = substr(rest, j + length(cl))
			}
			print out rest
		}'
}
# A run id is "<UTC stamp>-<pid>" (RUN_ID): the shell report shows it without its process id, as the Python
# report does (setup_report._PID_SUFFIX_RE; B13 round 5 F5, F15), in the Installer tail and in Recent errors
# alike. Anchored to the run id's shape, so no other number is touched; LC_ALL=C, as every stage here.
strip_run_pids() { LC_ALL=C sed -E 's/(run=[0-9]{8}T[0-9]{6}Z)-[0-9]+([^0-9A-Za-z_-]|$)/\1\2/g'; }
write_fallback_report() { # RC WHY
	local dir tmp friction c
	# --report-only reads no SOURCE: its Summary names this installer's checkout, as --version does. Only here,
	# so a report agentsync writes runs no git.
	if [ "$REPORT_ONLY" -eq 1 ] && [ -e "$repo/.git" ]; then
		read_commit "$repo"
		if [ "$COMMIT" = "-" ]; then
			c="$(git_head_file "$repo")"
			[ -z "$c" ] || COMMIT="$c (local changes not checked)"
		fi
	fi
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
			fallback_body "$1" "$2" "$friction" | strip_run_pids | redact_stream | scrub_exclude_lists
			fallback_redaction
			printf '\n%s\n' "$ISSUE_URL" # the issue form, after redaction (a login may match the URL's owner)
		} >"$tmp"
	) 2>/dev/null || {
		rm -f "$tmp"
		return 1
	}
	mv -f "$tmp" "$REPORT_PATH"
}
# When an earlier attempt reached its report, for section 3 of the bring-back file: the time of the last
# "<UTC> | end | finished" line that a later "Attempt:" line follows, or a step 1 error line dated more than
# NEW_SESSION_GAP after it (a session whose step 1 stopped before --log-start: parse_friction's header-less
# attempt), and that does not close an attempt --log-start stopped (a copy of the prompt that is not this
# installer's: its report is not brought back).
# Prints nothing when there is none, or no friction log. The current attempt's own end line never counts,
# so a second --report-only in one attempt writes the same section 3.
friction_earlier_end() {
	local f
	f="$(friction_file)"
	[ -f "$f" ] || return 0
	LC_ALL=C awk -v mark="| step 1 | error | $PROMPT_STOPPED " -v gap="$NEW_SESSION_GAP" -v s1="$STEP1_ERROR_RE" "$FRICTION_AWK"'
		{ q = track($0) }
		/^Attempt:/ { if (e != "" && !stopped) last = e; e = ""; stopped = 0; next }
		q { if (e != "" && !stopped) last = e; e = ""; stopped = 0; next }
		e != "" && $0 ~ s1 && secs($0) - secs(e) > gap { if (!stopped) last = e; e = ""; stopped = 0; restate($0); next }
		index($0, mark) { stopped = 1 }
		/^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z \| end \| finished[[:space:]]*$/ { e = substr($0, 1, 20) }
		END { print last }' "$f" 2>/dev/null || true
}
# A value that is exactly "<YYYY>-<MM>-<DD>T<HH>:<MM>:<SS>Z", else nothing: what section 3 compares. Anything
# else (a hand-written end line, the output of a stat that is not macOS's) is no time, and the patch is sent.
utc_stamp() {
	case "$1" in
	[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) printf '%s' "$1" ;;
	esac
}
# The one file the person copies back (field report 2026-10-05): the redacted setup report, then the fix request
# and the local work step 1 of the prompt kept, which are NOT redacted, so the person reviews it first. The
# file's own headings name the setup folder with ~: the home path holds the login name (field report
# 2026-10-06).
# Section 3 holds the local work kept since an earlier attempt reached its report (field report 2026-10-07:
# one 3,587-line patch came back a second time, a round after it was rebuilt). A patch file last written at
# or before that moment (friction_earlier_end) is not repeated: one line gives its time, line count and the
# first 12 hex digits of its SHA-256, so whoever receives the file can check it against what they hold, and
# says where it still is. This Mac cannot know what was copied back, so the line claims nothing about that.
# Every doubt sends the patch: no friction log, no earlier finished attempt, a time or a hash that cannot be
# read. Nothing is deleted or moved.
# Each embedded file sits inside a tilde fence longer than any tilde run that starts one of its lines (at least
# 10), so its own headings are no headings of this file and none of its lines can close the fence (B13 round 5
# F14): the outline is this file's title and its three numbered sections, and every embedded byte is kept, so
# section 2 still compares line for line with an earlier file's.
fence_for() { # FILE...: the fence
	local n
	n="$(LC_ALL=C awk 'match($0, /^ *~+/) && RLENGTH > m { m = RLENGTH } END { print (m < 10 ? 10 : m + 1) }' "$@" 2>/dev/null)" || n=""
	case "$n" in "" | *[!0-9]*) n=40 ;; esac
	printf '%*s' "$n" '' | LC_ALL=C tr ' ' '~'
}
embed() { # FILE INFO: the file inside its fence, the fence's info string INFO
	local fence
	fence="$(fence_for "$1")"
	printf '%s%s\n' "$fence" "$2"
	cat "$1"
	[ -z "$(tail -c 1 "$1")" ] || printf '\n'
	printf '%s\n' "$fence"
}
write_bring_back() {
	local out="${REPORT_PATH%/*}/bring-back.md" tmp p fence shown="$SETUP_DIR"
	local prev at sha sent=0 skipped=""
	case "$shown" in
	"$HOME"/*) shown='~'"${shown#"$HOME"}" ;;
	esac
	prev="$(utc_stamp "$(friction_earlier_end)")"
	tmp="$(mktemp "${REPORT_PATH%/*}/.bring-back.XXXXXX" 2>/dev/null)" || return 1
	{
		printf '# agentsync: the one file to bring back\n\n'
		printf 'Private: copy this file back as it is, and never paste it into the public issue form.\n'
		printf 'Section 1 is redacted. Sections 2 and 3 are not: they name real folders and files, so review\n'
		printf 'them before sending.\n\n'
		printf '## 1. Setup report\n\n'
		embed "$REPORT_PATH" markdown
		printf '\n## 2. Fix request (%s)\n\n' "$shown/fix-request.md"
		if [ -s "$SETUP_DIR/fix-request.md" ]; then embed "$SETUP_DIR/fix-request.md" markdown; else printf 'none\n'; fi
		printf '\n## 3. Local work kept by setup prompt step 1 (%s)\n\n' "$shown/local-work"
		fence="$(fence_for "$SETUP_DIR"/local-work/*.patch)"
		for p in "$SETUP_DIR"/local-work/*.patch; do
			[ -e "$p" ] || continue
			at=""
			[ -z "$prev" ] || at="$(utc_stamp "$(TZ=UTC0 /usr/bin/stat -f %Sm -t %Y-%m-%dT%H:%M:%SZ "$p" 2>/dev/null || true)")"
			if [ -n "$at" ] && [ "${at//[!0-9]/}" -le "${prev//[!0-9]/}" ]; then
				sha="$(/usr/bin/shasum -a 256 "$p" 2>/dev/null | cut -c 1-12 || true)"
				case "$sha" in # anything but 12 hex digits is no hash to check the patch by: it is sent below
				[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f])
					skipped="${skipped}not repeated: a patch file last written $at, at or before an earlier attempt's report ($prev): $(wc -l <"$p" | tr -d ' ') lines, sha256 $sha; still in $shown/local-work"$'\n'
					continue
					;;
				esac
			fi
			[ "$sent" -gt 0 ] || printf '%sdiff\n' "$fence"
			sent=$((sent + 1))
			cat "$p"
		done
		[ "$sent" -eq 0 ] || printf '%s\n' "$fence"
		printf '%s' "$skipped"
		[ "$sent" -gt 0 ] || [ -n "$skipped" ] || printf 'none\n'
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
		out="$(AGENTSYNC_REPORT_RUN="$RUN_ID" with_timeout 120 "$bin" setup-report --out "$REPORT_PATH" --config "$CONFIG" </dev/null 2>&1)" || src=$?
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

# The report of an attempt a copy of the prompt that does not match this installer started (older, naming no
# version, or newer: B13 round 5 F18) is written to its own file, so the
# setup-report.md of the last attempt that was not stopped keeps its bytes, as bring-back.md does. Its first
# line says not to send it, and names that report only where one exists (B13 round 3 R17). Written after the
# report's redaction, so it names no path under this home but as ~/.
# Nor does its end invite sending it (B13 round 4 Q10, Q18): the "Send it:" line and the issue link are
# dropped here, not by the writer, so an agentsync older than this installer (a stopped copy stops before the
# install) and the shell fallback are covered alike. Both links start with ISSUE_URL, written after redaction;
# index() and LC_ALL=C read them byte for byte. An earlier run's "bring back:", issue link and NEXT lines, quoted in
# the report's install.out tail, go too, with one line saying so in their place, and the Redaction legend's
# "Read the report before sending it: a..." reads "A..." (B13 round 5 F16, F7): a field Mac has an earlier run.
stopped_report_note() { # KEPT-REPORT-PATH
	local tmp shown="$1" note="> This report is of an attempt that a copy of the setup prompt that does not match this installer started: do not send it or bring it back."
	case "$shown" in "$HOME"/*) shown='~'"${shown#"$HOME"}" ;; *) shown="${shown##*/}" ;; esac
	[ ! -f "$1" ] || note="$note The report of the last attempt that was not stopped is $shown."
	tmp="$(mktemp "${REPORT_PATH%/*}/.stopped-report.XXXXXX" 2>/dev/null)" || return 1
	if {
		printf '%s\n\n' "$note" &&
			LC_ALL=C awk -v u="$ISSUE_URL" -v r="Read the report before sending it: a" \
				-v gone="(an earlier run's bring-back, issue link and NEXT lines are left out of this report)" '
				index($0, "Send it: ") == 1 || index($0, u) == 1 { next }
				index($0, u) > 0 || index($0, "bring back: ") == 1 || index($0, "bring-back.md and copy") > 0 { if (shown++) next; $0 = gone }
				(j = index($0, r)) > 0 { $0 = substr($0, 1, j - 1) "A" substr($0, j + length(r)) }
				$0 == "" { blank++; next }
				{ for (; blank > 0; blank--) print ""; print }
				END { print "Not for sending: see the note at the top of this report." }' "$REPORT_PATH"
	} >"$tmp"; then
		mv -f "$tmp" "$REPORT_PATH"
	else
		rm -f "$tmp"
		return 1
	fi
}

on_exit() {
	local rc=$? suffix="" link secs stopped=0 kept="$REPORT_PATH" back="${REPORT_PATH%/*}/bring-back.md" tell
	trap - EXIT
	set +e
	[ "$PARSED" -eq 1 ] || exit "$rc"
	[ -z "$TICKER" ] || kill "$TICKER" 2>/dev/null
	step_end failed "$rc"
	if [ "$STARTED" -eq 1 ]; then
		END_AT="$(utc_now)"
		secs=$SECONDS
		log_at "$END_AT" "end rc=$rc seconds=$secs" # provisional: the report reads a finished run
		END_LINE="$END_AT run=$RUN_ID end rc=$rc seconds=$secs"
	fi
	if [ "$REPORT" -eq 1 ]; then
		# A copy of the prompt that is not this installer's started the attempt (--log-start said so and
		# stopped it). Its own text may still end at this report: the NEXT says what to do instead, and
		# nothing invites sending it: the report goes to its own file (stopped_report_note), no bring-back
		# file is written (the last one stays as it is), and no "bring back:" or issue link line is printed.
		if [ "$REPORT_ONLY" -eq 1 ] && tell="$(friction_attempt_stopped)"; then
			stopped=1
			REPORT_PATH="$SETUP_DIR/stopped-copy-report.md"
		fi
		write_report "$rc"
		case $? in
		0)
			suffix=" [setup report: $REPORT_PATH]"
			if [ "$stopped" -eq 1 ]; then
				stopped_report_note "$kept" || warn "could not mark $REPORT_PATH as not to be sent"
				# What to do instead is the fix the stop logged: copy the prompt again, or, for a copy newer
				# than a checkout that does not follow main, point the checkout there (B13 round 5 F17, F18).
				# Only a fix this installer writes for a newer copy is repeated; any other text in that column
				# (an older installer's, a hand edit) gets the copy-again sentence.
				case "$tell" in
				"copy the prompt again from $PROMPT_SOURCE (\"Set up on a new Mac: one prompt\") and paste it into a new session"[:\;]* | \
					"point this Mac's checkout at main of $CANON_REPO "*)
					tell="$tell (this installer is for setup prompt v$SETUP_PROMPT_COMPAT)"
					;;
				*) tell="copy the prompt again from $PROMPT_SOURCE (\"Set up on a new Mac: one prompt\"; this installer is for setup prompt v$SETUP_PROMPT_COMPAT) and paste it into a new session" ;;
				esac
				NEXT_MSG="this report is of an attempt that a copy of the setup prompt that does not match this installer started, so do not send it or bring it back: $tell"
				# R18: bring-back.md is named only where one exists, and not as "finished": a failed
				# attempt's report writes it too.
				[ ! -f "$back" ] || NEXT_MSG="$NEXT_MSG ($back was not touched: it still holds the report of the last attempt that was not stopped)"
			else
				# A stopped copy's report from before is stale from here (it names this file as the last
				# report that was not stopped), and nothing this attempt prints names it (B13 round 4 Q20).
				# A dry run (2) and a report not written (*) leave it.
				rm -f "$SETUP_DIR/stopped-copy-report.md"
				write_bring_back || warn "could not write ${REPORT_PATH%/*}/bring-back.md"
				link="$(report_issue_link)"
				if [ -n "$link" ]; then
					[ "$REPORT_ONLY" -eq 0 ] ||
						NEXT_MSG="review ${REPORT_PATH%/*}/bring-back.md and copy that one file back privately, or paste the setup report into the issue the link above opens (nothing is sent for you)"
					say "$ISSUE_LINK_PREFIX$link" # the last line before NEXT (step 3 of the setup prompt names it)
				fi
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
	# Last, so it also covers what this run wrote and what the agent wrote before it: --report-only is the
	# prompt's last command and comes right after the agent's last write to fix-request.md.
	[ "$DRY_RUN" -eq 1 ] || tighten_setup_tree "$SETUP_DIR"
	[ -z "$DOCTOR_LOG" ] || rm -f "$DOCTOR_LOG"
	[ -z "$SYNC_OUT" ] || rm -f "$SYNC_OUT"
	[ -z "$LOOP_OUT" ] || rm -f "$LOOP_OUT"
	[ -z "$NEXT_MSG" ] || say "NEXT: $NEXT_MSG$suffix"
	stop_capture
	exit "$rc"
}
# The issue link the setup report ends with (its last line, when it starts with $ISSUE_URL), else empty.
# Byte-wise: the shell report can hold a byte that is not UTF-8 (a log line's), where BSD sed stops.
report_issue_link() {
	local l
	l="$(LC_ALL=C sed '/^[[:space:]]*$/d' "$REPORT_PATH" 2>/dev/null | LC_ALL=C tail -1)"
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
# The installed agentsync's own interpreter (uv's tool environment: the path step 2 names TOOL_PY), when
# there is one. `uv tool dir` only prints a path.
tool_python() {
	local uv="" dir="$HOME/.local/share/uv/tools"
	if command -v uv >/dev/null 2>&1; then
		uv="$(command -v uv)"
	elif [ -x "$HOME/.local/bin/uv" ]; then
		uv="$HOME/.local/bin/uv"
	fi
	[ -z "$uv" ] || dir="$("$uv" tool dir 2>/dev/null || printf '%s' "$dir")"
	[ ! -x "$dir/agentsync/bin/python" ] || printf '%s' "$dir/agentsync/bin/python"
}
SYNCED_MARK="[synced]" # what --list-folders writes before a folder the config already syncs
# A sync reads the whole tree under a synced folder, so a listed folder inside one is synced too, and one
# that holds one would be read twice if it were added. Each gets its own mark.
INSIDE_MARK="[inside a synced folder]"
CONTAINS_MARK="[contains a synced folder]"
# The folders the config already syncs (see "Folders already synced" in the header), read by the interpreter
# $1: prints their number, then how many of them the file $2 (one folder path per line) does not hold, then
# each of those as "$SYNCED_MARK <path>" (the path the config has, sorted), then each line of the file, with
# "$SYNCED_MARK " before a folder that is one of them, "$INSIDE_MARK " before one inside one of them and
# "$CONTAINS_MARK " before one that holds one. So every synced folder is printed once with its mark, the
# ones no line of the file names first. Both sides go through agentsync's own rule, so a folder the config
# names through a link is still that folder. Both are then compared the way macOS names a folder, in any
# case and in either Unicode form (NFC + casefold, agentsync's own key for "the same path"): a config path
# typed in lower case, or with a composed é where the disk holds e and an accent, is that folder. Fails when
# that agentsync cannot load the config, and stops after 10 s. It uses only names every agentsync since
# 2026-09-29 has: step 1 of the setup prompt runs this before step 2 updates the tool. -I: nothing from the
# folder this runs in is imported.
synced_folders() { # PYTHON LIST
	with_timeout 10 "$1" -I -c '
import os, sys, unicodedata
from pathlib import Path
from agentsync.config import canonical_source_root, load_config


def key(path):
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", str(path)).casefold()).rstrip("/") + "/"


synced = {}
for s in load_config(Path(sys.argv[1])).sources:
    if s.kind.value == "local" and s.state.value == "live" and s.path is not None:
        synced.setdefault(key(s.path), str(s.path))
with open(sys.argv[2], "rb") as fh:
    listed = [os.fsdecode(line) for line in fh.read().split(b"\n") if line]
same, inside, contains = (mark + " " for mark in sys.argv[3:6])
shown, body = set(), []
for p in listed:
    folder = key(canonical_source_root(Path(p)))
    if folder in synced:
        shown.add(folder)
        body.append(same + p)
    elif any(folder.startswith(s) for s in synced):
        body.append(inside + p)
    elif any(s.startswith(folder) for s in synced):
        body.append(contains + p)
    else:
        body.append(p)
first = sorted(same + path for k, path in synced.items() if k not in shown)
out = [str(len(synced)), str(len(first)), *first, *body]
sys.stdout.buffer.write(os.fsencode("\n".join(out) + "\n"))
' "$CONFIG" "$2" "$SYNCED_MARK" "$INSIDE_MARK" "$CONTAINS_MARK"
}
# --list-folders: the folders 1-2 levels inside each ~/Library/CloudStorage/<provider>, names only (find reads
# directory entries and their metadata; no file is opened, so nothing is downloaded). Sets NEXT_MSG; returns
# 0 listed, 3 none, 4 denied or still asking.
list_folders() {
	local cs="$HOME/Library/CloudStorage" tmp e label rc total providers=0 denied="" asking="" term max=200
	local deadline=$((SECONDS + LIST_TOTAL)) t py synced="" unlisted=""
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
	sort "$tmp/list" | awk -v m="$max" 'NR <= m' >"$tmp/shown" # awk reads it all: no SIGPIPE for sort under pipefail
	# A Mac that already runs agentsync (field report 2026-10-07): the person chose its folders once, so the
	# list says which they are and nobody is asked again. Only with a config: without one the output is what
	# it always was. Also when a provider was denied or is still asking: the marks need only the config and
	# the lines that were listed, and the list still ends on that click (exit 4). And also when nothing was
	# listed: a Mac whose synced folders are all elsewhere is set up all the same.
	if [ -f "$CONFIG" ]; then
		py="$(tool_python)"
		if [ -n "$py" ] && synced_folders "$py" "$tmp/shown" >"$tmp/marked" 2>/dev/null; then
			synced="$(sed -n 1p "$tmp/marked")"
			unlisted="$(sed -n 2p "$tmp/marked")"
		fi
		case "$synced/$unlisted" in
		/* | */ | *[!0-9/]*)
			synced=""
			warn "could not read which folders $CONFIG already syncs (no installed agentsync loads it), so none is marked below"
			;;
		esac
	fi
	# Every synced folder is printed with its mark, so the reader can name each: the ones the list does not
	# reach (deeper than it goes, outside ~/Library/CloudStorage, past its cap) come first, then the list.
	if [ "${synced:-0}" -gt 0 ]; then
		if [ "$unlisted" -eq 0 ]; then
			say "already synced on this Mac: $synced folder(s) (marked $SYNCED_MARK below)"
		else
			say "already synced on this Mac: $synced folder(s) (marked $SYNCED_MARK below: first the $unlisted outside the list, then the list)"
		fi
		tail -n +3 "$tmp/marked"
	else
		cat "$tmp/shown"
	fi
	[ "$total" -le "$max" ] || say "($((total - max)) more)"
	rm -rf "$tmp"
	term="$(terminal_app)"
	term="this terminal app${term:+ ($term)}"
	rc=0
	if [ -n "$denied" ]; then
		rc=4
		NEXT_MSG="$term was denied access to files managed by $denied: allow it in System Settings > Privacy & Security > Files and Folders (turn on $denied under $term; a click, not a command), then re-run: $RERUN"
		step_end failed "$rc" "denied${synced:+ synced=$synced}"
	elif [ -n "$asking" ]; then
		rc=4
		NEXT_MSG="macOS is asking whether $term may access files managed by $asking: click Allow (or turn it on in System Settings > Privacy & Security > Files and Folders), then re-run: $RERUN"
		step_end failed "$rc" "tcc-pending${synced:+ synced=$synced}"
	elif [ "${synced:-0}" -gt 0 ]; then # nothing to choose: a run with no folder keeps them (step 4)
		# Before the two exits 3: with nothing listed this Mac is still set up, and the same run keeps it.
		if [ "$providers" -eq 0 ]; then
			NEXT_MSG="this Mac already syncs $synced folder(s), and a re-run keeps them: run $SELF (no folder to add is listed: OneDrive is not signed in on this Mac)"
		elif [ "$total" -eq 0 ]; then
			NEXT_MSG="this Mac already syncs $synced folder(s), and a re-run keeps them: run $SELF (no folder to add is listed: no folders are synced yet in $cs)"
		else
			NEXT_MSG="this Mac already syncs $synced folder(s), and a re-run keeps them: run $SELF, with one --source-local \"<folder>\" for each unmarked folder to add from the list above (none is needed)"
		fi
		step_end "done" 0 "listed-$total synced=$synced"
	elif [ "$providers" -eq 0 ]; then
		rc=3
		NEXT_MSG="OneDrive is not signed in on this Mac: sign in to OneDrive, then re-run: $RERUN"
		step_end failed "$rc" "not-signed-in${synced:+ synced=$synced}"
	elif [ "$total" -eq 0 ]; then
		rc=3
		NEXT_MSG="no folders are synced yet in $cs: sign in to OneDrive (or let it finish setting up), then re-run: $RERUN"
		step_end failed "$rc" "no-folders${synced:+ synced=$synced}"
	else
		NEXT_MSG="choose the folders to sync from the list above (project folders rather than a whole library), then run: $SELF --source-local \"<folder>\" (one --source-local per folder)"
		step_end "done" 0 "listed-$total${synced:+ synced=$synced}"
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
case "$REINSTALL_WAIT" in
'' | *[!0-9]*) usage_error "AGENTSYNC_REINSTALL_WAIT_SECONDS must be a whole number of seconds: $REINSTALL_WAIT" ;;
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
	read_commit "$SOURCE"
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
uv_run() { { "$@" </dev/null 2>&1 1>&3 3>&- | uv_filter >&2; } 3>&1; } # COMMAND...: its status (pipefail)
# The single-writer lock the background sync takes (<state_dir>/agentsync.lock), read by the tool about to be
# replaced from this run's config. Empty without that tool or config, or when it cannot say: a background job
# starts that interpreter on that config, so none can be running agentsync then.
sync_lock() { # CONFIG: its <state_dir>/agentsync.lock, read by the tool being replaced
	local py="$TOOL_DIR/agentsync/bin/python"
	[ -x "$py" ] && [ -f "$1" ] || return 0
	with_timeout 20 "$py" -I -c '
import sys
from pathlib import Path
from agentsync.config import load_config

print(load_config(Path(sys.argv[1])).state_paths.lock)
' "$1" 2>/dev/null || true
}
# The configs the LaunchAgents run with: the value after --config in each plist's ProgramArguments, fixed when
# the plist was written, so it can differ from this run's AGENTSYNC_CONFIG (B13 round 3 R114).
job_configs() {
	local l
	for l in "$POLL_LABEL" "$RECONCILE_LABEL"; do
		/usr/libexec/PlistBuddy -c 'Print :ProgramArguments' "$HOME/Library/LaunchAgents/$l.plist" 2>/dev/null |
			LC_ALL=C awk '{ sub(/^[ \t]+/, "") } want { print; exit } $0 == "--config" { want = 1 }' || true
	done
}
# The lock files the reinstall holds, one a line: those of this run's config and of each job's, once each, and
# only a regular file that exists (lockf -n creates none).
sync_locks() {
	local c l seen=$'\n'
	while IFS= read -r c; do
		[ -n "$c" ] || continue
		l="$(sync_lock "$c")"
		[ -n "$l" ] && [ -f "$l" ] && [ ! -L "$l" ] || continue
		case "$seen" in *$'\n'"$l"$'\n'*) continue ;; esac
		seen="$seen$l"$'\n'
		printf '%s\n' "$l"
	done <<EOF
$CONFIG
$(job_configs)
EOF
}
# The reinstall under the background sync's lock (see step 2 in the header). lockf takes the flock(2) lock the
# sync takes, keeps the file (-k), never creates one (-n: no lock file, no sync ever ran there), and writes
# nothing in it, so the next sync finds no body to break. Its own exit codes (64, 69, 71, 73, 75) mean it did
# not start uv, which never exits with those: then uv runs as before, not waited for. The probe's own error
# line is dropped (lockf prints it whatever -s says), so a lock it cannot open is reported once, by the call
# that runs uv.
# The checkout is installed as it is, not as a wheel built first, though --force deletes the environment
# before it builds (missing about 6 s on a loaded Mac, against 0.9 s for a built wheel): uv records a wheel as
# the install source (direct_url.json), and the report's install source, its git proxy reading and
# it-request's checkout guard all read the checkout from there (B13 round 3 R114, measured).
# AGENTSYNC_NOTE is the step's install.log note: what the script saw of the lock (B13 round 3 R34).
# The wait has one deadline, however many locks (B13 round 4 Q39, Q63): nested lockf -t timers each start when
# the lock before is taken, so their caps added up. Each lockf is started by a small sh that gives it the
# seconds left when it is reached and execs it: no lock is let go between the probe and the hold (a probe of
# all, then a hold at -t 0, let a sync started in between end the wait at once), and lockf's exit codes pass
# through. A deadline already past gives -t 0, so a wait of 0 still never waits.
# shellcheck disable=SC2016 # expanded by the sh that runs it
HOLD_SH='n=$(($1 - $(date +%s))); [ "$n" -gt 0 ] || n=0; k=$2; f=$3; shift 3; exec "$k" -k -n -s -t "$n" "$f" "$@"'
hold_locks() { # LOCKS: sets the caller's held to the nested holds, with one deadline REINSTALL_WAIT from now
	local l deadline=$(($(date +%s) + REINSTALL_WAIT))
	held=()
	while IFS= read -r l; do
		held+=(/bin/sh -c "$HOLD_SH" sh "$deadline" "$LOCKF" "$l")
	done <<<"$1"
}
uv_tool_install() {
	local args=(tool install --force --reinstall-package agentsync --python 3.11 "$SOURCE") locks l
	local held=() busy=0 rc=0
	AGENTSYNC_NOTE=""
	if [ "$DRY_RUN" -eq 1 ]; then
		run "$UV" "${args[@]}"
		return 0
	fi
	locks="$(sync_locks)"
	if [ -n "$locks" ] && [ -x "$LOCKF" ]; then
		while IFS= read -r l; do
			[ "$busy" -eq 75 ] || { "$LOCKF" -k -n -s -t 0 "$l" /usr/bin/true 2>/dev/null || busy=$?; }
		done <<<"$locks"
		[ "$busy" -eq 75 ] || busy=0
		if [ "$busy" -eq 75 ]; then
			say "agentsync: a background sync is running; the reinstall waits for it to end (at most ${REINSTALL_WAIT}s)"
			ticker "agentsync: reinstalling after the background sync ends" &
		else # a sync that starts after this probe is waited for too, so a line still comes
			ticker "agentsync: reinstalling" &
		fi
		TICKER=$!
		hold_locks "$locks"
		uv_run "${held[@]}" "$UV" "${args[@]}" || rc=$?
		# A tick that starts the tool's Python while uv deletes the environment fails uv ("Directory not
		# empty") and leaves no interpreter: once more, still under the locks.
		if [ "$rc" -ne 0 ] && [ "$rc" -ne 75 ] && [ ! -x "$TOOL_DIR/agentsync/bin/python" ] &&
			[ -d "$TOOL_DIR/agentsync" ]; then
			say "agentsync: uv left the tool environment half removed (a background run started in it?); installing once more"
			rc=0
			hold_locks "$locks" # a deadline of its own: the first one may be past
			uv_run "${held[@]}" "$UV" "${args[@]}" || rc=$?
		fi
		kill "$TICKER" 2>/dev/null || true
		TICKER=""
		case "$rc" in
		0) [ "$busy" -ne 75 ] || AGENTSYNC_NOTE="waited-for-a-background-sync" ;;
		64 | 69 | 71 | 73) AGENTSYNC_NOTE="lock-not-taken-rc$rc" ;;
		75) AGENTSYNC_NOTE="wait-ran-out-after-${REINSTALL_WAIT}s" ;;
		esac
		case "$rc" in
		64 | 69 | 71 | 73 | 75) ;;
		*) return "$rc" ;;
		esac
		[ "$rc" -ne 75 ] ||
			say "agentsync: the background sync is still running after ${REINSTALL_WAIT}s; reinstalling without waiting longer"
	fi
	uv_run "$UV" "${args[@]}"
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
	uv_tool_install || {
		UV_RC=$? # fail reads the failed command's status from $?: each call below restores it
		if [ ! -x "$TOOL_DIR/agentsync/bin/python" ] && [ -d "$TOOL_DIR/agentsync" ]; then
			(exit "$UV_RC") || fail "uv tool install failed and left the tool environment without its interpreter (a background run started in it while uv removed it, or the network failed after the removal): run this command again"
		fi
		(exit "$UV_RC") || fail "uv tool install failed (network, proxy or TLS inspection? set HTTPS_PROXY / UV_SYSTEM_CERTS=1)"
	}
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
	step_end "done" 0 "$AGENTSYNC_NOTE"
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
step_end "$LAUNCHER_RESULT" 0 "$LAUNCHER_NOTE"
if [ "$LAUNCHER_STATE" = "installed" ] && [ "$DRY_RUN" -eq 0 ]; then
	say "launcher: $(/usr/bin/codesign -d -r- "$APP_DEST" 2>&1 | sed -n 's/^#* *designated => /designated requirement: /p')"
fi

# ------------------------------------------------------------------------------------------------- 3. helpers
step_start helpers
# The on-device OCR helper (agentsync.convert.ocr), with or without background sync: this is the one place it
# is compiled, so status, a sync and the LaunchAgent never start a compiler. The module reads [convert] ocr
# itself (off builds nothing) and prints one line. OCR is optional: a helper that does not build is that
# line, never a failed step. Without developer tools nothing is tried, and the line says so: status sends the
# reader here for the helper, and a run that said nothing would send them round again. The media helper
# (agentsync.convert.media, which reads recordings) is built the same way right after it, with its own line;
# [convert] recordings = false builds nothing. The speech helper (agentsync.convert.speech, a SwiftPM package
# over FluidAudio, which hears recordings) follows the media helper the same way: one "speech: ..." line, off
# whenever the media helper is, and never a download of a model.
# -I: with -m alone Python puts the folder this script is run from first on its import path, so a random.py
# or types.py lying there would be imported in place of the standard library's.
if have_devtools; then
	if [ "$DRY_RUN" -eq 1 ]; then
		run "$TOOL_PY" -I -m agentsync.convert.ocr
		run "$TOOL_PY" -I -m agentsync.convert.media
		run "$TOOL_PY" -I -m agentsync.convert.speech
		step_end "done"
	elif [ -x "$TOOL_PY" ]; then
		# One build with a ticker naming its helper, killed before the helper's line, so the ticks between
		# the media line and the speech line say they were the speech helper's build.
		build_helper() { # MODULE WHAT FALLBACK
			local line
			ticker "helpers: building the $2" &
			TICKER=$!
			line="$(AGENTSYNC_CONFIG="$CONFIG" "$TOOL_PY" -I -m "agentsync.convert.$1" </dev/null 2>/dev/null | head -n 1)" || true
			kill "$TICKER" 2>/dev/null || true
			TICKER=""
			say "${line:-$3}"
		}
		build_helper ocr "OCR helper" "OCR helper: not built (the build did not run)"
		build_helper media "media helper" "media helper: not built (the build did not run)"
		build_helper speech "speech helper" "speech: not built (the build did not run)"
		step_end "done"
	else
		step_end skipped 0 no-interpreter
	fi
else
	say "OCR helper: not built (no Xcode or Command Line Tools)"
	say "media helper: not built (no Xcode or Command Line Tools)"
	say "speech: not built (no Xcode or Command Line Tools)"
	step_end skipped 0 no-devtools
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
# How many folders the config syncs (see "Folders already synced" in the header), by the agentsync step 2
# installed: the count --list-folders gives. Empty when it cannot say (a dry run, or a config it does not load).
synced_count() {
	local n=""
	if [ "$DRY_RUN" -eq 0 ] && [ -x "$TOOL_PY" ] && [ -f "$CONFIG" ]; then
		n="$(synced_folders "$TOOL_PY" /dev/null 2>/dev/null | head -n 1 || true)"
	fi
	case "$n" in
	*[!0-9]*) n="" ;;
	esac
	printf '%s' "$n"
}
KEPT=0 # the folders synced before this step, and below the ones it added: both in its log line
[ "$CONFIG_STATE" = "created" ] || KEPT="$(synced_count)"
# add-source ends every call on two summary lines, "docs repo ..." and "sources: ..." (cli._ensure_setup).
# With several folders that block came once per folder (field reports 2026-10-06 and 2026-10-07: 16 source
# ids, twice). Call I of N keeps the "docs repo" line only as the first call, the one that can say "created"
# and count the first files written, and the "sources:" line only as the last, the one that lists every
# folder. With one folder nothing is dropped. Every other line passes, stderr is not read, and the call's
# exit status is the pipeline's (pipefail). LC_ALL=C: a byte that is no character must not fail the filter.
add_source_filter() { # I N
	LC_ALL=C awk -v i="$1" -v n="$2" '(/^docs repo / && i > 1) || (/^sources: / && i < n) { next } { print }'
}
if [ "${#FOLDERS[@]}" -gt 0 ]; then
	nth=0
	for f in "${FOLDERS[@]}"; do
		nth=$((nth + 1))
		run "$AGENTSYNC" add-source "$f" --config "$CONFIG" </dev/null | add_source_filter "$nth" "${#FOLDERS[@]}" ||
			fail "agentsync add-source $f failed (see the error above)"
	done
else
	run "$AGENTSYNC" init --config "$CONFIG" </dev/null || fail "agentsync init failed (see the error above)"
	[ "$CONFIG_STATE" = "created" ] || say "config: $CONFIG exists (inbox ensured)"
fi
# What became of the folder choice, as counts: a re-run with no --source-local keeps what the person chose
# before, and one with it adds only the folders not yet there. One line for the reader, and two fields on
# the step's log line for the setup report.
FOLDER_COUNTS=""
NOW="$(synced_count)"
if [ -n "$KEPT" ] && [ -n "$NOW" ] && [ "$NOW" -ge "$KEPT" ]; then
	ADDED=$((NOW - KEPT))
	FOLDER_COUNTS=" kept=$KEPT added=$ADDED"
	if [ "$KEPT" -gt 0 ] && [ "$ADDED" -eq 0 ]; then
		say "folders: kept the $KEPT already synced (none added)"
	elif [ "$KEPT" -gt 0 ]; then
		say "folders: $ADDED added to the $KEPT already synced"
	elif [ "$ADDED" -gt 0 ]; then
		say "folders: $ADDED added (none was synced before)"
	fi
fi
if [ "$CONFIG_STATE" = "created" ]; then
	step_end "done" 0 "created$FOLDER_COUNTS"
elif [ "${#FOLDERS[@]}" -gt 0 ]; then
	step_end "done" 0 "add-source$FOLDER_COUNTS"
else
	step_end skipped 0 "exists$FOLDER_COUNTS"
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
# The pandoc status is about to check: [convert] pandoc_path, else the one bundled with the tool (the rule
# of ops/doctor.py's pandoc check), read by the tool step 2 installed. Empty when it cannot say (no
# interpreter, a config that does not load): status then reports that. -I: see the OCR helper above.
status_pandoc() {
	[ -x "$TOOL_PY" ] && [ -f "$CONFIG" ] || return 0
	with_timeout 20 "$TOOL_PY" -I -c '
import sys
from pathlib import Path
from agentsync.config import load_config

pandoc = load_config(Path(sys.argv[1])).convert.pandoc_path
if pandoc is None:
    import pypandoc

    pandoc = Path(pypandoc.__file__).parent / "files" / "pandoc"
print(pandoc)
' "$CONFIG" 2>/dev/null || true
}
# Start that pandoc once before status does (see step 5 in the header): its first start after an install can
# take longer than the 60 s status gives it, and this wait is the installer's own, with progress lines. At
# most $PANDOC_START_SECONDS, the time a conversion gives pandoc; past that it is stopped and status says
# what it finds. Never fails the run: a pandoc that is missing or broken is the check's to report, with its
# fix. Quiet when the start is quick, which every start but the first is. Its clock is $PROGRESS_SINCE (see
# with_progress): the status wait after it goes on counting from this function's last line, or from its
# start when it printed none, the time status_pandoc took included.
PANDOC_START_SECONDS=300
start_pandoc_once() {
	local pandoc pid t0=$SECONDS shown=0
	PROGRESS_SINCE=$SECONDS
	pandoc="$(status_pandoc)"
	[ -n "$pandoc" ] && [ -x "$pandoc" ] || return 0
	"$pandoc" --version </dev/null >/dev/null 2>&1 &
	pid=$!
	while kill -0 "$pid" 2>/dev/null; do
		if [ $((SECONDS - t0)) -ge "$PANDOC_START_SECONDS" ]; then
			kill "$pid" 2>/dev/null || true
			wait "$pid" 2>/dev/null || true
			PROGRESS_SINCE=$SECONDS
			say "pandoc: no answer after ${PANDOC_START_SECONDS}s; status checks it next"
			return 0
		fi
		if [ $((SECONDS - PROGRESS_SINCE)) -ge "$PROGRESS_EVERY" ]; then
			PROGRESS_SINCE=$SECONDS
			shown=1
			say "pandoc: still running, $((SECONDS - t0))s (the first start after an install can take a minute; later ones are quick)"
		fi
		sleep 0.2
	done
	wait "$pid" 2>/dev/null || true
	[ "$shown" -eq 1 ] || return 0
	PROGRESS_SINCE=$SECONDS
	say "pandoc: started after $((SECONDS - t0))s"
}
status_run() { "$AGENTSYNC" status --config "$CONFIG" | tee "$DOCTOR_LOG"; } # the pipeline's status is status's
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
	start_pandoc_once
	# With progress lines, like the first sync: status can wait two minutes on one folder macOS holds for a
	# click, and it prints its lines only at its end.
	with_progress "status" status_run || DOCTOR_RC=$?
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
			{ for (i = 1; i < NF; i++) { if ($i == "converted") c += $(i + 1); if ($i == "deferred") d += $(i + 1)
				if ($i == "again" && $(i - 1) == "read") r += $(i + 1) } }
			END { printf "converted-%d-deferred-%d", c, d; if (r > 0) printf "-reread-%d", r }')"
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
	# A sync still running (step 2's wait ran out, or one started since) holds the config's lock, and this one
	# waits for it, up to 10 minutes, saying so only on stderr: the note names the wait, so install.log and the
	# Summary do not put its seconds on conversion (B13 round 4 Q29). The probe is uv_tool_install's.
	SYNC_LOCK_BUSY=0
	l="$(sync_lock "$CONFIG")"
	if [ -n "$l" ] && [ -f "$l" ] && [ -x "$LOCKF" ]; then
		"$LOCKF" -k -n -s -t 0 "$l" /usr/bin/true 2>/dev/null || [ $? -ne 75 ] || SYNC_LOCK_BUSY=1
	fi
	with_progress "$SYNC_LABEL" first_sync_run || SYNC_RC=$?
	show_first_sync "$SYNC_RC"
	if [ "$SYNC_RC" -eq 0 ]; then
		[ "$SYNC_LOCK_BUSY" -eq 0 ] || FIRST_SYNC_NOTE="${FIRST_SYNC_NOTE:+$FIRST_SYNC_NOTE-}waited-for-the-lock"
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
*) say "note: $BIN_DIR is not on PATH; nothing to change: this setup and its NEXT lines call agentsync by its full path, ~/.local/bin/agentsync" ;;
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
