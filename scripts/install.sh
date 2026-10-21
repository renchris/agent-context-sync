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
#                           setup log. Exit 0 listed; 3 none (OneDrive not signed in, or nothing synced yet),
#                           or a folder this Mac already syncs under ~/Library/CloudStorage is not on disk and
#                           its app is signed out (its provider folder gone with no other folder of that app,
#                           or there and empty), even when other folders were listed; 4 this terminal app was
#                           denied access (macOS "Operation not permitted"), or macOS is still asking; its
#                           NEXT: line says which and names the click. On a Mac whose
#                           config already syncs folders (see "Folders already synced" below) the list starts
#                           with "already synced on this Mac: 1 folder ..." (or N folders) and each has
#                           "[synced] " before its path (one the list does not reach is printed first, as
#                           the config names it); a listed folder inside one of them has "[inside a
#                           synced folder] " there, and one that holds one "[contains a synced folder] "; its
#                           NEXT: names the command that keeps them. A list that exits 4 has that line and
#                           the marks too, for what it could list, and still ends on the click. With nothing
#                           to list, such a Mac gets that line, its synced folders and exit 0, not 3, as
#                           long as no synced folder's app is signed out: then the list still prints the
#                           [synced] lines but exits 3, and its NEXT: asks for that app's sign-in, listed or
#                           not. A synced folder gone while its app is signed in (renamed, moved or deleted
#                           there, or not synced on this Mac), or in a SharePoint library not synced on this Mac beside its
#                           organization's OneDrive folder, keeps exit 0, and the NEXT: names it: step 2's
#                           status names its fix
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
# installed agentsync cannot read (or no installed agentsync) is a warning, and no folder is marked; the folder
# question NEXT, or either sign-in one, then adds that the config is there and that a run with no --source-local keeps whatever it
# syncs, with no count, and, when the reader ran out of time, that a re-run of the list can mark them (B13
# round 8 J17). A run with no --source-local over such a config keeps every source, updates agentsync, and runs status and a sync
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
#      trust store for TLS; UV_TOOL_BIN_DIR pinned to ~/.local/bin, so the binary sits where the guides say;
#      the dependencies at the versions of the checkout's uv.lock when uv can export it); skipped when the last
#      install came from this same clean checkout commit and the tool environment's bundled pandoc is the file
#      pypandoc_binary installed (doctor's fix for that pandoc is this re-run). With --force, uv rebuilds the
#      tool environment the background sync runs from: the package, its interpreter and the bundled pandoc
#      are missing for a moment, and a cycle already running meets a new build. So the reinstall holds that
#      sync's single-writer lock: <state_dir>/agentsync.lock of the config, read by the tool being replaced,
#      held with /usr/bin/lockf, which writes nothing in it.
#      It holds the lock of this run's config and of the config each LaunchAgent's plist names after --config
#      (they can differ).
#      It waits for a running sync, at most $AGENTSYNC_REINSTALL_WAIT_SECONDS (default 120 s, for all its locks
#      together; 0 never waits), with "agentsync: reinstalling after the other sync ends, still running, <N>s" lines
#      naming who started the sync when its holder sidecar (agentsync.lock.json, B13 round 10 L73 on) says so for
#      the lock body's own pid, else "the background job, or a sync started another way" (B13 round 11 M43, M60).
#      A background sync that starts meanwhile and reaches its lock is skipped (exit 75); a launchd tick that
#      starts while the environment is being recreated ends in the launcher's SPAWN_FAILED (exit 71) or an
#      import error instead, and uv itself may fail on the half-removed environment, so a failed locked
#      install whose interpreter is gone runs once more. launchd starts the next sync at its interval. A sync
#      still running after the wait, or a lock lockf cannot take, leaves the reinstall as it was: not waited
#      for. A run killed with SIGKILL during the wait leaves lockf to run the reinstall once the lock frees,
#      still under the lock. install.log's step=agentsync line notes what the script saw:
#      waited-for-a-background-sync, wait-ran-out-after-<N>s or lock-not-taken-rc<N> (a sync that starts
#      after the probe is waited for too, with no note); a lock with an empty body, which no sync need hold,
#      notes waited-for-an-unrecorded-holder or lock-wait-ran-out-after-<N>s instead (B13 round 20 X43)
#   3. with --confirm-install-agent only: the signed launcher at ~/Applications/AgentSyncLauncher.app, built
#      with launcher/build.sh when developer tools exist (SIGN_IDENTITY passes through for a Developer ID
#      build), or copied from --launcher PATH; else a valid installed one is kept. An up-to-date one is never
#      rebuilt, because an ad-hoc rebuild is a new TCC identity and macOS would ask again (the developer
#      variable AGENTSYNC_REBUILD_LAUNCHER=1 rebuilds it anyway)
#      Then, with or without that option, a step of its own, logged `helpers` (done when the modules ran,
#      note not-built-<names> such as not-built-speech or not-built-ocr+speech when a build exited non-zero;
#      skipped, note no-devtools, without developer tools; skipped, note config-unreadable, when the config
#      cannot be read: the media and speech lines then say so, unrun). When developer tools exist: the
#      on-device OCR helper (python -I -m agentsync.convert.ocr with the tool's interpreter; nothing else ever
#      compiles it).
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
#      from the first call, printed beside "sources: ..." from the last, with the inbox line ("added inbox
#      source ...") the first call prints
#   5. status: first the pandoc that status checks ([convert] pandoc_path, else the operator's native
#      arm64 copy when there is one, else the one bundled with the tool: the one the converters run) is
#      started once, as "pandoc --version", outside that check's 60 s. The bundled one is an Intel
#      program: on Apple silicon macOS prepares each new copy at its first start (10 to 67 s measured, under
#      1 s after), and step 2 writes a new copy at every update, so the check ran out of time there and the
#      run exited 1 (rehearsal 2026-10-07). A slow start prints "pandoc: still running, <N>s (...)" lines,
#      then "pandoc: started after <N>s"; a quick one prints nothing; one with no answer in 300 s is stopped
#      and left to status. Then agentsync status (its TCC probe may raise the one-time "wants to access
#      files managed by" prompt). Any [FAIL] line stops steps 6-8 and the run exits 1, except the launcher's
#      own TCC_PENDING (a "tcc.<source>" line, with --confirm-install-agent or LaunchAgents an earlier run
#      installed), which the wait (step 8) asks the Allow for when this run installs them. A listing macOS holds for an Allow click in this terminal is a
#      source.<id>.listable [FAIL]: it stops them. Every [FAIL] line names its fix; for a check that ran out
#      of time the fix is this same command again, then what to do if the same line comes back. When every
#      [FAIL] that stops them is the person's (a folder or file to put back or sync, a [[source]] path or
#      sentinel, a [[source]] block to put back, a [policy] or [governance] value, a docs-repo remote to
#      remove or allow, a state file that is damaged or another account's, a sign-in, a [convert] pandoc_path,
#      Rosetta to install or a native pandoc copy), the NEXT says so and sends the agent on to the report
#      (B13 round 13 O11, O30; round 16 T5, T9; round 17 lane E; round 18 V53)
#   6. first-sync, whenever the config has a folder to sync (a [[source]] other than the inbox) and step 5 has
#      no [FAIL] that stops it: agentsync sync --once --materialise-budget 0 (a non-zero exit fails the run;
#      75, a cycle already running, skips): no downloads, so the files already on this Mac are converted now,
#      no online-only file is downloaded here and this step's time does not grow with the folders' size; each
#      later sync downloads and converts the online-only files it deferred, within its per-run budget. Its
#      output is shown as its "converted N, deferred M online-only[, read again R | , first read R]" line(s), each prefixed
#      "first sync: " (and logged as the step's note converted-N-deferred-M, with -reread-R when R > 0, then
#      -first-F when F of the R were first reads, and
#      -waited-for-the-lock when another sync held the config's lock as it started), else as all it printed. The flag is passed unconditionally: sync --help hides it, and the agentsync
#      installed here always has it. When another sync holds the config's lock as it starts, a line before
#      the sync says it waits for that one to end, at most 10 minutes; one still running then skips it (75)
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
# status is printed and the NEXT says to fix it, or, as step 5's, whose fix it is; a listing macOS held for an Allow click in this terminal is
# the NEXT and the run exits 1. A run without a folder over an existing config ends on that status's NEXT
# too (its "no folder is synced yet" step). Nothing
# else it prints is an instruction: agentsync is always called by its full path (~/.local/bin/agentsync), so
# nothing needs adding to PATH or to a shell profile; uv's "not on your PATH ... update-shell" hint is
# filtered out of its output; every other agentsync call gets AGENTSYNC_NO_NEXT_HINT=1 (no "next:" hints of
# its own; the first sync also gets AGENTSYNC_QUIET_LOCK_WAIT=1, since that step prints its own lock-wait line
# and ticker, and AGENTSYNC_LOCK_WAIT_ANNOUNCED, whether that line was printed), and with --confirm-install-agent step 5 gets AGENTSYNC_AGENT_STEP_PENDING=1 (its launchd.* lines
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
# synced, 0 included, or "synced=unreadable marks=<why>" when a config is there and it could not be read (why: no-tool, timeout,
# older:<the name the installed agentsync lacks>, failed or failed:<the class of its error>); the config line ends with kept=N added=M when agentsync could count them: the folders
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
SETUP_PROMPT_COMPAT=29 # the README prompt's "setup prompt vN": bump both on any change to its text (see the header)
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
# The proxy values in the config files git reads, one a line: the files setup_report._git_proxy_urls reads,
# the global ones and the repository config of two checkouts (checkout_git_configs, a linked worktree too):
# the one holding this script, which agentsync is installed from, and the one the prompt clones. Read as files: the logged git error names git's proxy, and step 1 may have stopped
# with no git. An empty "proxy =" (git's off switch) prints nothing.
git_proxy_values() {
	local g
	git_config_files | while IFS= read -r g; do
		# An unreadable file is skipped: under set -e a failed sed would end the caller's whole list (the
		# LaunchAgent's proxies after it went unread).
		[ ! -f "$g" ] ||
			LC_ALL=C sed -n 's/^[[:space:]]*[Pp][Rr][Oo][Xx][Yy][[:space:]]*=[[:space:]]*"\{0,1\}\([^"[:space:];#][^"[:space:];#]*\).*/\1/p' "$g" ||
			true
	done 2>/dev/null
}
# The config files git reads that the report reads too, one a line (setup_report._git_config_names).
git_config_files() {
	printf '%s\n' "$HOME/.gitconfig" "${XDG_CONFIG_HOME:-$HOME/.config}/git/config" /etc/gitconfig
	[ -z "${repo:-}" ] || checkout_git_configs "$repo"
	checkout_git_configs "$HOME/src/agent-context-sync"
}
# The bases of git's URL rewrites ([url "<base>"] with an insteadOf key), one a line, from the same files, as
# setup_report._git_rewrite_bases reads them (B13 round 7 H20): v15's git stop names a rewrite as a cause, and
# git's error then names its host. As files, with no git process; pushInsteadOf is left out.
git_rewrite_bases() {
	local g
	git_config_files | while IFS= read -r g; do
		[ ! -f "$g" ] || LC_ALL=C awk '
			/^[ \t]*\[/ {
				base = ""
				if (match($0, /^[ \t]*\[[ \t]*[Uu][Rr][Ll][ \t]+"[^"]+"[ \t]*\]/)) {
					base = $0; sub(/^[^"]*"/, "", base); sub(/"[ \t]*\].*$/, "", base)
					# [url "<base>"] insteadOf = <url> on one line, which git applies (B13 round 20 X8): the rest
					# is cut after the matched header, so a base holding "]" (an IPv6 host) keeps its key
					rest = substr($0, RSTART + RLENGTH)
					if (tolower(rest) ~ /^[ \t]*insteadof[ \t]*(=|$)/) print base
				}
				next
			}
			base != "" && tolower($0) ~ /^[ \t]*insteadof[ \t]*(=|$)/ { print base }
		' "$g" || true
	done 2>/dev/null | LC_ALL=C awk '!seen[$0]++'
}
# Each URL rewrite as "<insteadOf value> TAB <base>", one a line, from the same files: a [url "<base>"] section
# can hold several insteadOf lines. fallback_install_source_line applies them as `git remote get-url` does,
# with no git process (B13 round 19 W7, W23); pushInsteadOf is left out.
git_rewrite_pairs() {
	local g repo="" # the files `git -C ~/src/agent-context-sync` reads: not the clone running this script
	git_config_files | while IFS= read -r g; do
		[ ! -f "$g" ] || LC_ALL=C awk '
			function pair(line,    v) { # an insteadOf key: its value after an "x", so an empty one (every URL) survives read
				if (base == "" || tolower(line) !~ /^[ \t]*insteadof[ \t]*=/) return
				v = line; sub(/^[^=]*=[ \t]*/, "", v); sub(/[ \t]+[#;].*$/, "", v); sub(/[ \t\r]*$/, "", v)
				gsub(/^"|"$/, "", v)
				print "x" v "\t" base
			}
			/^[ \t]*\[/ {
				base = ""
				if (match($0, /^[ \t]*\[[ \t]*[Uu][Rr][Ll][ \t]+"[^"]+"[ \t]*\]/)) {
					base = $0; sub(/^[^"]*"/, "", base); sub(/"[ \t]*\].*$/, "", base)
					# [url "<base>"] insteadOf = <value> on one line, cut after the matched header (B13 round 20 X8)
					pair(substr($0, RSTART + RLENGTH))
				}
				next
			}
			{ pair($0) }
		' "$g" || true
	done 2>/dev/null
}
# The hosts of the git remotes' URLs ([remote "<name>"] with a url key), one a line, from the same files, as
# setup_report._git_remotes reads them (B13 round 16 T88): a 403 or a TLS error names the host of a
# hand-set origin, and step 1 tells the agent to log it. Only a URL with "://" (not file://) or git's
# [user@]host:path form, a host with a dot and none of PUBLIC_HOSTS or GitHub's: a local remote ("../x", a bare
# word, an absolute path) is no host, and "<proxy-N>" in place of ".." or "agent-context-sync" broke paths.
git_remote_entries() { # host TAB url, one a line; git_remote_hosts and git_remote_urls read them
	local g
	# the docs repo's own config last, as setup_report reads it, so no number moves (B13 round 17 U93); the
	# shell reads only its default place, since it never loads the config
	{
		git_config_files
		checkout_git_configs "$HOME/agent-context/docs"
	} | while IFS= read -r g; do
		[ ! -f "$g" ] || PUBLIC="$PUBLIC_HOSTS" LC_ALL=C awk '
			/^[ \t]*\[/ { remote = ($0 ~ /^[ \t]*\[[ \t]*[Rr][Ee][Mm][Oo][Tt][Ee][ \t]+"[^"]*"[ \t]*\]/); next }
			remote && tolower($0) ~ /^[ \t]*url[ \t]*=/ {
				u = $0; sub(/^[^=]*=[ \t]*/, "", u); sub(/[ \t\r]*$/, "", u); gsub(/^"|"$/, "", u)
				if (u ~ /:\/\//) {
					if (tolower(u) ~ /^file:\/\//) next
					h = u; sub(/^[^:]*:\/\//, "", h); sub(/[\/?#].*$/, "", h); sub(/^.*@/, "", h); sub(/:.*$/, "", h)
				} else {
					c = index(u, ":")
					if (c == 0) next
					h = substr(u, 1, c - 1)
					if (h ~ /\//) next
					sub(/^.*@/, "", h)
				}
				l = tolower(h)
				if (h !~ /^[A-Za-z0-9._-]+$/ || h !~ /\./ || l == "github.com" || l ~ /\.github(usercontent)?\.com$/) next
				if (index(" " ENVIRON["PUBLIC"] " ", " " l " ")) next
				print h "\t" u
			}
		' "$g" || true
	done 2>/dev/null | LC_ALL=C awk -F '\t' '!seen[tolower($1)]++'
}
git_remote_hosts() { git_remote_entries | LC_ALL=C cut -f 1; }
# The first URL of each host, as setup_report._git_remotes gives it: the redaction pairs take it as a rewrite
# base, so a path with a company word in it is a <proxy-N> too (B13 round 17 U15).
git_remote_urls() { git_remote_entries | LC_ALL=C cut -f 2-; }
# The repository config files git reads for checkout DIR, one a line, as setup_report._checkout_git_configs
# reads them: <common dir>/config and <git dir>/config.worktree, where .git is the git dir or a file naming it
# ("gitdir: <path>", a linked worktree's or a submodule's; relative to DIR) and the git dir's commondir file
# names the common dir (relative to the git dir). No git process: step 1 may have stopped without the tools.
checkout_git_configs() { # DIR
	local dirs
	dirs="$(checkout_git_dirs "$1")"
	[ -n "$dirs" ] || return 0
	printf '%s\n' "${dirs#*$'\n'}/config" "${dirs%%$'\n'*}/config.worktree"
}
# The git dir and the common dir of checkout DIR, one a line, as setup_report._checkout_git_dirs reads them;
# nothing when DIR has no .git, or a .git file that does not start "gitdir:". The common dir holds the
# branches, packed-refs and reflogs of a linked worktree or a --separate-git-dir clone (B13 round 11 M49, M70).
checkout_git_dirs() { # DIR
	local dot="$1/.git" gd c=""
	if [ -d "$dot" ]; then
		gd="$dot"
	elif [ -f "$dot" ]; then
		# trailing white space (a CRLF line) dropped, as Python's strip() drops it
		gd="$(LC_ALL=C sed -n '1s/^gitdir:[[:space:]]*//p' "$dot" 2>/dev/null | LC_ALL=C sed 's/[[:space:]]*$//')"
		[ -n "$gd" ] || return 0
		case "$gd" in /*) ;; *) gd="$1/$gd" ;; esac
	else
		return 0
	fi
	[ ! -f "$gd/commondir" ] || c="$(LC_ALL=C sed -n '1{s/^[[:space:]]*//;s/[[:space:]]*$//;p;}' "$gd/commondir" 2>/dev/null)"
	case "$c" in "") c="$gd" ;; /*) ;; *) c="$gd/$c" ;; esac
	printf '%s\n' "$gd" "$c"
}
# The public hosts the installer reaches, which a resolver message may name and which stay readable:
# setup_report._PUBLIC_HOSTS (a test keeps them equal; scripts/download_hosts.py lists where they come from).
PUBLIC_HOSTS="github.com api.github.com objects.githubusercontent.com release-assets.githubusercontent.com"
PUBLIC_HOSTS="$PUBLIC_HOSTS astral.sh pypi.org files.pythonhosted.org"
# The proxy and rewrite hosts in force when the agent logs a line, kept in the friction log's folder as
# proxy-hosts (one host a line, no userinfo or port, 0600), so a report written after the person unset a stale
# proxy (the git stop asks them to) still redacts the host an earlier line quotes: both readers register them
# last (B13 round 11 M29). Lower case, then as written where that differs (B13 round 12 N42), as
# friction_proxy_hosts gives them; both readers now match a proxy value in any ASCII case (B13 round 13 O2,
# O67), so the second line only takes the lower-case key's number, as before. github.com, which every rewrite
# of this repository names, is left out.
record_proxy_hosts() {
	# repo: the checkout holding this script, whose config git_config_files reads too; --log-start and --log
	# run before the script sets it
	local f d u h w x out="" repo
	repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)" || repo=""
	f="$(friction_file)"
	d="${f%/*}"
	[ "$d" != "$f" ] || d="."
	[ -d "$d" ] && [ ! -L "$d/proxy-hosts" ] || return 0
	while IFS= read -r u; do
		h="${u#*://}"
		h="${h%%[/?#]*}"
		h="${h##*@}"
		w="${h%%:*}"
		h="$(printf '%s' "$w" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
		case "$h" in "" | github.com | direct | none | off | *[!a-z0-9._-]*) continue ;; esac
		for x in "$h" "$w"; do
			case $'\n'"$out" in *$'\n'"$x"$'\n'*) continue ;; esac
			[ ! -f "$d/proxy-hosts" ] || ! LC_ALL=C grep -qxF -- "$x" "$d/proxy-hosts" 2>/dev/null || continue
			out+="$x"$'\n'
		done
	done < <(
		printf '%s\n' "${HTTPS_PROXY:-}" "${https_proxy:-}" "${ALL_PROXY:-}" "${all_proxy:-}" "${HTTP_PROXY:-}" \
			"${http_proxy:-}"
		git_proxy_values
		git_rewrite_bases
		git_remote_hosts # B13 round 16 T88
	)
	[ -n "$out" ] || return 0
	(
		umask 077
		printf '%s' "$out" >>"$d/proxy-hosts"
	) 2>/dev/null || return 0
	if [ -O "$d/proxy-hosts" ]; then chmod 600 "$d/proxy-hosts" 2>/dev/null || true; fi
}
# The hosts a resolver message in the friction log names ("Could not resolve proxy: <host>", "Could not resolve
# host: <host>", "Failed to connect to <host> port <n>"), in log order, lower case, less a trailing dot or
# hyphen, and none of PUBLIC_HOSTS: a proxy the report's shell no longer has set is still named there (B13 round
# 11 M29). setup_report._resolver_hosts reads the same.
friction_proxy_hosts() {
	local f h w
	f="$(friction_file)"
	[ -f "$f" ] || return 0
	LC_ALL=C grep -oE "Could not resolve (proxy|host): [A-Za-z0-9._-]+|Failed to connect to [A-Za-z0-9._-]+ port [0-9]+" "$f" 2>/dev/null |
		LC_ALL=C sed -E -e 's/^Could not resolve (proxy|host): //' -e 's/^Failed to connect to //' -e 's/ port [0-9]+$//' \
			-e 's/[.-]+$//' | while IFS= read -r w; do
		h="$(printf '%s' "$w" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
		# and a public host without its ".com", as a paraphrase writes it ("GitHub"; B13 round 12 N43)
		case " $PUBLIC_HOSTS " in *" $h "* | *" $h.com "*) continue ;; esac
		# lower case first, the number's key; then as written where that differs, which takes the same number:
		# both readers now match a proxy pair in any ASCII case (B13 round 13 O2, O67), so it adds no match
		[ -z "$h" ] || printf '%s\n' "$h"
		[ "$w" = "$h" ] || printf '%s\n' "$w"
	done
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
# With no upstream, git's own config says why (B13 round 6 G21): a branch with no merge setting follows no
# upstream branch and a detached HEAD is on no branch, while a merge setting whose remote ref git cannot read
# (a narrowed fetch refspec) is a branch git could not name.
newer_copy_fix() {
	local src shown url="" u="" r=origin d canon=0 follows b="" m="" how="git could not say which branch it follows"
	src="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)" || src=""
	shown="$src"
	# shellcheck disable=SC2088 # the ~ is printed
	case "$shown" in "$HOME"/*) shown='~/'"${shown#"$HOME"/}" ;; esac
	d="$(/usr/bin/xcode-select -p 2>/dev/null || true)"
	if [ -n "$src" ] && [ -n "$d" ] && [ -d "$d" ]; then
		git_ro_resolve
		u="$(git_ro -C "$src" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
		[ -z "$u" ] || r="${u%%/*}"
		# the configured URL, not `remote get-url`, which a URL rewrite (insteadOf) changes: a rewrite alone
		# read as a checkout that follows another remote (B13 round 18 V70)
		url="$(git_ro -C "$src" config --get "remote.$r.url" 2>/dev/null || true)"
		b="$(git_ro -C "$src" symbolic-ref -q --short HEAD 2>/dev/null || true)"
		[ -z "$b" ] || m="$(git_ro -C "$src" config --get "branch.$b.merge" 2>/dev/null || true)"
	fi
	if [ -n "$url" ] && [ -z "$u" ]; then
		if [ -z "$b" ]; then how="it is on no branch"; elif [ -z "$m" ]; then how="it follows no upstream branch"; fi
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
		follows="${how#it follows }"
		if [ -n "$u" ]; then follows="$u of $url"; else follows="$url (${follows#it is })"; fi
		FIX="point this Mac's checkout at main of $CANON_REPO (it follows another remote or branch, so a fresh copy of the prompt would stop here again), or move the checkout aside so step 1 clones it, then paste the same prompt into a new session"
		TELL="point this Mac's checkout ($shown) at main of $CANON_REPO (it follows $follows, so a fresh copy of the prompt would stop here again), or move the checkout aside so step 1 clones it, then paste the same prompt into a new session"
	else
		follows="does not follow main of $CANON_REPO ($how): point it there, or move it aside so step 1 clones it, then paste the prompt again"
		FIX="copy the prompt again from $PROMPT_SOURCE (\"Set up on a new Mac: one prompt\") and paste it into a new session; if that copy stops here too, this Mac's checkout $follows"
		TELL="copy the prompt again from $PROMPT_SOURCE (\"Set up on a new Mac: one prompt\") and paste it into a new session; if that copy stops here too, this Mac's checkout ($shown) $follows"
	fi
}
# git, read-only: GIT_OPTIONAL_LOCKS=0 keeps even `git diff` from rewriting the index's stat cache. Every
# caller reads a checkout (-C DIR): git looks no higher than DIR, so a .git it cannot read (one holding only
# HEAD) is not read past into a repository around it, such as a home that is one (B13 round 11 M51). The
# ceiling is DIR's physical parent; git ignores a relative entry, and a ":" would split the list. Defined
# here, before the friction-log commands, whose newer_copy_fix reads the checkout too.
git_ro() {
	local p=""
	[ "${1-}" != -C ] || p="$(cd -P "$2" 2>/dev/null && cd -P .. && pwd -P)" || p=""
	case "$p" in *:*) p="" ;; esac
	if [ -n "$p" ]; then
		GIT_CEILING_DIRECTORIES="$p" GIT_OPTIONAL_LOCKS=0 "${GIT_RO_BIN:-/usr/bin/git}" "$@"
	else
		GIT_OPTIONAL_LOCKS=0 "${GIT_RO_BIN:-/usr/bin/git}" "$@"
	fi
}
# The git /usr/bin/git runs, found once: /usr/bin/git is an xcrun shim that looks it up again at every exec
# (load root cause 2026-10-17 X3). Called in the parent shell before the reads, never inside $( ), where the
# value would be lost; only a found git is kept, so a failed lookup leaves /usr/bin/git. Its own developer
# tools test, not have_devtools, which is not yet defined when the friction-log commands run.
GIT_RO_BIN=""
git_ro_resolve() {
	local d g
	[ -z "$GIT_RO_BIN" ] || return 0
	case "${OSTYPE-}" in darwin*) ;; *) return 0 ;; esac
	d="$(/usr/bin/xcode-select -p 2>/dev/null || true)"
	[ -n "$d" ] && [ -d "$d" ] || return 0
	g="$(/usr/bin/xcrun --find git 2>/dev/null || true)"
	case "$g" in /*/git) [ ! -x "$g" ] || GIT_RO_BIN="$g" ;; esac
	return 0
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
			# The fix column names the second reading's remedy too, after the first's, where
			# setup_report._prompt_copy_note still finds that one (B13 round 9 K18). It names the remedy without
			# its cause: the error column just before it already says the --log-start value may have changed,
			# and the stop line said it twice (B13 round 10 L20).
			fix="$fix; or, if the first line of the prompt says \"setup prompt v$SETUP_PROMPT_COMPAT\": run step 1's command again once, exactly as the prompt writes it"
			# The line names both readings, as the message below does: a current copy whose agent changed the
			# value is stopped the same way, and only this line reaches the report (B13 round 8 J9). The words
			# come after the reason, where setup_report._PROMPT_STOP_RE still reads the line.
			# The attempt is closed here: the agent is told to run no other step, so an agent that obeys runs
			# no --report-only (an older copy's step 3 may still run one, and that report reads this closed
			# attempt), and a later session's line must not join an attempt left open.
			friction_append "Attempt: $now"$'\n'"Prompt: $said"$'\n'"Agent: ${v:-unknown}"$'\n'"$now | step 1 | error | $PROMPT_STOPPED $what, or the --log-start value was changed; setup stopped (exit 2) | $fix"$'\n'"$now | end | finished"$'\n' || true
			# The last sentence is for a current copy whose agent changed the value. It names no value that
			# passes: an old copy's agent that read one here used it, and ran its old wording after all.
			printf 'error: the pasted setup prompt is %s. Stop here and run no other step of that prompt. Tell the person to %s. If the first line of your prompt says "setup prompt v%s", the --log-start value was changed: run step 1'\''s command again once, exactly as the prompt writes it.\n' "$what" "$TELL" "$SETUP_PROMPT_COMPAT" >&2
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
	[ "$op" = --log-end ] || record_proxy_hosts
	return "$rc"
}
# Whether the attempt the setup report judges is one --log-start stopped (a copy of the prompt that is not
# this installer's): the friction log's last attempt holds that error line. The attempt ends where
# `agentsync setup-report` ends it (parse_friction), so this NEXT and the report's Summary are of the same
# attempt: a step 1 error dated more than NEW_SESSION_GAP seconds after its end line is another session's,
# whose step 1 stopped before --log-start, and that one is not a stopped copy; so, at any gap, is a step 1
# error that starts with "prompt vN, <agent>:", since that prefix is written only before --log-start and a
# stopped attempt has a header (B13 round 5 F23, track()). Any other line after the end line is a late line
# of the stopped session (an old copy logs the failed command, then writes its report).
# When it is, it prints the fix that stop logged (its line's last column), for the NEXT (B13 round 5 F17).
NEW_SESSION_GAP=600 # setup_report._NEW_SESSION_GAP, in seconds
# awk: secs(T), "2026-10-06T16:42:00Z" as seconds on a day count (to subtract two of them), and the start of
# a step 1 error line, which after an end line may open a header-less attempt (parse_friction). track(LINE),
# called on every line first, keeps whether the open attempt has a header (HD) or stated words (W; only that
# they were seen, since B13 round 14 Q28 compares no version or agent: round 15 S38), and is 1 for a step 1
# error that states "prompt vN, <agent>:" words another session's: after any header line parse_friction keeps
# (Prompt, Agent, Outcome, Run: HD, which an Attempt: line clears), and after a header-less attempt that
# already has stated words, always, at any gap: a session writes the prefix once, only before --log-start (so
# after a header it is another session's, B13 round 5 F23), at the Xcode or git stop, then goes to its
# report, so a second stated line is another session's too (B13 round 14 Q28).
# restate(LINE) gives an attempt a step 1 error opened the words that line states; an attempt with no header and
# no words takes them from its first step 1 error that states them, wherever it sits (B13 round 10 L26).
# A second Prompt: line in one attempt opens another, as in parse_friction, for any log version (B13 round 6
# G81): track() then reads it as a time-less Attempt: line (PS: a Prompt: line seen in this attempt) and sets
# NH, which each reader takes right after track() as it takes an Attempt: line, and clears.
FRICTION_AWK='
		function secs(t,    y, m) { # "2026-10-06T16:42:00Z": seconds on a day count, to subtract two of them
			y = substr(t, 1, 4) + 0; m = substr(t, 6, 2) + 0
			if (m <= 2) { y--; m += 12 }
			return (365 * y + int(y / 4) - int(y / 100) + int(y / 400) + int((153 * (m - 3) + 2) / 5) + substr(t, 9, 2)) * 86400 \
				+ substr(t, 12, 2) * 3600 + substr(t, 15, 2) * 60 + substr(t, 18, 2)
		}
		function stated(line,    w, a, c, l, y, p) { # sets SV and SR (the agent as written) from "prompt vN, <agent>:" at the start of the what-text
			w = line; sub("^[^|]*[|][^|]*[|][^|]*[|][ \t]*", "", w)
			# the what-text ends at the last "|", as parse_friction\047s rpartition cuts the fix column off (B13 round 20 X83)
			if ((p = match(w, "[|][^|]*$"))) w = substr(w, 1, p - 1)
			while (match(w, "^([\"\047 \t]|\342\200[\230\231])")) w = substr(w, RLENGTH + 1)
			if (tolower(substr(w, 1, 8)) != "prompt v" || !match(substr(w, 9), "^[0-9][0-9]?[0-9]?")) return 0
			SV = substr(w, 9, RLENGTH) + 0; w = substr(w, 9 + RLENGTH)
			if (!match(w, "^[ ,:;-]+")) return 0
			# the agent may start inside that run, as _STEP1_STATED_RE backtracks: "prompt v27,,:" is agent ","
			# (unstate() reads it so too, B13 round 20 X83); a shorter run only gives a longer agent
			for (l = RLENGTH; l > 0; l--) {
				y = substr(w, l + 1)
				if (!match(y, "^[^: \t][^:]*:")) continue
				a = substr(y, 1, RLENGTH - 1); sub("[ \t]+$", "", a)
				# _STEP1_STATED_RE reads at most 100 characters of agent: UTF-8 ones, so continuation bytes do not count
				c = a; gsub("[\200-\277]", "", c)
				if (length(c) > 100) return 0
				SR = a; gsub("[ \t]+", " ", SR); return 1
			}
			return 0
		}
		function restate(line) { HD = 0; PS = 0; W = stated(line) }
		function track(line,    new) {
			if (line ~ /^Attempt:/) { SEEN = 1; HD = 0; PS = 0; W = 0; return 0 }
			if (line ~ /^[-* ]*Prompt:/) {
				if (PS) { NH = 1; W = 0 }
				SEEN = 1; HD = 1; PS = 1; return 0
			}
			if (line ~ /^[-* ]*(Outcome|Run|Agent):/) { SEEN = 1; HD = 1; return 0 }
			if (line !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z [|]/) return 0
			new = 0
			if (!SEEN) { SEEN = 1; if (line ~ s1) restate(line) }
			else if (line ~ s1 && (HD || W) && stated(line)) new = 1
			else if (line ~ s1 && stated(line)) W = 1 # no header and no words yet: the error states them (L26)
			if (new) { HD = 0; PS = 0; W = 1 }
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
		NH { NH = 0; stopped = 0; ended = ""; next }
		/^Attempt:/ { stopped = 0; ended = ""; next }
		q { stopped = 0; ended = ""; next }
		/\| end \| finished[[:space:]]*$/ { ended = substr($0, 1, 20); next }
		ended != "" && $0 ~ s1 && secs($0) - secs(ended) > gap { stopped = 0; ended = ""; restate($0); next }
		# the remedy of the first reading only: the NEXT names the version of this installer once (B13 round 9 K18)
		index($0, mark) { stopped = 1; fx = $0; sub(/.* [|] /, "", fx); if (i = index(fx, "; or, if the first line of the prompt says ")) fx = substr(fx, 1, i - 1) }
		END { if (stopped) print fx; exit !stopped }' "$f" 2>/dev/null
}
# --report-only: close the friction log's current attempt with "<UTC> | end | finished", only when its last
# "Attempt:" has no end line yet (so a second --report-only adds none). No log or no attempt: nothing to close.
# The latest attempt is the one track() splits, as parse_friction does: a header-less attempt a stated step 1
# error opened (a git or Xcode stop of another session, after this one's header) is never closed, since the
# report says --report-only had no attempt to close, and an end line there was credited to the stopped
# session (B13 round 17 U33). An Attempt: line alone, or a stated line with no agent, opens none (G51).
friction_close_attempt() {
	local f
	f="$(friction_file)"
	[ -f "$f" ] || return 0
	LC_ALL=C awk -v gap="$NEW_SESSION_GAP" -v s1="$STEP1_ERROR_RE" "$FRICTION_AWK"'
		{ q = track($0) }
		NH { NH = 0; open = 1; ended = 0; hl = 0; next }
		/^Attempt:/ { open = 1; ended = 0; hl = 0; next }
		q { hl = 1; next }
		/\| end \| finished[[:space:]]*$/ { ended = 1 }
		END { exit !(open && !ended && !hl) }' "$f" || return 0
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

# A --source-local folder as a NEXT shows it: in double quotes, with \ " $ and ` escaped, as a person types a
# spaced name (B13 round 16 T35). A name %q writes as $'..' (a byte it cannot show) or one with a "!", which
# an interactive zsh expands inside double quotes, keeps the %q form. install.log's args= keeps %q.
shown_arg() {
	local q v="$1"
	q="$(printf '%q' "$1")"
	case "$q$1" in
	\$\'* | *!*) printf '%s' "$q" ;;
	*)
		v="${v//\\/\\\\}" v="${v//\"/\\\"}" v="${v//\$/\\\$}" v="${v//\`/\\\`}"
		printf '"%s"' "$v"
		;;
	esac
}
ORIG_ARGS=""  # every flag given, for a re-run after a failure (install.log's args=)
ORIG_SHOWN="" # the same as a NEXT shows it (shown_arg)
BASE_ARGS=""  # the same without --source-local FOLDER, for a re-run once the folders are in the config
ALL_ARGS=""   # every argument as given, for install.out
skip_next=0
for a in "$@"; do
	ALL_ARGS="$ALL_ARGS $(printf '%q' "$a")"
	if [ "$skip_next" -eq 1 ]; then
		skip_next=0
		ORIG_ARGS="$ORIG_ARGS $(printf '%q' "$a")"
		ORIG_SHOWN="$ORIG_SHOWN $(shown_arg "$a")"
		continue
	fi
	case "$a" in
	--report-only | --no-report | --list-folders | --version | -h | --help) ;; # NEXT is for the real run
	--source-local)
		skip_next=1
		ORIG_ARGS="$ORIG_ARGS $(printf '%q' "$a")"
		ORIG_SHOWN="$ORIG_SHOWN $(printf '%q' "$a")"
		;;
	*)
		ORIG_ARGS="$ORIG_ARGS $(printf '%q' "$a")"
		ORIG_SHOWN="$ORIG_SHOWN $(printf '%q' "$a")"
		BASE_ARGS="$BASE_ARGS $(printf '%q' "$a")"
		;;
	esac
done
# The arguments a NEXT's re-run names: every one until the config step has added the folders, then none of
# them, as step 2 says to name only folders to add (B13 round 16 T22)
RERUN_ARGS="$ORIG_SHOWN"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
# This script, for the commands NEXT and re-run lines name: ~/... under this home, as the README's approval
# rules write it (Bash(~/src/agent-context-sync/scripts/install.sh *)), else absolute. The ~ stays outside
# %q, which would write it as \~, a path that never expands (B13 round 4 Q7), and so does the slash after it:
# bash expands a ~ only before an unquoted /, and %q writes a name with a byte it finds unprintable (an accent
# under C, a curly apostrophe) as $'..', which would hold the slash (B13 round 5 F53).
SELF_FILE="$here/$(basename "${BASH_SOURCE[0]}")" # the file itself, which report_word reads
SELF="$SELF_FILE"
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
COMMIT_TRIED=0 # read_commit ran: a "-" after it is git that could not read the checkout (commit_shown)
DIRTY="" # the SOURCE checkout's local-change fingerprint (tree_fingerprint), empty when clean or unknown
PARSED=0       # 1 once the arguments are read: from then on every exit writes the report and a NEXT line
NEXT_MSG=""    # the NEXT line the EXIT trap prints
REPORT_BY=""   # agentsync when write_report's report is the installed agentsync's (step1_report_note)
FAIL_NEXT=""   # the NEXT fail() writes in place of its own, set by a step that knows better (config_failed)
RERUN=""       # the command a stopped run names (default: this script with the same arguments)
EXITING=0      # 1 once on_exit runs: a signal then only sets SIG_RC (B13 round 21 Y16)
SIG_RC=""      # the exit code of a signal that came while on_exit ran (129, 130 or 143)
LAST_ERROR=""  # the last error message, for the shell report
BACK_WHY=""    # why write_bring_back wrote no bring-back.md, for its warning (B13 round 14 Q19)
BACK_UNREAD=0  # the kept patches write_bring_back could not read, for the NEXT (B13 round 14 Q20, Q29)
BACK_BRANCHES=0 # the local-work branches whose commits bring-back.md does not carry, for the NEXT (B13 round 21 Y23)
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
	NEXT_MSG="${FAIL_NEXT:-fix the error above, then re-run: $SELF$RERUN_ARGS}"
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
# Whether git refuses the checkout $1 as another user account's ("detected dubious ownership"): the reason the
# report gives beside a commit git could not read, which only the friction log named (B13 round 10 L29). Never
# the owning account. Its own call, not checkout_commit's, whose output read_commit and show_version parse; the
# text captured, not piped to grep, which pipefail would make git's status.
git_refuses_owner() { # DIR
	local e
	have_devtools || return 1
	e="$(LC_ALL=C git_ro -C "$1" rev-parse HEAD 2>&1 >/dev/null || true)"
	case "$e" in *"dubious ownership"*) return 0 ;; esac
	return 1
}
# COMMIT and DIRTY from the checkout $1; both stay as they are when git cannot say.
read_commit() {
	local c
	COMMIT_TRIED=1
	git_ro_resolve # here, so commit_shown's unread_reason, run in $( ) after it, has it too
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
# Why the commit of the checkout $1 is unknown, from the folder alone (no run state, so --version and the
# report read it alike), its path as ~/... under the home: git refuses it, git cannot read it, or, with no
# developer tools, git never ran and its .git/HEAD names no commit (B13 round 12 N75).
unread_reason() { # DIR
	local d="$1"
	# shellcheck disable=SC2088 # the ~ is printed
	case "$d" in "$HOME"/*) d='~/'"${d#"$HOME"/}" ;; esac
	if git_refuses_owner "$1"; then
		printf 'git refuses %s/.git: the folder belongs to another user account' "$d"
	elif have_devtools; then
		printf 'git cannot read %s/.git' "$d"
	else
		printf 'no developer tools to run git, and its .git/HEAD names no commit'
	fi
}
show_version() { # the compat line last: step 1 of the setup prompt reads the last line of its output
	local c
	if [ -e "$repo/.git" ]; then
		git_ro_resolve
		c="$(checkout_commit "$repo")"
		[ -n "$c" ] || c="$(git_head_file "$repo")"
		[ -n "$c" ] || c="unknown ($(unread_reason "$repo"))"
		case "$c" in
		*" dirty "*) say "source commit: $c (local changes in this checkout; setup prompt step 1 keeps them on a local branch before it updates)" ;;
		*) say "source commit: $c" ;;
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
	65) printf "the state file (manifest.sqlite) cannot be read: the person's to fix" ;; # B13 round 17 U55, U65
	71) printf "%s" "the launcher could not start agentsync: the Python it runs is missing or not executable, and a SPAWN_FAILED line in the job's error log names the program and the error; one 71 during a reinstall is expected, a 71 at every run means run install.sh again with the options of the run that installed agentsync, not --report-only, which builds nothing" ;; # B13 round 19 W72; round 20 X44: no bracket inside the callers' own
	75) printf 'lock busy: another agentsync cycle is running' ;;
	77) printf 'sign-in required' ;;
	78) printf 'configuration invalid' ;;
	79) printf 'TCC pending: macOS is waiting for Allow' ;;
	80) printf "TCC denied: an earlier \"Don't Allow\"" ;;
	129) printf 'stopped by SIGHUP' ;;
	130) printf 'stopped by SIGINT' ;;
	143) printf 'stopped by SIGTERM, perhaps a tool timeout' ;; # no inner brackets: callers wrap it (B13 round 20 X7)
	*)
		case "$1" in 129 | 1[3-8][0-9] | 19[0-2]) n="$(kill -l "$(($1 - 128))" 2>/dev/null || true)" ;; esac
		if [ -n "$n" ]; then printf 'stopped by SIG%s (signal %s)' "${n#SIG}" "$(($1 - 128))"; else printf 'exit %s' "$1"; fi
		;;
	esac
}
# with_timeout's watchdog, and start_pandoc_once's limit: kills PID once LIMIT seconds have passed. It waits
# on its sleep and ends it when killed itself: no sleep left to launchd (load root cause 2026-10-17 X11). It
# sleeps in 1 s slices while PID runs, because bash 3.2 holds a TERM that lands just before `wait` until the
# sleep ends, and drops one that lands in the child before its trap is set: one sleep of the whole limit then
# kept the caller silent for all of it (B13 round 16 T80, the CI timeout of --list-folders). A failed kill of a
# sleep already reaped is no error under set -e (T41, T79).
watchdog() { # LIMIT PID
	local s="" n="$1" # slices, not a deadline on $SECONDS, which turns at whole seconds: never early
	trap 'kill "$s" 2>/dev/null || :; exit 0' TERM
	while [ "$n" -gt 0 ] && kill -0 "$2" 2>/dev/null; do
		sleep 1 &
		s=$!
		wait "$s"
		s="" # reaped: the trap must not kill a pid the system may have given another process
		n=$((n - 1))
	done
	! kill -0 "$2" 2>/dev/null || kill "$2"
}
# Run a command for at most $1 seconds (bash 3.2 and macOS have no timeout(1)); its status, 143 if killed.
with_timeout() {
	local t="$1" pid wd rc=0
	shift
	"$@" &
	pid=$!
	watchdog "$t" "$pid" >/dev/null 2>&1 &
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
# One ticker child prints them: it checks every 0.2 s until $SECONDS turns over, then sleeps until 0.2 s before
# the second a line is due and checks every 0.2 s from there, so each line comes in the second a check every
# 0.2 s would print it, with a few sleeps a line instead of five a second (load root cause 2026-10-17 X11).
# WAIT_TICKER is its pid; on_exit ends it too.
PROGRESS_SINCE=""
WAIT_TICKER=""
WAIT_WATCHDOG="" # start_pandoc_once's limit, which on_exit ends too
with_progress() {
	local label="$1" pid rc=0 t0=$SECONDS last="${PROGRESS_SINCE:-$SECONDS}"
	PROGRESS_SINCE=""
	shift
	"$@" </dev/null &
	pid=$!
	(
		s="" turned=0
		trap 'kill "$s" 2>/dev/null || :; exit 0' TERM
		while kill -0 "$pid" 2>/dev/null; do
			if [ $((SECONDS - last)) -ge "$PROGRESS_EVERY" ]; then
				last=$SECONDS
				say "$label: still running, $((SECONDS - t0))s"
			fi
			n=$SECONDS
			w=0.2
			[ "$turned" -eq 0 ] || [ $((last + PROGRESS_EVERY - n)) -lt 2 ] || w="$((last + PROGRESS_EVERY - n - 1)).8"
			sleep "$w" >/dev/null 2>&1 &
			s=$!
			wait "$s"
			s="" # reaped: the trap must not kill a pid the system may have given another process
			[ "$SECONDS" -eq "$n" ] || turned=1
		done
	) &
	WAIT_TICKER=$!
	wait "$pid" || rc=$?
	kill "$WAIT_TICKER" 2>/dev/null || true
	wait "$WAIT_TICKER" 2>/dev/null || true
	WAIT_TICKER=""
	return "$rc"
}
# Print "LINE, still running, <N>s" every $PROGRESS_EVERY s until killed, for the command the caller runs in
# the foreground meanwhile (a stopped run still lets the one under way end before it exits, so a re-run never
# meets a second build in the same tree, or a second reinstall). A helper build's LINE names the helper
# ("helpers: building the speech helper"): the speech helper's first build takes minutes after the OCR and
# media lines said ready, and ends in "speech: off" while its models are not placed. TICKER is its pid;
# on_exit kills it too.
# The sleep holds no output open, so a killed ticker never keeps a reader of this run's output waiting, and
# the ticker waits on it and ends it when killed: no sleep left to launchd (load root cause 2026-10-17 X11).
TICKER=""
ticker() { # LINE
	local t0=$SECONDS s=""
	trap 'kill "$s" 2>/dev/null || :; exit 0' TERM
	while :; do
		sleep "$PROGRESS_EVERY" >/dev/null 2>&1 &
		s=$!
		wait "$s" || break
		s="" # reaped: the trap must not kill a pid the system may have given another process
		kill -0 "$$" 2>/dev/null || break
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
# One clock read for the run id and its start time, so the Installer section shows one second for both
# (B13 round 13 O4): the start line used to be stamped later, after the commit read.
RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="$RUN_STAMP-$$"
RUN_AT="${RUN_STAMP:0:4}-${RUN_STAMP:4:2}-${RUN_STAMP:6:2}T${RUN_STAMP:9:2}:${RUN_STAMP:11:2}:${RUN_STAMP:13:2}Z"
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
	log_at "$RUN_AT" "start install.sh compat=$SETUP_PROMPT_COMPAT commit=$COMMIT${DIRTY:+ tree=$DIRTY}" \
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
	printf '# run=%s %s install.sh%s\n' "$RUN_ID" "$RUN_AT" "$ALL_ARGS" >>"$INSTALL_OUT" 2>/dev/null || return 0
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
# WORD (lower case): joined_word, or a lower-case word of 3 or more letters on one of this script's lines that is
# not a comment: the text the shell report prints, read when first asked, as setup_report.report_words reads
# its string literals (B13 round 5 F93: a word left off was over-redacted, which gives a joined name back; a
# comment's example, "janedoe", is no word of the report). name_forms reads it, and so does fallback_redaction's
# private-only rule; the organisation rule reads context_org_word (B13 round 6 G46).
REPORT_WORDS=""
report_word() {
	joined_word "$1" && return 0
	# a URL or this repository's locator names its owner, a login run together: never a report word. Only
	# printed text counts, as setup_report.report_words reads only string literals: double-quoted strings with
	# their expansions removed, and the single-quoted format of a printf or say. A code word (a variable, a
	# case label, an awk or Python name) is no report word, so an organisation named so is cut out of a longer
	# word and makes no private-only report, as in the Python report (B13 round 17 U4, U94). Each line is read
	# by a shell lexer (outside, inside "...", inside '...', and the depth of $( ), so the quotes of a string
	# nested in $( ... ) pair with each other, never with the quotes around it (B13 round 18 V85: the text
	# between two strings read as one gave echo, dev, null, gap, index). A single-quoted program other than a
	# printf or say format gives its own double-quoted strings and a sed s/// replacement ("designated
	# requirement:"), never an awk # comment (token). The word of a "${v:-word}" or "${v:+word}" is printed text too.
	[ -n "$REPORT_WORDS" ] || REPORT_WORDS=" $(LC_ALL=C awk '
		function words(t,    n, a, i) {
			gsub(/\$\([^)]*\)|\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*/, " ", t)
			n = split(t, a, /[^A-Za-z]+/)
			for (i = 1; i <= n; i++) if (a[i] ~ /^[a-z][a-z][a-z]+$/) w[a[i]] = 1
		}
		function prog(t, before,    q, c, i, n, s) {
			if (before ~ /(printf|say)[ \t]+$/) { words(t); return }
			n = length(t)
			for (i = 1; i <= n; i++) {
				c = substr(t, i, 1)
				if (c == "\"") {
					s = ""
					for (i++; i <= n && (c = substr(t, i, 1)) != "\""; i++) { if (c == "\\") { s = s c substr(t, i + 1, 1); i++ } else s = s c }
					words(s)
					continue
				}
				if (c == "#" && (i == 1 || substr(t, i - 1, 1) ~ /[ \t{;]/)) { t = substr(t, 1, i - 1); break }
			}
			q = t
			while (match(q, /(^|[^A-Za-z0-9_])s\/([^\/\\]|\\.)*\/([^\/\\]|\\.)*\//)) {
				s = substr(q, RSTART, RLENGTH); q = substr(q, RSTART + RLENGTH)
				sub(/^[^s]*s\/([^\/\\]|\\.)*\//, "", s); sub(/\/$/, "", s); words(s)
			}
		}
		/^[[:space:]]*#/ { next }
		{
			line = $0
			gsub(/[A-Za-z]+:\/\/[^ \t"\047]*|github\.com[:\/][^ \t"\047]*/, " ", line)
			n = length(line); buf = ""; d = 0; stk[0] = "o"
			for (i = 1; i <= n; i++) {
				c = substr(line, i, 1); top = stk[d]
				if (top == "d") { # inside "...": its text, a nested $( ) aside
					if (c == "\\") { buf = buf c substr(line, i + 1, 1); i++; continue }
					if (c == "\"") { words(buf); buf = ""; d--; continue }
					if (c == "$" && substr(line, i + 1, 1) == "(") { words(buf); buf = ""; stk[++d] = "c"; i++; continue }
					# ${v:-word} or ${v:+word} prints its word (B13 round 18: "not found (builtin patterns only)")
					if (c == "$" && substr(line, i + 1, 1) == "{" && (j = index(substr(line, i), "}"))) {
						x = substr(line, i + 2, j - 3); i += j - 1
						buf = buf " " (match(x, /^[A-Za-z_][A-Za-z0-9_]*:?[-+]/) ? substr(x, RLENGTH + 1) : "") " "
						continue
					}
					if (c == "$" && substr(line, i + 1, 1) ~ /[A-Za-z_0-9@#?*!-]/) { i++; while (substr(line, i + 1, 1) ~ /[A-Za-z0-9_]/) i++; buf = buf " "; continue }
					buf = buf c
					continue
				}
				if (c == "\\") { i++; continue }
				if (c == "#" && (i == 1 || substr(line, i - 1, 1) ~ /[ \t;]/)) break
				if (c == "\"") { stk[++d] = "d"; buf = ""; continue }
				if (c == "\047") {
					j = index(substr(line, i + 1), "\047")
					if (!j) { prog(substr(line, i + 1), substr(line, 1, i - 1)); break }
					prog(substr(line, i + 1, j - 1), substr(line, 1, i - 1)); i += j
					continue
				}
				if (c == "$" && substr(line, i + 1, 1) == "(") { stk[++d] = "c"; i++; continue }
				if (c == "(" && top == "c") { stk[++d] = "c"; continue }
				if (c == ")" && top == "c") d--
			}
		}
		END { for (k in w) printf "%s ", k }' "$SELF_FILE" 2>/dev/null || true) "
	case "$REPORT_WORDS" in *" $1 "*) return 0 ;; esac
	return 1
}
# WORD (lower case): a word the report prints from a command's or the agent's output, which no scan of this
# script's printed text sees: uname -m's arm, profiles status's enrolled, via, dep and enrollment, the embedded
# doctor lines' env, info and resolves, the PAC sentence's words, the agent's name. setup_report._OUTPUT_WORDS (a
# test keeps them equal). It decides only the private-only rule, never how a value is matched (B13 round 18 V84,
# V86: an organisation Arm cut "arm64" to "<org-1>64" and the report still offered the public issue link).
output_word() {
	case "$1" in
	arm | auto | claude | dep | enrolled | enrollment | env | evaluate | info | launchagent | microsoft | pac) return 0 ;;
	reachable | resolves | returns | unsupported | via) return 0 ;;
	esac
	return 1
}
# WORD (lower case): a generic word a one-word organisation is registered for only in each entry's own name and
# its domains, never in the report's sentences: setup_report._CONTEXT_ORG_WORDS (a test keeps them equal). Any
# other one-word organisation, a report word or not, is replaced everywhere; a report word then makes the report
# private only (B13 round 6 G46: a tenant named like any word of this script printed bare in a sentence).
context_org_word() {
	case "$1" in
	data | docs | domain | files | folder | group | home | issue | link | links | report | shared | sync | team | work) return 0 ;;
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
# WORD (lower case): a folder below the home folder that names no project: setup_report._GENERIC_HOME_DIRS (a test
# keeps them equal). project_values skips it at the front of a path (B13 round 18 V46).
generic_home_dir() {
	case "$1" in
	applications | code | desktop | dev | development | documents | downloads | git | library | projects) return 0 ;;
	repos | sites | source | src | work | workspace) return 0 ;;
	esac
	return 1
}
# Whether a folder name is made of coding-agent product words only ("Copilot", "github-copilot"), as
# setup_report._agent_words reads it: in lower case, split on runs of blanks, "_", "\" and "-", at least one
# word and every word one of setup_report._AGENT_WORDS (a test keeps them equal). A folder only step 1's list
# printed with such a name is not registered, or "GitHub Copilot CLI" in the Agent: line read "GitHub
# <folder-N> CLI" in the Summary and the issue link (B13 round 9 K1, K47).
agent_words_name() {
	local w any=""
	local -a ws=()
	# shellcheck disable=SC2141 # the backslash is a separator, as in setup_report._SEP_RE
	IFS=$' \t_\\-' read -r -d '' -a ws < <(printf '%s' "$1" | LC_ALL=C tr '[:upper:]' '[:lower:]') || true
	for w in ${ws[@]+"${ws[@]}"}; do
		case "$w" in
		"") ;;
		claude | codex | copilot | cursor | gemini | github) any=1 ;;
		*) return 1 ;;
		esac
	done
	[ -n "$any" ]
}
# A proxy host with no dot that names no one and that the report prints as a word of its own (HTTPS_PROXY,
# network.proxy): setup_report._GENERIC_PROXY_HOSTS (a test keeps them equal). Matched only after // or @, or as
# host:port with its own port.
generic_proxy_host() {
	case "$(printf '%s' "$1" | LC_ALL=C tr '[:upper:]' '[:lower:]')" in
	proxy | webproxy | wpad | localhost | gateway | firewall | squid | cache | http | https) return 0 ;;
	direct | none | off | network | host) return 0 ;;
	esac
	return 1
}
# What setup_report._norm keeps of an id or folder name: no blank, "_", "\" or "-", no accent, lower case. The
# accents go as add_forms drops them: the NFD form (iconv UTF-8-MAC) without its combining marks, U+0300-U+036F,
# so the id "cafe-plans" is the folder "Café Plans" in either Unicode form (B13 round 7 H76). A value that is
# not UTF-8 keeps its bytes, so it shares no folder's placeholder (the safe side).
id_key() {
	local k d
	k="$(printf '%s' "$1" | LC_ALL=C tr -d ' \t_\\-')"
	# Every value goes through iconv: a bash pattern for "not ASCII" follows the locale of the shell, and under
	# a UTF-8 one a range such as a-z holds a precomposed é, so the accents would stay.
	if d="$(printf '%s' "$k" | LC_ALL=C /usr/bin/iconv -f UTF-8 -t UTF-8-MAC 2>/dev/null)"; then
		k="$(printf '%s' "$d" | LC_ALL=C sed "s/$(printf '\314')[$(printf '\200')-$(printf '\277')]//g;s/$(printf '\315')[$(printf '\200')-$(printf '\257')]//g")"
	fi
	printf '%s' "$k" | LC_ALL=C tr '[:upper:]' '[:lower:]'
}
# Source ids that are agentsync's own words (setup_report._GENERIC_IDS; a test holds the two equal).
GENERIC_SOURCE_IDS="agent agentsync archive calendar chats config docs drive files graph inbox local mail mirror notes"
GENERIC_SOURCE_IDS="$GENERIC_SOURCE_IDS onedrive sharepoint source sources status sync teams"
# "value<TAB>placeholder<TAB>w" lines (w: whole words only; t: not followed by a word character, a dot or a
# hyphen) for redact_stream; the %q form too, since install.log quotes paths that way.
# The folder paths step 1's lists wrote to install.out ("$cs/<provider>/<name>[/<name>]"), less their
# marks: what redaction_pairs registers from the lists, and what the legend reads (B13 round 9 K45).
listed_folder_lines() {
	local cs="$HOME/Library/CloudStorage" line
	[ -f "$INSTALL_OUT" ] && [ ! -L "$INSTALL_OUT" ] || return 0
	while IFS= read -r line; do
		case "$line" in "$cs"/*/*) printf '%s\n' "$line" ;; esac
	done < <(LC_ALL=C awk '/^# run=/ { list = ($0 ~ / install[.]sh( .*)? --list-folders( |$)/); next }
		list { sub(/^\[(synced|inside a synced folder|contains a synced folder)\] /, ""); print }' "$INSTALL_OUT" 2>/dev/null)
}
# Each name part of those paths, as folder_parts reads them: "skip<TAB><name>" for one it does not register
# (every drive's name, or coding-agent product words only), else "keep<TAB><name>" (B13 round 11 M3).
listed_folder_names() {
	local cs="$HOME/Library/CloudStorage" f rel part
	while IFS= read -r f; do
		rel="${f#"$cs"/*/}"
		while [ -n "$rel" ]; do
			part="${rel%%/*}"
			[ "$part" = "$rel" ] && rel="" || rel="${rel#*/}"
			if generic_folder "$part" || agent_words_name "$part"; then
				printf 'skip\t%s\n' "$part"
			else
				printf 'keep\t%s\n' "$part"
			fi
		done
	done < <(listed_folder_lines)
}
# The path = "..." values of sources.toml, one a line, each read as a TOML basic string in one pass from the left
# (so an escaped backslash before a t stays a backslash and a t). add-source writes them with json.dumps, so a
# TAB in a folder name is the two characters \t: read raw, the fuzzy match took "\" as a separator and the name
# printed in clear (B13 round 20 X75). A whitespace escape (\t, \n, \r, \b, \f, \u0009, \u000A, \u000D) is one
# space, which the fuzzy match takes as any run of blanks and the pair format carries; another \uXXXX or
# \UXXXXXXXX is its UTF-8 bytes (json.dumps writes only control characters so); \\ and \" are \ and ".
config_path_values() {
	[ -f "$CONFIG" ] || return 0
	LC_ALL=C awk '
		function hex(h,    i, v) {
			v = 0; h = tolower(h)
			for (i = 1; i <= length(h); i++) v = v * 16 + index("0123456789abcdef", substr(h, i, 1)) - 1
			return v
		}
		function utf8(c) {
			if (c < 128) return sprintf("%c", c)
			if (c < 2048) return sprintf("%c%c", 192 + int(c / 64), 128 + c % 64)
			if (c < 65536) return sprintf("%c%c%c", 224 + int(c / 4096), 128 + int(c / 64) % 64, 128 + c % 64)
			return sprintf("%c%c%c%c", 240 + int(c / 262144), 128 + int(c / 4096) % 64, 128 + int(c / 64) % 64, 128 + c % 64)
		}
		/^[[:space:]]*path[[:space:]]*=[[:space:]]*".*"[[:space:]]*$/ {
			v = $0; sub(/^[^"]*"/, "", v); sub(/"[[:space:]]*$/, "", v)
			out = ""
			while ((i = index(v, "\\")) > 0) {
				out = out substr(v, 1, i - 1); e = substr(v, i + 1, 1); v = substr(v, i + 2)
				x = "0123456789ABCDEFabcdef"
				if (e ~ /^[tnrbf]$/) out = out " "
				else if (e == "u" && match(v, "^[" x "][" x "][" x "][" x "]") ||
					e == "U" && match(v, "^[" x "][" x "][" x "][" x "][" x "][" x "][" x "][" x "]")) {
					c = hex(substr(v, 1, RLENGTH)); v = substr(v, RLENGTH + 1)
					out = out (c == 9 || c == 10 || c == 13 ? " " : utf8(c))
				} else out = out e
			}
			print out v
		}' "$CONFIG" 2>/dev/null || true
}
redaction_pairs() {
	local cs="$HOME/Library/CloudStorage" n=0 e org f rel part name core tok user mdm line w take u h v pac seen i whole orgs_seen c kept low x links walked lk
	local port k folders_seen=$'\n' ph b auth sep tail rw keys key m sp top folder_keys=$'\n' FUZZY
	local tables=0 ids=" " hp beside
	local -a known=(${FOLDERS[@]+"${FOLDERS[@]}"}) words toks segs loose=() listed=()
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
		done < <(config_path_values)
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
	# The folders step 1's lists printed into install.out (the agent saw them and may quote one), as the Python
	# report registers them: read from the file, never by listing CloudStorage (B13 round 8 J69). A path is
	# "$cs/<provider>/<name>[/<name>]", with a mark and a space before a folder the config syncs, one inside such a
	# folder or one that holds one: SYNCED_MARK, INSIDE_MARK, CONTAINS_MARK (B13 round 9 K45). Kept apart from the
	# configured ones: a listed name that every drive has or that is coding-agent product words only is not
	# registered, as in the Python report (B13 round 9 K1, K47).
	while IFS= read -r line; do
		listed+=("$line")
	done < <(listed_folder_lines)
	emit_pair() { # VALUE PLACEHOLDER [w] [utf8]: the pair and its printf %q form; with utf8, %q in UTF-8 too
		# a TAB or a newline in a value would split the line-based pair and print the name (install.out's list
		# can carry a raw one): a space, which the fuzzy match reads as any run of blanks (B13 round 20 X75)
		local v="${1//$'\t'/ }" q u
		v="${v//$'\n'/ }"
		set -- "$v" "${@:2}"
		printf '%s\t%s\t%s\n' "$1" "$2" "${3:-}"
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
	# A folder's or an organisation's pair is matched as the Python report's fuzzy mode matches it (B13 round 10
	# L66): in any case and with any separator or none between its words, as a whole word (redact_stream's flag
	# f). Flag j for a name of two or more words that run together spell a report word ("Ho Me"): only with a
	# separator or a CamelCase boundary between them, as setup_report's joined mode. Sets FUZZY.
	fuzzy_flag() { # VALUE
		local low
		FUZZY=f
		case "$1" in *[\ _-]* | *[[:lower:]][[:upper:]]* | *[[:digit:]][[:upper:]]*) ;; *) return 0 ;; esac
		low="$(printf '%s' "$1" | LC_ALL=C tr -d ' \t_\\-' | LC_ALL=C tr '[:upper:]' '[:lower:]')"
		! report_word "$low" || FUZZY=j
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
			if [ -z "$s" ] && [ "$#" -ge 2 ] && report_word "$(recase lower "$v")"; then
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
		u="$(shown_arg "$w")" # as a NEXT's re-run shows it (B13 round 16 T35)
		[ "$u" = "$v" ] || printf '%s\t%s\t\n' "--source-local $u" "--source-local <path>"
	done
	if [ -d "$cs" ]; then
		# The organisation, team or account after the app's name ("<app>-<domain>", as setup_report._org_of
		# reads it): not a bare app name, Personal, Business or Work, nor a name repeating the app's (Box-Box).
		# An account's email address too (GoogleDrive-<address>): the shell has no email pattern. An organisation
		# already registered takes no second number (OneDrive-X and OneDrive-SharedLibraries-X; B13 round 3
		# R44), nor another spelling of it (B13 round 10 L66: its pair is matched as Python's fuzzy mode).
		# OneDrive's entries first, so <org-1> is the OneDrive organisation, and a symlink is no organisation: its
		# own name is registered whole, after the organisations, as a <link-N>, a dangling one too (B13 round 17
		# U61, as setup_report._build_redactor since round 16 T40)
		orgs_seen=$'\n'
		links=() walked=$'\n'
		for e in "$cs"/OneDrive-* "$cs"/*; do
			[ -e "$e" ] || [ -L "$e" ] || continue
			case "$walked" in *$'\n'"$e"$'\n'*) continue ;; esac
			walked+="$e"$'\n'
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
			if [ -L "$cs/$e" ]; then
				links+=("$e")
				continue
			fi
			low="$(recase lower "$org")"
			if [ "${org#* }" = "$org" ] && context_org_word "$low"; then
				# a word the report prints ("Link"): in each entry's own name (and its %q form) and the domains
				# only, never the word in the report's own sentences (B13 round 5 F42, F55), as setup_report's
				# context mode. Every entry gets its pair; only the number is shared (OneDrive-X, -SharedLibraries-X)
				x="$(printf '%s' "$orgs_seen" | O="$org" LC_ALL=C awk -F '\t' '$1 == ENVIRON["O"] && NF == 2 { print $2; exit }')"
				if [ -z "$x" ]; then
					n=$((n + 1))
					x="$n"
					orgs_seen+="$org	$n"$'\n'"$org"$'\n'
					for c in .sharepoint.com -my.sharepoint.com .onmicrosoft.com; do add_pair "$low$c" "<org-$n>$c"; done
				fi
				add_pair "$e" "${e%"$org"}<org-$x>"
				continue
			fi
			case "$orgs_seen" in *$'\n'"$org"$'\n'*) continue ;; esac
			# another spelling of one (GoogleDrive-CONTOSO beside Box-Contoso) takes the number it has, as the
			# Python report's _norm key (B13 round 10 L66), and still a pair of its own: an accented spelling
			# or one with other separators is no match of the first one's pattern
			k="~$(id_key "$org")"$'\t'
			case "$orgs_seen" in
			*$'\n'"$k"*)
				x="${orgs_seen#*$'\n'"$k"}"
				x="${x%%$'\n'*}"
				;;
			*)
				n=$((n + 1))
				x="$n"
				;;
			esac
			orgs_seen+="$org"$'\n'"$k$x"$'\n'
			fuzzy_flag "$org"
			# flag o: an organisation matched everywhere is also cut out of a longer word at a CamelCase, digit or
			# leading-capital boundary (ContosoGroup, Contoso2026, AcmeContoso), as setup_report's org mode (B13
			# round 11 M50); a report word too, which then makes the report private only (round 18 V1: one left
			# whole-word printed in clear inside a longer word, MyStatusTeam). A joined name (flag j) stays as it is
			[ "$FUZZY" != f ] || FUZZY=o
			add_pair "$org" "<org-$x>" "$FUZZY"
		done
		# a link named as an organisation is that organisation's (OneDrive-Contoso (1) beside OneDrive-Contoso): a
		# word-named one is matched only in its entries' names, so the link's name takes its pair (F42, F55)
		lk=0
		for e in ${links[@]+"${links[@]}"}; do
			org="${e#*-}"
			org="${org#SharedLibraries-}"
			case "$orgs_seen" in *$'\n'"~$(id_key "$org")"$'\t'*) continue ;; esac
			x="$(printf '%s' "$orgs_seen" | O="$org" LC_ALL=C awk -F '\t' '$1 == ENVIRON["O"] && NF == 2 { print $2; exit }')"
			if [ -n "$x" ]; then
				add_pair "$e" "${e%"$org"}<org-$x>"
				continue
			fi
			lk=$((lk + 1))
			add_pair "$e" "<link-$lk>"
		done
	fi
	# The configured folders first, then the listed ones, as the Python report registers them. One number per
	# folder (B13 round 9 K4): a name found in two places (configured and listed), a parent counted again by a
	# nested listed path, or another spelling of it (id_key, setup_report._norm's sharing) takes the number it
	# has; each spelling still gets its own pair, all matched in any case and with any separator (flag f, B13
	# round 10 L66). A listed name skipped takes no number.
	folder_parts() { # LISTED PATH...: LISTED non-empty for step 1's list
		local only="$1" f rel part whole k
		shift
		for f in "$@"; do
			case "$f" in
			"$cs"/*/*) rel="${f#"$cs"/*/}" ;;
			*) continue ;;
			esac
			while [ -n "$rel" ]; do
				part="${rel%%/*}"
				[ "$part" = "$rel" ] && rel="" || rel="${rel#*/}"
				[ -z "$only" ] || ! { generic_folder "$part" || agent_words_name "$part"; } || continue
				k="$(id_key "$part")"
				case "$folder_keys" in
				*$'\n'"$k"$'\t'*)
					m="${folder_keys#*$'\n'"$k"$'\t'}"
					m="${m%%$'\n'*}"
					;;
				*)
					n=$((n + 1))
					m="$n"
					folder_keys+="$k"$'\t'"$m"$'\n'
					;;
				esac
				fuzzy_flag "$part"
				whole="$FUZZY"
				if generic_folder "$part"; then whole=w; fi # "Documents" is kept inside "MyDocuments", byte for byte
				add_pair "$part" "<folder-$m>" "$whole"
				# the number its pair has: a later part of the same bytes loses to the first (add_pair)
				case "$folders_seen" in *$'\n'"$part"$'\t'*) ;; *) folders_seen+="$part"$'\t'"$m"$'\n' ;; esac
			done
		done
	}
	n=0
	folder_parts "" ${known[@]+"${known[@]}"}
	folder_parts listed ${listed[@]+"${listed[@]}"}
	# A folder outside CloudStorage (a project checkout), configured or a vanished source's, as
	# setup_report._project_values registers it (B13 round 18 V46: the shell printed both in clear): below the
	# home folder, the path from its first part that names something, and that part alone, each in any case
	# and as a whole word (flag i); leading parts that are generic_home_dir, start with a dot, or are
	# agent_words_name with more parts after them are skipped, so "Projects" stays a word. Anywhere else, the
	# whole path. Never agentsync's own: the inbox and the folder beside the docs repo (unless that folder is a
	# generic one, or the home itself), as the Python report's "own".
	hp="$(cd "$HOME" 2>/dev/null && pwd -P || true)"
	beside=""
	[ ! -f "$CONFIG" ] || beside="$(LC_ALL=C awk '/^[[:space:]]*\[/ { t = ($0 ~ /^[[:space:]]*\[[[:space:]]*agentsync[[:space:]]*\]/); next }
		t && /^[[:space:]]*docs_repo[[:space:]]*=[[:space:]]*"/ { v = $0; sub(/^[^"]*"/, "", v); sub(/".*$/, "", v); print v; exit }' "$CONFIG" 2>/dev/null || true)"
	beside="${beside:-$HOME/agent-context/docs}"
	case "$beside" in \~) beside="$HOME" ;; \~/*) beside="$HOME/${beside#\~/}" ;; esac
	beside="${beside%/}"
	beside="${beside%/*}"
	own_folder() { # PATH: agentsync's own
		case "$1" in "$beside/inbox" | "$beside/inbox/"*) return 0 ;; esac
		[ "$beside" != "$HOME" ] && [ "$beside" != "$hp" ] || return 1
		generic_home_dir "$(recase lower "${beside##*/}")" && return 1
		case "$1" in "$beside" | "$beside/"*) return 0 ;; esac
		return 1
	}
	folder_value() { # VALUE: its <folder-N>, the number another spelling has (folder_keys), in any case
		k="$(id_key "$1")"
		case "$folder_keys" in
		*$'\n'"$k"$'\t'*)
			m="${folder_keys#*$'\n'"$k"$'\t'}"
			m="${m%%$'\n'*}"
			;;
		*)
			n=$((n + 1))
			m="$n"
			folder_keys+="$k"$'\t'"$m"$'\n'
			;;
		esac
		add_pair "$1" "<folder-$m>" i
		# and as a double-quoted word shows it, \ " $ and ` escaped (shown_arg, cycle._shown_arg), and as
		# shlex.quote's single-quoted one does, each ' written '"'"' (a name holding !): a vanished source's
		# re-add folder is printed so, which the Python report cuts by structure (B13 round 19 W34)
		case "$1" in
		*[\\\"\$\`]*)
			fe="$1" fe="${fe//\\/\\\\}" fe="${fe//\"/\\\"}" fe="${fe//\$/\\\$}" fe="${fe//\`/\\\`}"
			add_pair "$fe" "<folder-$m>" i
			;;
		esac
		fe="'\"'\"'" # in a variable: bash 3.2 keeps a replacement's backslashes
		case "$1" in *\'*) add_pair "${1//\'/$fe}" "<folder-$m>" i ;; esac
	}
	project_values() { # PATH
		local rel first rest
		case "$1" in \~) set -- "$HOME" ;; \~/*) set -- "$HOME/${1#\~/}" ;; esac
		own_folder "$1" && return 0
		case "$1" in
		"$HOME"/*) rel="${1#"$HOME"/}" ;;
		"$hp"/*) rel="${1#"$hp"/}" ;;
		"$HOME" | "$hp") return 0 ;;
		*) folder_value "$1" && return 0 ;;
		esac
		rel="${rel%/}"
		while [ -n "$rel" ]; do
			first="${rel%%/*}"
			[ "$first" = "$rel" ] && rest="" || rest="${rel#*/}"
			if generic_home_dir "$(recase lower "$first")" || [ "${first#.}" != "$first" ] ||
				{ [ -n "$rest" ] && agent_words_name "$first"; }; then
				rel="$rest"
				continue
			fi
			folder_value "$rel"
			[ -z "$rest" ] || folder_value "$first"
			return 0
		done
	}
	if [ -f "$CONFIG" ]; then
		while IFS= read -r line; do
			case "$line" in "$cs"/* | \~/Library/CloudStorage/* | "$hp/Library/CloudStorage/"*) continue ;; esac
			project_values "$line"
		done < <(config_path_values)
	fi
	# Every configured source id, as <source-N> by its [[source]] table's place (B13 round 5 F6): an id is its
	# folder's name in slug form ("project-beta---general"), and the empty-folder wait an agent quotes names it.
	# A whole word, where "-" is no word character, so "exclude-empty <id>" is matched. An id that is one of
	# agentsync's own words is kept unless its source is a cloud folder (setup_report._GENERIC_IDS), so "the
	# docs repo" and "inbox" stay readable. Only ids inside [[source]] tables: the graph and policy ones are not.
	# A cloud folder's id that is its folder's name in another spelling (setup_report._norm: lower case, no
	# blanks, "_", "\" or "-") shares that folder's <folder-N>, as in the Python report, so the wait an agent
	# quotes reads as the folder it is about (B13 round 6 G8). Matched by the id's spelling, not its path: of
	# two folders "Reports" under two organisations, "reports-2" keeps its own <source-N>.
	if [ -f "$CONFIG" ]; then
		while IFS=$'\t' read -r i v f; do
			tables="$i" ids="$ids$v "
			case " $GENERIC_SOURCE_IDS " in
			*" $v "*) case "$f" in "$cs"/* | \~/Library/CloudStorage/*) ;; *) continue ;; esac ;;
			esac
			ph="<source-$i>"
			case "$f" in
			"$cs"/* | \~/Library/CloudStorage/*)
				b="${f%/}"
				b="${b##*/}"
				case "$folders_seen" in
				*$'\n'"$b"$'\t'*)
					x="${folders_seen#*$'\n'"$b"$'\t'}"
					[ "$(id_key "$v")" != "$(id_key "$b")" ] || ph="<folder-${x%%$'\n'*}>"
					;;
				esac
				;;
			esac
			add_pair "$v" "$ph" w
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
	# A source still mirrored whose [[source]] block is gone is in no table above, yet doctor's
	# config.vanished_sources [FAIL] and the sync's refusal quote its id ("source 'x' (3 files) is mirrored but
	# no longer in sources.toml"): the ids those lines quote, registered after the configured ones (B13 round
	# 16 T42). Numbered by table place, the inbox's too, so a number is this report's own (round 18 V51).
	while IFS= read -r v; do
		case "$ids" in *" $v "*) continue ;; esac
		case " $GENERIC_SOURCE_IDS " in *" $v "*) continue ;; esac
		tables=$((tables + 1)) ids="$ids$v "
		add_pair "$v" "<source-$tables>" w
	done < <({
		[ -z "$DOCTOR_LOG" ] || [ ! -f "$DOCTOR_LOG" ] || LC_ALL=C grep -E '^\[FAIL\] config\.vanished_sources ' "$DOCTOR_LOG"
		[ ! -f "$INSTALL_OUT" ] || LC_ALL=C grep -E '(^configuration error: |config\.vanished_sources ).*mirrored but no longer in sources\.toml' "$INSTALL_OUT"
	} 2>/dev/null | LC_ALL=C sed 's/ mirrored but no longer in sources\.toml.*//' |
		LC_ALL=C grep -oE "'[a-z0-9][a-z0-9-]*' \([0-9]+ file(s|\(s\))?\)" | LC_ALL=C sed "s/^'//; s/' .*//" | LC_ALL=C sort -u)
	# and the folder each one is added back from: doctor's fix and the sync's refusal end with
	# cycle.vanished_add_back_tail, "... install.sh --source-local "<folder>" [--source-local ...]" under
	# install.sh and "... add-source "<folder>"[, then ... add-source "<folder>"]" elsewhere, each folder
	# double-quoted with \ " $ and ` escaped (cycle._shown_arg), single-quoted by shlex.quote for a name holding
	# ! or a character that does not print, and bare or single-quoted in an older build's log (B13 round 19 W34),
	# as the Python report registers each vanished id's scope_root (B13 round 17 U46; round 18 V46): one in
	# CloudStorage by its parts, any other as a project folder. Numbered in byte order, as the Python report
	# numbers them (round 19 W73).
	while IFS= read -r v; do
		case "$v" in
		\~/Library/CloudStorage/*) folder_parts "" "$cs/${v#\~/Library/CloudStorage/}" ;;
		"$cs"/* | "$hp/Library/CloudStorage/"*) folder_parts "" "$cs/${v#*/Library/CloudStorage/}" ;;
		?*) project_values "$v" ;;
		esac
	done < <({
		[ -z "$DOCTOR_LOG" ] || [ ! -f "$DOCTOR_LOG" ] || LC_ALL=C grep -E '^\[FAIL\] config\.vanished_sources ' "$DOCTOR_LOG"
		[ ! -f "$INSTALL_OUT" ] || LC_ALL=C grep -E '(^configuration error: |config\.vanished_sources ).*mirrored but no longer in sources\.toml' "$INSTALL_OUT"
	} 2>/dev/null | LC_ALL=C awk -v q="'" '{
		s = $0
		for (;;) {
			i = index(s, " add-source "); k = index(s, " --source-local ")
			if (i == 0 && k == 0) break
			if (k > 0 && (i == 0 || k < i)) s = substr(s, k + 16); else s = substr(s, i + 12)
			v = ""
			if (substr(s, 1, 1) == "\"") { # double-quoted, each \\ \" \$ and \` its own character
				s = substr(s, 2)
				while (s != "") {
					c = substr(s, 1, 1); s = substr(s, 2)
					if (c == "\"") break
					if (c == "\\" && s != "" && index("\\\"$`", substr(s, 1, 1)) > 0) { c = substr(s, 1, 1); s = substr(s, 2) }
					v = v c
				}
			} else if (substr(s, 1, 1) == q) { # a quoted one, with each of its own quotes written as q "\"" q "\"" q
				s = substr(s, 2)
				while ((j = index(s, q)) > 0) {
					v = v substr(s, 1, j - 1); s = substr(s, j + 1)
					if (substr(s, 1, 4) == "\"" q "\"" q) { v = v q; s = substr(s, 5) } else break
				}
			} else {
				v = s; sub(/ .*/, "", v); sub(/\)$/, "", v); sub(/,$/, "", v) # "<folder>, then" or "<folder>)"
			}
			if (v != "") print v
		}
	}' | LC_ALL=C sort -u)
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
	# print them. scutil by name (on every Mac's PATH), so a test can stand in for it. Numbered in the Python
	# report's order (B13 round 8 J78), so one Mac's two reports give a value the same <proxy-N>: the hosts of
	# the environment, of the system proxy where its Enable flag is 1 and of the PAC URL, and [network] proxy's,
	# then the PAC value, git's proxies, each rewrite's host, value and path, and the LaunchAgent's; one number
	# for a value in any case, as Python lower-cases a host and matches a value in any case. A system proxy host
	# whose flag is 0, which Python never registers, comes last: still redacted, and no shared number moves.
	n=0
	seen=" "
	keys=""
	while IFS= read -r u; do
		pac=""
		tail=""
		rw=""
		# a value that names no proxy ([network] proxy = "direct"), which Python skips, takes no number
		case "$(printf '%s' "$u" | LC_ALL=C tr '[:upper:]' '[:lower:]')" in direct | none | off) continue ;; esac
		case "$u" in
		pacv=*)
			# the PAC URL's value alone: its host came with the URL itself, before [network] proxy
			u="${u#pacv=}"
			pac="${u#*://}"
			u=""
			# A PAC URL that is only a generic host ("http://wpad") is that host, matched only after // or @ or as host:port.
			case "$pac" in */*) ;; *) ! generic_proxy_host "${pac%%:*}" || pac="" ;; esac
			;;
		rw=*)
			# A URL rewrite's base (B13 round 7 H20), a URL or git's scp form (git@host:path): its host, then
			# an scp base as host:path, as the Python report registers them, in their written and lower case
			# forms (git prints the host as configured, a resolver error in lower case); the path too, unless it
			# is a server's generic layout (git, scm, repos) or words the report prints. A URL base after its
			# scheme is no value of its own: it gave the host a second number, so git's error read as naming two
			# hosts (B13 round 8 J1, J20). The path is matched with an end boundary only, no letter or digit
			# after it, as setup_report.Redactor matches a rewrite path (pair flag e): its own ":" or "/" marks the
			# start, and before it in any URL is the last letter of a host or a port's digit, which a whole-word
			# match refused, so the pair never matched (B13 round 8 J37). A ".git" or a "-tools" may follow it
			# (B13 round 9 K46). GitHub's own host is skipped, so the repository locator and the issue link stay.
			rw=1
			u="${u#rw=}"
			case "$u" in *://*) h="${u#*://}" sep="/" ;; *) h="$u" sep=":" ;; esac
			auth="${h%%"$sep"*}"
			case "$h" in *"$sep"*) tail="$sep${h#*"$sep"}" ;; esac
			while :; do case "$tail" in */ | *:) tail="${tail%?}" ;; *) break ;; esac; done
			auth="${auth##*@}"
			case "$(printf '%s' "${auth%%:*}" | LC_ALL=C tr '[:upper:]' '[:lower:]')" in "" | github.com) continue ;; esac
			[ "$sep" = / ] || auth="${auth%%:*}"
			pac=""
			[ "$sep" = / ] || [ "$auth$tail" = "$auth" ] || pac="$auth$tail"
			u="rw://$auth/"
			k=""
			b=" git scm repo repos mirror mirrors src source code gh gitlab bitbucket homebrew npm node pypi artifactory vendor " # setup_report._GENERIC_PATH_WORDS
			# a word of the report's own (docs, tools) is no company word either, as in the Python report: a
			# /docs path would cut ~/agent-context/docs (B13 round 8 J20)
			for w in $(printf '%s' "$tail" | LC_ALL=C tr -c 'A-Za-z0-9' ' ' | LC_ALL=C tr '[:upper:]' '[:lower:]'); do
				case "$b" in *" $w "*) ;; *) report_word "$w" || k=1 ;; esac
			done
			[ -n "$k" ] || tail=""
			;;
		esac
		h="${u#*://}"
		h="${h%%[/?#]*}"
		h="${h##*@}"
		port=""
		case "$h" in *:*) port="${h#*:}" ;; esac
		h="${h%%:*}"
		for v in "$h" "$pac" "$tail"; do
			[ -n "$v" ] || continue
			case "$seen" in *" $v "*) continue ;; esac
			seen="$seen$v "
			# the number of this value in any case, else a new one (tab and \001 cannot be in a value)
			key="$(printf '%s' "$v" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
			case "$keys" in
			*$'\t'"$key"$'\001'*)
				m="${keys#*$'\t'"$key"$'\001'}"
				m="${m%%$'\n'*}"
				;;
			*)
				n=$((n + 1))
				m="$n"
				keys="$keys"$'\t'"$key"$'\001'"$m"$'\n'
				;;
			esac
			case "$v" in
			"$tail")
				# flag e: no letter or digit may follow, as in Python; a ".git" or a "-tools" may. Matched with
				# its own "/" or ":", which anchors the start and keeps a two-letter path over add_pair's floor,
				# and that separator is written back before the placeholder, so the host and the path read as two
				# (https://<proxy-1>/<proxy-2>/...; B13 round 9 K2, K27), as setup_report's keep_lead
				add_pair "$v" "${v:0:1}<proxy-$m>" e
				add_pair "$key" "${key:0:1}<proxy-$m>" e
				;;
			"$pac" | *.*)
				add_pair "$v" "<proxy-$m>"
				[ -z "$rw" ] || add_pair "$key" "<proxy-$m>"
				;;
			*)
				# A host with no dot (B13 R42, R67): a generic word (proxy, wpad) only after // or @ or as host:port
				# with its own port, so
				# the report's HTTPS_PROXY and network.proxy stay; any other (contosogw) can name the company and
				# is a whole word, as git and curl print it ("Could not resolve proxy: contosogw"). A word the
				# report prints is matched as a generic one: "Could not resolve host: the proxy was down" made
				# every "the" a placeholder (B13 round 12 N43), and so is one of one or two letters ("an"), which
				# no report word is
				if generic_proxy_host "$v" || [ "${#v}" -lt 3 ] || report_word "$(printf '%s' "$v" | LC_ALL=C tr '[:upper:]' '[:lower:]')"; then
					add_pair "//$v" "//<proxy-$m>" t
					add_pair "@$v" "@<proxy-$m>" t
					[ -z "$port" ] || add_pair "$v:$port" "<proxy-$m>:$port" w
				else
					add_pair "$v" "<proxy-$m>" w
				fi
				;;
			esac
		done
	done < <(
		{
			sp="$(with_timeout 5 "$(command -v scutil || printf /usr/sbin/scutil)" --proxy 2>/dev/null </dev/null || true)"
			printf '%s\n' "${HTTPS_PROXY:-}" "${https_proxy:-}" "${ALL_PROXY:-}" "${all_proxy:-}" \
				"${HTTP_PROXY:-}" "${http_proxy:-}"
			# the top-level keys, as net.parse_scutil_proxy reads them: the HTTPS host, then the HTTP host, each
			# where its Enable flag is 1, then the PAC URL for its host (P lines: a per-interface one is not read)
			top="$(printf '%s\n' "$sp" | LC_ALL=C awk '
				/[{][[:space:]]*$/ { d++; next }
				/^[[:space:]]*[}]/ { d--; next }
				d == 1 && $2 == ":" { a[$1] = $3 }
				END {
					if (a["HTTPSEnable"] == "1" && a["HTTPSProxy"] != "") print "H" a["HTTPSProxy"]
					if (a["HTTPEnable"] == "1" && a["HTTPProxy"] != "") print "H" a["HTTPProxy"]
					if (a["ProxyAutoConfigURLString"] != "") print "P" a["ProxyAutoConfigURLString"]
				}' || true)"
			printf '%s\n' "$top" | LC_ALL=C sed -n 's/^[HP]//p'
			[ ! -f "$CONFIG" ] || LC_ALL=C sed -n 's/^[[:space:]]*proxy[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$CONFIG"
			printf '%s\n' "$top" | LC_ALL=C sed -n 's/^P/pacv=/p'
			git_proxy_values
			git_rewrite_bases | LC_ALL=C sed 's/^/rw=/'
			# The LaunchAgent environment (launchctl setenv) that the doctor network.proxy.job line prints (B13 R68); a
			# read, as launchd_print is, and none when launchd is simulated (no launchctl call at all).
			[ "$SIMULATE" -eq 1 ] || for k in HTTPS_PROXY https_proxy ALL_PROXY all_proxy HTTP_PROXY http_proxy; do
				with_timeout 5 "$LAUNCHCTL" getenv "$k" </dev/null || true
			done
			# a host that a resolver message in an earlier friction line names, then the proxy-hosts that the
			# --log lines of the agent kept: after the LaunchAgent proxies, in the order of the Python report, so no
			# shared number moves (B13 round 11 M29)
			friction_proxy_hosts
			f="$(friction_file)"
			f="${f%/*}/proxy-hosts"
			[ ! -f "$f" ] || LC_ALL=C sed -n 's/^\([A-Za-z0-9._-][A-Za-z0-9._-]*\)$/\1/p' "$f"
			# the hosts of the git remotes, after them, as the Python report registers them, so no number moves
			# (B13 round 16 T88; no apostrophe in a comment here: bash 3.2 reads one as a quote inside this block),
			# each URL as a rewrite base, so a company word in its path is redacted too (B13 round 17 U15)
			git_remote_urls | LC_ALL=C sed 's/^/rw=/'
			# every other system proxy host (a flag of 0, or below the top level) and PAC URL (a per-interface one,
			# by host and by value): redacted still, numbered last
			printf '%s\n' "$sp" | LC_ALL=C sed -n -e 's/^[[:space:]]*HTTPS\{0,1\}Proxy : //p' \
				-e 's/^[[:space:]]*ProxyAutoConfigURLString : \(.*\)$/\1\
pacv=\1/p' || true
		} 2>/dev/null
	)
}
# The values a governance or policy error quotes, as setup_report.Redactor matches them by pattern (B13 round 16
# T3, round 17 U93): the remote URL in "remote <name> (<url>) exists but [governance] allow_remote", "... is not
# under a tenant-owned prefix" and push's "refused: remote <name> (<url>) is not tenant-owned per
# remote_url_prefixes" (any host: the path names the company too), a label id or name after
# exclude_label_ids: or exclude_label_names:, and a URL prefix after remote_url_prefixes:. Before every pair,
# so a folder or organisation pair never cuts the URL first.
# The [governance] keys whose type error quotes the value after "got ": setup_report._GOVERNANCE_TYPED_KEYS (a
# test keeps them equal).
GOVERNANCE_TYPED_KEYS="history_days|compaction_slack_days|allow_remote|hold|hold_reason|hold_owner|purge_on_upstream_delete|archive"
gov_value_sed() {
	# The value a [governance] type error quotes ("got ['Jane Contoso']") is <value>, as the Python report's
	# _GOVERNANCE_VALUE_RE writes it (B13 round 17 U103; round 18 V44, V72): a Python repr's own shape when one
	# ends at a blank, ";", ",", ".", ")", "|", " (fix: " or the line's end, so the words after it stay (a
	# second refusal, a friction line's " | " fix column); else up to the first " (fix: " or the line's end.
	# sed -E has no lazy match and no lookahead, so each " (fix: " is a byte no report text carries (\001)
	# while the value is cut, each value's start is marked (\003), and the shape is tried once per delimiter,
	# which is put back, "." last ("1.5;" is 1.5, not 1); a value the shape does not cover takes the wide cut.
	# The shape is _repr_shape(1), one level of brackets, not Python's two: two levels make a pattern over
	# BSD sed's 2048 bytes, which then prints nothing at all; a deeper value takes the wide cut, which covers
	# more, never less (B13 round 19 W69). A blank is any byte or UTF-8 sequence Python's \s matches (\r, \v,
	# \f, \034-\037, U+0085, the no-break and other Unicode spaces), else "1.5<no-break space>z" kept ".5" in
	# clear (B13 round 20 X82). The sequences are written out as literals: d1 holds more than nine groups, so
	# sed's \1-\9 cannot reach a capture after it.
	local soh stx a items d1 c seq
	soh="$(printf '\001')" stx="$(printf '\003')"
	a='('"'"'([^'"'"'\\]|\\.)*'"'"'|"([^"\\]|\\.)*"|-?[0-9]+(\.[0-9]+)?|True|False|None)'
	items="($a( *[,:] *$a)*( *,)?)?"
	d1="($a|\\[ *$items *\\]|\\{ *$items *\\}|\\( *$items *\\))"
	printf 's/ \\(fix: /%s/g\n' "$soh"
	printf 's/(\\[governance\\]\\.(%s): expected (a non-empty string|true\\/false|an integer >= [0-9]), got )/\\1%s/g\n' \
		"$GOVERNANCE_TYPED_KEYS" "$stx"
	printf 's/%s%s$/<value>/g\n' "$stx" "$d1"
	for c in ' ' "$(printf '\t')" ';' ',' ')' '|' "$soh" "$(printf '\r')" "$(printf '\v')" "$(printf '\f')" \
		"$(printf '\034')" "$(printf '\035')" "$(printf '\036')" "$(printf '\037')"; do
		printf 's/%s%s[%s]/<value>%s/g\n' "$stx" "$d1" "$c" "$c"
	done
	for seq in '\302\205' '\302\240' '\341\232\200' '\342\200\200' '\342\200\201' '\342\200\202' \
		'\342\200\203' '\342\200\204' '\342\200\205' '\342\200\206' '\342\200\207' '\342\200\210' \
		'\342\200\211' '\342\200\212' '\342\200\250' '\342\200\251' '\342\200\257' '\342\201\237' \
		'\343\200\200'; do
		seq="$(printf '%b' "$seq")"
		printf 's/%s%s%s/<value>%s/g\n' "$stx" "$d1" "$seq" "$seq"
	done
	printf 's/%s%s[.]/<value>./g\n' "$stx" "$d1"
	printf 's/%s[^%s]+/<value>/g\n' "$stx" "$soh"
	printf 's/%s//g\n' "$stx"
	printf 's/%s/ (fix: /g\n' "$soh"
	cat <<'SED'
s/\(([^()]+)\) (exists but \[governance\] allow_remote|is not under a tenant-owned prefix|is not tenant-owned per remote_url_prefixes)/(<remote>) \2/g
s/(exclude_label_(ids|names): )('([^'\\]|\\.)*'|"([^"\\]|\\.)*")/\1<label>/g
s/(remote_url_prefixes: )('([^'\\]|\\.)*'|"([^"\\]|\\.)*")( is not a remote URL| carries a password)/\1<remote>\5/g
s/(remote_url_prefixes: )[[:blank:]]*[^[:space:]'"]*(:\/\/|@)[^[:space:]'"]*/\1<remote>/g
SED
}
repo_slug() { # owner/repo of ISSUE_URL: redact_stream holds this repository's own locator
	local s="${ISSUE_URL#https://github.com/}"
	printf '%s' "${s%%/issues/*}"
}
# The shell twin of setup_report.Redactor.redact_agent (B13 round 19 W17; round 20 X40, X81): where the report
# prints an agent's name, a configured folder or source id made only of coding-agent product words
# (agent_words_name) stays as written where it stands as a word of that name, so "GitHub Copilot CLI" beside a
# configured Copilot folder does not read "GitHub <folder-N> CLI", whose placeholder the name around it decodes;
# everywhere else it is <folder-N> as before. The places: the friction log's Agent: header line in any form
# parse_friction reads, a step 1 line's stated "prompt vN, <agent>:" prefix and the Summary's " · agent: "
# field (the issue link's agent field is read from them after redaction). A word of its own: on each side the
# span's edge, a blank, sentence punctuation, "-" or "_", never a word character, and no "/", "~" or "\" in
# its blank-delimited token, so ~/Development/Copilot or CopilotX is still replaced. agent_protect hides such
# a run from redact_stream as \004, two bytes from \016-\035 per byte of it, \005 (no pair holds those bytes,
# and gov_value_sed's own markers are \001 and \003); agent_restore puts it back and, when it did, creates
# the file $AGENT_KEPT_FILE for the legend's clause.
agent_word_values() { # "a<TAB>value" for each configured folder or source value agent_protect keeps, "o<TAB>value" for every other
	local v p
	while IFS=$'\t' read -r v p _; do
		case "$p" in "<folder-"*">" | "<source-"*">") if agent_words_name "$v"; then printf 'a\t%s\n' "$v"; else printf 'o\t%s\n' "$v"; fi ;; esac
	done < <(redaction_pairs 2>/dev/null)
}
agent_protect() {
	local vals
	vals="$(agent_word_values | LC_ALL=C sort -u)"
	case $'\n'"$vals" in
	*$'\n'a$'\t'*) ;;
	*)
		cat
		return 0
		;;
	esac
	VALS="$vals" LC_ALL=C awk '
		function rx(v,    m, j, r) { # its words in lower case, any run of blanks, "_", "-" or "\" or none between them
			m = split(tolower(v), W, /[ \t_\\-]+/); r = ""
			for (j = 1; j <= m; j++) if (W[j] != "") r = r (r == "" ? "" : sep) W[j]
			return r
		}
		BEGIN {
			sep = "[ \t_\\\\-]*"
			m = split(ENVIRON["VALS"], L, "\n")
			for (i = 1; i <= m; i++) {
				v = substr(L[i], 3)
				if (substr(L[i], 1, 1) == "a") V[++n] = v; else O[++no] = v
			}
			for (i = 2; i <= n; i++) { x = V[i]; for (j = i - 1; j > 0 && length(V[j]) < length(x); j--) V[j + 1] = V[j]; V[j + 1] = x }
			for (i = 1; i <= n; i++) R[i] = rx(V[i])
			# a longer value of another kind ("Claude Code" beside "Claude") is matched first by the pairs, as the
			# agentsync report matches the longest value first: its matches are left to redact_stream whole
			for (i = 1; i <= no; i++) Q[i] = rx(O[i])
			for (i = 1; i < 256; i++) ord[sprintf("%c", i)] = i
			nib = "\016\017\020\021\022\023\024\025\026\027\030\031\032\033\034\035"
		}
		function enc(s,    i, b, o) {
			o = "\004"
			for (i = 1; i <= length(s); i++) { b = ord[substr(s, i, 1)]; o = o substr(nib, int(b / 16) + 1, 1) substr(nib, b % 16 + 1, 1) }
			return o "\005"
		}
		function edge(c) { return c == "" || c ~ /[ \t]/ || index(",;:()[]\047\"`.-_", c) > 0 }
		function pathy(s, a, b) { # whether the blank-delimited token around s[a..b] holds a path sign
			while (a > 1 && substr(s, a - 1, 1) !~ /[ \t]/) a--
			while (b < length(s) && substr(s, b + 1, 1) !~ /[ \t]/) b++
			return substr(s, a, b - a + 1) ~ /[\/~\\]/
		}
		function protect(span,    low, i, j, k, p, s, l, ok, out) {
			low = tolower(span); split("", cov); split("", take)
			for (i = 1; i <= no; i++) {
				if (Q[i] == "") continue
				p = 1
				while (p <= length(low) && match(substr(low, p), Q[i])) {
					s = p + RSTART - 1; l = RLENGTH
					# no substr(span, 0, 1): BSD awk gives the first character for it
					if (l > 0 && (s == 1 || substr(span, s - 1, 1) !~ /[A-Za-z0-9]/) && substr(span, s + l, 1) !~ /[A-Za-z0-9]/)
						for (j = s; j < s + l; j++) cov[j] = 1
					p = s + 1
				}
			}
			for (i = 1; i <= n; i++) {
				if (R[i] == "") continue
				p = 1
				while (p <= length(low) && match(substr(low, p), R[i])) {
					s = p + RSTART - 1; l = RLENGTH
					ok = l > 0 && edge(s > 1 ? substr(span, s - 1, 1) : "") && edge(substr(span, s + l, 1)) && !pathy(span, s, s + l - 1)
					for (j = s; ok && j < s + l; j++) if ((j in cov) || (j in held)) ok = 0
					if (ok) { take[s] = l; for (j = s; j < s + l; j++) held[j] = 1; p = s + l } else p = s + 1
				}
				split("", held)
				for (j in take) for (k = j; k < j + take[j]; k++) held[k] = 1
			}
			split("", held)
			out = ""; j = 1
			while (j <= length(span)) {
				if (j in take) { out = out enc(substr(span, j, take[j])); j += take[j] } else { out = out substr(span, j, 1); j++ }
			}
			return out
		}
		# the friction log copy is fenced: a "## " line inside it is the log, not a section of the report
		fence && index($0, "~~~") == 1 { fence = 0; print; next }
		!fence && /^## / { sec = $0 }
		sec == "## Agent friction log" && !fence && $0 == "~~~text" { fence = 1; print; next }
		fence && match($0, /^[-* \t]*\**[Aa][Gg][Ee][Nn][Tt]\**[ \t]*:[ \t]*\**[ \t]*/) {
			h = substr($0, 1, RLENGTH); v = substr($0, RLENGTH + 1); t = ""
			if (match(v, /[ \t]+$/)) { t = substr(v, RSTART); v = substr(v, 1, RSTART - 1) }
			print h protect(v) t; next
		}
		fence && match($0, /[|] step 1 [|][^|]*[|] /) {
			h = substr($0, 1, RSTART + RLENGTH - 1); w = substr($0, RSTART + RLENGTH); f = ""
			# the what-text ends at the last "|", as parse_friction cuts the fix column off
			if ((c = match(w, /[|][^|]*$/))) { f = substr(w, c); w = substr(w, 1, c - 1) }
			if (match(w, /^([ \t\047"]|\342\200[\230\231])*/)) { h = h substr(w, 1, RLENGTH); w = substr(w, RLENGTH + 1) }
			if (tolower(substr(w, 1, 8)) == "prompt v" && match(substr(w, 9), /^[0-9][0-9]?[0-9]?/) && substr(w, 9 + RLENGTH, 1) !~ /[A-Za-z0-9_]/) {
				k = 8 + RLENGTH; r = substr(w, k + 1)
				if (match(r, /^[ ,:;-]+/) && (c = index(substr(r, (rl = RLENGTH) + 1), ":")) > 0) {
					# rl, not RLENGTH: protect() runs match() itself
					print h substr(w, 1, k) substr(r, 1, rl) protect(substr(r, rl + 1, c - 1)) substr(r, rl + c) f; next
				}
			}
		}
		sec == "## Summary" && (k = index($0, " · agent: ")) > 0 {
			k += length(" · agent: "); r = substr($0, k); t = ""
			if ((c = index(r, " · ")) > 0) { t = substr(r, c); r = substr(r, 1, c - 1) }
			print substr($0, 1, k - 1) protect(r) t; next
		}
		{ print }'
}
agent_restore() {
	LC_ALL=C awk '
		BEGIN { for (i = 1; i < 256; i++) chr[i] = sprintf("%c", i); for (i = 0; i < 16; i++) val[sprintf("%c", 14 + i)] = i }
		{
			out = ""
			while (match($0, "\004[\016-\035]*\005")) {
				s = substr($0, RSTART + 1, RLENGTH - 2); w = ""
				for (i = 1; i < length(s); i += 2) w = w chr[val[substr(s, i, 1)] * 16 + val[substr(s, i + 1, 1)]]
				out = out substr($0, 1, RSTART - 1) w; $0 = substr($0, RSTART + RLENGTH); kept = 1
			}
			print out $0
		}
		END { if (kept && ENVIRON["AGENT_KEPT_FILE"] != "") printf "" > ENVIRON["AGENT_KEPT_FILE"] }'
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
		LC_ALL=C sed -E -f <(gov_value_sed) |
		REDACT_PAIRS="$(redaction_pairs | LC_ALL=C awk -F '\t' '{ k = $1; if ($3 == "f" || $3 == "j" || $3 == "o") gsub(/[ \t_\\-]/, "", k); print length(k) "\t" $0 }' |
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
	# So are the fixed names of agentsync itself (B13 round 8 J71, the shell twin of H74): agent-context, agent-context-sync
	# and agent-context-sync.git right after a "/", and not followed by a word character, a hyphen or a dot and a
	# word character; and the word agentsync. A one-word organisation named agent or context cut them otherwise
	# (~/<org-1>-context/setup, <org-1>sync, com.<org-1>sync.poll), which the Python report keeps whole. A name
	# is held only where no longer pair covers it, as the Python report checks for a longer value: else a folder
	# named "agent-context Fabrikam" lost its first words to the hold and printed the rest in clear.
	# A heading of the report itself is never redacted, as in the Python report.
	# A pair flagged f or j (a folder or an organisation) is matched as setup_report.Redactor matches a fuzzy
	# value (B13 round 10 L66): a regex of its words, each ASCII letter in either case, any run of blanks, "_",
	# "-" or "\" (printf %q) or none between words (j: at least one, or a CamelCase boundary with the case as
	# written there), a "\" allowed before any other ASCII sign, as %q writes it; a match only where no ASCII
	# letter or digit is right before or after it (when the value starts or ends with a letter, a digit or a
	# byte outside ASCII). Sorted by its length without separators, as Python sorts by _norm. The NFC, NFD and
	# accentless forms stay pairs of their own (add_forms).
	function letters(t, up, low, i, c, out) { # one word; up: its first letter in upper case only, low: its last in lower
		out = ""
		for (i = 1; i <= length(t); i++) {
			c = substr(t, i, 1)
			if (c ~ /[A-Za-z]/) out = out ((i == 1 && up) ? toupper(c) : (i == length(t) && low) ? tolower(c) : "[" toupper(c) tolower(c) "]")
			else if (c ~ /[0-9]/ || c ~ "[\200-\377]") out = out c
			else out = out "\\\\?" (c == "^" || c == "]" ? "\\" c : "[" c "]")
		}
		return out
	}
	function fuzzy(k, v, jn, i, c, p, q, nt, tok, cur, a, b) {
		nt = 0; cur = ""
		for (i = 1; i <= length(v); i++) { # its words: split at separators and at CamelCase boundaries (_tokens)
			c = substr(v, i, 1)
			if (c ~ /[ \t_-]/ || c == "\\") { if (cur != "") tok[++nt] = cur; cur = ""; continue }
			p = substr(v, i - 1, 1); q = substr(v, i + 1, 1)
			# a byte outside ASCII before the capital is read as a small letter ("CaféPlans")
			if (cur != "" && c ~ /[A-Z]/ && (p ~ /[a-z0-9]/ || p ~ "[\200-\377]" || (p ~ /[A-Z]/ && q ~ /[a-z]/))) { tok[++nt] = cur; cur = "" }
			cur = cur c
		}
		if (cur != "") tok[++nt] = cur
		if (!nt) return
		a = ""
		for (i = 1; i <= nt; i++) a = a (i > 1 ? (jn ? "[ \t_\\\\-]+" : "[ \t_\\\\-]*") : "") letters(tok[i], 0, 0)
		if (jn && nt >= 2) { # "HoMe": a small letter, then the next word\047s capital, as written
			b = ""
			for (i = 1; i <= nt; i++) {
				if ((i < nt && substr(tok[i], length(tok[i]), 1) !~ /[A-Za-z]/) || (i > 1 && substr(tok[i], 1, 1) !~ /[A-Za-z]/)) { b = ""; break }
				b = b letters(tok[i], i > 1, i < nt)
			}
			if (b != "") a = "(" a ")|(" b ")"
		}
		rx[k] = a
		key[k] = tolower(tok[1]); sub(/[^a-z0-9].*$/, "", key[k]) # a word every match holds, in lower case
		lead[k] = substr(v, 1, 1) ~ /[A-Za-z0-9]/ || substr(v, 1, 1) ~ "[\200-\377]"
		trail[k] = substr(v, length(v), 1) ~ /[A-Za-z0-9]/ || substr(v, length(v), 1) ~ "[\200-\377]"
		for (i = 1; i < k; i++) if ((i in rx) && rx[i] == a && ph[i] == ph[k]) delete rx[k] # one form of many
	}
	function keep(text, isheld) { if (text != "") { m++; tx[m] = text; th[m] = isheld } }
	# Flag o (B13 round 11 M50): an organisation is cut out of a longer word where a small letter or a digit comes
	# before a capital it starts with (AcmeContoso), and where a capital and a small letter (ContosoGroup) or a
	# digit (Contoso2026) follow it; never where the text before or after it, as written, completes one of the
	# identifiers hw (an organisation Drive after One, or One before Drive): setup_report._org_edges, case for case.
	function org_lead(k, before, hit,    i, h, p, n) {
		if (!og[k] || substr(before, length(before), 1) !~ /[a-z0-9]/ || substr(hit, 1, 1) !~ /[A-Z]/) return 0
		n = length(okey[k])
		for (i = 1; i <= nw; i++) {
			h = hw[i]
			if (length(h) <= n || tolower(substr(h, length(h) - n + 1)) != okey[k]) continue
			p = substr(h, 1, length(h) - n)
			if (length(before) >= length(p) && substr(before, length(before) - length(p) + 1) == p) return 0
		}
		return 1
	}
	function org_trail(k, hit, after,    i, h, r, n) {
		if (!og[k] || (substr(after, 1, 1) !~ /[0-9]/ && substr(after, 1, 2) !~ /^[A-Z][a-z]/)) return 0
		n = length(okey[k])
		for (i = 1; i <= nw; i++) {
			h = hw[i]
			if (length(h) <= n || tolower(substr(h, 1, n)) != okey[k]) continue
			r = substr(h, n + 1)
			if (substr(after, 1, length(r)) == r && substr(after, length(r) + 1, 1) !~ /[a-z]/) return 0
		}
		return 1
	}
	function covered(s, c, L, k, v, t, i, off, rs, rl, hit) { # 1: a pair longer than L spans s from c on, for L chars or more
		for (k = 1; k <= np; k++) {
			if (k in rx) { # a fuzzy pair by its pattern, as Python checks a longer value (B13 round 10 L66);
				# RSTART and RLENGTH of the caller, set before this runs, are kept
				rs = RSTART; rl = RLENGTH; t = s; off = 0; hit = 0
				while (!hit && match(t, rx[k])) {
					if (RLENGTH > L && off + RSTART <= c && off + RSTART + RLENGTH >= c + L) hit = 1
					off += RSTART; t = substr(t, RSTART + 1)
				}
				RSTART = rs; RLENGTH = rl
				if (hit) return 1
				continue
			}
			v = val[k]
			if (length(v) <= L) continue
			t = s; off = 0
			while ((i = (k in lv) ? index(tolower(t), lv[k]) : index(t, v)) > 0) {
				if (off + i <= c && off + i + length(v) >= c + L) return 1
				off += i; t = substr(t, i + 1)
			}
		}
		return 0
	}
	function own_at(s, best, t, i, off, c, n, x, ok) { # where an own name to hold starts in s (0: none); OL its length
		best = 0; t = s; off = 0
		while ((i = index(t, "/agent-context")) > 0) {
			c = off + i + 1; ok = 0
			for (x = 1; x <= 3 && !ok; x++) {
				n = (x == 1 ? 22 : x == 2 ? 18 : 13)
				if (n > 13 && substr(s, c + 13, n - 13) != substr("-sync.git", 1, n - 13)) continue
				ok = substr(s, c + n, 1) !~ /[A-Za-z0-9_-]/ && !(substr(s, c + n, 1) == "." && substr(s, c + n + 1, 1) ~ /[A-Za-z0-9_]/)
			}
			if (ok && !covered(s, c, n)) { best = c; OL = n; break }
			off += i; t = substr(t, i + 1)
		}
		t = s; off = 0
		while ((i = index(t, "agentsync")) > 0 && (!best || off + i < best)) {
			# a pair of the word itself (a folder named agentsync) is no hold either, as in the Python report
			if (!covered(s, off + i, 8)) { best = off + i; OL = 9; break }
			off += i; t = substr(t, i + 1)
		}
		return best
	}
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
		for (k = 1; k <= np; k++) {
			split(rows[k], f, "\t"); val[k] = f[1]; ph[k] = f[2]
			# a proxy value is matched in any ASCII case, as setup_report registers it (ignore_case or url mode):
			# under LC_ALL=C tolower keeps every byte where it is, so the offsets found in the lower-cased text cut
			# the line as written (B13 round 13 O2, O67). Its kind is in its placeholder: no other pair writes one
			if ((index(f[2], "<proxy-") || index(f[2], "<link-")) && !(f[3] == "f" || f[3] == "j" || f[3] == "o")) lv[k] = tolower(f[1]) # a link: ignore_case (B13 round 17 U61)
			if (f[3] == "i") lv[k] = tolower(f[1]) # a project folder: ignore_case, a whole word (B13 round 18 V46)
			if (f[3] == "f" || f[3] == "j" || f[3] == "o") { fz[k] = 1; fuzzy(k, f[1], f[3] == "j"); og[k] = f[3] == "o" }
			if (og[k]) { okey[k] = tolower(f[1]); gsub(/[ \t_\\-]/, "", okey[k]) }
		}
		nh = split("# agentsync setup report|## Summary|## Agent friction log|## Environment|## Installer|" \
			"## Configuration|## Doctor|## Status|## Background runs|## Recent errors", hd, "|")
		for (k = 1; k <= nh; k++) heading[hd[k]] = 1
		# the identifiers of agentsync itself, held whole beside an organisation of flag o ("One", "Cloud"):
		# setup_report._HELD_IDENTIFIERS, a test keeps them equal (B13 round 11 M50); and CamelCase words the
		# report prints itself, FluidAudio and LaunchAgent(s) (B13 round 12 N57), the plist keys and the doctor
		# words (B13 round 13 O54, O68: "environment" is a report word here, not in the Python report)
		nw = split("OneDrive GoogleDrive SharedLibraries Dropbox Box Egnyte CloudStorage SharePoint GitHub FluidAudio LaunchAgent LaunchAgents ProgramArguments EnvironmentVariables StandardOutPath StandardErrorPath StartInterval StartCalendarInterval RunAtLoad ProcessType LowPriorityIO ThrottleInterval LimitLoadToSessionType MaterializeDatalessFiles AgentSyncLauncher CodeDirectory CodeRequirement SystemPolicyAllFiles TeamIdentifier TeamID sharedRepository OpenDocument insteadOf macOS iTerm", hw, " ")
	}
	# and the opener of the friction fence, a line the report itself writes (a "~~~" in the log is broken up
	# before it gets here): a folder or a source id named "text" does not turn it into a placeholder (B13 round 7 H41)
	$0 in heading || $0 == "~~~text" { print; next }
	{
		m = 0; s = $0
		while (1) {
			if (!match(s, /<(name|user|host|mdmhost|tmp|path|serial|label|remote|value|org|Org|team|TEAMID|(org|folder|library|source|email|guid|hash|commit|proxy|link)-([0-9]+|N))>/)) RSTART = 0
			if ((r = repo_at(s)) && (!RSTART || r < RSTART)) { RSTART = r; RLENGTH = RL }
			if ((r = own_at(s)) && (!RSTART || r < RSTART)) { RSTART = r; RLENGTH = OL }
			if (!RSTART) break
			keep(substr(s, 1, RSTART - 1), 0)
			keep(substr(s, RSTART, RLENGTH), 1)
			s = substr(s, RSTART + RLENGTH)
		}
		keep(s, 0)
		ns = m
		for (j = 1; j <= m; j++) { seg[j] = tx[j]; held[j] = th[j] }
		low = tolower($0)
		for (k = 1; k <= np; k++) {
			if (split(rows[k], f, "\t") < 2) continue
			v = f[1]
			if (k in fz) {
				if (!(k in rx) || (key[k] != "" && !index(low, key[k])) || !match($0, rx[k])) continue
				m = 0
				for (j = 1; j <= ns; j++) {
					if (held[j]) { keep(seg[j], 1); continue }
					s = seg[j]; acc = ""
					while (match(s, rx[k])) {
						i = RSTART; n = RLENGTH
						pre = (i > 1) ? substr(s, i - 1, 1) : substr(acc, length(acc), 1)
						post = substr(s, i + n, 1)
						if ((lead[k] && pre ~ /[A-Za-z0-9]/ && !org_lead(k, acc substr(s, 1, i - 1), substr(s, i, n))) ||
							(trail[k] && post ~ /[A-Za-z0-9]/ && !org_trail(k, substr(s, i, n), substr(s, i + n)))) {
							acc = acc substr(s, 1, i); s = substr(s, i + 1); continue
						}
						keep(acc substr(s, 1, i - 1), 0); acc = ""
						keep(f[2], 1)
						s = substr(s, i + n)
					}
					keep(acc s, 0)
				}
				ns = m
				for (j = 1; j <= m; j++) { seg[j] = tx[j]; held[j] = th[j] }
				continue
			}
			if ((k in lv) ? !index(low, lv[k]) : !index($0, v)) continue # every plain piece is a piece of the input line
			n = length(v); word = (f[3] == "w" || f[3] == "i"); tail = (f[3] == "t"); alnum = (f[3] == "e"); m = 0
			for (j = 1; j <= ns; j++) {
				if (held[j]) { keep(seg[j], 1); continue }
				s = seg[j]; acc = ""
				while ((i = (k in lv) ? index(tolower(s), lv[k]) : index(s, v)) > 0) {
					pre = (i > 1) ? substr(s, i - 1, 1) : substr(acc, length(acc), 1)
					post = substr(s, i + n, 1)
					if ((word && (pre ~ /[A-Za-z0-9_]/ || post ~ /[A-Za-z0-9_]/)) || (tail && post ~ /[A-Za-z0-9_.-]/) || (alnum && post ~ /[A-Za-z0-9]/)) {
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
# The label of that run when another run of install.log started in its second, as strip_run_pids numbers
# them (first-appearance order over the whole log, the first keeping the bare stamp); nothing when none did.
# Its start time alone names two runs then (B13 round 13 O22), as setup_report._run_named says.
install_run_label() {
	[ -f "$SETUP_LOG" ] || return 0
	LC_ALL=C awk '
		$2 !~ /^run=/ { next }
		{ id = substr($2, 5) }
		!(id in label) { st = substr(id, 1, 16); k = ++seen[st]; label[id] = k == 1 ? st : st "." k; stamp[id] = st; order[++n] = id }
		index($0 " ", " step=list-folders ") { lists[id] = 1 }
		END {
			for (i = n; i > 0; i--) if (!(order[i] in lists)) { if (seen[stamp[order[i]]] > 1) print label[order[i]]; exit }
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
		NH { NH = 0; b = ""; ended = ""; late = ""; next }
		/^Attempt:/ { b = substr($0, 10, 20); if (b !~ (stamp "$")) b = ""; ended = ""; late = ""; next }
		q { b = (late != "" && ended != "") ? late : substr($0, 1, 20); ended = ""; late = ""; next }
		ended != "" && $0 ~ s1 && secs($0) - secs(ended) > gap { b = late != "" ? late : substr($0, 1, 20); ended = ""; late = ""; restate($0); next }
		$0 !~ (stamp " [|]") { next }
		b == "" { b = substr($0, 1, 20) }
		# the unbroken run of step 1 lines at or after the end line, whatever the gap, which an opening step 1
		# error takes with it (parse_friction, B13 round 10 L26; round 12 N23: the finished session runs
		# nothing after its report, so a line 71 s after its end line is the next one)
		ended != "" { if ($0 ~ (stamp " [|] step 1 [|]") && secs($0) >= secs(ended)) { if (late == "") late = substr($0, 1, 20) } else late = "" }
		/\| end \| finished[[:space:]]*$/ { ended = substr($0, 1, 20); late = "" }
		END { print b }' "$f" 2>/dev/null || true
}
# The one outcome the shell report computes (B13 round 7 H24): "<N><TAB><agent as written>" when every timed
# line of the friction log's latest attempt (friction_attempt_begin's) but its end line is a step 1 line, the
# attempt has no header, and its first step 1 error that states "prompt vN, <agent>:" (at any place: a stopping
# session may log a deviation first, B13 round 10 L26) is not the line --log-start writes for a copy it
# stopped. Nothing otherwise. With no install run and an error among events that are all
# step 1's, the agentsync report calls that attempt "failed at step 1" whichever error it picks
# (compute_outcome), and takes the version and the agent from the same line, its first stated step 1 error;
# a line of another step leaves it to that report, so the two cannot drift. A second step 1 error, or a step 1 prompt or question line, after the stated one no
# longer leaves the outcome uncomputed (B13 round 9 K14: it was the attempt's only line).
# A copy --log-start stopped has a header and a stop line that states nothing, so it was never computed, though
# the agentsync report computes "failed at step 1" for it: when the attempt's first step 1 error is that stop
# line and every timed line is step 1's, the version and the agent come from the attempt's first Prompt: and
# Agent: header lines, and a third field says so and carries what the stop line states (B13 round 11 M14,
# round 12 N44): "header:<V>:<old|new>:<fork|remote>", where V is the installer's prompt version, then which of
# its two reasons stopped the copy, then the cause its fix names; any part may be empty (B13 round 13 O46).
# Prints "<N><TAB><agent><TAB><third field>", with an empty third field for a stated step 1 error.
friction_step1_stated() {
	local f
	f="$(friction_file)"
	[ -f "$f" ] || return 0
	LC_ALL=C awk -v mark="| step 1 | error | $PROMPT_STOPPED " -v gap="$NEW_SESSION_GAP" -v s1="$STEP1_ERROR_RE" "$FRICTION_AWK"'
		BEGIN { stamp = "^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z" }
		function fresh() { first = ""; other = 0; ended = ""; hp = ""; ha = ""; e1 = "" }
		function value(line) { # as parse_friction: the text after the colon, less stars and blanks around it
			sub(/^[^:]*:/, "", line); gsub(/^[ \t*]+|[ \t*]+$/, "", line); return line
		}
		{ q = track($0) }
		NH { NH = 0; fresh() }
		/^Attempt:/ { fresh(); next }
		/^[-* ]*Prompt:/ { if (hp == "") hp = value($0); next }
		/^[-* ]*Agent:/ { if (ha == "") ha = value($0); next }
		q { fresh() }
		ended != "" && $0 ~ s1 && secs($0) - secs(ended) > gap { fresh(); restate($0) }
		$0 !~ (stamp " [|]") { next }
		/\| end \| finished[[:space:]]*$/ { ended = substr($0, 1, 20); next }
		e1 == "" && $0 ~ s1 { e1 = $0 }
		first == "" && $0 ~ s1 && stated($0) { first = $0 }
		$0 !~ (stamp " [|] step 1 [|]") { other = 1 }
		END {
			if (!HD && !other && first != "" && !index(first, mark) && stated(first)) printf "%s\t%s\t\n", SV, SR
			else if (HD && !other && index(e1, mark) && hp != "") {
				# and what the stop line says, as setup_report._prompt_copy_note reads it (B13 round 12 N44): the
				# version of the installer that stopped it, which of its two reasons, and the cause its fix names
				sub(/^[vV]/, "", hp); v = ""; why = ""; fix = e1
				if (match(e1, /this installer is for setup prompt v[0-9]+/)) v = substr(e1, RSTART + 36, RLENGTH - 36) + 0
				r = index(e1, ": the pasted copy is not the current one") ? "old" : index(e1, ": this checkout is older than the prompt") ? "new" : ""
				while ((i = index(fix, " | ")) > 0) fix = substr(fix, i + 3)
				if (index(fix, "so a copy newer than it came from another branch or a fork")) why = "fork"
				else if (index(fix, "point this Mac\047s checkout at main of") == 1) why = "remote"
				printf "%s\t%s\theader:%s:%s:%s\n", hp, ha, v, r, why
			}
		}' "$f" 2>/dev/null || true
}
# Whether the friction log's latest attempt holds a timed "<UTC> | step N | ..." line, with parse_friction's
# attempt boundaries: the unbroken run of step 1 lines after an end line goes to the next Attempt: header (B13
# round 13 O24), which friction_step1_stated does not carry. Its Attempt:, Prompt: and Agent: lines and its
# "| end | finished" line are no step line (B13 round 11 M15).
friction_latest_has_step() {
	local f
	f="$(friction_file)"
	[ -f "$f" ] || return 1
	LC_ALL=C awk -v gap="$NEW_SESSION_GAP" -v s1="$STEP1_ERROR_RE" "$FRICTION_AWK"'
		BEGIN { step = "^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z [|] step [0-9]+ [|]" }
		function fresh() { has = 0; ended = ""; late = 0 }
		{ q = track($0) }
		NH { NH = 0; fresh() }
		# the trailing step 1 lines of the finished attempt, at or after its end line, go to the next Attempt: header,
		# as parse_friction moves them (B13 round 13 O24); the attempt still begins at its Attempt: time
		/^Attempt:/ { carried = ended != "" && late; fresh(); has = carried; next }
		q { fresh() }
		ended != "" && $0 ~ s1 && secs($0) - secs(ended) > gap { fresh(); restate($0) }
		/\| end \| finished[[:space:]]*$/ { ended = substr($0, 1, 20); late = 0; next }
		# the run is unbroken when the last timed line since the end line is such a step 1 line
		ended != "" && $0 ~ step { late = $0 ~ /^[^|]*[|] step 1 [|]/ && secs($0) >= secs(ended) }
		$0 ~ step { has = 1 }
		END { exit !has }' "$f" 2>/dev/null
}
# The Prompt and Agent values of the friction log's latest attempt, as Attempt.said gives them: the attempt's
# first Prompt: and Agent: header lines, else what the step 1 error line that opened a header-less attempt
# states (the log's first line, a line track() reads as another session's, or one dated more than
# NEW_SESSION_GAP after an end line), with the attempt boundaries every other reader here takes. Reads the
# friction text on stdin (the redacted one of the report, for the link: B13 round 10 L2) and prints
# "<prompt><TAB><agent>", either possibly empty; nothing when the text holds no attempt.
friction_latest_said() {
	LC_ALL=C awk -v gap="$NEW_SESSION_GAP" -v s1="$STEP1_ERROR_RE" "$FRICTION_AWK"'
		BEGIN { stamp = "^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z [|]" }
		function fresh() { hp = ""; ha = ""; sp = ""; sa = ""; ended = "" }
		function from(line) { fresh(); if (line ~ s1 && stated(line)) { sp = "v" SV; sa = SR } }
		function value(line) { # as parse_friction: the text after the colon, less stars and blanks around it
			sub(/^[^:]*:/, "", line); gsub(/^[ \t*]+|[ \t*]+$/, "", line); return line
		}
		{ first = !SEEN; q = track($0) }
		NH { NH = 0; fresh() }
		/^Attempt:/ { fresh(); next }
		q { from($0); next }
		ended != "" && $0 ~ s1 && secs($0) - secs(ended) > gap { from($0); restate($0); next }
		first && $0 ~ stamp { from($0); next }
		# an attempt with no header and no words takes them from its first step 1 error that states them, wherever
		# it sits, as parse_friction (B13 round 10 L26)
		!HD && sp == "" && $0 ~ s1 && stated($0) { sp = "v" SV; sa = SR; next }
		/^[-* ]*Prompt:/ { if (hp == "") hp = value($0); next }
		/^[-* ]*Agent:/ { if (ha == "") ha = value($0); next }
		/\| end \| finished[[:space:]]*$/ { ended = substr($0, 1, 20) }
		END { if (SEEN) printf "%s\t%s\n", (hp != "" ? hp : sp), (ha != "" ? ha : sa) }'
}
# Whether the friction log's latest attempt has no install run of its own, by the test fallback_install_line
# makes: a --report-only run, and no install log, no install run in it, or a last one that started before the
# attempt began.
attempt_ran_no_install() {
	local last at begin
	[ "$REPORT_ONLY" -eq 1 ] || return 1
	[ -f "$SETUP_LOG" ] || return 0
	last="$(last_install_run)"
	[ -n "$last" ] || return 0
	at="${last%% *}"
	begin="$(utc_stamp "$(friction_attempt_begin)")"
	[ -n "$begin" ] && [ -n "$(utc_stamp "$at")" ] && [[ "$at" < "$begin" ]]
}
# The Summary's outcome line. Computed in the one case above, in the agentsync report's words; in every other
# case the line says that it is not, so "copy the five values from the Summary" (setup-feedback.md) finds a
# reason and not a gap. OUTCOME_LINE_HEAD is how write_fallback_report finds the computed line again, after
# redaction, for the link.
OUTCOME_LINE_HEAD="- outcome: failed at step 1 (computed: no install.sh run; step 1 logged an error) · prompt: v"
# Where macOS keeps throwaway files: paths.TEMPORARY_ROOTS (a test keeps them equal).
TEMPORARY_ROOTS="/tmp /private/tmp /var/folders /private/var/folders"
# The run type the agentsync report computes when no install run says launchd=simulated (compute_run_type, by
# paths.is_temporary): "sandbox" when HOME, or the folder it resolves to, is one of TEMPORARY_ROOTS or lies under
# one, else "real mac" (B13 round 8 J22). run_kind adds the simulated type, which the run line of an attempt with
# an install run needs (B13 round 9 K8).
home_run_kind() {
	local c r
	for c in "$HOME" "$(cd "$HOME" 2>/dev/null && pwd -P)"; do
		[ -n "$c" ] || continue
		for r in $TEMPORARY_ROOTS; do
			case "$c" in "$r" | "$r"/*)
				printf 'sandbox'
				return 0
				;;
			esac
		done
	done
	printf 'real mac'
}
# Whether the run compute_run_type judges says launchd=simulated (any of its lines, as _SIMULATED_RE reads
# them): of the runs in install.log that started since the friction log's latest attempt began (every run when
# it gives no time, runs_for_attempt), the last install run, else the last run (a --list-folders one).
attempt_run_simulated() {
	[ -f "$SETUP_LOG" ] || return 1
	LC_ALL=C awk -v lo="$(utc_stamp "$(friction_attempt_begin)")" '
		$2 !~ /^run=/ { next }
		{ id = substr($2, 5) }
		!(id in at) { order[++n] = id; at[id] = $1 }
		index($0 " ", " step=list-folders ") { lists[id] = 1 }
		tolower($0) ~ /(^|[^a-z0-9_])launchd[ \t]*[=:][ \t]*simulated([^a-z0-9_]|$)/ { sim[id] = 1 }
		END {
			for (i = n; i > 0; i--) if ((lo == "" || at[order[i]] >= lo) && !(order[i] in lists)) { last = order[i]; break }
			if (last == "") for (i = n; i > 0; i--) if (lo == "" || at[order[i]] >= lo) { last = order[i]; break }
			exit !(last != "" && (last in sim))
		}' "$SETUP_LOG" 2>/dev/null
}
run_kind() { if attempt_run_simulated; then printf 'simulated launchd'; else home_run_kind; fi; }
# The form's run_type option and the Summary's words for it, as setup_report.ISSUE_RUN_TYPES and its Summary have them.
run_type_label() {
	case "$(run_kind)" in
	simulated*) printf 'Sandbox with simulated launchd' ;;
	sandbox) printf 'Sandbox' ;;
	*) printf 'Real Mac' ;;
	esac
}
run_type_why() {
	case "$(run_kind)" in
	simulated*)
		printf 'install.log says launchd=simulated'
		[ "$(home_run_kind)" != sandbox ] || printf '; HOME is also under a temporary folder'
		;;
	sandbox) printf 'HOME is under a temporary folder' ;;
	*) printf 'HOME is not under a temporary folder and launchd was not simulated' ;;
	esac
}
# A stated version above this installer's has the agentsync report's note (H23) in the same parenthesis, naming
# this installer, which writes the report (B13 round 8 J21); no blank comes before the digits, and no " · agent: "
# is added, so fallback_issue_link reads the version and the agent as before. Like _newer_than_me_note it names no
# cause: a step 1 error can come from the Xcode or the not-a-checkout stop, before any pull ran (B13 round 9 K26).
# The run type comes before the agent, where the agentsync report has it. Not computed, the line says which
# field comes from where, and a run line of its own gives the run type the agentsync report computes, which the
# friction log does not hold (B13 round 9 K8); the link fills that run type, and the latest attempt's Prompt
# version and Agent where it states them, as _issue_link does with or without an outcome (B13 round 10 L2).
fallback_outcome_line() {
	local stated="" n note="" agent hdr=0 choose why bracket
	! attempt_ran_no_install || stated="$(friction_step1_stated)"
	if [ -n "$stated" ]; then
		n="${stated%%$'\t'*}"
		agent="${stated#*$'\t'}"
		case "${agent#*$'\t'}" in
		header:*)
			# a copy --log-start stopped: the version from the attempt's header, and the agentsync report's note
			# on its prompt line (B13 round 11 M14), word for word: read from the stop line, its reason and its
			# fix column's cause, for "v7 or older" and a newer copy too (B13 round 12 N44). The whole bracket is
			# that note, with no "from" label, as _prompt_line_note gives it (B13 round 13 O18)
			hdr=1
			set -- "${agent##*$'\t'header:}"
			why="${1##*:}"
			set -- "${1%:*}"
			case "${1##*:}" in
			old) note="; older than the installer's v${1%%:*}: the pasted copy was not the current README" ;;
			new)
				note="; newer than the installer's v${1%%:*}"
				case "$why" in
				fork) note="$note: this Mac's checkout follows main, so the pasted copy came from another branch or a fork" ;;
				remote) note="$note: this Mac's checkout follows another remote or branch" ;;
				esac
				;;
			esac
			[ -z "$note" ] || note="$note, or the --log-start value was changed"
			;;
		esac
		agent="${agent%%$'\t'*}"
		# H23's note, for a version a step 1 error line states (no installer stop, so no cause: K26)
		case "$hdr/$n" in
		1/* | */ | */*[!0-9]*) ;;
		*) [ "$n" -le "$SETUP_PROMPT_COMPAT" ] || note="; newer than this installer, which is for prompt v$SETUP_PROMPT_COMPAT" ;;
		esac
		if [ "$hdr" -eq 1 ]; then bracket="${note:+ (${note#; })}"; else bracket=" (from the step 1 error line$note)"; fi
		say "$OUTCOME_LINE_HEAD$n$bracket · run: $(run_type_label) (computed: $(run_type_why))${agent:+ · agent: $agent}"
	else
		# where the Outcome is read from: the attempt's step lines, the install runs of the attempt when it has
		# no step line, or neither (B13 round 11 M15: it named step lines an attempt did not have)
		if friction_latest_has_step; then
			choose="; the Outcome is yours to choose from the friction log's step lines"
		elif ! attempt_ran_no_install; then
			choose=", and the friction log has no step line for it; the Outcome is yours to choose from the Installer section, which has this attempt's install runs"
		else
			choose=", and the friction log has no step line for it; the Outcome is yours to choose"
		fi
		say "- outcome: not computed (this shell fallback computes none for this attempt$choose; the link fills Run type, and Prompt version and Agent where the latest attempt states them)"
		say "- run: $(run_type_label) (computed: $(run_type_why))"
	fi
}
# The Summary's install.sh line. A --report-only run only writes the report, so its own exit 0 says nothing of
# the setup: it gives the last install run's instead, dated, so an earlier run's is not read as this
# attempt's (B13 round 4 Q23). A run that started before the friction log's latest attempt began is said to be
# an earlier one, as setup_report._install_line scopes runs to the attempt (B13 round 5 F30); no install log is
# not "no install run in the install log" (F29).
# The commit as the Summary shows it: COMMIT and its tree; where COMMIT is "-", which install.log's commit= field
# and the install stamp keep as their machine word, why there is none (B13 round 9 K17, K29): a wheel, a copy
# with no .git (the --report-only checkout, or a real run's SOURCE), or a .git git could not read, as --version
# says "unknown". "-" as before when no read was tried (a run stopped before it).
commit_shown() {
	local d="$repo"
	if [ "$COMMIT" != "-" ]; then
		printf '%s' "$COMMIT${DIRTY:+ tree=$DIRTY}"
		return 0
	fi
	if [ "$REPORT_ONLY" -eq 0 ]; then
		[ "$SOURCE_KIND" != wheel ] || { printf 'none (installed from a wheel)' && return 0; }
		d="$SOURCE"
	fi
	if [ -n "$d" ] && [ ! -e "$d/.git" ]; then
		printf 'none (%s has no .git: not a git checkout)' "$d"
	elif [ -n "$d" ] && [ "$COMMIT_TRIED" -eq 1 ]; then
		printf 'unknown (%s)' "$(unread_reason "$d")"
	else
		printf '%s' "$COMMIT"
	fi
}
fallback_install_line() { # RC
	local head last at rc begin what label
	head="- install.sh: setup-prompt-compat $SETUP_PROMPT_COMPAT, source commit $(commit_shown)"
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
	label="$(install_run_label)"
	[ -z "$label" ] || label=", run $label" # a comma, as the Python report's: no bracket in the line's own
	if [ -n "$begin" ] && [ -n "$(utc_stamp "$at")" ] && [[ "$at" < "$begin" ]]; then
		say "$head; no install run during this attempt (began $begin); the last one, started $at$label, $what"
	else
		say "$head; the last install run (started $at$label) $what"
	fi
}
# The label setup_report.origin_label gives URL: github.com/renchris/agent-context-sync for that repository's
# https, ssh or scp URL (with or without .git or a trailing /, its owner and name in any case), else "other
# (redacted)", or "none" for no URL. Never the URL itself: a fork or mirror URL may name a person or a company.
origin_label() { # URL
	local u="$1" s re='^((https?|ssh|git)://([^@/]+@)?|[^@/]+@)github\.com[:/]+([^/]+)/([^/]+)$'
	if [ -z "$u" ]; then
		printf 'none'
		return 0
	fi
	s="${u%/}"
	s="${s%.git}"
	if [[ "$s" =~ $re ]] && [ "$(printf '%s/%s' "${BASH_REMATCH[4]}" "${BASH_REMATCH[5]}" | LC_ALL=C tr '[:upper:]' '[:lower:]')" = renchris/agent-context-sync ]; then
		printf 'github.com/renchris/agent-context-sync'
	else
		printf 'other (redacted)'
	fi
}
# The shell report's install source line, which the setup prompt's git quote promises (B13 round 18 V29):
# ~/src/agent-context-sync's origin as it is configured, read from its own config files the way
# git_remote_entries reads them (the last url of [remote "origin"]; config.worktree last, as git reads it), as
# the agentsync report reads `git config --get remote.origin.url` (V70). No git process: without the developer
# tools /usr/bin/git offers to install them.
fallback_install_source_line() {
	local dir="$HOME/src/agent-context-sync" g v url=""
	if [ ! -e "$dir/.git" ]; then
		say "- install source: no checkout at ~/src/agent-context-sync"
		return 0
	fi
	while IFS= read -r g; do
		[ -f "$g" ] || continue
		v="$(LC_ALL=C awk '
			/^[ \t]*\[/ { o = ($0 ~ /^[ \t]*\[[ \t]*[Rr][Ee][Mm][Oo][Tt][Ee][ \t]+"origin"[ \t]*\]/); next }
			o && tolower($0) ~ /^[ \t]*url[ \t]*=/ {
				u = $0; sub(/^[^=]*=[ \t]*/, "", u); sub(/[ \t\r]*$/, "", u); gsub(/^"|"$/, "", u); last = u
			}
			END { if (last != "") print last }
		' "$g" 2>/dev/null || true)"
		[ -z "$v" ] || url="$v"
	done < <(checkout_git_configs "$dir")
	# a URL rewrite is said beside the label, as setup_report._checkout_facts says it, never its host: git's
	# longest insteadOf value that starts the URL, case-sensitive, and only when the rewritten URL's label
	# differs (a rewrite to git@github.com: is the same repository; B13 round 19 W7, W23)
	local label best="" base="" iv b said="" found=""
	label="$(origin_label "$url")"
	if [ "$label" != none ]; then
		while IFS=$'\t' read -r iv b; do
			iv="${iv#x}" # an empty value matches every URL, as in git
			case "$url" in "$iv"*) [ -n "$found" ] && [ "${#iv}" -le "${#best}" ] || best="$iv" base="$b" found=1 ;; esac
		done < <(git_rewrite_pairs)
		[ -z "$found" ] || [ "$(origin_label "$base${url#"$best"}")" = "$label" ] ||
			said=" (a URL rewrite sends it elsewhere)"
	fi
	say "- install source: checkout ~/src/agent-context-sync · origin: $label$said"
}
# The shell report's body (redact_stream redacts it): the same headings as `agentsync setup-report`.
# The sha REF names in the checkout GIT, read from its ref files with no git process: the loose file first, else
# its packed-refs line; empty when neither holds it.
checkout_ref_sha() { # GIT REF
	local s=""
	if [ -f "$1/$2" ] && [ ! -L "$1/$2" ]; then { IFS= read -r s; } <"$1/$2" 2>/dev/null || true; fi
	s="${s#"${s%%[![:space:]]*}"}" # as Python strips it: a blank or a CR around the sha is no part of it
	s="${s%%[[:space:]]*}"
	[ -n "$s" ] || s="$(R="$2" LC_ALL=C awk '$2 == ENVIRON["R"] { print $1; exit }' "$1/packed-refs" 2>/dev/null || true)"
	printf '%s' "$s"
}
# The words after the local-work line patch count, as setup_report.LOCAL_WORK_WHERE (B13 round 10 L8, L25); "by its
# hash when it can be read", since an unreadable carried patch is listed with none (round 17 U36, U60, U89).
LOCAL_WORK_WHERE="no patch text is in this report: each patch goes once to the private bring-back.md section 3; any patch an earlier report already carried is listed there, by its hash when it can be read"
# The command that sends a patch made readable, as setup_report.LOCAL_WORK_AGAIN (B13 round 14 Q29).
# shellcheck disable=SC2088 # the ~ is printed
LOCAL_WORK_AGAIN="~/src/agent-context-sync/scripts/install.sh --report-only"
# The count-only line that says step 1 kept local work, as setup_report._local_work_line writes it (B13 round 9
# K30, round 10 L8, L25, L40, L58). Patches are the measure of kept work: the *.patch files at the top of the
# setup folder local-work, the files section 3 of bring-back.md reads, with the newest time. The checkout
# local-work-* branches, read from the ref files of its common dir (no git: without developer tools it offers
# to install them; a .git file names the git dir, B13 round 11 M49, M70), are counted beside them, less a
# branch at the main or origin/main commit or at any commit the origin/main reflog names, which holds no commit
# of its own, unless a patch is named for it (*.<branch>.patch; round 11 M54, M71). The patches are counted
# whatever the checkout is. With branches but no patch the line does not say kept. Patches this account cannot
# read are counted apart: bring-back.md does not hold them (B13 round 14 Q29), less one an earlier attempt's
# report carried, which section 3 does not send again (unread_patch_count, B13 round 16 T24).
fallback_local_work_line() {
	local n newest b counts u=0 unread=""
	counts="$(local_work_counts)"
	IFS='|' read -r n newest b <<<"$counts"
	if [ "$n" -gt 0 ]; then
		u="$(unread_patch_count)"
		u="${u:-0}"
		[ "$u" -eq 0 ] || unread=", $u of them unreadable and not in bring-back.md (chmod u+r, then run $LOCAL_WORK_AGAIN again)"
		# a known count takes its noun from the count, as the report's line does (B13 round 15 S31)
		local pf="patch files" lb="local-work branches"
		[ "$n" != 1 ] || pf="patch file"
		[ "$b" != 1 ] || lb="local-work branch"
		say "- local work kept by setup prompt step 1: $n $pf in the setup folder's local-work$unread${newest:+, newest written $newest} ($LOCAL_WORK_WHERE); $b $lb in the checkout"
	elif [ "$b" -gt 0 ]; then
		# the counted branches hold commits of their own: those commits are on the branch only (B13 round 13 O23);
		# section 3 has the remedy, which the issue path, sending only this report, never shows (B13 round 16 T82);
		# a known count takes no "(es)", and one branch has commits of its own (round 17 U38)
		local own="their own"
		[ "$b" != 1 ] || own="its own"
		say "- local-work branches in the checkout: $b with commits of $own, no patch file in the setup folder's local-work (moved, deleted or never written; those commits are not in bring-back.md, whose section 3 says how to send them)"
	fi
	return 0
}
# The counts fallback_local_work_line prints, and write_bring_back's section 3 reads (one count, B13 round 13
# O23): "<patches>|<newest patch time, or empty>|<branches>" (no tab: read would merge an empty field).
local_work_counts() {
	local git b=0 p n=0 at newest="" f s main origin patches published=" "
	patches="$(find "$SETUP_DIR/local-work" -maxdepth 1 -type f -name '*.patch' ! -name '.*' 2>/dev/null || true)"
	while IFS= read -r p; do
		[ -n "$p" ] || continue
		n=$((n + 1))
		at="$(date -u -r "$p" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
		[[ -z "$at" || "$at" < "$newest" ]] || newest="$at"
	done <<<"$patches"
	git="$(checkout_git_dirs "$HOME/src/agent-context-sync")"
	git="${git#*$'\n'}"
	if [ -n "$git" ]; then
		main="$(checkout_ref_sha "$git" refs/heads/main)"
		origin="$(checkout_ref_sha "$git" refs/remotes/origin/main)"
		# every commit origin/main pointed at is published: both columns of its reflog, read as a file
		published+="$(LC_ALL=C awk '{ for (i = 1; i <= 2; i++) if ($i ~ /^[0-9a-f]+$/ && (length($i) == 40 || length($i) == 64) && $i !~ /^0+$/) printf "%s ", $i }' "$git/logs/refs/remotes/origin/main" 2>/dev/null || true)"
		while IFS= read -r f; do
			s="$(checkout_ref_sha "$git" "refs/heads/$f")"
			if [ -n "$s" ] && { [ "$s" = "$main" ] || [ "$s" = "$origin" ] || [[ "$published" == *" $s "* ]]; }; then
				[[ $'\n'"$patches"$'\n' == *".$f.patch"$'\n'* ]] || continue
			fi
			b=$((b + 1))
		done < <({
			for f in "$git"/refs/heads/local-work-*; do [ -f "$f" ] && [ ! -L "$f" ] && printf '%s\n' "${f##*/}"; done
			LC_ALL=C sed -n 's|^[^ ]* refs/heads/\(local-work-.*\)$|\1|p' "$git/packed-refs" 2>/dev/null
		} | LC_ALL=C sort -u)
	fi
	printf '%s|%s|%s\n' "$n" "$newest" "$b"
}
fallback_body() {
	local rc="$1" why="$2" friction="$3" cs="$HOME/Library/CloudStorage" p v k n l o numbered
	say "# agentsync setup report"
	say ""
	say "## Summary"
	say ""
	say "- written by: scripts/install.sh shell fallback ($why), $(utc_now)"
	fallback_install_line "$rc"
	fallback_outcome_line
	# The mode, and whether this home holds each job's plist: the mode alone read as "installed" on a setup that
	# installed none (B13 round 4 Q6). Background runs has each job's loaded state. Labelled "background sync" in
	# the agentsync report's words: "not simulated (the real launchctl)" named a test mode a reader cannot know
	# of (B13 round 8 J5).
	if [ "$SIMULATE" -eq 1 ]; then
		say "- background sync: launchd: simulated (no LaunchAgent of this setup ran)"
	else
		v=""
		for l in "$POLL_LABEL" "$RECONCILE_LABEL"; do
			if [ -f "$HOME/Library/LaunchAgents/$l.plist" ]; then k="present"; else k="absent"; fi
			v="$v${v:+, }$l plist $k"
		done
		say "- background sync: $v in this home (Background runs has each job's state)"
	fi
	[ -z "$LAST_ERROR" ] || say "- last error: $LAST_ERROR"
	fallback_local_work_line
	fallback_install_source_line # last, as the agentsync report's install source line is in its Summary's last block
	say ""
	say "## Agent friction log"
	say ""
	if [ -n "$friction" ]; then
		# fenced as setup_report._fence does: unfenced, the lines ran into one paragraph and a placeholder such as
		# <folder-1> read as an HTML tag a renderer drops (B13 round 6 G6); a fence inside is broken up
		say "~~~text"
		# shellcheck disable=SC2016 # the backticks are text
		printf '%s\n' "$friction" | LC_ALL=C sed 's/~~~/~ ~ ~/g; s/```/` ` `/g'
		say "~~~"
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
	# A count, never a host (B13 round 7 H20)
	k="$(git_rewrite_bases | LC_ALL=C wc -l | LC_ALL=C tr -d ' ')"
	if [ "${k:-0}" -gt 0 ]; then
		say "- URL rewrite in git's config: set ($k)"
	else
		say "- URL rewrite in git's config: not set"
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
	[ "$l" -eq 0 ] || { [ "$l" -eq 1 ] && v="$v and 1 symlink" || v="$v and $l symlinks"; }
	[ "$o" -eq 0 ] || { [ "$o" -eq 1 ] && v="$v and 1 other entry" || v="$v and $o other entries"; }
	say "- ~/Library/CloudStorage providers: $v"
	say ""
	say "## Installer"
	say ""
	# The header counts what the fence shows, as the agentsync report does (B13 round 8 J4): a 15-line log read
	# "The last 60 lines". awk counts a last line with no newline, which tail prints and wc -l misses, and ends
	# it, so the closing fence keeps a line of its own; the filters after this body drop no line.
	if [ -f "$SETUP_LOG" ]; then
		n="$(LC_ALL=C awk 'END { print NR }' "$SETUP_LOG" 2>/dev/null || echo 0)"
		if [ "${n:-0}" -eq 0 ]; then
			say "$SETUP_LOG is empty."
		else
			# strip_run_pids numbers runs started in one second (B13 round 12 N21): the lead-in says so when a
			# numbered one is among the lines shown, as setup_report._numbered does
			numbered="$(LC_ALL=C awk -v from="$((n > 60 ? n - 59 : 1))" '
				match($0, /run=[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z-[0-9]+/) {
					r = substr($0, RSTART + 4, RLENGTH - 4)
					if (!(r in k)) k[r] = ++c[substr(r, 1, 16)]
					if (NR >= from && k[r] > 1) hit = 1
				}
				END { if (hit) printf ", numbered .2 on for runs started in one second" }' "$SETUP_LOG" 2>/dev/null || true)"
			if [ "$n" -le 60 ]; then
				if [ "$n" -eq 1 ]; then
					say "The one line of $SETUP_LOG (run ids without their process id$numbered):"
				else
					say "All $n lines of $SETUP_LOG (run ids without their process id$numbered):"
				fi
			else
				say "The last 60 of $n lines of $SETUP_LOG (run ids without their process id$numbered):"
			fi
			say ""
			say "~~~"
			tail -60 "$SETUP_LOG" | LC_ALL=C awk '{ print }'
			say "~~~"
		fi
	else
		say "No install log at $SETUP_LOG."
	fi
	say ""
	say "## Configuration"
	say ""
	if [ -f "$CONFIG" ]; then
		say "- config: $CONFIG (exists)"
		# Whether it loads is agentsync's to say (setup_report's "- loads: no: <error>"): a syntax-only check here
		# would read yes for a schema error. So the line says it was not checked and why, which the finish reads
		# as a case of its own, "unknown: ... was not checked" (since v21), not as its "loads: no" one: v22 keys
		# that one on "loads: no:", with the colon this line does not have, and reads this one first. The count
		# says it reads headers only (B13 round 12 N25, round 13 O44).
		say "- loads: not checked ($why)"
		say "- [[source]] table headers (not parsed): $(grep -c '^[[:space:]]*\[\[source\]\]' "$CONFIG" 2>/dev/null || true)"
		say "$(source_excludes_line "$CONFIG")"
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
					# the full path, as every command this script prints; the backticks are escaped, so bash prints them
					say "- $l: loaded, but the plist is absent (removed without \`launchctl bootout\`; \`~/.local/bin/agentsync install-agent\` repairs it): $n"
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
PRIVATE_ONLY="" # 1 once fallback_redaction says the shell report is for a private copy only
# The shell report's Redaction section: fixed text, written after redact_stream (as the issue link is), so no
# value can replace a word of it ("the login name" with a folder named "name").
# The placeholder kinds the redacted body (file $1) shows, as the Python report's "Placeholders used:" line
# reads its replacements: " ~ <kind> ... ", with each numbered kind as its stem and "[0-9]". A pair is not the
# test, since the $'...' stage, scrub_exclude_lists and this script's own run commands write placeholders with
# none (B13 round 10 L1). ~ always: the body writes ~/ paths of its own. Read before the legend is written, so
# its own words never count.
legend_kinds() { # BODY
	local used=" ~ " k
	for k in '<tmp>' '<user>' '<name>' '<org-[0-9]' '<link-[0-9]' '<mdmhost>' '<proxy-[0-9]' '<folder-[0-9]' '<source-[0-9]' '<path>' '<label>' '<remote>' '<value>'; do
		! LC_ALL=C grep -q -- "$k" "$1" 2>/dev/null || used+="$k "
	done
	printf '%s' "$used"
}
# One legend item, wrapped at 115 columns and continued on lines indented by two spaces (B13 round 9 K3, K15).
say_item() { # TEXT
	printf '%s\n' "$1" | LC_ALL=C awk '{
		n = split($0, w, " "); line = ""
		for (i = 1; i <= n; i++) {
			if (line != "" && length(line) + 1 + length(w[i]) > 115) { print line; line = "  " w[i] }
			else line = (line == "" ? w[i] : line " " w[i])
		}
		if (line != "") print line
	}'
}
fallback_redaction() { # [FILLED: the form fields the link fills, as the Send it line names them] [USED: legend_kinds] [BODY]
	local used="${2:-}" folder_item in_list="" whole="" skip_generic="" skip_agent="" proj="" v p fl
	local low org_item apps="" heads e email="" inword="" inword_ex="" ag="one made of coding-agent product words (Copilot, GitHub Copilot)"
	[ -n "$used" ] || used=" ~ <tmp> <user> <name> <org-[0-9] <link-[0-9] <mdmhost> <proxy-[0-9] <folder-[0-9] <source-[0-9] <path> <label> <remote> <value> "
	local pairs generic=" "
	pairs="$(redaction_pairs 2>/dev/null || true)"
	# The <folder-N> item's clauses, each only where the report shows what it explains (B13 round 11 M3, as
	# round 10 L1 for the items): step 1's list where one of its folders was registered; the whole-word rule
	# where a configured name every drive has (a pair of flag w) has its placeholder in the body; the list-only
	# rule where a name step 1's list alone gave, and folder_parts skipped, is in the body as a whole word; the
	# project-folder clause where a folder outside CloudStorage (folder_value's pairs, flag i) has its
	# placeholder in the body (B13 round 19 W5, W22).
	if [ -n "${3:-}" ]; then
		# the agent's own name is no folder: every whole-word copy of an Agent: header's value is left out of
		# the list-only test (B13 round 13 O10), as setup_report._without_agents, longest first, so a name
		# inside another ("Copilot" in "GitHub Copilot") leaves no part of it (B13 round 14 Q49); read through
		# a here-string, since a grep -q at a pipe's end can fail the pipe under pipefail
		local unagent
		unagent="$(LC_ALL=C awk '
			function strip(s, v,    out, k, b, e) {
				out = ""
				while ((k = index(s, v)) > 0) {
					b = k > 1 ? substr(s, k - 1, 1) : substr(out, length(out), 1)
					e = substr(s, k + length(v), 1)
					if (b !~ /[A-Za-z0-9_]/ && e !~ /[A-Za-z0-9_]/) out = out substr(s, 1, k - 1) " "
					else out = out substr(s, 1, k - 1 + length(v))
					s = substr(s, k + length(v))
				}
				return out s
			}
			NR == FNR {
				if (match($0, /^[[:space:]>*-]*([0-9]+[[:space:]]+)?[Aa][Gg][Ee][Nn][Tt]:[[:space:]]*/)) {
					v = substr($0, RLENGTH + 1); sub(/[[:space:]]+$/, "", v)
					if (v != "" && !(v in seen)) { seen[v] = 1; a[++n] = v }
				}
				next
			}
			!sorted {
				for (i = 2; i <= n; i++) { x = a[i]; for (j = i - 1; j > 0 && length(a[j]) < length(x); j--) a[j + 1] = a[j]; a[j + 1] = x }
				sorted = 1
			}
			{ line = $0; for (i = 1; i <= n; i++) line = strip(line, a[i]); print line }
		' "$3" "$3" 2>/dev/null || true)"
		while IFS=$'\t' read -r fl v; do
			case "$fl" in
			keep) in_list=1 ;;
			# which kind of skipped name, as folder_parts checks them: a name every drive has first (B13 round 12
			# N45), so the clause gives no Copilot example for a quoted Documents, nor Documents for a Copilot
			skip)
				if LC_ALL=C grep -qiwF -- "$v" <<<"$unagent"; then
					if generic_folder "$v"; then skip_generic=1; else skip_agent=1; fi
				fi
				;;
			esac
		done < <(listed_folder_names)
		while IFS=$'\t' read -r v p fl; do
			case "$p" in
			"<folder-"*)
				if [ "$fl" = i ]; then
					! LC_ALL=C grep -qF -- "$p" "$3" 2>/dev/null || proj=1
					continue
				fi
				if [ "$fl" != w ] || ! generic_folder "$v"; then continue; fi
				! LC_ALL=C grep -qF -- "$p" "$3" 2>/dev/null || whole=1
				;;
			# an account's email address is an <org-N> pair too: the <org-N> item names it where one is shown
			"<org-"*) case "$v" in *@*) ! LC_ALL=C grep -qF -- "$p" "$3" 2>/dev/null || email=1 ;; esac ;;
			esac
		done <<<"$pairs"
		# The <org-N> item's example is the app the body shows before one, and it says where one stands inside a
		# longer word (FUZZY=o cuts it out at a CamelCase, digit or capital boundary: <org-1>Drive), each only
		# where the body shows it (B13 round 12 N16, M3's rule). Not "-<org-1>", "<org-1>.sharepoint.com" or
		# "<org-1>-my". Only an app's name counts, as setup_report's app heads (_CONTEXT_HEADS and this Mac's
		# CloudStorage entries), after no letter or digit: a quoted "sales-Contoso" is no app (B13 round 13 O45).
		heads="OneDrive|GoogleDrive|SharedLibraries|Dropbox|Box|Egnyte"
		for e in "$HOME/Library/CloudStorage"/*-*; do
			e="${e##*/}"
			e="${e%%-*}"
			case "$e" in "" | *[!A-Za-z]*) ;; *) heads="$heads|$e" ;; esac
		done
		apps="$(LC_ALL=C grep -oE "(^|[^A-Za-z0-9])($heads)(-SharedLibraries)?-<org-[0-9]+>" "$3" 2>/dev/null |
			LC_ALL=C sed -E 's/^[^A-Za-z]//; s/-<org-[0-9]+>$/-<org-N>/' | LC_ALL=C sort -u | LC_ALL=C awk 'NR <= 2' |
			LC_ALL=C paste -sd ',' - | LC_ALL=C sed 's/,/, /g')"
		# the in-word example is a whole word the body shows, by the detector's pattern, so the two agree; a
		# word over 32 bytes (a hash, a run-together path piece) is no example (B13 round 14 Q5)
		! LC_ALL=C grep -qE '[A-Za-z0-9]<org-[0-9]+>|<org-[0-9]+>[A-Za-z0-9]' "$3" 2>/dev/null || inword=1
		[ -z "$inword" ] || inword_ex="$(LC_ALL=C grep -oE '[A-Za-z0-9]+<org-[0-9]+>[A-Za-z0-9]*|<org-[0-9]+>[A-Za-z0-9]+' "$3" 2>/dev/null |
			LC_ALL=C awk 'length($0) <= 32' | LC_ALL=C sort -u | LC_ALL=C awk 'NR == 1')"
	else # no body to read: every clause, as before
		in_list=1 whole=1 skip_generic=1 skip_agent=1 email=1 inword=1 proj=1 apps="OneDrive-<org-N>, Dropbox-<org-N>"
		inword_ex="<org-1>Drive"
	fi
	org_item="- <org-N>: the organization or team after the app's name in a CloudStorage folder (${apps:-OneDrive-<org-N>})"
	[ -z "$email" ] || org_item+=", and an account's email address"
	[ -z "$inword" ] || org_item+=", also where it stands inside a longer word${inword_ex:+ (such as $inword_ex)}"
	folder_item="- <folder-N>: the names of the folders under CloudStorage that --source-local, sources.toml"
	if [ -n "$in_list" ]; then
		folder_item+=", install.log or step 1's folder list in install.out name"
	else
		folder_item+=" or install.log name"
	fi
	# what project_values registers, in words a reader can apply (B13 round 20 X6, X23): the path from its first
	# folder that is not a common container, that folder alone, the whole path outside home; never a later part
	[ -z "$proj" ] || folder_item+="; also a folder outside CloudStorage that sources.toml names, or that doctor's fix adds a removed source back from: inside the home folder, its path from the first folder that is not a common container (such as Projects, Documents or a dot folder) down, and that folder's name on its own (in ~/Projects/Harborview/notes, both \"Harborview/notes\" and \"Harborview\"); outside the home folder, its whole path; each as a whole word, in any letter case; a later folder's name on its own, such as notes, is not replaced"
	[ -z "$whole" ] || folder_item+="; a name every OneDrive or Google Drive has, such as Documents, only where it stands as a whole word"
	if [ -n "$whole" ]; then
		if [ -n "$skip_generic" ] && [ -n "$skip_agent" ]; then
			folder_item+=", and neither such a name nor $ag where only step 1's list names it"
		elif [ -n "$skip_generic" ]; then
			folder_item+=", and not such a name where only step 1's list names it"
		elif [ -n "$skip_agent" ]; then
			folder_item+=", and not $ag where only step 1's list names it"
		fi
	elif [ -n "$skip_generic" ] && [ -n "$skip_agent" ]; then
		folder_item+="; not a name every OneDrive or Google Drive has, such as Documents, nor $ag, where only step 1's list names it"
	elif [ -n "$skip_generic" ]; then
		folder_item+="; not a name every OneDrive or Google Drive has, such as Documents, where only step 1's list names it"
	elif [ -n "$skip_agent" ]; then
		folder_item+="; not $ag, where only step 1's list names it"
	fi
	# a configured product-word name kept in the agent's name (agent_protect), as the agentsync report's legend
	# says it (B13 round 19 W17; round 20 X40, X81)
	[ -z "${AGENT_KEPT:-}" ] || folder_item+="; a configured one's coding-agent product word stays as written in the agent's name"
	# numbers are given to names the body need not show (folder_value numbers a project path and its first
	# part), as setup_report._LEGEND["folder"] says (B13 round 20 X22)
	folder_item+="; a number can be missing (one given to a name this report does not show)"
	say "## Redaction"
	say ""
	# One placeholder kind a list item, continued on lines indented by two spaces; no blank line inside, so the
	# legend stays one block that ends on the "Read the report" line (B13 round 9 K3, K15).
	say "Placeholders used, one kind per item:"
	say "- ~: the home path"
	case "$used" in *" <tmp> "*) say "- <tmp>: this account's temporary folder (\$TMPDIR)" ;; esac
	case "$used" in *" <user> "*) say "- <user>: the login name" ;; esac
	case "$used" in *" <name> "*) say "- <name>: the full name and its joined forms" ;; esac
	case "$used" in *" <org-[0-9] "*) say_item "$org_item" ;; esac
	case "$used" in *" <link-[0-9] "*) say "- <link-N>: a symlink's own name in ~/Library/CloudStorage (not an organization)" ;; esac
	case "$used" in *" <mdmhost> "*) say "- <mdmhost>: the MDM server's host" ;; esac
	# numbered as in agentsync's own report (B13 round 8 J78), so a number the other report gave can be missing
	# here: the shell names an environment proxy only by its variable (B13 round 10 L3)
	case "$used" in *" <proxy-[0-9] "*)
		say "- <proxy-N>: proxy hosts (a host that is just a common word, such as proxy or wpad, is replaced only where a"
		say "  URL or host:port names it), a git URL rewrite's host and its company-named path (insteadOf; github.com"
		say "  kept) and the system PAC URL after its scheme; numbered as in agentsync's own report, so a number can be"
		say "  missing here (an environment proxy this report names only by its variable keeps its number); also a host"
		say "  a friction line's resolver error names, or a git remote's host (neither need be a proxy)"
		;;
	esac
	case "$used" in *" <folder-[0-9] "*) say_item "$folder_item" ;; esac
	case "$used" in *" <source-[0-9] "*)
		say "- <source-N>: a configured source id, or its folder's <folder-N> where it is that name in slug form; one of"
		say "  agentsync's own words, such as docs or inbox, only for a cloud folder; also the id of a source still mirrored"
		say "  whose [[source]] block is gone, as doctor's config.vanished_sources line and the sync's refusal quote it; a"
		say "  number can be missing"
		;;
	esac
	case "$used" in *" <path> "*)
		# only a relative argument is <path>: a ~/ or absolute one is matched by its configured folders, as the body
		# shows it (B13 round 21 Y8)
		say "- <path>: a relative --source-local argument in install.log (which records no working directory) whose first"
		say "  part is not a CloudStorage entry, and every word bash wrote in its dollar-quote form (\$'...'), which install.log"
		say "  uses for a name with a tab or another control character, a curly apostrophe, an en dash or an accent (under the"
		say "  C locale, any character outside ASCII)"
		;;
	esac
	# as setup_report._LEGEND words them (B13 round 16 T3, round 17 U93)
	case "$used" in *" <label> "*) say "- <label>: a label id or name a policy error quotes" ;; esac
	case "$used" in *" <remote> "*) say "- <remote>: a remote URL, or a URL prefix, that a [governance] error or check quotes" ;; esac
	case "$used" in *" <value> "*) say "- <value>: any other value a [governance] error quotes" ;; esac
	say "Read the report before sending it: anything else confidential is yours to remove."
	# A one-word folder or organisation pair that is a word the report prints: its placeholder in the report's own
	# text reads back as the word, so the report is for a private copy only (setup_report's F92, B13 round 5; an
	# organisation since G46 registers bare whatever is not context_org_word). The send advice is one "Send it: "
	# line in either case, as the agentsync report's, which a stopped copy's report drops whole (B13 round 6
	# G50; round 7 H4: the report that may be sent had none, and ended on a bare link).
	# A name every OneDrive or Google Drive has (Documents) reads back as nothing private (B13 round 6 G48), and
	# so does whatever else shares its placeholder: its source id, which is that name in slug form (G8 gave
	# the id "downloads" the folder's <folder-N>, and the id is a word this report prints; round 7 H39, H57).
	# The folder's own name decides for the placeholder, in whatever order the pairs come.
	while IFS=$'\t' read -r v p _; do
		case "$p" in "<folder-"*) ! generic_folder "$v" || generic+="$p " ;; esac
	done <<<"$pairs"
	while IFS=$'\t' read -r v p _; do
		case "$p" in "<folder-"* | "<org-"*) ;; *) continue ;; esac
		case "$v" in "" | *[!A-Za-z]*) continue ;; esac
		case "$generic" in *" $p "*) continue ;; esac
		low="$(printf '%s' "$v" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
		report_word "$low" || output_word "$low" || continue
		say "Private only: one of your folder or organization names is also a word this report prints, so where its"
		say "placeholder stands in the report's own text it can be read back."
		say "Send it: privately (docs/deploy/setup-feedback.md in the agentsync repository), never as a public issue. Nothing is sent automatically."
		PRIVATE_ONLY=1 # the report then ends with no issue link (the caller's subshell reads it)
		break
	done <<<"$pairs"
	# One line that starts at column 0, in the agentsync report's words: for a link that fills no field, or
	# for the fields it fills (H24).
	[ -n "$PRIVATE_ONLY" ] || {
		v='a blank "Setup report" issue form'
		[ -z "${1:-}" ] || v="a new \"Setup report\" issue with the $1 field$([ "$1" = "Run type" ] || printf s) filled in (nothing else)"
		say ""
		say "Send it: the link on the last line opens $v; paste this report into its Setup report field, or send it privately (docs/deploy/setup-feedback.md in the agentsync repository). Nothing is sent automatically."
	}
}
# VALUE percent-encoded as urllib.parse.quote does it (every byte but A-Z a-z 0-9 _ . - ~ /), byte by byte.
url_quote() {
	printf '%s' "$1" | LC_ALL=C od -An -v -tx1 | LC_ALL=C tr -s ' ' '\n' | LC_ALL=C awk '
		BEGIN { for (i = 0; i < 256; i++) ch[sprintf("%02x", i)] = sprintf("%c", i) }
		$0 == "" { next }
		{ c = ch[$0]; printf "%s", (c ~ /^[A-Za-z0-9_.~\/-]$/ ? c : "%" toupper($0)) }'
}
# The issue link the shell report ends with, as setup_report.build_issue_url writes it (H24). When the Summary
# (the redacted report in file $1, above its friction log) holds the computed outcome line, with the title and the
# fields that line gives; otherwise with the run type, and the Prompt version and Agent the friction log's latest
# attempt states (friction_latest_said, read from the redacted log, as _issue_link reads Attempt.said), and no
# outcome, which only the agentsync report computes (B13 round 10 L2: the link was bare). The agent is read from
# redacted text, since the link is written after redaction (link_agent). The run type is computed again, not
# read from the redacted line, where a folder named after it would read as its placeholder (B13 round 8 J22).
# Sets LINK and LINK_FIELDS (the fields it fills, for the Send it line).
# link_agent AGENT: what _issue_link keeps of it, at most 100 characters: letters and digits outside ASCII too,
# by the UTF-8 classes, so an accented name is not cut (B13 round 8 J77); a symbol Python counts as a digit,
# such as a superscript two, is the gap left. A byte that is not UTF-8 is dropped first, and where the UTF-8
# filter fails the ASCII one runs instead.
link_agent() {
	local agent="$1" v
	# as " ".join(agent.split()): each blank Python splits on is one space (tab, the C0 separators, and the
	# Unicode spaces such as U+00A0 and U+2009, byte by byte), then a run of them is one
	agent="$(printf '%s' "$agent" | LC_ALL=C awk 'BEGIN {
			n = split("\302\205 \302\240 \341\232\200 \342\200\200 \342\200\201 \342\200\202 \342\200\203 \342\200\204 \342\200\205 \342\200\206 \342\200\207 \342\200\210 \342\200\211 \342\200\212 \342\200\250 \342\200\251 \342\200\257 \342\201\237 \343\200\200", ws, " ")
		}
		{ for (i = 1; i <= n; i++) gsub(ws[i], " "); gsub(/[\011\013\014\015\034\035\036\037]/, " "); print }' | LC_ALL=C tr -s ' ')"
	# iconv -c exits 1 at a last character cut short, after writing the rest: its status is not the filter's
	v="$(printf '%s' "$agent" | { LC_ALL=C /usr/bin/iconv -c -f UTF-8 -t UTF-8 2>/dev/null || true; } |
		LC_ALL=en_US.UTF-8 sed 's/[^[:alnum:]_ .:/()+,@<>-]//g' 2>/dev/null | LC_ALL=en_US.UTF-8 cut -c 1-100)" ||
		v="$(printf '%s' "$agent" | LC_ALL=C tr -cd 'A-Za-z0-9_ .:/()+,@<>-' | LC_ALL=C cut -c 1-100)"
	printf '%s' "$v" | LC_ALL=C sed 's/^ *//; s/ *$//'
}
# The version _prompt_version reads from a Prompt value (re.search(r"\bv?(\d{1,3})\b")): the first run of one to
# three digits, after an optional "v", with no letter, digit or "_" right before it or right after it (a byte
# outside ASCII counts as a letter, so such a run is not read: no field rather than a wrong one).
prompt_number() {
	printf '%s' "$1" | LC_ALL=C awk '{
		w = "[A-Za-z0-9_\200-\377]"
		for (i = 1; i <= length($0); i++) {
			if (i > 1 && substr($0, i - 1, 1) ~ w) continue
			j = i; if (substr($0, j, 1) == "v") j++
			for (k = j; k <= length($0) && substr($0, k, 1) ~ /[0-9]/; k++) ;
			if (k == j || k - j > 3 || (k <= length($0) && substr($0, k, 1) ~ w)) continue
			print substr($0, j, k - j) + 0; exit
		}
	}'
}
LINK=""
LINK_FIELDS=""
fallback_issue_link() { # REDACTED-BODY-FILE
	local line n agent run said fail="Failed at step 1 (preflight)"
	LINK="$ISSUE_URL"
	LINK_FIELDS=""
	run="$(run_type_label)"
	line="$(LC_ALL=C awk -v p="$OUTCOME_LINE_HEAD" '$0 == "## Agent friction log" { exit } index($0, p) == 1 { print; exit }' "$1" 2>/dev/null || true)"
	n="${line#"$OUTCOME_LINE_HEAD"}"
	n="${n%% *}"
	case "$n" in
	"" | *[!0-9]*)
		# inside the section's fence only: a "## " line of the log itself is no heading of the report
		said="$(LC_ALL=C awk '$0 == "## Agent friction log" { f = 1; next } f && $0 == "~~~text" { fence = 1; next }
			fence && index($0, "~~~") == 1 { exit } fence' "$1" 2>/dev/null | friction_latest_said 2>/dev/null || true)"
		n="$(prompt_number "${said%%$'\t'*}")"
		agent="$(link_agent "${said#*$'\t'}")"
		[ "$said" != "${said#*$'\t'}" ] || agent=""
		LINK="$ISSUE_URL&title=$(url_quote "Setup report: $run${n:+ · v$n}")&run_type=$(url_quote "$run")"
		LINK_FIELDS="Run type"
		[ -z "$n" ] || {
			LINK="$LINK&prompt_version=v$n"
			LINK_FIELDS="$LINK_FIELDS, Prompt version"
		}
		[ -z "$agent" ] || {
			LINK="$LINK&agent=$(url_quote "$agent")"
			LINK_FIELDS="$LINK_FIELDS, Agent tool and model"
		}
		# the agentsync report's list: "A and B" for two, "A, B, and C" for three
		case "$LINK_FIELDS" in
		*", "*", "*) LINK_FIELDS="${LINK_FIELDS%, *}, and ${LINK_FIELDS##*, }" ;;
		*", "*) LINK_FIELDS="${LINK_FIELDS%, *} and ${LINK_FIELDS##*, }" ;;
		esac
		return 0
		;;
	esac
	agent="${line##* · agent: }"
	[ "$agent" != "$line" ] || agent=""
	agent="$(link_agent "$agent")"
	LINK="$ISSUE_URL&title=$(url_quote "Setup report: $fail · $run · v$n")&outcome=$(url_quote "$fail")"
	LINK="$LINK&run_type=$(url_quote "$run")&prompt_version=v$n"
	LINK_FIELDS="Outcome, Run type, and Prompt version"
	[ -z "$agent" ] || {
		LINK="$LINK&agent=$(url_quote "$agent")"
		LINK_FIELDS="Outcome, Run type, Prompt version, and Agent tool and model"
	}
}
# The friction log for the shell report: friction.md, else what an earlier report holds under its heading
# (byte-wise: that report can hold a byte that is not UTF-8, and macOS awk's regex match exits there), less
# the fence either writer puts around it, so the new fence neither nests nor grows a pair a run (G6). Every
# line that starts with "~~~" is a fence: both writers break a "~~~" in the body up, and an opener written
# before redact_stream passed "~~~text" through read "~~~<folder-N>" for a folder named "text" and
# "~~~<source-N>" for a source id "text", which was kept and grew by a line a run (B13 round 7 H41).
friction_text() {
	local f
	f="$(friction_file)"
	if [ -f "$f" ]; then
		head -c 262144 "$f"
	elif [ -f "$REPORT_PATH" ]; then
		LC_ALL=C awk '/^## / { found = ($0 == "## Agent friction log"); next } found { print }' "$REPORT_PATH" |
			LC_ALL=C grep -v '^~~~' |
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
# alike. Anchored to the run id's shape, so no other number is touched; LC_ALL=C, as every stage here. Runs
# started in one second are numbered as setup_report.read_install_runs numbers them, over the whole of
# install.log in its order, the first keeping the bare stamp: <stamp>.2, .3 (B13 round 12 N21: step 1 lists
# and the install starts in the same second, and both read as one id).
strip_run_pids() {
	LC_ALL=C awk -v file="$SETUP_LOG" '
		BEGIN {
			id = "run=[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z-[0-9]+"
			while ((getline ln < file) > 0)
				if (match(ln, id)) {
					r = substr(ln, RSTART + 4, RLENGTH - 4)
					if (!(r in label)) { st = substr(r, 1, 16); n = ++seen[st]; label[r] = n == 1 ? st : st "." n }
				}
			close(file)
		}
		{
			out = ""; rest = $0
			while (match(rest, id)) {
				r = substr(rest, RSTART + 4, RLENGTH - 4)
				if (substr(rest, RSTART + RLENGTH, 1) ~ /[0-9A-Za-z_-]/) {
					out = out substr(rest, 1, RSTART + RLENGTH - 1)
				} else out = out substr(rest, 1, RSTART + 3) ((r in label) ? label[r] : substr(r, 1, 16))
				rest = substr(rest, RSTART + RLENGTH)
			}
			print out rest
		}'
}
write_fallback_report() { # RC WHY
	local dir tmp friction c used
	# --report-only reads no SOURCE: its Summary names this installer's checkout, as --version does. Only here,
	# so a report agentsync writes runs no git.
	if [ "$REPORT_ONLY" -eq 1 ] && [ -e "$repo/.git" ]; then
		read_commit "$repo"
		if [ "$COMMIT" = "-" ]; then
			c="$(git_head_file "$repo")"
			if [ -z "$c" ]; then
				:
			elif git_refuses_owner "$repo"; then
				COMMIT="$c (local changes not checked: git refuses this folder, which belongs to another user account)"
			else
				COMMIT="$c (local changes not checked)"
			fi
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
		AGENT_KEPT_FILE="$tmp.agent"
		export AGENT_KEPT_FILE
		rm -f "$AGENT_KEPT_FILE"
		fallback_body "$1" "$2" "$friction" | strip_run_pids | agent_protect | redact_stream | agent_restore |
			scrub_exclude_lists >"$tmp"
		AGENT_KEPT=""
		[ ! -f "$AGENT_KEPT_FILE" ] || AGENT_KEPT=1
		rm -f "$AGENT_KEPT_FILE"
		# the issue form, after redaction (a login may match the URL's owner); none for a private copy (G65).
		# What it fills is read from the redacted Summary just written.
		fallback_issue_link "$tmp"
		used="$(legend_kinds "$tmp")"
		# shellcheck disable=SC2094 # the body is read before the first legend line is appended
		{
			fallback_redaction "$LINK_FIELDS" "$used" "$tmp"
			[ -n "$PRIVATE_ONLY" ] || printf '\n%s\n' "$LINK"
		} >>"$tmp"
	) 2>/dev/null || {
		rm -f "$tmp"
		return 1
	}
	mv -f "$tmp" "$REPORT_PATH" || { # no temp file stays beside the report (B13 round 11 M84)
		rm -f "$tmp"
		return 1
	}
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
		NH { NH = 0; if (e != "" && !stopped) last = e; e = ""; stopped = 0; next }
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
# read. Nothing is deleted or moved. A patch this account could not read when a report was written is listed in
# local-work/.not-sent, which no *.patch glob reads, by its identity (device, inode, time and size: chmod changes
# none of them) and the time of that report; one listed at or after the earlier attempt's end is sent whatever
# its time, since that attempt's report did not carry it, in each report of the attempt (B13 round 14 Q29). The
# list is rewritten once the file is in place: a patch gone or changed leaves it. Not ctime, which a Finder or
# Spotlight attribute write and a moved folder change (it would send the 2026-10-07 patch again). A patch is
# listed only when the report would have sent it, so one an earlier attempt's report carried is not (B13 round
# 15 S22); a listed patch sent once readable gets a third field, when its attempt began, and goes from the list
# once a later attempt reports, since the attempt's report carried it (S34). local-work/.sent keeps each patch a
# report embedded, by identity and that first report's time: an attempt step 1 stopped writes no header and so no
# end line, so the time rule cannot see its report, and a later attempt's report lists such a patch by its hash;
# a report of the same attempt sends it again (S21).
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
# setup_report._excludes_line for the shell report (B13 round 5 F40): each local or inbox [[source]]'s exclude
# entries beyond SHIPPED_EXCLUDES, counted, with the source by its place in sources.toml. Never a glob's text.
source_excludes_line() { # CONFIG
	SHIPPED="$SHIPPED_EXCLUDES" LC_ALL=C awk 'BEGIN { shipped = ENVIRON["SHIPPED"] } # not -v, which reads the \r of Icon\r as a CR

		function flush() {
			if (!open) return
			if (kind == "local" || kind == "inbox") {
				if (extra) { out = out (out == "" ? "" : ", ") "(source " n ") " extra (folders ? " (" folders " folder glob" (folders == 1 ? "" : "s") ")" : "") }
				else others++
			}
			open = 0
		}
		function take(text,    g) {
			while (match(text, /"([^"\\]|\\.)*"|\047[^\047]*\047/)) {
				g = substr(text, RSTART + 1, RLENGTH - 2); text = substr(text, RSTART + RLENGTH)
				if (index(shipped, "\"" g "\"")) continue
				extra++
				if (length(g) > 2 && substr(g, 1, 1) == "/" && substr(g, length(g)) == "/") folders++
			}
		}
		function closes(text) { gsub(/"([^"\\]|\\.)*"|\047[^\047]*\047/, "", text); return text ~ /\]/ } # outside quotes
		/^[[:space:]]*\[\[source\]\]/ { flush(); open = 1; n++; kind = "local"; extra = 0; folders = 0; inlist = 0; next }
		/^[[:space:]]*\[/ { flush(); inlist = 0; next }
		open && /^[[:space:]]*kind[[:space:]]*=/ { kind = $0; sub(/^[^=]*=[[:space:]]*["\047]?/, "", kind); sub(/["\047].*$/, "", kind) }
		open && /^[[:space:]]*exclude[[:space:]]*=/ { line = $0; sub(/^[^=]*=/, "", line); take(line); inlist = !closes(line); next }
		open && inlist { take($0); if (closes($0)) inlist = 0 }
		END {
			flush()
			if (out == "") { print "- source excludes beyond agentsync\047s own: none"; exit }
			print "- source excludes beyond agentsync\047s own: " out (others ? "; 0 on the other " others " source" (others == 1 ? "" : "s") : "") \
				" (globs not shown; an excluded folder is not mirrored if it later gains files)"
		}' "$1" 2>/dev/null || true
}
# What section 3's send rule reads, set in the caller (its locals): prev, the earlier attempt's end; begin, when
# this attempt began ("-" when that cannot be told: .not-sent marks the attempt whose report sent a patch by it,
# B13 round 15 S34); and the .not-sent and .sent ledgers. A link at either, or at local-work, is never read.
patch_ledgers() {
	local kept="$SETUP_DIR/local-work"
	prev="$(utc_stamp "$(friction_earlier_end)")"
	begin="$(utc_stamp "$(friction_attempt_begin)")"
	begin="${begin:--}"
	wasnot="" wassent=""
	[ ! -f "$kept/.not-sent" ] || [ -L "$kept/.not-sent" ] || [ -L "$kept" ] || wasnot="$(cat "$kept/.not-sent" 2>/dev/null || true)"
	[ ! -f "$kept/.sent" ] || [ -L "$kept/.sent" ] || [ -L "$kept" ] || wassent="$(cat "$kept/.sent" 2>/dev/null || true)"
}
# Section 3's send rule for one kept patch, from patch_ledgers' values, in the caller's locals: id (its
# identity), nsat and sentat (its .not-sent record), force (sent whatever its time), at (its time, when at or
# before the earlier end), held (.sent's report that first embedded it) and carried (held, when that report was
# an earlier attempt's). A patch is not sent again when at or carried is set. One function, so section 1 counts an unread patch by the rule section 3 sends it by (B13
# round 16 T24).
patch_rule() {
	local p="$1" rec=""
	id="$(/usr/bin/stat -f %d:%i:%m:%z "$p" 2>/dev/null || true)"
	[ -z "$id" ] || rec="$(ID="$id" LC_ALL=C awk '$1 == ENVIRON["ID"] { s = $2 " " $3 } END { print s }' <<<"$wasnot" 2>/dev/null || true)"
	nsat="" sentat=""
	read -r nsat sentat _ <<<"$rec" || true
	# a record a report already sent the patch by, in an attempt that has ended since, goes: that report
	# carried it, so the time rule below decides (B13 round 15 S34); a two-field record is not sent yet
	if [ -n "$sentat" ] && [ "$sentat" != "$begin" ]; then
		nsat="" sentat=""
	fi
	# one the earlier attempt could not read at its end is sent: its report did not carry it (B13 round 14
	# Q29), in each report of this attempt
	force=0
	[ -z "$nsat" ] || { [ -n "$prev" ] && [ "${nsat//[!0-9]/}" -lt "${prev//[!0-9]/}" ]; } || force=1
	at=""
	[ -z "$prev" ] || [ "$force" -eq 1 ] || at="$(utc_stamp "$(TZ=UTC0 /usr/bin/stat -f %Sm -t %Y-%m-%dT%H:%M:%SZ "$p" 2>/dev/null || true)")"
	[ -z "$at" ] || [ "${at//[!0-9]/}" -le "${prev//[!0-9]/}" ] || at=""
	# the report that first embedded it (.sent), when that was before this attempt began: an attempt with
	# no header writes no end line, so the time rule above cannot see its report (B13 round 15 S21)
	held=""
	[ -z "$id" ] || held="$(ID="$id" LC_ALL=C awk '$1 == ENVIRON["ID"] { s = $2 } END { print s }' <<<"$wassent" 2>/dev/null || true)"
	held="$(utc_stamp "$held")"
	carried="$held"
	# .not-sent's rule, no attempt to tell, or this attempt's own report: sent again, in each report of the
	# attempt. The time rule no longer clears it: .sent names a report that embedded the patch, where the earlier
	# end's report may be one that could not read it, or one before a later attempt sent it (B13 round 16 T16,
	# T26)
	if [ -n "$carried" ] && { [ "$force" -eq 1 ] || [ "$begin" = "-" ] || [ "${carried//[!0-9]/}" -ge "${begin//[!0-9]/}" ]; }; then
		carried=""
	fi
}
# The kept patches this account cannot read that section 3 would send, by its rule (patch_rule): section 1's
# count of them, so a patch an earlier attempt's report carried is not called missing (B13 round 16 T24)
unread_patch_count() {
	local p prev begin wasnot wassent id nsat sentat force at held carried u=0
	patch_ledgers
	for p in "$SETUP_DIR"/local-work/*.patch; do
		[ -e "$p" ] && [ ! -L "$p" ] && [ -f "$p" ] && [ ! -L "$SETUP_DIR/local-work" ] || continue
		[ ! -r "$p" ] || continue
		patch_rule "$p"
		[ -n "$at$carried" ] || u=$((u + 1))
	done
	printf '%s\n' "$u"
}
write_bring_back() {
	local out="${REPORT_PATH%/*}/bring-back.md" tmp local_work p fence shown="$SETUP_DIR"
	local prev at sha sent=0 skipped="" fix=0 nskip=0 unread=0 catfail=0 readable=() branches=0
	local id nsat now notsent="" wasnot="" kept="$SETUP_DIR/local-work" up uthem sentat begin force
	local wassent="" sentlist="" carried held nohash=0 lbr lhold outshown
	BACK_WHY="" BACK_UNREAD=0 BACK_BRANCHES=0
	case "$shown" in
	"$HOME"/*) shown='~'"${shown#"$HOME"}" ;;
	esac
	outshown="$out"
	case "$outshown" in
	"$HOME"/*) outshown='~'"${outshown#"$HOME"}" ;;
	esac
	# mv onto a folder (or a link to one) moves the file into it and exits 0, so the run would name a folder as
	# the file to send and leave the unredacted file hidden in it (B13 round 13 O7, O26): build nothing for it
	# Each return 1 sets BACK_WHY, which the caller's warning names, since its NEXT says to fix the cause (B13
	# round 14 Q19).
	[ ! -d "$out" ] || {
		BACK_WHY="a folder, or a link to a folder, is at that path; move it away"
		return 1
	}
	patch_ledgers
	BACK_WHY="could not make a temporary file in ${REPORT_PATH%/*}"
	tmp="$(mktemp "${REPORT_PATH%/*}/.bring-back.XXXXXX" 2>/dev/null)" || return 1
	local_work="$(mktemp "${REPORT_PATH%/*}/.bring-back-local-work.XXXXXX" 2>/dev/null)" || {
		rm -f "$tmp"
		return 1
	}
	BACK_WHY="could not write a temporary file in ${REPORT_PATH%/*}"
	now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	# Section 3 first, so the header names only what the file holds: the review sentence only the sections that
	# hold something, and the author lines only when a patch is embedded (a patch not repeated has none; B13
	# round 10 L4)
	# the fence fits the patches that can be read: one that cannot stops the awk, which then gave the
	# 40-tilde default whatever the others hold (B13 round 13 O25)
	for p in "$SETUP_DIR"/local-work/*.patch; do
		[ ! -r "$p" ] || readable+=("$p")
	done
	fence="$(fence_for ${readable[@]+"${readable[@]}"} /dev/null)"
	{
		for p in "$SETUP_DIR"/local-work/*.patch; do
			# a symlink, or a symlinked local-work, is no patch the report counts, so none is sent (B13 round 10 L8)
			[ -e "$p" ] && [ ! -L "$p" ] && [ -f "$p" ] && [ ! -L "$SETUP_DIR/local-work" ] || continue
			patch_rule "$p"
			[ -z "$held" ] || sentlist+="$id $held"$'\n'
			# a patch this account cannot read is counted, not sent: its cat failed unseen, under a fence
			# already opened, and the header said section 3 held it (B13 round 13 O25)
			[ -r "$p" ] || {
				# one an earlier attempt's report carried is no unread patch: that report sent it, and a rerun once it
				# is readable sends nothing (B13 round 16 T24); its line has no hash, as it cannot be read
				if [ -n "$at$carried" ]; then
					if [ -n "$carried" ]; then
						skipped+="not repeated: a patch file an earlier attempt's report held ($carried); this account cannot read it now, so no line count or hash; still in $shown/local-work"$'\n'
					else
						skipped+="not repeated: a patch file last written $at, at or before an earlier attempt's report ($prev); this account cannot read it now, so no line count or hash; still in $shown/local-work"$'\n'
					fi
					nskip=$((nskip + 1)) nohash=$((nohash + 1))
					continue
				fi
				unread=$((unread + 1))
				# listed only when this report would have sent it: a patch an earlier attempt's report carried is
				# not, or the next readable report would send it again (B13 round 15 S22)
				[ -z "$id" ] || notsent+="$id $now"$'\n'
				continue
			}
			if [ "$force" -eq 1 ]; then # sent now: the record says by which attempt (B13 round 15 S34)
				notsent+="$id $nsat $begin"$'\n'
			elif [ -n "$nsat" ]; then
				notsent+="$id $nsat${sentat:+ $sentat}"$'\n'
			fi
			if [ -n "$at$carried" ]; then
				sha="$(/usr/bin/shasum -a 256 "$p" 2>/dev/null | cut -c 1-12 || true)"
				case "$sha" in # anything but 12 hex digits is no hash to check the patch by: it is sent below
				[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f])
					# .sent first: it names a report that embedded the patch, where the time rule's report may be one
					# that could not read it (B13 round 16 T16)
					if [ -n "$carried" ]; then
						skipped="${skipped}not repeated: a patch file an earlier attempt's report held ($carried): $(wc -l <"$p" | tr -d ' ') lines, sha256 $sha; still in $shown/local-work"$'\n'
					else
						skipped="${skipped}not repeated: a patch file last written $at, at or before an earlier attempt's report ($prev): $(wc -l <"$p" | tr -d ' ') lines, sha256 $sha; still in $shown/local-work"$'\n'
					fi
					nskip=$((nskip + 1))
					continue
					;;
				esac
			fi
			# embedded: the first report that does is the one .sent keeps (B13 round 15 S21)
			[ -z "$id" ] || case "$sentlist" in "$id "* | *$'\n'"$id "*) ;; *) sentlist+="$id $now"$'\n' ;; esac
			[ "$sent" -gt 0 ] || printf '%sdiff\n' "$fence"
			sent=$((sent + 1))
			cat "$p" || catfail=1
		done
		[ "$sent" -eq 0 ] || printf '%s\n' "$fence"
		printf '%s' "$skipped"
		# a known count needs no "(s)" (B13 round 15 S14; agentsync.text.plural)
		up="$unread patch files" uthem=them
		[ "$unread" != 1 ] || { up="1 patch file" uthem=it; }
		# no file name: a patch's name carries its commit's subject
		# the command by name: this line is in a file the person reads, where "this command" is none (B13 round 15
		# S23)
		[ "$unread" -eq 0 ] || printf 'not sent: %s in %s/local-work this account cannot read; make %s readable (chmod u+r) and run %s again\n' "$up" "$shown" "$uthem" "$LOCAL_WORK_AGAIN"
		if [ "$sent" -eq 0 ] && [ -z "$skipped" ] && [ "$unread" -eq 0 ]; then
			# local-work branches with commits of their own and no patch: those commits are not here (B13 round 13
			# O23: a bare "none" read as no kept work). Its remedy names no branch, with the keep's own --suffix,
			# so the patch pairs with its branch; a branch at an older commit of origin/main writes nothing (B13
			# round 14 Q30), and the keep's -c format.filenameMaxLength=128, so one commit has one file name (B13
			# round 15 S25); it names the report command, not "this command" (S23).
			branches="$(local_work_counts)"
			branches="${branches##*|}"
			# a known count takes its noun and verb from the count (B13 round 16 T27, T50, T83)
			lbr="${branches:-0} local-work branches" lhold=hold
			[ "${branches:-0}" != 1 ] || lbr="1 local-work branch" lhold=holds
			if [ "${branches:-0}" -gt 0 ]; then
				# shellcheck disable=SC2016 # the backticks are text
				printf 'none: no patch file is in %s/local-work, but %s in ~/src/agent-context-sync %s commits this file does not carry; to send them, for each branch `git -C ~/src/agent-context-sync branch --list "local-work-*"` lists, run `git -C ~/src/agent-context-sync -c format.filenameMaxLength=128 format-patch -q --suffix=".<branch>.patch" origin/main..<branch> -o %s/local-work` (a branch that writes nothing holds no new commit), then run %s again\n' "$shown" "$lbr" "$lhold" "$shown" "$LOCAL_WORK_AGAIN"
			else
				printf 'none\n'
			fi
		fi
	} >"$local_work" 2>/dev/null || {
		rm -f "$tmp" "$local_work"
		return 1
	}
	# a read that failed partway would leave a patch cut short in section 3
	[ "$catfail" -eq 0 ] || {
		BACK_WHY="a kept patch in $SETUP_DIR/local-work could not be read in full"
		rm -f "$tmp" "$local_work"
		return 1
	}
	[ ! -s "$SETUP_DIR/fix-request.md" ] || fix=1
	# a patch this account cannot read has no hash on its line (B13 round 16 T24)
	{
		printf '# agentsync: the one file to bring back\n\n'
		printf 'Private: copy this file back as it is, and never paste it into the public issue form.\n'
		printf 'Section 1 is redacted.'
		if [ "$fix" -eq 1 ] && [ "$sent" -gt 0 ]; then
			printf ' Sections 2 and 3 are not redacted: they may name a real folder, file or person, so\nreview them before sending.\n'
		elif [ "$fix" -eq 1 ] || [ "$sent" -gt 0 ]; then
			printf ' Section %s is not redacted: it may name a real folder, file or person, so review it\nbefore sending.\n' "$((fix ? 2 : 3))"
		else
			printf '\n'
		fi
		# a line with no hash (a patch this account cannot read now) is listed there (B13 round 16 T24)
		{
			# what a person-made commit puts in section 3: its From: line is kept, so whoever triages the file can
			# tell it from the agentsync commit of the keep (B13 round 8 J65)
			if [ "$sent" -gt 0 ]; then
				# the count sent, not "one per kept commit": beside patches listed by their hash or unread ones, that
				# claimed a patch for each, and one patch is no "each" (B13 round 17 U37, as round 11 M18)
				if [ "$sent" -eq 1 ]; then
					printf '%s' "Section 3 sends one patch; it starts"
				elif [ "$nskip" -eq 0 ] && [ "$unread" -eq 0 ]; then
					printf '%s' "Section 3 sends $sent patches, one per kept commit; each starts"
				else
					printf '%s' "Section 3 sends $sent patches; each starts"
				fi
				printf '%s\n' " with its author's name and email and the" \
					"commit's date and message; a commit of your own carries your git name and email (or, if git never had them" \
					"set, a name and address git filled in from this Mac's account), so check those lines too."
				# the remedy here too, in the file the person reviews (B13 round 14 Q29)
				[ "$unread" -eq 0 ] || printf '%s\n' "Section 3 does not hold $up this account cannot read; make $uthem readable (chmod u+r) and run the report again (install.sh --report-only), as its last line says."
			elif [ "$unread" -gt 0 ]; then # before the hash sentences, which would call every patch an earlier one
				printf '%s\n' "Section 3 sends no patch: $up could not be read; make $uthem readable (chmod u+r) and run the report again (install.sh --report-only), as its last line says."
			elif [ "$nskip" -eq 1 ]; then # one patch is no "each" (B13 round 11 M18)
				printf '%s\n' "Section 3 sends no patch this time: its one patch was in an earlier report and is listed by its hash."
			elif [ -n "$skipped" ]; then # B13 round 10 L25 (lane RA), beside L4
				printf '%s\n' "Section 3 sends no patch this time: each was in an earlier report and is listed by its hash."
			elif [ "${branches:-0}" -gt 0 ]; then # B13 round 14 Q30: it said nothing of section 3
				printf '%s\n' "Section 3 sends no patch: $lbr $lhold commits this file does not carry; its line says how to add them."
			fi
			# the patches listed by their hash are a sentence of their own, counted by nskip, beside a patch sent or
			# one that could not be read: with neither, the sentences above say it (B13 round 15 S24, round 16 T25)
			if [ "$sent" -gt 0 ] || [ "$unread" -gt 0 ]; then
				if [ "$nskip" -eq 1 ]; then
					printf '%s\n' "The one other patch was in an earlier report and is listed by its hash."
				elif [ "$nskip" -gt 1 ]; then
					printf '%s\n' "The other $nskip patches were in an earlier report and are listed by their hash."
				fi
			fi
		} | if [ "$nohash" -eq 0 ]; then cat; else LC_ALL=C sed -E 's/listed by (its|their) hash/listed there/'; fi
		printf '\n## 1. Setup report\n\n'
		embed "$REPORT_PATH" markdown
		printf '\n## 2. Fix request (%s)\n\n' "$shown/fix-request.md"
		if [ "$fix" -eq 1 ]; then embed "$SETUP_DIR/fix-request.md" markdown; else printf 'none\n'; fi
		printf '\n## 3. Local work kept by setup prompt step 1 (%s)\n\n' "$shown/local-work"
		cat "$local_work"
	} >"$tmp" 2>/dev/null || {
		rm -f "$tmp" "$local_work"
		return 1
	}
	rm -f "$local_work"
	BACK_WHY="a folder, or a link to a folder, is at that path; move it away"
	# a file left behind would hold section 3's patches, which are not redacted; a folder made at the path since
	# the check above would take the file inside it, so it is checked again here
	if [ -d "$out" ] || ! {
		BACK_WHY="chmod or mv failed (its error is above)"
		chmod 600 "$tmp" && mv -f "$tmp" "$out"
	}; then
		rm -f "$tmp"
		return 1
	fi
	BACK_WHY=""
	if [ -d "$kept" ] && [ ! -L "$kept" ]; then
		if [ -n "$notsent" ]; then
			(umask 077 && printf '%s' "$notsent" >"$kept/.not-sent") 2>/dev/null || true
		else
			rm -f "$kept/.not-sent"
		fi
		# what this file embeds, by identity and the first report that did (B13 round 15 S21); a link is left
		# alone, as one at .not-sent is never read
		if [ -L "$kept/.sent" ]; then
			:
		elif [ -n "$sentlist" ]; then
			(umask 077 && printf '%s' "$sentlist" >"$kept/.sent") 2>/dev/null || true
		else
			rm -f "$kept/.sent"
		fi
	fi
	say "bring back: $out (one file: report, fix request, local work)"
	BACK_UNREAD="$unread" BACK_BRANCHES="${branches:-0}"
	# Step 2's run (no --report-only) says it as the person's, a WAITING ON YOU: line, the form the setup
	# prompt's step 2 shows the person and does not act on; its NEXT, which drives the setup, is left alone.
	# The report's run keeps the warning its NEXT points to ("see the warning above") (B13 round 18 V25). The
	# report sends nothing (its NEXT says "nothing is sent for you"): it puts the patch in this file, which the
	# person copies back, by the path this run writes (B13 round 19 W8, W12).
	if [ "$unread" -ne 0 ] && [ "$REPORT_ONLY" -eq 0 ]; then
		say "WAITING ON YOU: $up in $shown/local-work cannot be read by this account; make $uthem readable (chmod u+r), and the setup report at the end of this setup puts $uthem in $outshown for you to copy back"
	elif [ "$unread" -ne 0 ]; then
		warn "$up in $SETUP_DIR/local-work could not be read, so $out does not hold $uthem"
	fi
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
		# section 1's unread count by section 3's rule (B13 round 16 T24); an older agentsync counts by mode
		out="$(AGENTSYNC_REPORT_RUN="$RUN_ID" AGENTSYNC_REPORT_UNREAD_PATCHES="$(unread_patch_count)" with_timeout 120 "$bin" setup-report --out "$REPORT_PATH" --config "$CONFIG" </dev/null 2>&1)" || src=$?
		if [ "$src" -eq 0 ] && [ -s "$REPORT_PATH" ]; then
			REPORT_BY=agentsync
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

# The report of an attempt started by a copy of the prompt that does not match this installer (older, naming no
# version, or newer: B13 round 5 F18) is written to its own file, so the
# setup-report.md of the last attempt that was not stopped keeps its bytes, as bring-back.md does. Its first
# line says not to send it, and names that report only where one exists (B13 round 3 R17). Written after the
# report's redaction, so it names no path under this home but as ~/.
# Nor does its end invite sending it (B13 round 4 Q10, Q18): the "Send it:" line and the issue link are
# dropped here, not by the writer, so an agentsync older than this installer (a stopped copy stops before the
# install) and the shell fallback are covered alike. Both links start with ISSUE_URL, written after redaction;
# index() and LC_ALL=C read them byte for byte. An earlier run's "bring back:" and issue link lines, quoted in
# the report's install.out tail, go too, with one line saying so in their place, and the Redaction legend's
# "Read the report before sending it: a..." reads "A..." (B13 round 5 F16, F7): a field Mac has an earlier run.
# Its NEXT line stays: it invites no sending, the Installer section counts it, and install.out never holds a
# --report-only run's NEXT, the one that names bring-back.md (B13 round 6 G14).
# The closing line has a blank line of its own: the one held before the dropped issue link is never printed, and
# without it Markdown reads the closing line into the Redaction part (B13 round 6 G18).
# Two reads of the report. The first counts the lines left out of the install.out embed, between its
# "<details><summary>the last N line(s) of ..." line and "</details>", and the second says so in that summary
# line, at the end of the renderer's bracket ("; K of them left out: the bring-back and issue link lines"): the
# fence holds fewer lines than N (G16). Inside it, so the bracket, which says whose output all N lines are, is
# not read as why K were left out, and with no "an earlier run's": K can span runs (B13 round 12 N22); the
# fence's one marker line is number-neutral for the same reason (B13 round 13 O70). An
# older summary wording is left as it is. The second read also gives the summary F20's label where the agentsync that rendered the report is
# older than F20 (a stopped copy stops before step 2 reinstalls it): only where the report's own Summary says
# no run of this attempt is in install.out's tail, as the renderer's condition is, and with its two texts (G17).
# And an agentsync older than c6aa4485 (B13 round 8 J18) read the install.commit warn of a stopped copy as
# unexpected and printed doctor's "re-run install.sh" fix, while the report's NEXT says to copy the prompt again
# (B13 round 9 K21). A shim for those renderers, in the newer one's words: a "[warn] install.commit — " line of the
# Doctor part that holds ", but the checkout at " and " (fix: " ends in "(expected: STOPPED_COPY_WARN)" instead
# of its fix, the Doctor header gains (or counts in) the note on expected warns, and the Summary's doctor line
# moves "install.commit warn" from its unexpected list to its expected one. Only that old wording is changed: a
# newer renderer never lists the warn as unexpected for a stopped copy, and on_exit runs this only for one.
# The reason the report of this attempt's own step 1 stop gives install.commit's warn, which the older installed
# build lists as unexpected: nothing reinstalled it (B13 round 16 T11; older_report_rewrite step1)
STEP1_STOP_WARN="step 1 stopped this attempt before step 2 reinstalls agentsync"
STOPPED_COPY_WARN="the installer stopped this copy in step 1, before step 2 reinstalls agentsync" # setup_report._STOPPED_COPY_WARN (a test keeps them equal)
# A Doctor header beside that reason ends "nothing for this setup to do" plus these words, whichever renderer wrote
# it: the shim's own note and a renderer from J18 up to K24 wrote the bare words (B13 round 10 L19, L39).
STOPPED_COPY_BEYOND=" beyond the report's NEXT line (the fresh copy of the prompt reinstalls in its step 2)" # setup_report._STOPPED_COPY_BEYOND (a test keeps them equal)
# An older renderer's Summary prompt line gives one reason for the stop and its Run metadata names its own prompt
# version: the stop line names two reasons (B13 round 9 K18) and this installer's version (B13 round 10 L18, L24).
# An older renderer's Summary "- friction (attempt N): K event line(s): ..." line that counts only these kinds,
# with no legacy lines, is dropped, as a renderer from 78500114 on never writes it: it said no more than the
# agent-friction line, often "1 error, 1 finished" beside "failed at step 1" (B13 round 12 N20, round 13 O20).
STOPPED_COPY_QUIET_KINDS="error deviation prompt finished" # setup_report.PROBLEM_KINDS and "finished" (a test keeps them equal)
# An older renderer's Installer table shows the raw install.log notes of these steps, and its doctor names a held
# listing "deletions held (held at its last walk ...)": a renderer from 476b7dfd (round 13 O3) and 8045aeca (O36)
# on writes these words. The table's result cell is mapped only where its bracket is exactly a known token (", rc
# N" before it kept), and the table is padded again as _step_table pads it (B13 round 14 Q27). One line each:
# "<step> <token> <words>".
STOPPED_STEP_WORDS="launcher not-requested background sync was not asked for
launcher up-to-date already up to date
launcher kept the installed launcher is kept: no developer tools to rebuild it
launcher no-launcher no launcher: no developer tools and no --launcher PATH
agent not-requested background sync was not asked for
agent no-sources no folders to sync
agent status-failed agentsync status reported failures
wait not-requested background sync was not asked for
wait no-sources no folders to sync
wait status-failed agentsync status reported failures
wait agent-failed the agent step failed
report agentsync agentsync's report
report fallback the installer's fallback report
status fail-lines agentsync status reported [FAIL] lines
status tcc-pending its only [FAIL] lines were a macOS Allow still to be clicked (the [FAIL] line names it)
status status-error agentsync status exited with no [FAIL] line
doctor fail-lines agentsync status reported [FAIL] lines
doctor tcc-pending its only [FAIL] lines were a macOS Allow still to be clicked (the [FAIL] line names it)
doctor status-error agentsync status exited with no [FAIL] line" # setup_report._STEP_NOTE_WORDS less helpers (a test keeps them equal)
# The Installer output paragraph's sentence on a person's fix: line, which a renderer before ba5e253f lacks (B13
# round 16 T23): it goes after STOPPED_NEXT_STEP, whose full stop becomes a semicolon.
STOPPED_NEXT_STEP=" before install.sh's own next step. "
STOPPED_FIX_LINE="a fix: on a [FAIL] line of a run whose stopping [FAIL] lines are all the person's is not (that run's NEXT says so)." # setup_report._installer_section (a test keeps them equal)
STOPPED_HELD_OLD="(held at its last walk: the Summary's Loop line says whether a WAITING ON YOU line still holds it)"
STOPPED_HELD_NOW="(as of its last walk: the Summary's Loop line says whether a WAITING ON YOU line still names it)" # doctor.LISTING_YOURS_REPORT_NOTE (a test keeps them equal)
# An older renderer gives two runs started in one second one id, where a renderer from 3b47e3e7 on (and the shell's
# strip_run_pids) numbers the later ones <stamp>.2, .3 over install.log in its order (B13 round 12 N21). A stopped
# copy is rendered by the older installed agentsync, so this gives its report those labels (B13 round 13 O21):
# the Installer headings, the Every-run rows and the open-run lines (the k-th of a stamp shown is the k-th of that
# stamp's last runs in install.log, as many as are shown), the raw install.log lines (each shown run takes its
# install.log line count, the last the rest: the current run may log after the render), the install.out tail's
# "# run=" headers (the last ones of install.out), both lead-ins' "numbered .2 on" clause, and the Summary's retry
# line and the doctor's first-sync note (the run whose end rc matches, or that logged its status step; else the
# last of the stamp). Only where the report holds no numbered label and install.log has a shared second: a newer
# renderer's labels are left as they are, and only its Summary and sync lines pass through named() below (one
# from 3b47e3e7 up to 878ebc7e numbers runs but names none: B13 round 15 S19). Reads the report on stdin.
# The Summary's "the install run of <start>" (the first sync's install.log clause, or install.out's) gains
# ", run <label>" when that run's stamp is shared, as the renderer's _run_named does since 878ebc7e: the run is
# the last of that start time that logged a first sync, or that is no list (B13 round 14 Q26).
stopped_run_labels() {
	LC_ALL=C awk -v file="$SETUP_LOG" -v outf="$INSTALL_OUT" '
		function labelled(st, k) { return (st SUBSEP k) in ord ? label[ord[st, k]] : st }
		function named(t,    out, k, pre, rest, T, after, want, r, j, x) {
			out = ""
			while ((k = index(t, "the install run of ")) > 0) {
				pre = substr(t, 1, k - 1); rest = substr(t, k + 19); T = substr(rest, 1, 20); after = substr(rest, 21)
				out = out pre "the install run of " T; t = after
				if (T !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z$/ || index(after, ", run ") == 1) continue
				want = substr(pre, length(pre) - 13); r = ""
				for (j = 1; j <= nr; j++) { x = seq[j]; if (began[x] == T && (want == "(install.log: " ? fs[x] : !lst[x])) r = x }
				if (r == "") for (j = 1; j <= nr; j++) { x = seq[j]; if (began[x] == T && !lst[x]) r = x }
				if (r != "" && runs[substr(r, 1, 16)] > 1) out = out ", run " label[r]
			}
			return out t
		}
		function swap(line, at, n, to) { return substr(line, 1, at - 1) to substr(line, at + n) }
		BEGIN {
			sp = "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z"
			id = "run=" sp "-[0-9]+"
			while ((getline ln < file) > 0)
				if (match(ln, id)) {
					r = substr(ln, RSTART + 4, RLENGTH - 4); st = substr(r, 1, 16)
					if (!(r in label)) { k = ++runs[st]; label[r] = k == 1 ? st : st "." k; ord[st, k] = r; seq[++nr] = r }
					lines[r]++
					if (index(ln, " start install.sh ")) { split(ln, f, " "); began[r] = f[1]; if (index(ln, "--list-folders")) lst[r] = 1 }
					if (index(ln, " step=first-sync ")) fs[r] = 1
					if (match(ln, / end rc=[0-9]+/)) endrc[r] = substr(ln, RSTART + 8, RLENGTH - 8)
					if (index(ln, " step=status ")) doc[r] = 1
				}
			close(file)
			while ((getline ln < outf) > 0) if (index(ln, "# run=") == 1 && match(ln, "^# " id)) oh[++no] = substr(ln, 7, RLENGTH - 6)
			close(outf)
		}
		{ L[++n] = $0 }
		END {
			shared = 0; numbered = 0
			for (st in runs) if (runs[st] > 1) shared = 1
			for (i = 1; i <= n; i++) if (match(L[i], sp "\\.[0-9]")) numbered = 1
			if (!shared) { for (i = 1; i <= n; i++) print L[i]; exit }
			# a renderer from 3b47e3e7 up to 878ebc7e numbers the runs but names none in its Summary: no relabel
			# (the list run would be numbered twice), but the Summary and sync lines still gain ", run <label>"
			# (B13 round 15 S19)
			if (numbered) {
				sec = ""
				for (i = 1; i <= n; i++) {
					t = L[i]
					if (index(t, "## ") == 1) sec = t
					if (sec == "## Summary" || index(t, "- sync: ") == 1 || index(t, "- first sync: ") == 1) t = named(t)
					print t
				}
				exit
			}
			# the first read: how many of each stamp the report shows, in each place
			sec = ""; blk = ""
			for (i = 1; i <= n; i++) {
				t = L[i]
				if (index(t, "## ") == 1) { sec = t; blk = "" }
				if (sec != "## Installer") continue
				if (index(t, "<details><summary>raw install.log lines") == 1) blk = "raw"
				else if (index(t, "<details><summary>the last ") == 1) blk = "out"
				else if (t == "</details>") blk = ""
				if (blk == "out" && match(t, "^# run=" sp "( |$)")) nh++
				else if (blk == "raw" && match(t, "run=" sp "([^0-9A-Za-z_.-]|$)")) raw[substr(t, RSTART + 4, 16)]++
				else if (match(t, "^run " sp ": ")) hd[substr(t, 5, 16)]++
				else if (match(t, "^- run " sp "[:, ]")) op[substr(t, 7, 16)]++
				else if (match(t, "^\\| " sp " \\|")) rw[substr(t, 3, 16)]++
			}
			for (st in hd) if (runs[st] > 1) dhd = 1
			for (st in rw) if (runs[st] > 1) drw = 1
			# the open runs of each stamp, in order: those with no end line
			for (st in runs) for (k = 1; k <= runs[st]; k++) if (!(ord[st, k] in endrc)) opn[st, ++nop[st]] = k
			sec = ""; blk = ""
			for (i = 1; i <= n; i++) {
				t = L[i]
				if (index(t, "## ") == 1) { sec = t; blk = "" }
				if (sec == "## Installer") {
					if (index(t, "<details><summary>raw install.log lines") == 1) blk = "raw"
					else if (index(t, "<details><summary>the last ") == 1) blk = "out"
					else if (t == "</details>") blk = ""
					if (blk == "out" && match(t, "^# run=" sp "( |$)")) {
						j = no - nh + (++ch); r = j >= 1 ? oh[j] : ""
						if (r in label) t = swap(t, 7, 16, label[r])
					} else if (blk == "raw" && match(t, "run=" sp "([^0-9A-Za-z_.-]|$)")) {
						st = substr(t, RSTART + 4, 16); at = RSTART + 4
						if (!(st in cur)) { cur[st] = runs[st] - hd[st] + 1; left[st] = lines[ord[st, cur[st]]] }
						while (left[st] < 1 && cur[st] < runs[st]) { cur[st]++; left[st] = lines[ord[st, cur[st]]] }
						left[st]--
						if (hd[st] >= 1 && cur[st] >= 1) t = swap(t, at, 16, labelled(st, cur[st]))
					} else if (match(t, "^run " sp ": ")) {
						st = substr(t, 5, 16); k = runs[st] - hd[st] + (++sh[st])
						if (k >= 1) t = swap(t, 5, 16, labelled(st, k))
					} else if (match(t, "^- run " sp "[:, ]")) {
						st = substr(t, 7, 16); k = nop[st] - op[st] + (++so[st])
						if (k >= 1) t = swap(t, 7, 16, labelled(st, opn[st, k]))
					} else if (match(t, "^\\| " sp " \\|")) {
						st = substr(t, 3, 16); k = runs[st] - rw[st] + (++sr[st])
						if (k >= 1) t = swap(t, 3, 16, labelled(st, k))
					} else if (match(t, /run ids without their process id\):$/) && !index(t, "numbered .2 on")) {
						if (index(t, "Every run, oldest first") == 1 ? drw : dhd)
							t = substr(t, 1, length(t) - 2) ", numbered .2 on for runs started in one second):"
					}
				}
				# the run a Summary or doctor sentence names: its end rc, or its status step
				if (match(t, " run " sp " exited [0-9]+")) {
					at = RSTART + 5; st = substr(t, at, 16); rc = substr(t, at + 24, RLENGTH - 29); k = 0
					for (j = 1; j <= runs[st]; j++) if (endrc[ord[st, j]] == rc) k = j
					t = swap(t, at, 16, labelled(st, k ? k : runs[st]))
				}
				if (match(t, "doctor before its first sync \\(run " sp "\\)")) {
					at = RSTART + RLENGTH - 17; st = substr(t, at, 16); k = 0
					for (j = 1; j <= runs[st]; j++) if (ord[st, j] in doc) k = j
					t = swap(t, at, 16, labelled(st, k ? k : runs[st]))
				}
				if (sec == "## Summary" || index(t, "- sync: ") == 1 || index(t, "- first sync: ") == 1) t = named(t)
				print t
			}
		}'
}
# The install.sh a report names for its rerun: skill.INSTALL_SH, setup_report's INSTALL_SH (a test keeps them equal)
# shellcheck disable=SC2088 # the ~ is printed, for the reader's shell to expand
REPORT_INSTALL_SH="~/src/agent-context-sync/scripts/install.sh"
# Facts older_report_rewrite reads where the renderer reads them (B13 round 17 U27, U28), as files with no
# agentsync: OR_LASTRUN, the last install.sh run that reached an install step (a --list-folders run, or one a
# usage error stopped, logs commit=- and says nothing of the installed copy), "commit <c>[ tree <t> (...)]" or
# "no commit read", empty with none, as setup_report._installed_where words it; OR_FETCH, the checkout's
# FETCH_HEAD as _last_fetch words it, empty when it cannot be read; OR_FSSTART, the start time of the last run
# with a first-sync step. DIR: the checkout the report's fetch line names (B13 round 18 V91: the date of install.sh's
# own checkout went on another checkout's line); empty, OR_FETCH stays empty and the line keeps its undated words.
older_report_facts() { # DIR
	local d="$1" paths=() p rel common best="" bm=-1 m z
	OR_LASTRUN="" OR_FETCH="" OR_FSSTART=""
	if [ -f "$SETUP_LOG" ]; then
		IFS='|' read -r OR_LASTRUN OR_FSSTART < <(LC_ALL=C awk '
			match($0, / run=[^ ]+ /) { r = substr($0, RSTART + 5, RLENGTH - 6) }
			index($0, " start install.sh ") { st[r] = $0; at[r] = $1 }
			match($0, / step=[^ ]+ /) {
				s = substr($0, RSTART + 6, RLENGTH - 7)
				if (s != "report" && s != "list-folders") real = r
				if (s == "first-sync") fs = r
			}
			END {
				w = ""
				if (real != "" && (real in st)) {
					c = v(st[real], "commit"); t = v(st[real], "tree")
					if (t == "" && match(st[real], / commit=[^ ]+-dirty dirty [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]+/)) { t = substr(st[real], RSTART, RLENGTH); sub(/.* /, "", t) }
					if (c == "-") w = "no commit read"
					else if (c != "") w = "commit " c (t != "" ? " tree " t " (the SHA-256 prefix of `git diff HEAD` in that checkout)" : "")
				}
				printf "%s|%s\n", w, (fs != "" && (fs in at)) ? at[fs] : ""
			}
			function v(l, k) { if (!match(l, " " k "=[^ ]*")) return ""; return substr(l, RSTART + length(k) + 2, RLENGTH - length(k) - 2) }
		' "$SETUP_LOG" 2>/dev/null) || true
	fi
	# a worktree's FETCH_HEAD is its own, but origin/main is shared: the newer of the two dates it
	if [ -z "$d" ]; then
		return 0
	elif [ -d "$d/.git" ]; then
		paths=("$d/.git/FETCH_HEAD")
	elif [ -e "$d/.git" ] && /usr/bin/xcode-select -p >/dev/null 2>&1; then # no git shim's install dialog
		rel="$(GIT_OPTIONAL_LOCKS=0 git -C "$d" rev-parse --git-path FETCH_HEAD 2>/dev/null)" || return 0
		common="$(GIT_OPTIONAL_LOCKS=0 git -C "$d" rev-parse --git-common-dir 2>/dev/null)" || return 0
		case "$rel" in /*) ;; *) rel="$d/$rel" ;; esac
		case "$common" in /*) ;; *) common="$d/$common" ;; esac
		paths=("$rel" "$common/FETCH_HEAD")
	else
		return 0
	fi
	for p in "${paths[@]}"; do
		[ -e "$p" ] || [ -L "$p" ] || continue
		m="$(/usr/bin/stat -f %m "$p" 2>/dev/null)" || return 0
		[ "$m" -le "$bm" ] || { bm="$m" best="$p"; }
	done
	if [ -z "$best" ]; then
		OR_FETCH="no fetch since the clone"
		return 0
	fi
	z="$(/usr/bin/stat -f %z "$best" 2>/dev/null)" || return 0
	m="$(TZ=UTC0 /usr/bin/stat -f %Sm -t %Y-%m-%dT%H:%M:%SZ "$best" 2>/dev/null)" || return 0
	if [ "$z" = 0 ]; then
		OR_FETCH="the checkout's last fetch, $m, failed; origin/main is from an earlier fetch"
	else
		OR_FETCH="as of the checkout's last fetch, $m"
	fi
	return 0
}
# The rewrite of a report an agentsync older than this checkout rendered (stopped_report_note's, above), on
# stdout, MODE copy or step1. "copy" is a stopped copy's: everything above. "step1" is the report of this
# attempt's own step 1 stop, which the installed build renders too, as nothing reinstalled it (B13 round 16
# T11; on_exit names when): only the renderer-only words (the counts, the quiet friction line, the Installer
# table's tokens and its fix: sentence, the Status words), install.commit moved to the Summary's expected list
# with STEP1_STOP_WARN, and three shims for the stop (step1_stop_facts): its cause in the outcome's computed
# bracket, the expected-turns line, and the stopped app's source FAILs in the doctor line's "from the sign-in
# stop" bucket, as setup_report._sign_in_stop_fails takes them. Its Loop NEXT and Doctor part stay as the
# installed build wrote them: doctor stays one build (a checkout's renderer over an old install mixed two
# builds, §16.110 Q27).
older_report_rewrite() { # MODE
	local named cp=0 note="${S1_NOTE:-}" apps="${S1_APPS:-}" cause="${S1_CAUSE:-}" turns="${S1_TURNS:-}" item="${S1_ITEM:-}"
	local cmd="${S1_CMD:-}" at="${S1_AT:-}" fline="${S1_FLINE:-}" one="${S1_ONE:-}"
	[ "$1" != copy ] || { cp=1 note="" apps="" cause="" turns="" item="" cmd="" at="" fline="" one=""; }
	# the checkout the undated fetch line names ("checkout <dir> @ ", "checkout <dir> (editable) @ "), as the
	# renderer dates the folder it was installed from: that folder when it is one here, else no date (a
	# redacted or missing path keeps the older words, which are true)
	named="$(LC_ALL=C awk 'index($0, "(as of the checkout\047s last fetch)") && (i = index($0, "checkout ")) {
		p = substr($0, i + 9); j = index(p, " @ ")
		if (j) { p = substr(p, 1, j - 1); sub(/ \(editable\)$/, "", p); print p }
		exit
	}' "$REPORT_PATH" 2>/dev/null || true)"
	case "$named" in \~) named="$HOME" ;; \~/*) named="$HOME/${named#\~/}" ;; esac
	[ -n "$named" ] && [ -d "$named" ] || named=""
	older_report_facts "$named"
	LC_ALL=C STEPWORDS="$STOPPED_STEP_WORDS" OR_LASTRUN="$OR_LASTRUN" OR_FETCH="$OR_FETCH" OR_FSSTART="$OR_FSSTART" awk \
		-v cp="$cp" -v s1note="$note" -v s1apps="$apps" -v s1item="$item" -v inst="$REPORT_INSTALL_SH" \
		-v s1cmd="$cmd" -v s1at="$at" -v s1fline="$fline" -v s1one="$one" \
		-v s1cause="$cause" -v s1turns="$turns" -v w1="$STEP1_STOP_WARN" -v u="$ISSUE_URL" -v r="Read the report before sending it: a" \
		-v gone="(earlier runs' bring-back and issue link lines are left out of this report)" \
		-v saw=" (what the agent saw)</summary>" -v was=" (earlier attempts' install.sh output: " \
		-v w="$STOPPED_COPY_WARN" -v b="$STOPPED_COPY_BEYOND" -v compat="$SETUP_PROMPT_COMPAT" \
		-v changed=", or the --log-start value was changed" -v quietkinds=" $STOPPED_COPY_QUIET_KINDS " \
		-v heldold="$STOPPED_HELD_OLD" -v heldnow="$STOPPED_HELD_NOW" -v nextstep="$STOPPED_NEXT_STEP" -v fixline="$STOPPED_FIX_LINE" '
		function load(    m, i, ws, p, q, rest) { # words[step, token]: one "<step> <token> <words>" a line (not -v: a newline)
			m = split(ENVIRON["STEPWORDS"], ws, "\n")
			for (i = 1; i <= m; i++) {
				p = index(ws[i], " "); rest = substr(ws[i], p + 1); q = index(rest, " ")
				words[substr(ws[i], 1, p - 1), substr(rest, 1, q - 1)] = substr(rest, q + 1)
			}
		}
		BEGIN {
			load()
			one["were"] = "was"; one["are"] = "is"; one["have"] = "has"; one["end"] = "ends"
			one["keep"] = "keeps"; one["hold"] = "holds"; one["name"] = "names"
		}
		function trim(x) { sub(/^ +/, "", x); sub(/ +$/, "", x); return x }
		function ulen(x) { gsub(/[\200-\277]/, "", x); return length(x) } # characters, as Python counts them
		function fill(x, w,    i) { for (i = ulen(x); i < w; i++) x = x " "; return x }
		function lfill(x, w,    i, p) { p = ""; for (i = ulen(x); i < w; i++) p = p " "; return p x }
		function dashes(w,    x) { x = ""; while (w-- > 0) x = x "-"; return x }
		# setup_report._item_what for a step 1 error\047s what-text, by characters as Python counts them (B13
		# round 19 W25): the older build cut it to 220 with its "prompt vN, <agent>: " prefix, before git\047s reason
		function upre(s, n,    i, k) { # the first n characters of s
			k = 0
			for (i = 1; i <= length(s); i++) if (substr(s, i, 1) !~ /[\200-\277]/) { if (k == n) return substr(s, 1, i - 1); k++ }
			return s
		}
		function shorten(s, lim,    cut) { # setup_report._shorten
			gsub(/[ \t]+/, " ", s); sub(/^ /, "", s); sub(/ $/, "", s)
			if (ulen(s) <= lim) return s
			cut = upre(s, lim - 1)
			if (substr(s, length(cut) + 1, 1) != " " && index(cut, " ")) sub(/ [^ ]*$/, "", cut)
			sub(/ +$/, "", cut)
			return cut "\342\200\246"
		}
		# where setup_report._FAILED_EXIT_RE first matches w ("... failed (exit 1): ", "exited 128:", "(exit 1):",
		# "failed with exit code 2:", "rc=1:", "returned 1:"), 0 for nowhere: by hand, as awk has no lazy match
		function fail_at(w,    i, n, s, b, k, c, seg, d) {
			n = length(w)
			for (i = 1; i <= n; i++) {
				b = (i == 1 || substr(w, i - 1, 1) !~ /[A-Za-z0-9_]/)
				s = substr(w, i)
				k = 0
				if (b && match(s, /^(failed|exited|returned)/) && substr(s, RLENGTH + 1, 1) !~ /[A-Za-z0-9_]/) k = RLENGTH
				else if (substr(s, 1, 5) == "(exit" && substr(s, 6, 1) !~ /[A-Za-z0-9_]/) k = 5
				if (k && (c = index(substr(s, k + 1), ":"))) { # up to 24 more, then digits, ")" and blanks
					seg = substr(s, k + 1, c - 1); sub(/[ \t]+$/, "", seg); sub(/\)$/, "", seg)
					if (match(seg, /[0-9]+$/) && RSTART - 1 <= 24) return i
				}
				if (b && match(s, /^(exit([ \t]+(code|status))?|rc)[ \t]*=?[ \t]*[0-9]+\)?[ \t]*:/)) return i
			}
			return 0
		}
		function item_what(w,    p, head) {
			gsub(/[ \t]+/, " ", w); sub(/^ /, "", w); sub(/ $/, "", w)
			if (!(p = fail_at(w))) return shorten(w, 220)
			if (ulen(w) <= 320) return w
			head = shorten(substr(w, 1, p - 1), 80)
			if (head == "") return shorten(w, 320)
			return head " " shorten(substr(w, p), 319 - ulen(head))
		}
		# the what-text without its "prompt vN, <agent>:" words, as parse_friction drops _STEP1_STATED_RE\047s match
		function unstate(w,    x, y, a, l) {
			x = w
			while (match(x, /^([ \t\047"]|\342\200[\230\231])/)) x = substr(x, RLENGTH + 1)
			if (tolower(substr(x, 1, 8)) != "prompt v" || !match(substr(x, 9), /^[0-9][0-9]?[0-9]?/)) return w
			x = substr(x, 9 + RLENGTH)
			if (x ~ /^[A-Za-z0-9_]/ || !match(x, /^[ ,:;-]+/)) return w
			# the agent may start inside that run, as the regex backtracks: "prompt v27,,:" is agent ","
			for (l = RLENGTH; l > 0; l--) {
				y = substr(x, l + 1)
				if (!match(y, /^[^: \t][^:]*:/)) continue
				a = substr(y, 1, RLENGTH - 1); sub(/[ \t]+$/, "", a)
				if (ulen(a) > 100) return w
				y = substr(y, RLENGTH + 1)
				while (match(y, /^([ \t\047"]|\342\200[\230\231])/)) y = substr(y, RLENGTH + 1)
				return y
			}
			return w
		}
		function row_what(row,    n, f) { # the report\047s own (redacted) friction row: "", unless it splits in five
			n = split(row, f, / [|] /)
			if (n != 5 || f[2] != "step 1" || f[3] != "error") return ""
			return item_what(unstate(f[4]))
		}
		function cut_item(line,    pre, j, tail) { # an Items line the older build cut before " → ", whole
			pre = "- F" s1fline " \302\267 step 1 \302\267 error \302\267 "
			if (s1what == "" || index(line, pre) != 1) return line
			if ((j = index(line, "\342\200\246 \342\206\222 "))) tail = substr(line, j + 3)
			else if (ends(line, "\342\200\246")) tail = ""
			else return line
			return pre s1what tail
		}
		# the helpers step note not-built-<names> as setup_report._helpers_note words it (a test keeps them
		# equal), "" for any other token (B13 round 18 V6)
		function not_built(tok,    n, a, i, what, head, ws, hs) {
			if (tok !~ /^not-built-[a-z]+(\+[a-z]+)*$/) return ""
			n = split(substr(tok, 11), a, "+")
			for (i = 1; i <= n; i++) {
				if (a[i] == "ocr") { what = "the OCR helper"; head = "\"OCR helper:\"" }
				else if (a[i] == "media") { what = "the media helper"; head = "\"media helper:\"" }
				else if (a[i] == "speech") { what = "the speech helper"; head = "\"speech:\"" }
				else return ""
				ws = ws (i == 1 ? "" : i == n ? " and " : ", ") what
				hs = hs (i == 1 ? "" : i == n ? " and " : ", ") head
			}
			if (n == 1) return "not ready: " ws "; its " hs " line in install.out says why"
			return "not ready: " ws "; their " hs " lines in install.out say why"
		}
		function table(    i, k, c, ch, w1, w2, w3, tok, x) { # the Installer table buffered in tb[1..tn]
			for (; blank > 0; blank--) print ""
			ch = tn >= 3 && tb[2] ~ /^\|-+\|-+:\|-+\|$/
			for (i = 1; ch && i <= tn; i++) {
				if (i == 2) continue
				if (split(substr(tb[i], 3, length(tb[i]) - 4), c, " \\| ") != 3) { ch = 0; break }
				S[i] = trim(c[1]); N[i] = trim(c[2]); R[i] = trim(c[3])
				if (i > 2 && match(R[i], / \([^()]*\)$/)) {
					tok = substr(R[i], RSTART + 2, RLENGTH - 3)
					if ((S[i] SUBSEP tok) in words) { R[i] = substr(R[i], 1, RSTART) "(" words[S[i], tok] ")"; ch = 2 }
					else if (S[i] == "helpers" && (x = not_built(tok)) != "") { R[i] = substr(R[i], 1, RSTART) "(" x ")"; ch = 2 }
					# the words of round 18: a helper built that fails its probe is not "not built" (B13 round 19 W38)
					else if (S[i] == "helpers" && index(tok, "not built: the ") == 1) { R[i] = substr(R[i], 1, RSTART) "(not ready: " substr(tok, 12) ")"; ch = 2 }
				}
				# each cell: its "(s)" by its count, after the token lookup, which reads the token as written; the
				# table is padded again (B13 round 14 Q39 "listed N folders", round 16 T23)
				if (i > 2 && (x = plural_all(R[i])) != R[i]) { R[i] = x; ch = 2 }
				if (i > 2 && (x = plural_all(S[i])) != S[i]) { S[i] = x; ch = 2 }
			}
			if (ch == 2) {
				w1 = w2 = w3 = 0
				for (i = 1; i <= tn; i++) if (i != 2) {
					if (ulen(S[i]) > w1) w1 = ulen(S[i])
					if (ulen(N[i]) > w2) w2 = ulen(N[i])
					if (ulen(R[i]) > w3) w3 = ulen(R[i])
				}
				for (i = 1; i <= tn; i++)
					if (i == 2) print "|" dashes(w1 + 2) "|" dashes(w2 + 1) ":|" dashes(w3 + 2) "|"
					else print "| " fill(S[i], w1) " | " lfill(N[i], w2) " | " fill(R[i], w3) " |"
			} else for (i = 1; i <= tn; i++) print tb[i]
			tn = 0
		}
		function quiet(line,    c, n, a, i, x) { # a friction count line of quiet kinds only ("; N legacy" keeps it)
			if (index(line, "- friction (attempt ") != 1 || !index(line, " event line(s): ") || index(line, "; ")) return 0
			c = substr(line, index(line, " event line(s): ") + 16)
			if (c == "none") return 1
			n = split(c, a, ", ")
			for (i = 1; i <= n; i++) {
				x = a[i]
				if (!sub(/^[0-9]+ /, "", x) || !index(quietkinds, " " x " ")) return 0
			}
			return 1
		}
		function left(line) { return index(line, "Send it: ") == 1 || index(line, u) > 0 || index(line, "bring back: ") == 1 }
		function old(line) { return index(line, "[warn] install.commit \342\200\224 ") == 1 && index(line, ", but the checkout at ") > 0 && index(line, " (fix: ") > 0 }
		function doctor_line(line, why,    k, p, i, j, a, m, items, rest, e, done) { # the Summary\047s, as c6aa4485 writes it
			k = split(substr(line, 11), p, " \302\267 "); e = 0; done = 0
			for (i = 2; i <= k; i++) {
				if (!done && p[i] ~ /^unexpected [0-9]+: /) {
					rest = substr(p[i], index(p[i], ": ") + 2); m = split(rest, a, ", "); items = ""
					for (j = 1; j <= m; j++) if (!done && a[j] == "install.commit warn") done = 1; else items = items (items == "" ? "" : ", ") a[j]
					if (done) p[i] = "unexpected " (substr(p[i], 12) - 1) (items == "" ? "" : ": " items)
				}
				if (p[i] ~ /^expected [0-9]+: /) e = i
			}
			if (!done) return line
			if (e) p[e] = "expected " (substr(p[e], 10) + 1) substr(p[e], index(p[e], ":")) "; install.commit (" why ")"
			line = "- doctor: " p[1] (e ? "" : " \302\267 expected 1: install.commit (" why ")")
			for (i = 2; i <= k; i++) line = line " \302\267 " p[i]
			return line
		}
		# A Doctor [FAIL] line of a source check under the CloudStorage folder of an app the step 1 list found
		# signed out (s1apps, its apps= tokens), as setup_report._sign_in_stop_fails takes them: its check name,
		# else "". By the redacted path the line names ("~/Library/CloudStorage/OneDrive-<org-1>/..."), never
		# the fix text; "another sync app" (other) names no folder.
		function stopped_fail(line,    name, k, prov, n, t, i, rest) {
			if (!match(line, /^\[FAIL\] source\.[^ ]+\.(listable|volume) \342\200\224 /)) return ""
			name = substr(line, 8, RLENGTH - 12); rest = substr(line, RSTART + RLENGTH)
			# the path is the detail\047s first ~/Library/CloudStorage/ one before the fix: v10\047s volume line
			# opens "File Provider root UUID not checked: ~/Library/..."
			if ((k = index(rest, " (fix: ")) > 0) rest = substr(rest, 1, k - 1)
			if (!(k = index(rest, "~/Library/CloudStorage/"))) return ""
			prov = tolower(substr(rest, k + 23)); k = index(prov, "/"); if (k) prov = substr(prov, 1, k - 1)
			n = split(s1apps, t, ",")
			for (i = 1; i <= n; i++) if (t[i] != "other" && t[i] != "" && (prov == t[i] || index(prov, t[i] "-") == 1)) return name
			return ""
		}
		# The Summary doctor line with those FAILs out of its unexpected list and in "from the sign-in stop (N
		# checks)", after the expected and yours parts, as the renderer orders and words them (B13 round 16
		# T20: a bare number after "stop" read as a name; round 17 U53, U81)
		function stop_bucket(line,    k, p, i, j, a, m, items, moved, nm, out, put) {
			k = split(substr(line, 11), p, " \302\267 "); moved = ""; nm = 0
			for (i = 2; i <= k; i++) if (p[i] ~ /^unexpected [0-9]+: /) {
				m = split(substr(p[i], index(p[i], ": ") + 2), a, ", "); items = ""
				for (j = 1; j <= m; j++) {
					if (a[j] ~ / FAIL$/ && (substr(a[j], 1, length(a[j]) - 5) in mv)) { moved = moved (nm++ ? ", " : "") a[j] }
					else items = items (items == "" ? "" : ", ") a[j]
				}
				p[i] = "unexpected " (substr(p[i], 12) - nm) (items == "" ? "" : ": " items)
			}
			if (!nm) return line
			out = "- doctor: " p[1]; put = 0
			for (i = 2; i <= k; i++) {
				if (!put && p[i] !~ /^(expected|yours) [0-9]+: /) { out = out " \302\267 from the sign-in stop (" pl(nm, "check") "): " moved; put = 1 }
				out = out " \302\267 " p[i]
			}
			return out (put ? "" : " \302\267 from the sign-in stop (" pl(nm, "check") "): " moved)
		}
		function ends(line, tail) { return length(line) >= length(tail) && substr(line, length(line) - length(tail) + 1) == tail }
		# An older renderer writes "(s)" beside a known count in the lines the current one words by the count (B13
		# round 14 Q39, Q42, Q43, Q81 and Q41, renderer only; round 15 S20): each keyed on the number in its
		# own line, and never inside a <details> block, which holds history (the install.out tail).
		function pl(n, w, many) { return n " " (n == 1 ? w : (many == "" ? w "s" : many)) }
		# Every "word(s)" or "word(ies)" outside <details> and ~~~ fences, by its count, as the current renderer
		# words them all (B13 round 15 S26; round 16 T23, T84, T18): the count is the nearest whole number
		# before it in its clause (back to a ";", "," or "("), at most three words before it, so "5 of 6
		# source(s)" goes by 6 and "2 legacy v4 line(s)" by 2 ("v4" is no number). A plural verb right after
		# it agrees with a count of 1 ("1 conversion was"). An "(s)" with no count in its clause stays. Run
		# at the print and on the Installer table cells, so quiet() and the Doctor header, which read the
		# old "(s)" forms, see them first.
		function plural_all(line,    out, pre, noun, ies, base, cl, i, c, m, ws, j, n, gap, w) {
			out = ""
			while (match(line, /[A-Za-z]+\((s|ies)\)/)) {
				pre = substr(line, 1, RSTART - 1); noun = substr(line, RSTART, RLENGTH); line = substr(line, RSTART + RLENGTH)
				ies = substr(noun, length(noun) - 4) == "(ies)"
				base = substr(noun, 1, length(noun) - (ies ? 5 : 3))
				cl = out pre
				for (i = length(cl); i > 0; i--) { c = substr(cl, i, 1); if (c == ";" || c == "," || c == "(") break }
				m = split(substr(cl, i + 1), ws, " "); n = -1; gap = 0
				for (j = m; j >= 1 && gap <= 3; j--) { if (ws[j] ~ /^[0-9]+$/) { n = ws[j] + 0; break }; gap++ }
				if (n < 0) { out = out pre noun; continue }
				# "entry(ies)" (61474fdf) or "entr(ies)"
				if (ies && base ~ /y$/) base = substr(base, 1, length(base) - 1)
				out = out pre base (n == 1 ? (ies ? "y" : "") : (ies ? "ies" : "s"))
				if (n == 1 && match(line, /^ [a-z]+/) && (w = substr(line, 2, RLENGTH - 1)) in one)
					line = " " one[w] substr(line, RLENGTH + 1)
			}
			return out line
		}
		function fix(line, pat, at, n, to,    i) { # swap the first match of pat, from its at-th character
			if (!match(line, pat)) return line
			return substr(line, 1, RSTART - 1) to substr(line, RSTART + RLENGTH)
		}
		# The exclude-empty waits of the Summary, each up to the next one, by the count that opens it: the clauses
		# that are not "N word(s)" (a count in another clause, a pronoun); plural_all does the rest. The next
		# wait is looked for after the whole count, so "12 empty" is not read as a wait at its "2" (B13 round 16
		# T43), and v10\047s loop.py wrote "if they are meant" (T18).
		function counted(line,    k, seg, out, rest, n) {
			out = ""
			while (match(line, /[0-9]+ empty cloud folder/)) {
				out = out substr(line, 1, RSTART - 1); line = substr(line, RSTART)
				n = substr(line, 1, index(line, " ") - 1) + 0
				rest = index(line, " ")
				k = match(substr(line, rest), /[0-9]+ empty cloud folder/) ? rest + RSTART - 2 : length(line)
				seg = substr(line, 1, k); line = substr(line, k + 1)
				seg = fix(seg, "empty cloud folder\\(s\\) keep the listing", 0, n, n == 1 ? "empty cloud folder keeps the listing" : "empty cloud folders keep the listing")
				seg = fix(seg, "if (they|the folder\\(s\\)) are meant", 0, n, n == 1 ? "if the folder is meant" : "if the folders are meant")
				if (n == 1) seg = fix(seg, "\\(it adds (them|it) to that source", 0, n, "(it adds the folder to that source")
				if (match(seg, /which names the folder(\(s\)|s)?, up to five, and past five how to list the rest/))
					seg = substr(seg, 1, RSTART - 1) "which names the folder" (n == 1 ? "" : "s") (n > 5 ? ", up to five, and past five how to list the rest" : "") substr(seg, RSTART + RLENGTH)
				else if (match(seg, /which names the folder\(s\)/))
					seg = substr(seg, 1, RSTART - 1) "which names the folder" (n == 1 ? "" : "s") substr(seg, RSTART + RLENGTH)
				out = out seg
			}
			return out line
		}
		function status_one(line,    n) { # a Status unknown line of one folder: its own pronouns
			if (index(line, " 1 materialised-and-empty (1 of them still empty on this Mac, by their link count of 2)"))
				line = fix(line, "\\(1 of them still empty on this Mac, by their link count of 2\\)", 0, 1, "(still empty on this Mac, by its link count of 2)")
			# v10 (4a42af9) wrote "(1 of them with a link count of 2: no entry by the folder\047s own metadata)" (B13 round 16 T18)
			if (index(line, " 1 materialised-and-empty (1 of them with a link count of 2: "))
				line = fix(line, "\\(1 of them with a link count of 2: [^)]*\\)", 0, 1, "(still empty on this Mac, by its link count of 2)")
			# "its last walk listed all N file(s)": the current words (B13 round 15 S30, round 16 T23)
			if (match(line, /its last walk listed all [0-9]+ file\(s\)/)) {
				n = substr(line, RSTART + 25) + 0
				line = substr(line, 1, RSTART - 1) "its last walk listed " (n == 1 ? "the one file" : "every one of the " n " files") substr(line, RSTART + RLENGTH)
			}
			if (match(line, /; 1 of [0-9]+ checked; /)) {
				line = fix(line, "; none of them excluded in sources\\.toml now;", 0, 1, "; it is not excluded in sources.toml now;")
				line = fix(line, "; the mirror holds no file below them;", 0, 1, "; the mirror holds no file below it;")
			}
			return line
		}
		function prompt_line(line,    i, j, r, q) { # the copy note gains the stop line\047s second reason
			if (index(line, "- prompt: ") != 1 || index(line, changed)) return line
			if (!(i = index(line, " (older than the installer\047s v"))) i = index(line, " (newer than the installer\047s v")
			if (!i && !(i = index(line, "; older than the installer\047s v"))) i = index(line, "; newer than the installer\047s v")
			if (!i) return line
			r = substr(line, i + 2)
			if (!(j = index(r, ")"))) return line
			q = i + 1 + j
			return substr(line, 1, q - 1) changed substr(line, q)
		}
		FNR == 1 { sec = "" }
		NR == FNR {
			if (index($0, "## ") == 1) sec = $0
			if (sec == "## Doctor" && old($0)) c++
			if (s1note != "" && sec == "## Doctor" && stopped_fail($0)) mv[stopped_fail($0)] = ++nmv
			if (sec == "## Doctor" && ends($0, "(expected: " w ")")) st = 1
			if (index($0, "- installer output: no run of this attempt in the last ") == 1) noout = 1
			if (index($0, "- install.sh: no run during this attempt") == 1) norun = 1
			if (!at && index($0, "<details><summary>the last ") == 1 && $0 ~ / line(s|\(s\))? of /) { at = FNR; inside = 1; next }
			if (inside && $0 == "</details>") inside = 0
			if (inside && left($0)) k++
			if (s1cmd && s1fline != "" && sec == "## Agent friction log" && match($0, "^ *" s1fline "  [0-9][0-9][0-9][0-9]-")) s1row = substr($0, RLENGTH - 4)
			# a git stop after a finished attempt (B13 round 18 V31): the older build files the stopping error
			# F<n> under "Agent friction (...; it does not change the outcome by itself)" beside "failed at step
			# 1"; read here whether that is the whole block and the friction line names F<n> alone
			if (s1cmd && sec == "## Summary") {
				if (index($0, "- **outcome: failed at step 1** (computed: no install.sh run; step 1 logged an error)") == 1) s1out = 1
				if (index($0, "- agent friction: ") == 1 && ends($0, " (F" s1fline "); none of these changes the outcome by itself")) s1fr = 1
				if ($0 ~ /^Agent friction \(attempt [0-9]+; it does not change the outcome by itself\):$/) { af = 1; s1att = substr($0, 25, index($0, ";") - 25) }
				else if (af && index($0, "- F" s1fline " ") == 1) mvline = $0
				else if (af && index($0, "- ") == 1) afother++
				else if (af && $0 != "") af = 0
			}
			next
		}
		FNR == 1 && s1out && s1fr && mvline != "" && !afother { s1mv = 1; if (s1item == "") s1item = mvline }
		FNR == 1 && s1row != "" { s1what = row_what(s1row); if (s1mv) s1item = cut_item(s1item) }
		cp && FNR == at {
			n = length($0) - length(saw)
			if (noout && n > 0 && substr($0, n + 1) == saw)
				$0 = substr($0, 1, n) was (norun ? "this attempt ran no install.sh" : "none of this attempt\047s runs is in it") ")</summary>"
			if (k > 0 && $0 ~ /^<details><summary>the last ([0-9]+ )?line(s|\(s\))? of .* \(.*\)<\/summary>$/)
				$0 = substr($0, 1, length($0) - 11) "; " k " of them left out: the bring-back and issue link lines)</summary>"
		}
		index($0, "## ") == 1 { sec = $0; det = 0; fence = 0 }
		index($0, "<details><summary>") == 1 { $0 = plural_all($0) } # its summary is the renderer\047s words
		index($0, "<details>") == 1 { det = 1 }
		!det && (sec == "## Summary" || index($0, "- [policy] label rule: on (") == 1) { $0 = counted($0) }
		!det && sec == "## Status" && index($0, "- ") == 1 { $0 = status_one($0) }
		$0 == "</details>" { det = 0 }
		!det && index($0, "~~~") == 1 { fence = !fence }
		# the Installer output paragraph of a renderer from before ba5e253f lacks its sentence on a fix: line
		# (B13 round 16 T23)
		!det && sec == "## Installer" && index($0, "Installer output: ") == 1 && (j = index($0, nextstep)) && !index($0, "whose stopping [FAIL] lines") {
			$0 = substr($0, 1, j + length(nextstep) - 3) "; " fixline " " substr($0, j + length(nextstep))
		}
		cp && sec == "## Summary" { $0 = prompt_line($0) }
		# a renderer from before c6c6a73b (B13 round 12 N26) joins sw_vers\047 product name to its version under a
		# label that says macOS already: the version and build, "?" for none, as setup_report.sw_vers (round 19 W16)
		!det && !fence && sec == "## Environment" && $0 == "- macOS: macOS" { $0 = "- macOS: ?" }
		!det && !fence && sec == "## Environment" && index($0, "- macOS: macOS ") == 1 {
			$0 = "- macOS: " substr($0, 16)
			if (substr($0, 10, 1) == "(") $0 = "- macOS: ? " substr($0, 10)
		}
		# after a finished attempt (V31): the renderer notes, right after the prompt line, that the stop was logged
		# after that attempt finished, which the older build\047s one attempt hides; s1mv is 0 on a second pass
		# (the friction line then says "stopped the run"), so it is added once (B13 round 19 W24)
		s1mv && sec == "## Summary" && index($0, "- prompt: ") == 1 {
			$0 = $0 "\n- note: the step 1 error (F" s1fline ") was logged by another session after attempt " s1att " finished: this session stopped before `install.sh --log-start`, so this report\047s attempt " s1att " holds both sessions."
		}
		sec == "## Summary" && quiet($0) { next }
		# inside the (python ...) bracket the renderer wrote, joined by "; ": one bracket, not two in a row (B13
		# round 11 M27, M52; the rule of G9 and J23/J40). Every renderer writes "setup prompt vN (python X, exe)".
		cp && index($0, "- agentsync: ") == 1 && match($0, /, setup prompt v[0-9]+ \(/) && substr($0, RSTART + 16, RLENGTH - 18) != compat && !index($0, "the installed agentsync, which renders this report") {
			$0 = substr($0, 1, RSTART + RLENGTH - 1) "the installed agentsync, which renders this report; the checkout\047s installer, which stopped this copy, is for setup prompt v" compat ", and the fresh copy\047s step 2 reinstalls agentsync; " substr($0, RSTART + RLENGTH)
		}
		cp && c && sec == "## Doctor" && old($0) { $0 = substr($0, 1, index($0, " (fix: ") - 1) " (expected: " w ")" }
		# a renderer from before B13 round 15 S26 writes "N check(s):" and "N expected warn(s) end in", a newer one
		# "N checks:" and "1 expected warn ends in" / "N expected warns end in": both are read, and a merged
		# count keeps its form (in the newer form, noun and verb go by the sum)
		cp && c && sec == "## Doctor" && $0 ~ /^[0-9]+ checks?(\(s\))?: .*\. Only the lines that are not ok:/ {
			# "its fix" for one warn (B13 round 17 U31); a merged count is 2 or more, so "their"
			if ($0 ~ /not ok:$/) $0 = $0 " " c " expected warn" (c == 1 ? " ends in (expected: <why>) instead of its fix" : "s end in (expected: <why>) instead of their fix") ": nothing for this setup to do."
			else if (match($0, / [0-9]+ expected warn\(s\) end in /)) {
				e = substr($0, RSTART + 1) + 0
				$0 = substr($0, 1, RSTART) (e + c) substr($0, RSTART + 1 + length(e ""))
			} else if (match($0, / [0-9]+ expected warns? ends? in /)) {
				e = substr($0, RSTART + 1) + 0
				$0 = substr($0, 1, RSTART) (e + c) " expected warns end in " substr($0, RSTART + RLENGTH)
				sub(/ instead of its fix:/, " instead of their fix:", $0)
			}
		}
		cp && (c || st) && sec == "## Doctor" && $0 ~ /^[0-9]+ checks?(\(s\))?: .*\. Only the lines that are not ok:/ && !index($0, b) && ends($0, "nothing for this setup to do.") {
			$0 = substr($0, 1, length($0) - 1) b "."
		}
		c && sec == "## Summary" && index($0, "- doctor: ") == 1 { $0 = doctor_line($0, cp ? w : w1) }
		s1note != "" && sec == "## Summary" && index($0, "- doctor: ") == 1 && nmv { $0 = stop_bucket($0) }
		# the stop of step 1 (B13 round 16 T11): its cause, as a renderer from N31 on writes it, and the turns
		# v10\047s "ran (step 1))" reads "ran, in step 1" since 92b5fd13 (B13 round 17 U81)
		s1cause != "" && sec == "## Summary" && index($0, "- **outcome: failed at step 1** (computed: no install run: only install.sh --list-folders ran") == 1 && !index($0, "; it stopped: ") && ends($0, ")") {
			if (ends($0, " ran (step 1))")) $0 = substr($0, 1, length($0) - 13) "ran, in step 1; it stopped: " s1cause ")"
			else $0 = substr($0, 1, length($0) - 1) "; it stopped: " s1cause ")"
		}
		s1turns != "" && sec == "## Summary" && index($0, "- expected turns: ") == 1 && !index($0, "list stopped: ") && !index($0, "command stopped before ") { $0 = s1turns }
		# the stop is the Items list\047s first entry, as _sign_in_stop_item puts it (B13 round 17 U26, U107)
		s1item != "" && sec == "## Summary" && $0 ~ /^Items that were not one command \(attempt [0-9]+\):$/ { itm = FNR }
		# a stopped copy of an attempt that ran no install.sh: its first sync is an earlier run\047s (B13 round 17 U28)
		cp && norun && sec == "## Summary" && $0 ~ /^- (first )?sync: [a-z]+ in [0-9]+s[^(]* \(install\.log\)/ && !index($0, "before this attempt") && (j = index($0, " (install.log)")) {
			$0 = substr($0, 1, j - 1) " (install.log: " (ENVIRON["OR_FSSTART"] != "" ? "the install run of " ENVIRON["OR_FSSTART"] : "an earlier install run") ", before this attempt; this attempt ran none)" substr($0, j + 14)
			if ((j = index($0, " (install.log)"))) $0 = substr($0, 1, j - 1) " (install.log: that earlier run)" substr($0, j + 14)
			else if ((j = index($0, " (install.out)"))) $0 = substr($0, 1, j - 1) " (install.out: an earlier run\047s; this attempt ran none)" substr($0, j + 14)
		}
		# a step 1 list stop: an older build says no run has a first-sync step, while install.log holds an
		# earlier one; the renderer words a list-only attempt (setup_report M22) and tags the install.out counts
		# as that earlier run\047s (B13 round 18 V16)
		s1note != "" && sec == "## Summary" && $0 ~ /^- (first )?sync: no install\.sh run with a first-sync step \(install\.log\)/ {
			j = index($0, "no install.sh run with a first-sync step (install.log)")
			$0 = substr($0, 1, j - 1) "this attempt ran only install.sh --list-folders, no first sync (install.log)" substr($0, j + 54)
			if ((j = index($0, " (install.out")) && !index($0, "before this attempt")) $0 = substr($0, 1, j - 1) " (install.out: " (ENVIRON["OR_FSSTART"] != "" ? "the install run of " ENVIRON["OR_FSSTART"] : "an earlier run\047s") ", before this attempt" substr($0, j + 13)
		}
		# a git stop (step1_cmd_stop_facts) after an install run with a first-sync step: the older build reads only
		# this attempt\047s runs (none, or a --list-folders one) and words it as if install.log had none; only when
		# install.log holds one (OR_FSSTART), whose start is before the error, which no install run follows (B13
		# round 19 W21). Not "ran no install.sh": the attempt can hold a --list-folders run.
		s1cmd && sec == "## Summary" && ENVIRON["OR_FSSTART"] != "" && $0 ~ /^- (first )?sync: no install\.sh run with a first-sync step \(install\.log\)/ {
			tagged = index($0, "before this attempt")
			j = index($0, "no install.sh run with a first-sync step (install.log)")
			$0 = substr($0, 1, j - 1) "this attempt ran no first sync (install.log: the install run of " ENVIRON["OR_FSSTART"] ", before this attempt)" substr($0, j + 54)
			if (!tagged && (j = index($0, " (install.out"))) $0 = substr($0, 1, j - 1) " (install.out: the install run of " ENVIRON["OR_FSSTART"] ", before this attempt" substr($0, j + 13)
		}
		# a git stop (step1_cmd_stop_facts; B13 round 18 V30): the older build warns that an attempt with no end
		# line may be stale, where the renderer splits off the session that logged the stop and notes why it
		# has no end line; with that line alone in its attempt, the time is one timestamp
		s1cmd && s1fline != "" && sec == "## Summary" && $0 ~ /^- WARNING: attempt [0-9]+ has no "end \| finished" line/ {
			$0 = "- note: the step 1 error (F" s1fline ") was logged by another session after the earlier session\047s `install.sh --log-start`, which has no end line: this session stopped before `install.sh --log-start`, so `install.sh --report-only` had no attempt to close."
		}
		s1cmd && s1one && sec == "## Summary" && index($0, "- time: ") == 1 { $0 = "- time: session: one timestamp (" s1at "), no duration" }
		# after a finished attempt (B13 round 18 V31): F<n> stopped the run, so it is an item, not friction
		s1mv && sec == "## Summary" && index($0, "- agent friction: ") == 1 && ends($0, "; none of these changes the outcome by itself") {
			$0 = substr($0, 1, length($0) - 45) "; F" s1fline " stopped the run (the outcome says so)"
		}
		s1mv && sec == "## Summary" && $0 ~ /^Agent friction \(attempt [0-9]+; it does not change the outcome by itself\):$/ { blank = 0; afdrop = 1; next }
		afdrop && $0 == mvline { blank = 0; afdrop = 0; next }
		s1what != "" && sec == "## Summary" { $0 = cut_item($0) }
		# v10\047s friction words, in both modes (B13 round 17 U27): the rerun the header names, the attempt\047s bits
		# joined by "; ", "line N" for one line, and no "finished" tail beside a count of it
		!det && !fence && (j = index($0, "(a line written after that is not in this report: run setup-report again)")) {
			$0 = substr($0, 1, j - 1) "(a line written after that is not in this report: run `" inst " --report-only` again)" substr($0, j + 73)
		}
		!det && !fence && sec == "## Agent friction log" && $0 ~ /^- attempt [0-9]+ \(lines? [0-9]+(-[0-9]+)?; / {
			# its own names (ax, ak, ay): k is the count of left-out install.out lines (B13 round 18 V22)
			if (match($0, /^- attempt [0-9]+ \(lines [0-9]+-[0-9]+;/)) {
				ax = substr($0, 1, RLENGTH); ak = index(ax, "("); ay = substr(ax, ak + 7, length(ax) - ak - 7); j = index(ay, "-")
				if (substr(ay, 1, j - 1) == substr(ay, j + 1)) $0 = substr(ax, 1, ak) "line " substr(ay, 1, j - 1) ";" substr($0, RLENGTH + 1)
			}
			if (!index($0, "; prompt ") && (j = index($0, ", prompt "))) {
				$0 = substr($0, 1, j - 1) "; prompt " substr($0, j + 9)
				if ((ak = index(substr($0, j), ", agent "))) $0 = substr($0, 1, j + ak - 2) "; agent " substr($0, j + ak + 7)
			}
			if (ends($0, "; finished") && index(substr($0, 1, length($0) - 10), " finished")) $0 = substr($0, 1, length($0) - 10)
		}
		# the last install run\047s commit, and the fetch\047s date, from install.log and FETCH_HEAD (B13 round 17 U27)
		!det && !fence && match($0, /; last install\.sh run: commit -($|[;) ])/) {
			# its own name (lt): k is the count of left-out install.out lines (B13 round 18 review)
			lt = substr($0, RSTART + RLENGTH - 1, 1); lt = (lt == "-" ? "" : lt)
			$0 = substr($0, 1, RSTART - 1) (ENVIRON["OR_LASTRUN"] != "" ? "; last install.sh run: " ENVIRON["OR_LASTRUN"] : "") lt substr($0, RSTART + RLENGTH)
		}
		!det && !fence && ENVIRON["OR_FETCH"] != "" && (j = index($0, "(as of the checkout\047s last fetch)")) {
			$0 = substr($0, 1, j) ENVIRON["OR_FETCH"] substr($0, j + 32)
		}
		cp && sec == "## Doctor" && (j = index($0, heldold)) > 0 { $0 = substr($0, 1, j - 1) heldnow substr($0, j + length(heldold)) }
		cp && (index($0, "Send it: ") == 1 || index($0, u) == 1) { next }
		cp && (index($0, u) > 0 || index($0, "bring back: ") == 1) { if (shown++) next; $0 = gone }
		cp && (j = index($0, r)) > 0 { $0 = substr($0, 1, j - 1) "A" substr($0, j + length(r)) }
		sec == "## Installer" && index($0, "|") == 1 { tb[++tn] = $0; next }
		tn { table() }
		$0 == "" { blank++; next }
		itm && FNR > itm {
			itm = 0
			if (s1item ~ /(^|[^A-Za-z0-9_])F[0-9]+([^A-Za-z0-9_]|$)/) fcited = 1 # printed here, not below (W15)
			if ($0 == "- none") $0 = s1item
			else if (index($0, "- ") == 1 && !index($0, "the folder list stopped")) { for (; blank > 0; blank--) print ""; print s1item }
		}
		# the one-warn note an older renderer writes itself says "their fix" (e5c5b0e2, dd5d55d5): in either
		# mode and whatever c is, it takes the current words, "its" (B13 round 17 U31; round 18 V27)
		{
			px = (det || fence ? $0 : plural_all($0))
			if (!det && !fence && sec == "## Doctor" && px ~ /^[0-9]+ checks?(\(s\))?: /) sub(/ 1 expected warn ends in \(expected: <why>\) instead of their fix:/, " 1 expected warn ends in (expected: <why>) instead of its fix:", px)
			# an older renderer says the F<n> ids in the Summary are these numbers whether or not the Summary
			# cites one; the renderer only where it does (setup_report: \bF\d+\b), read in the rewritten
			# Summary, which comes first (B13 round 19 W15)
			if (!det && !fence && sec == "## Summary" && px ~ /(^|[^A-Za-z0-9_])F[0-9]+([^A-Za-z0-9_]|$)/) fcited = 1
			if (!det && !fence && sec == "## Agent friction log" && !fcited && px == "Each line is shown with its line number: the F<n> ids in the Summary are these numbers.") px = "Each line is shown with its line number."
			for (; blank > 0; blank--) print ""
			print px
		}
		END { if (tn) table(); if (cp) { print ""; print "Not for sending: see the note at the top of this report." } }' "$REPORT_PATH" "$REPORT_PATH"
}
# The step 1 stop older_report_rewrite step1 words (B13 round 16 T11): when install.log's last run is this
# attempt's step 1 list (args=--list-folders, begun at or after friction_attempt_begin) that failed on a sign-in
# or with no folders (note=not-signed-in or no-folders, its exit 3), S1_NOTE, S1_APPS (its apps= tokens),
# S1_CAUSE (setup_report._list_stop_words) and S1_TURNS (the expected-turns line, as
# setup_report._expected_turns_line words it for a list that stopped, with "none": an unattended stop has no
# sign-in answer to count); else all empty, and 1 is returned. As files, with no agentsync: the installed one is
# older, which is why this runs. S1_ITEM is the Items list's entry for the stop, as setup_report._sign_in_stop_item
# words it, only under its guards: no install run in the attempt, every list run of it failed, and no error line
# in the attempt (a friction stop, which is the Items list's first entry then; B13 round 17 U26, U107).
step1_stop_facts() {
	local facts begin who="" names t n=0 of missing gone app label clause why
	S1_NOTE="" S1_APPS="" S1_CAUSE="" S1_TURNS="" S1_ITEM="" S1_CMD="" S1_AT="" S1_FLINE="" S1_ONE=""
	[ -f "$SETUP_LOG" ] || return 1
	begin="$(utc_stamp "$(friction_attempt_begin)")"
	facts="$(BEGIN_AT="$begin" LC_ALL=C awk '
		match($0, / run=[^ ]+ /) { r = substr($0, RSTART + 5, RLENGTH - 6); if (r != last) { last = r; st = ""; fl = "" } }
		index($0, " start install.sh ") { st = $1 " " ($0 ~ / args=--list-folders$/) }
		index($0, " step=list-folders ") { fl = $0 }
		END {
			split(st, a, " ")
			if (a[2] != 1 || fl !~ / result=failed / || fl !~ / note=(not-signed-in|no-folders)( |$)/) exit
			b = ENVIRON["BEGIN_AT"]; if (b != "" && a[1] < b) exit
			n = fl; sub(/.* note=/, "", n); sub(/ .*/, "", n)
			printf "%s|%s|%s|%s|%s\n", n, v(fl, "apps"), v(fl, "missing"), v(fl, "synced"), (other ? "" : "item")
		}
		# the attempt\047s other runs: an install run, or a list that did not fail, keeps the item out
		index($0, " start install.sh ") && (ENVIRON["BEGIN_AT"] == "" || $1 >= ENVIRON["BEGIN_AT"]) && $0 !~ / args=--list-folders$/ { other = 1 }
		index($0, " step=list-folders ") && (ENVIRON["BEGIN_AT"] == "" || $1 >= ENVIRON["BEGIN_AT"]) && $0 !~ / result=failed( |$)/ { other = 1 }
		# a list that ended on the Allow click (exit 4) makes the attempt that click\047s, as _list_stopped reads it
		index($0, " step=list-folders ") && (ENVIRON["BEGIN_AT"] == "" || $1 >= ENVIRON["BEGIN_AT"]) && $0 ~ / note=(denied|tcc-pending)( |$)/ { other = 1 }
		function v(l, k,    x) { if (!match(l, " " k "=[^ ]*")) return ""; x = substr(l, RSTART + length(k) + 2, RLENGTH - length(k) - 2); return x }
	' "$SETUP_LOG" 2>/dev/null)" || return 1
	[ -n "$facts" ] || return 1
	IFS='|' read -r S1_NOTE S1_APPS missing of item <<<"$facts"
	case "$missing" in *[!0-9]* | "") missing="" ;; esac
	case "$of" in *[!0-9]* | "") of="" ;; esac
	if [ "$S1_NOTE" = no-folders ]; then
		S1_CAUSE="no folders are synced yet"
	else
		# the apps by setup_report._SIGN_IN_APPS, a token none of them dropped; joined "A, B and C"
		names=()
		IFS=',' read -r -a t <<<"$S1_APPS"
		# each token from its app's name, as list_folders writes it: an app name in lower case on a line of this
		# script would be a report word (report_word), and an organisation of that name would go unreplaced
		for t in ${t[@]+"${t[@]}"}; do
			t="$(printf '%s' "$t" | LC_ALL=C tr '[:upper:]' '[:lower:]')" app=""
			[ "$t" != other ] || app="another sync app"
			for label in OneDrive "Google Drive" Dropbox Box Egnyte; do
				[ "$(printf '%s' "${label// /}" | LC_ALL=C tr '[:upper:]' '[:lower:]')" != "$t" ] || app="$label"
			done
			[ -n "$app" ] || continue
			t="$app"
			case " ${names[*]+"${names[*]}"} " in *" $t "*) continue ;; esac
			names+=("$t")
		done
		n=${#names[@]}
		if [ "$n" -eq 0 ]; then
			if [ "${of:-0}" -gt 0 ]; then S1_CAUSE="a sync app is not signed in"; else S1_CAUSE="OneDrive is not signed in"; fi
		else
			who="${names[$((n - 1))]}"
			[ "$n" -eq 1 ] || who="$(IFS=,; printf '%s' "${names[*]:0:$((n - 1))}" | LC_ALL=C sed 's/,/, /g') and $who"
			if [ "$n" -eq 1 ]; then S1_CAUSE="$who is not signed in"; else S1_CAUSE="$who are not signed in"; fi
		fi
	fi
	if [ "$S1_NOTE" = not-signed-in ] && [ "${of:-0}" -gt 0 ]; then
		if [ -z "$missing" ] && [ "$of" -gt 1 ]; then
			gone="some of the $of folders this Mac already syncs are"
		elif [ -n "$missing" ] && [ "$missing" -gt 0 ] && [ "$missing" -lt "$of" ]; then
			gone="$missing of the $of folders this Mac already syncs"
			if [ "$missing" -eq 1 ]; then gone="$gone is"; else gone="$gone are"; fi
		elif [ "$of" -eq 1 ]; then
			gone="the 1 folder this Mac already syncs is"
		else
			gone="the $of folders this Mac already syncs are"
		fi
		if [ "$of" -eq 1 ]; then t="it is"; else t="they are"; fi
		S1_TURNS="- expected turns: none (step 1's list stopped: $S1_CAUSE, so $gone missing; $t kept: no folder question and no Allow click)"
		clause="$S1_CAUSE, so $gone missing" why="an unattended run goes to the report"
	else
		S1_TURNS="- expected turns: none (step 1's list stopped: $S1_CAUSE, so no folder was listed: no folder question and no Allow click)"
		clause="$S1_CAUSE, so no folder was listed" why="on this stop step 1 goes to the report"
	fi
	# step 1: the shim fires only for the current prompt's step 1 list, as S1_TURNS says
	[ "$item" != item ] || friction_attempt_errors || S1_ITEM="- install.sh · step 1 · the folder list stopped: $clause (not logged: $why)"
	return 0
}
# Whether the friction log's last attempt (after its last Attempt: line) has an error line: a friction stop
friction_attempt_errors() {
	local f
	f="$(friction_file)"
	[ -f "$f" ] || return 1
	LC_ALL=C awk '/^Attempt:/ { e = 0; next } /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[^|]*[|][^|]*[|] error [|]/ { e = 1 }
		END { exit !e }' "$f" 2>/dev/null
}
# The step 1 stop of step 1's own command, before install.sh --log-start (a git or Xcode stop), when the
# installed build is older (B13 round 17 U34): friction.md's last event line, an end line aside, is a step 1
# error stating "prompt vN, <agent>:" (track()\047s stated()), and install.log has no install run (a --list-folders
# one aside) started at or after it. S1_TURNS is the renderer's line for that attempt (_expected_turns_line,
# unstarted); S1_NOTE and S1_APPS stay empty, so no FAIL moves to the sign-in stop, and S1_CAUSE too: the
# renderer names no cause beside "step 1 logged an error". The attempt split and its note are not shimmed.
# S1_CMD=1 marks this stop for older_report_rewrite (B13 round 18 V30, V31), with S1_AT, the error line's stamp,
# S1_FLINE, its line number (the report's F<n>), and S1_ONE=1 when it is the only event line, an end line aside,
# of the attempt parse_friction splits off: counted from the last Attempt: line, a stated step 1 error that
# opens a header-less attempt (track()), or one past the gap after an end line, each with the unbroken run of
# step 1 lines dated at or after the open attempt's end line, which late_run moves with it (the renderer's
# attempt is then that line alone: one timestamp; B13 round 19 W74).
step1_cmd_stop_facts() {
	local f at
	S1_NOTE="" S1_APPS="" S1_CAUSE="" S1_TURNS="" S1_ITEM="" S1_CMD="" S1_AT="" S1_FLINE="" S1_ONE=""
	f="$(friction_file)"
	[ -f "$f" ] && [ -f "$SETUP_LOG" ] || return 1
	at="$(LC_ALL=C awk -v gap="$NEW_SESSION_GAP" -v s1="$STEP1_ERROR_RE" "$FRICTION_AWK"'
		{ q = track($0) }
		/^Attempt:/ { ev = (endat != "" ? run : 0); endat = ""; run = 0 }
		NH { NH = 0; last = ""; ev = 0; endat = ""; run = 0; next }
		/^Attempt:/ || /^[-* ]*(Prompt|Agent|Outcome|Run):/ { last = ""; next }
		/^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z [|]/ {
			if (/[|] end [|] finished[[:space:]]*$/) { endat = substr($0, 1, 20); run = 0; next }
			if (q || (endat != "" && $0 ~ s1 && secs($0) - secs(endat) > gap)) { if (!q) restate($0); ev = (endat != "" ? run : 0); endat = ""; run = 0 }
			if (endat != "" && $0 ~ /^[0-9TZ:-]+ [|] step 1 [|]/ && substr($0, 1, 20) >= endat) run++; else run = 0
			last = $0; nr = NR; ev++
		}
		END { if (last ~ s1 && stated(last)) print substr(last, 1, 20) "|" nr "|" (ev == 1) }' "$f" 2>/dev/null)"
	[ -n "$at" ] || return 1
	S1_FLINE="${at#*|}" S1_ONE="${at##*|}"
	S1_FLINE="${S1_FLINE%|*}" at="${at%%|*}"
	AT="$at" LC_ALL=C awk 'index($0, " start install.sh ") && $1 >= ENVIRON["AT"] && $0 !~ / args=--list-folders$/ { found = 1 }
		END { exit found }' "$SETUP_LOG" 2>/dev/null || return 1
	S1_TURNS="- expected turns: none (step 1's command stopped before install.sh --log-start, before the folder list: no folder question and no Allow click)"
	S1_CMD=1 S1_AT="$at"
	[ "$S1_ONE" = 1 ] || S1_ONE=""
	# a --list-folders run started at or after the error that ended rc=0 resolves it, as the renderer's
	# stopping_error reads it: nothing stopped the run, so the error stays friction and the time is not one stamp
	AT="$at" LC_ALL=C awk 'match($0, / run=[^ ]+ /) { r = substr($0, RSTART + 5, RLENGTH - 6) }
		index($0, " start install.sh ") && $1 >= ENVIRON["AT"] && $0 ~ / args=--list-folders$/ { list[r] = 1 }
		(r in list) && index($0, " end rc=0 ") { found = 1 }
		END { exit !found }' "$SETUP_LOG" 2>/dev/null && S1_FLINE="" S1_ONE=""
	# any run started at or after it is in the renderer's attempt too, with its start and end: no one timestamp
	AT="$at" LC_ALL=C awk 'index($0, " start install.sh ") && $1 >= ENVIRON["AT"] { found = 1 } END { exit !found }' \
		"$SETUP_LOG" 2>/dev/null && S1_ONE=""
	return 0
}
# The report of this attempt's own step 1 stop rendered by the installed agentsync, which is older than this
# checkout (its install.commit warn says so: nothing reinstalled it), in this checkout's words where they are
# the renderer's only (older_report_rewrite step1; B13 round 16 T11). A report already in them passes unchanged.
step1_report_note() {
	local tmp
	LC_ALL=C grep -q '^\[warn\] install\.commit .*, but the checkout at ' "$REPORT_PATH" 2>/dev/null || return 0
	step1_stop_facts || step1_cmd_stop_facts || return 0
	tmp="$(mktemp "${REPORT_PATH%/*}/.step1-report.XXXXXX" 2>/dev/null)" || return 1
	if older_report_rewrite step1 >"$tmp" && [ -s "$tmp" ]; then
		if cmp -s "$tmp" "$REPORT_PATH"; then rm -f "$tmp"; return 0; fi
		chmod 600 "$tmp" 2>/dev/null || true
		mv -f "$tmp" "$REPORT_PATH" || { rm -f "$tmp"; return 1; }
	else
		rm -f "$tmp"
		return 1
	fi
}
stopped_report_note() { # KEPT-REPORT-PATH
	local tmp shown="$1" note="> This report is of an attempt started by a copy of the setup prompt that does not match this installer: do not send it or bring it back."
	case "$shown" in "$HOME"/*) shown='~'"${shown#"$HOME"}" ;; *) shown="${shown##*/}" ;; esac
	[ ! -f "$1" ] || note="$note The report of the last attempt that was not stopped is $shown."
	tmp="$(mktemp "${REPORT_PATH%/*}/.stopped-report.XXXXXX" 2>/dev/null)" || return 1
	if {
		printf '%s\n\n' "$note" &&
			older_report_rewrite copy |
			stopped_run_labels
	} >"$tmp"; then
		mv -f "$tmp" "$REPORT_PATH" || { # no temp file stays beside the report (B13 round 11 M84)
			rm -f "$tmp"
			return 1
		}
	else
		rm -f "$tmp"
		return 1
	fi
}

# A listing the step-5 status warned was held gets no answer from a run that ends before its closing status after
# its sync ran: step 6's exit 78, 80 or another code, or a --confirm-install-agent wait that failed after a sync that
# exited 0. Its note says the end of this run answers a sync that succeeds (B13 round 15 S11); this end says, per
# listing, that it does not and which command does (B13 round 15 S64). Nothing more runs. A dry run, a skipped sync
# and a busy lock's (75) listed nothing for this run, and a closing status (LOOP_OUT) answers through
# cleared_listings. Called from on_exit, before the report and the issue-link line, so install.out holds it and that
# link stays the last line before NEXT; defined before on_exit can run, and SYNC_GO and SYNC_RC are unset before
# step 6 (set -u).
unanswered_listings() {
	[ "${SYNC_GO:-0}" -eq 1 ] && [ "$DRY_RUN" -eq 0 ] && [ "$REPORT_ONLY" -eq 0 ] || return 0
	[ -n "${SYNC_RC:-}" ] && [ "$SYNC_RC" != 75 ] && [ -z "$LOOP_OUT" ] || return 0
	[ -n "$DOCTOR_LOG" ] && [ -s "$DOCTOR_LOG" ] || return 0
	LC_ALL=C awk '/^\[warn\] heartbeat\./ && index($0, "deletions held") { print substr($2, 11) }' "$DOCTOR_LOG" |
		LC_ALL=C sort -u | while IFS= read -r id; do
		say "heartbeat.$id: this run ended before its closing status, so it does not say whether its sync cleared it: $AGENTSYNC status does"
	done
}
on_exit() {
	local rc=$? suffix="" link secs stopped=0 kept="$REPORT_PATH" back="${REPORT_PATH%/*}/bring-back.md" tell ver held wrote up uthem
	trap - EXIT
	# A signal from here on (a coding tool's timeout during the report) is recorded, and the run finishes:
	# on_signal's exit came with the EXIT trap gone, so no report step line, bring-back.md, issue link or NEXT
	# was written, and install.log kept the provisional "end rc=0" of a run that exited 143 (B13 round 21 Y16).
	# The report child is not killed: bash runs a trap only once the $( ) returns, and with_timeout caps it.
	EXITING=1
	trap 'SIG_RC=129' HUP
	trap 'SIG_RC=130' INT
	trap 'SIG_RC=143' TERM
	set +e
	[ "$PARSED" -eq 1 ] || exit "$rc"
	# add-source's held stderr, when a signal ended the run inside the call: bash runs the trap once the
	# pipeline ends, and on_signal's exit came before the loop printed it, so its error was lost and the file
	# was left in TMPDIR. Printed here, before the report reads install.out for it (B13 round 19 W39).
	[ -z "${ERRF:-}" ] || { [ ! -s "$ERRF" ] || cat "$ERRF" >&2; rm -f "$ERRF"; }
	[ -z "${MARK:-}" ] || rm -f "$MARK"
	[ -z "$TICKER" ] || kill "$TICKER" 2>/dev/null
	[ -z "$WAIT_TICKER" ] || kill "$WAIT_TICKER" 2>/dev/null
	[ -z "$WAIT_WATCHDOG" ] || kill "$WAIT_WATCHDOG" 2>/dev/null
	[ -z "${AGENTSYNC_PINS:-}" ] || rm -f "$AGENTSYNC_PINS" # a run stopped during step 2's reinstall
	step_end failed "$rc"
	if [ "$STARTED" -eq 1 ]; then
		END_AT="$(utc_now)"
		secs=$SECONDS
		log_at "$END_AT" "end rc=$rc seconds=$secs" # provisional: the report reads a finished run
		END_LINE="$END_AT run=$RUN_ID end rc=$rc seconds=$secs"
	fi
	unanswered_listings
	if [ "$REPORT" -eq 1 ]; then
		# A copy of the prompt that is not this installer's started the attempt (--log-start said so and
		# stopped it). Its own text may still end at this report: the NEXT says what to do instead, and
		# nothing invites sending it: the report goes to its own file (stopped_report_note), no bring-back
		# file is written (the last one stays as it is), and no "bring back:" or issue link line is printed.
		# So for any other run that writes a report after such an attempt, with no --log-start since (a plain
		# install.sh the stopped copy's Doctor asked for): it was reported "fully one command" with an issue
		# link titled for the old copy (B13 round 20 X19). Its own NEXT stays, since its work is real and a
		# later stand-alone run is legitimate; one clause says why nothing is to be sent (stopped=2).
		if tell="$(friction_attempt_stopped)"; then
			stopped=1
			[ "$REPORT_ONLY" -eq 1 ] || stopped=2
			REPORT_PATH="$SETUP_DIR/stopped-copy-report.md"
		fi
		write_report "$rc"
		case $? in
		0)
			suffix=" [setup report: $REPORT_PATH]"
			if [ "$stopped" -eq 2 ]; then
				stopped_report_note "$kept" || warn "could not mark $REPORT_PATH as not to be sent"
				NEXT_MSG="${NEXT_MSG:+$NEXT_MSG; }the last setup attempt was a copy of the prompt this installer stopped at step 1; its report is not to be sent, so paste the current prompt into a new session to report a setup"
			elif [ "$stopped" -eq 1 ]; then
				stopped_report_note "$kept" || warn "could not mark $REPORT_PATH as not to be sent"
				# What to do instead is the fix the stop logged: copy the prompt again, or, for a copy newer
				# than a checkout that does not follow main, point the checkout there (B13 round 5 F17, F18).
				# Only a fix this installer writes for a newer copy is repeated; any other text in that column
				# (an older installer's, a hand edit) gets the copy-again sentence. The version this installer
				# is for is said once, of the installer: inside the copy-again sentence's own brackets, or
				# after "this installer" for a newer copy's fix, where a bracket at its end read as part of
				# its last clause and came right before the bring-back one (B13 round 6 G9, G19).
				ver=""
				case "$tell" in
				"copy the prompt again from $PROMPT_SOURCE (\"Set up on a new Mac: one prompt\") and paste it into a new session"[:\;]* | \
					"point this Mac's checkout at main of $CANON_REPO "*)
					ver=", which is for setup prompt v$SETUP_PROMPT_COMPAT"
					;;
				*) tell="copy the prompt again from $PROMPT_SOURCE (\"Set up on a new Mac: one prompt\"; this installer is for setup prompt v$SETUP_PROMPT_COMPAT) and paste it into a new session" ;;
				esac
				# R18: bring-back.md is named only where one exists, and not as "finished": a failed
				# attempt's report writes it too. Its bracket stands after "bring it back", which it is about:
				# after the fix it read as part of the fix's last clause (B13 round 7 H18). No colon inside
				# it, as the fix's colon follows.
				held=""
				[ ! -f "$back" ] || held=" ($back was not touched and still holds the report of the last attempt that was not stopped)"
				NEXT_MSG="this report is of an attempt started by a copy of the setup prompt that does not match this installer$ver, so do not send it or bring it back$held: $tell"
			else
				# A stopped copy's report from before is stale from here (it names this file as the last
				# report that was not stopped), and nothing this attempt prints names it (B13 round 4 Q20).
				# A dry run (2) and a report not written (*) leave it.
				rm -f "$SETUP_DIR/stopped-copy-report.md"
				# an older installed agentsync rendered a step 1 stop: before bring-back.md embeds the report
				[ "$REPORT_BY" != agentsync ] || step1_report_note || warn "could not reword $REPORT_PATH in this checkout's words"
				if write_bring_back; then wrote=1; else wrote=0; warn "could not write $back${BACK_WHY:+: $BACK_WHY}"; fi
				link="$(report_issue_link)"
				if [ -n "$link" ]; then
					[ "$REPORT_ONLY" -eq 0 ] ||
						NEXT_MSG="review $back and copy that one file back privately, or paste the setup report into the issue the link above opens (nothing is sent for you)"
					say "$ISSUE_LINK_PREFIX$link" # the last line before NEXT (step 3 of the setup prompt names it)
				fi
				# not "that one file" while it lacks a kept patch: the remedy first, with a link or without one (B13
				# round 14 Q20, Q29); the command by name, since an agent relays this line where "this command" is
				# none (B13 round 18 V17, V79, as round 15 S23 for section 3's line); still no file name
				if [ "$REPORT_ONLY" -eq 1 ] && [ "$wrote" -eq 1 ] && [ "$BACK_UNREAD" -gt 0 ]; then
					up="$BACK_UNREAD kept patch files" uthem="them readable (chmod u+r on the files"
					[ "$BACK_UNREAD" != 1 ] || { up="1 kept patch file" uthem="it readable (chmod u+r on the file"; }
					NEXT_MSG="$back does not hold $up this account cannot read (see the warning above): make $uthem in $SETUP_DIR/local-work) and run $LOCAL_WORK_AGAIN again, then review $back and copy that one file back privately${link:+, or paste the setup report into the issue the link above opens} (nothing is sent for you)"
				fi
				# Local-work branches with commits of their own and no patch (only counted when no patch is unread):
				# only section 3 said how to add them (B13 round 21 Y23). Advice beside the review, never a step
				# before it, as the cut-off clause below: a branch at an older commit of origin/main counts while
				# format-patch writes nothing for it, so it comes back on every run. No branch is named.
				if [ "$REPORT_ONLY" -eq 1 ] && [ "$wrote" -eq 1 ] && [ "$BACK_BRANCHES" -gt 0 ]; then
					up="$BACK_BRANCHES local-work branches"
					[ "$BACK_BRANCHES" != 1 ] || up="1 local-work branch"
					NEXT_MSG="$back does not carry the commits of $up (its section 3 says how to add them as patches, then run $LOCAL_WORK_AGAIN again; a branch that writes nothing holds no new commit); either way, review $back and copy that one file back privately${link:+, or paste the setup report into the issue the link above opens} (nothing is sent for you)"
				fi
				# B13 round 19 W3: a part the report's time limit cut off (its doctor on a busy Mac) asked for the
				# report again only inside the report. The NEXT the agent relays says so too, as advice beside the
				# review, never a step before it: a doctor that is always slow would hold the bring-back or loop an
				# agent. The marker is setup_report._IDLE_AGAIN, which every cut-off part writes, and so does an
				# evidence line whose only gap is a state file a sync held (B13 round 20 X37): the words name no
				# cause, the Summary does. An older installed agentsync without it adds nothing. Before the remedy
				# for an unreadable patch, which keeps its words. Read only at the start of the Summary's doctor and
				# evidence lines, the two that write it: the Summary and the friction log both repeat an agent's
				# notes word for word, and a note that quoted the advice faked a cut-off (B13 round 21 Y66)
				if [ "$REPORT_ONLY" -eq 1 ] && [ "$wrote" -eq 1 ] && [ -f "$REPORT_PATH" ] &&
					LC_ALL=C grep -qE '^- (doctor|evidence): .*write the report again when this Mac is idle' "$REPORT_PATH"; then
					NEXT_MSG="part of the report was cut off or not measured (its Summary says which, and why): if this Mac was busy, run $LOCAL_WORK_AGAIN again once it is idle; either way, $NEXT_MSG"
				fi
				# A bring-back.md this run could not write is not named as the file to review, with a link or
				# without one: the files it would hold are, the ones not redacted as private, and one an earlier
				# attempt wrote is said to be that, not this one (B13 round 12 N71). It is left as it is.
				if [ "$REPORT_ONLY" -eq 1 ] && [ "$wrote" -eq 0 ]; then
					held=""
					# inside the one bracket, and the path once (B13 round 13 O14: two brackets side by side)
					[ ! -f "$back" ] || held="; the one there now is an earlier attempt's file, not this one's, so do not send it"
					NEXT_MSG="$back could not be written (see the warning above$held): copy $REPORT_PATH back privately${link:+, or paste it into the issue the link above opens}, and if $SETUP_DIR/fix-request.md or $SETUP_DIR/local-work holds anything, copy it back privately too (it is not redacted); or fix the cause and run this command again (nothing is sent for you)"
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
	# a signal while this ran: its code is the run's, with on_signal's error, and the NEXT says to run again
	# first, before what the run had said (a second signal after on_signal's keeps that NEXT)
	if [ -n "$SIG_RC" ]; then
		rc="$SIG_RC"
		case "$NEXT_MSG" in
		"this run was stopped "*) ;;
		*)
			LAST_ERROR="install.sh was stopped by a signal ($(rc_meaning "$rc")) before it finished"
			printf 'error: %s\n' "$LAST_ERROR" >&2
			NEXT_MSG="this run was stopped while it wrote its report ($(rc_meaning "$rc")); it is safe to re-run: ${RERUN:-$SELF$RERUN_ARGS}${NEXT_MSG:+; the run had said: $NEXT_MSG}"
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
	[ -z "${HELD:-}" ] || rm -f "$HELD" # step 4 held line file, when the run ended inside the loop (J13)
	[ -z "$NEXT_MSG" ] || say "NEXT: $NEXT_MSG$suffix"
	stop_capture
	exit "$rc"
}
# The issue link the setup report ends with (its last line, when it starts with $ISSUE_URL), else empty.
# Byte-wise: the shell report can hold a byte that is not UTF-8 (a log line's), where BSD sed stops.
# Empty too for a report that says it is for a private copy only (a "Private only:" line, either writer's,
# at the start of a line): no line, issue link or NEXT, then offers the public issue (B13 round 6 G47, G65).
report_issue_link() {
	local l
	# in the Redaction part only: a friction line that quotes an earlier report's is not this report's flag
	! LC_ALL=C awk '$0 == "## Redaction" { r = 1 } r && index($0, "Private only:") == 1 { f = 1 } END { exit !f }' \
		"$REPORT_PATH" 2>/dev/null || return 0
	l="$(LC_ALL=C sed '/^[[:space:]]*$/d' "$REPORT_PATH" 2>/dev/null | LC_ALL=C tail -1)"
	case "$l" in
	"$ISSUE_URL"*) printf '%s' "$l" ;;
	esac
}

# A signal (a coding tool's timeout sends SIGTERM) still ends through on_exit: the log's end line, the report
# and a NEXT line that says to run the same command again.
on_signal() {
	# on_exit has begun (a signal before it set its own traps): record it, and let it finish
	[ "$EXITING" -eq 0 ] || {
		SIG_RC="$1"
		return 0
	}
	LAST_ERROR="install.sh was stopped by a signal ($(rc_meaning "$1")) before it finished"
	printf 'error: %s\n' "$LAST_ERROR" >&2
	NEXT_MSG="this run was stopped before it finished ($(rc_meaning "$1")); it is safe to re-run: ${RERUN:-$SELF$RERUN_ARGS}"
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
# Whether CloudStorage folder $1 shows provider $2's app signed out, by ops/doctor.py's _missing_source_fix
# rule: 0 signed out (the provider folder gone with no other folder of that app, the name before its first "-",
# beside it; or there and holding no folder or file the list would show, a stale one: hidden entries do not
# count); 2 the provider folder gone while another folder of that app is there (another account: doctor names
# the sign-in with that account, or the path); 1 signed in; 3 a SharePoint library folder
# (OneDrive-SharedLibraries-<org>) gone, or there and showing nothing, beside its organization's OneDrive-<org>:
# a library not synced on this Mac, never a sign-in (doctor's B13 round 12 N53 branch and round 13 O52; B13
# round 13 O41, O48, O57). The organization is read as org_of reads it: the text after the prefix, trimmed, and
# never empty or Personal. A symlink, dangling or not, is there, as lexists reads it, and a provider folder
# this account cannot read counts as signed in, as doctor reads it (B13 round 12 N61). Arguments: CS PROVIDER.
signed_out() {
	local f app="${2%%-*}" org=""
	case "$2" in
	OneDrive-SharedLibraries-*)
		org="${2#OneDrive-SharedLibraries-}"
		org="${org#"${org%%[![:space:]]*}"}"
		org="${org%"${org##*[![:space:]]}"}"
		case "$org" in
		"" | Personal) org="" ;;
		*) [ -e "$1/OneDrive-$org" ] || [ -L "$1/OneDrive-$org" ] || org="" ;;
		esac
		;;
	esac
	if [ -e "$1/$2" ] || [ -L "$1/$2" ]; then
		[ -r "$1/$2" ] && [ -x "$1/$2" ] || return 1
		for f in "$1/$2"/*; do
			[ ! -e "$f" ] && [ ! -L "$f" ] || return 1
		done
		[ -z "$org" ] || return 3
		return 0
	fi
	[ -z "$org" ] || return 3
	for f in "$1/$app" "$1/$app"-*; do
		[ ! -e "$f" ] && [ ! -L "$f" ] || return 2
	done
	return 0
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
# uv, where step 1 finds it: on PATH, else where its installer puts it; empty when there is none.
find_uv() {
	if command -v uv >/dev/null 2>&1; then
		command -v uv
	elif [ -x "$HOME/.local/bin/uv" ]; then
		printf '%s' "$HOME/.local/bin/uv"
	fi
}
# The installed agentsync's own interpreter (uv's tool environment: the path step 2 names TOOL_PY), when
# there is one. `uv tool dir` only prints a path.
tool_python() {
	local uv dir="$HOME/.local/share/uv/tools"
	uv="$(find_uv)"
	[ -z "$uv" ] || dir="$("$uv" tool dir 2>/dev/null || printf '%s' "$dir")"
	[ ! -x "$dir/agentsync/bin/python" ] || printf '%s' "$dir/agentsync/bin/python"
}
# Whether the last install's stamp names this clean checkout at the same commit. A commit of "-" (unread) or
# one ending in -dirty never matches.
stamp_is_current() { # STAMP AGENTSYNC SOURCE COMMIT
	case "$4" in "" | - | *-dirty) return 1 ;; esac
	[ -x "$2" ] && [ -f "$1" ] && [ "$(cat "$1" 2>/dev/null)" = "commit=$4 source=$3" ]
}
# Whether the tool environment ENV holds pypandoc_binary's pypandoc as pip installed it: every file its RECORD
# lists under pypandoc/ with a hash (the pandoc binary and the Python that finds it) is there, of the size and
# sha256 RECORD gives (urlsafe base64, unpadded), and the pandoc is executable. Nothing is run: a fresh copy's
# first start can take a minute (B13 round 16 T54).
bundled_pandoc_intact() { # ENV
	local r rec="" site path want size f got
	for r in "$1"/lib/python3*/site-packages/pypandoc_binary-*.dist-info/RECORD; do
		[ ! -f "$r" ] || rec="$r"
	done
	[ -n "$rec" ] && LC_ALL=C grep -q '^pypandoc/files/pandoc,sha256=' "$rec" || return 1
	site="${rec%/*/RECORD}"
	[ -x "$site/pypandoc/files/pandoc" ] || return 1
	while IFS=, read -r path want size; do
		f="$site/$path"
		if [ ! -f "$f" ] || [ "$(LC_ALL=C wc -c <"$f" | LC_ALL=C tr -d ' ')" != "$size" ]; then
			return 1
		fi
		got="$(LC_ALL=C /usr/bin/openssl dgst -sha256 -binary "$f" | LC_ALL=C /usr/bin/openssl base64 -A |
			LC_ALL=C tr '+/' '-_' | LC_ALL=C tr -d '=')" || return 1
		[ "sha256=$got" = "$want" ] || return 1
	done < <(LC_ALL=C grep '^pypandoc/[^,]*,sha256=' "$rec")
}
# Whether step 2 skips the reinstall: the stamp is current and the bundled pandoc is whole. Doctor's fix for a
# bundled pandoc that is missing or changed is a re-run of install.sh, which reinstalls under the sync locks
# and the TLS setting below, and at uv.lock's versions (B13 round 16 T54, T59, T60, T65); a bare `uv tool
# upgrade` had none of those and moved every dependency. One test for step 2 and for step 1's word on what step
# 2 will do, so the two cannot disagree (B13 round 11 M37).
install_is_current() { # STAMP AGENTSYNC SOURCE COMMIT
	stamp_is_current "$@" && bundled_pandoc_intact "${1%/*}"
}
# The same, for a step 2 run from this checkout with uv's tool folders as step 2 reads them (step 1 runs
# before step 2 sets UV, TOOL_DIR and COMMIT); prints the commit when it holds.
checkout_is_installed() {
	local uv c bin="$HOME/.local/bin" dir="$HOME/.local/share/uv/tools"
	uv="$(find_uv)"
	[ -n "$uv" ] && [ -e "$repo/.git" ] || return 1
	bin="$("$uv" tool dir --bin 2>/dev/null || printf '%s' "$bin")"
	dir="$("$uv" tool dir 2>/dev/null || printf '%s' "$dir")"
	c="$(checkout_commit "$repo")"
	case "$c" in *" dirty "*) return 1 ;; esac
	install_is_current "$dir/agentsync/.agentsync-install-source" "$bin/agentsync" "$repo" "$c" && printf '%s' "$c"
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
# 2026-09-29 has: step 1 of the setup prompt runs this before step 2 updates the tool. The one exception is
# provider_link_resolved (2026-10-13), whose import is guarded: an older agentsync gets the same single
# readlink written here with the standard library alone (B13 round 7 H7; round 6 imported it bare, so step 1
# marked nothing on every Mac set up before that day), returning the root form H34 gave the function (B13 round
# 8 J38: the copy kept the link text form, and a /tmp link under a /private/tmp home matched no listed folder). A new agentsync name goes in the same way, and
# tests/test_install_oneshot.py pins the list. -I: nothing from the folder this runs in is imported.
synced_folders() { # PYTHON LIST
	with_timeout 10 "$1" -I -c '
import os, sys, unicodedata
from pathlib import Path
from agentsync.config import canonical_source_root, load_config

try:
    from agentsync.paths import provider_link_resolved
except ImportError:  # an agentsync from before 2026-10-13: step 2 has not updated it yet

    def provider_link_resolved(path):
        p = Path(os.path.expanduser(str(path)))
        base = Path(os.path.expanduser("~/Library/CloudStorage"))
        roots = {base}
        try:
            roots.add(base.resolve())
        except (OSError, RuntimeError):
            pass
        for root in sorted(roots):
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if not rel.parts:
                return p
            entry = root / rel.parts[0]
            try:
                target = Path(os.path.normpath(entry.parent / os.readlink(entry)))
            except OSError:
                return p
            # in the form of the root that held p, as paths.provider_link_resolved returns it since H34 (B13
            # round 8 J38): under a home reached through a link, a link written in the other form names the
            # same folder
            return root.joinpath(target.name, *rel.parts[1:]) if target.parent in roots else p
        return p


def key(path):
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", str(path)).casefold()).rstrip("/") + "/"


# A folder stored through a provider entry that links to another provider folder is the folder listed by its
# real name: one readlink of that entry, nothing inside a domain, and the stored path is kept (B13 round 6 G39).
synced = {}
for s in load_config(Path(sys.argv[1])).sources:
    if s.kind.value == "local" and s.state.value == "live" and s.path is not None:
        synced.setdefault(key(provider_link_resolved(s.path)), str(s.path))
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
# The sources the manifest still mirrors (present file rows) whose [[source]] block is gone from the config,
# read by the interpreter $1 as cycle.vanished_sources reads them, which step 2's status FAILs on and every sync
# refuses (B13 round 16 T6, T30): prints how many match no folder of the file $2 (the list's unmarked folders,
# one per line), then each listed folder that one --source-local adds back: the folder the manifest recorded as
# that source's root (meta scope_root:<id>, "<run>:<path>", which every sync since 2026-10-06 writes), whose
# derive_source_id (the configured ids taken, as add-source derives it) is that id. A name alone never matches:
# a folder of the same name elsewhere is another folder. A source the inbox step 2 adds back is none.
# A reader of its own, after synced_folders and allowed to fail: the list and its marks do not depend on it.
# The manifest is opened read-only (sqlite3 mode=ro, never agentsync's Manifest, which migrates it), with the
# same query as setup_report._live_refusal, and a manifest it cannot read is none. Only names every agentsync
# since 2026-09-29 has, PRESENT_STATES behind a guard. Prints no source id: the report names none either.
vanished_folders() { # PYTHON LIST
	with_timeout 5 "$1" -I -c '
import os, sqlite3, sys
from pathlib import Path
from agentsync.config import canonical_source_root, derive_source_id, load_config
from agentsync.paths import expand

try:
    from agentsync.model import PRESENT_STATES

    states = [st.value for st in PRESENT_STATES]
except ImportError:
    states = ["live", "dataless", "quarantined", "refused"]
config = load_config(Path(sys.argv[1]))
configured = {s.id for s in config.sources}
db = expand(config.state_paths.db)
vanished, roots = set(), {}
if db.is_file():
    try:
        conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=1.0)
        marks = ", ".join("?" for _ in states)
        try:
            rows = conn.execute(
                "SELECT source_id, COUNT(*) FROM items WHERE is_dir = 0 "
                f"AND state IN ({marks}) GROUP BY source_id",
                states,
            ).fetchall()
            vanished = {str(sid) for sid, n in rows if str(sid) not in configured and n}
            try:
                roots = dict(
                    conn.execute("SELECT key, value FROM meta WHERE key LIKE ?", ("scope_root:%",)).fetchall()
                )
            except sqlite3.Error:
                roots = {}
        finally:
            conn.close()
    except sqlite3.Error:
        vanished = set()
inbox = canonical_source_root(expand(config.docs_repo).parent / "inbox")
if not any(s.path is not None and s.path == inbox for s in config.sources):
    vanished.discard(derive_source_id(inbox, configured))
with open(sys.argv[2], "rb") as fh:
    listed = [os.fsdecode(line) for line in fh.read().split(b"\n") if line]
back = {}
for p in listed:
    folder = canonical_source_root(Path(p))
    sid = derive_source_id(folder, configured)
    root = str(roots.get("scope_root:" + sid, "")).partition(":")[2]
    if sid in vanished and sid not in back and root == str(folder):
        back[sid] = p
out = [str(len(vanished) - len(back)), *back.values()]
sys.stdout.buffer.write(os.fsencode("\n".join(out) + "\n"))
' "$CONFIG" "$2"
}
# What step 2's config step will stop on in the config $CONFIG, read by the interpreter $1 (B13 round 17 U12):
# prints "policy" when policy.load_policy refuses it (a policy.toml beside it, a [policy] value), "governance"
# when governance.load_governance refuses its [governance] table, and "state" when the manifest is a file that
# is no database or a damaged one (doctor.manifest_unreadable, the read-only probe init's exit 65 uses), one
# word a line. load_config alone, which the readers above use, reads neither policy.toml nor [governance], so
# the list said "(none is needed)" over a config step 2 then stopped on. Also "remote" when that table loads
# and the docs repo has a git remote it does not allow (governance.remote_policy_findings, the check
# cli._refuse_remotes makes before init and add-source exit 1; never after "governance", which stops first),
# and "state-table" when the schema reads but a table's or an index's first page does not (SELECT 1 from each
# table NOT INDEXED and through each index INDEXED BY, read only: status reads the schema and some tables, so
# such damage stopped step 2 with nothing said at step 1; damage deeper in a table still shows only at a read
# of it) (B13 round 18 V9, V21, V36). A plain SELECT 1 from a table is answered from its smallest covering
# index, so it read neither the table's own pages nor any other index, and the real schema's items page said
# "(none is needed)" (round 19 W1, W10); an error other than a damaged page (an older SQLite refusing an
# INDEXED BY) skips that one b-tree. This checkout's manifest_unreadable reads every table's (round 19 W41) and
# every index's (round 20 X50) root page the same way, so an installed build of it says "state", and
# "state-table" comes only from an older one, whose damage step 2's reinstalled build stops on at its config
# step (round 20 X72). "state-owner" when another account owns the state folder, the manifest, its -wal or its
# -shm, and "state-access" when this account lacks read and write on one (and search on the folder), as doctor's
# state_dir lines judge them: the manifest probes, whose open fails as CANTOPEN, said nothing, and step 2 then
# stopped at its config step (round 20 X9, X29); then no other read of the file is made. Fixed words only,
# never the error's text, which can quote a host or a label (a finding names the remote's URL). A reader of
# its own, after synced_folders and allowed to fail: a name an older agentsync lacks reads nothing for that
# check (manifest_unreadable falls back to the same read-only schema read; the remote check catches every
# exception, a git call's included, so it cannot take the other words with it). -I: nothing from the folder
# this runs in is imported.
config_errors() { # PYTHON
	with_timeout 5 "$1" -I -c '
import os, sqlite3, sys
from pathlib import Path
from agentsync.config import ConfigError, load_config
from agentsync.paths import expand

config = load_config(Path(sys.argv[1]))
words = []
try:
    from agentsync.policy import load_policy
except ImportError:
    load_policy = None
if load_policy is not None:
    try:
        load_policy(config)
    except ConfigError:
        words.append("policy")
try:
    from agentsync.governance import load_governance
except ImportError:
    load_governance = None
if load_governance is not None:
    try:
        gov = load_governance(config.config_path)
    except ConfigError:
        words.append("governance")
    else:
        try:
            from agentsync.governance import remote_policy_findings

            repo = expand(config.docs_repo)
            if (repo / ".git").exists() and remote_policy_findings(repo, gov):
                words.append("remote")
        except Exception:
            pass
db = expand(config.state_paths.db)
# a state folder, manifest, -wal or -shm this account cannot open: another account owns one, or it lacks read
# and write (and search, for the folder) for this one; its probes below could not open it either
access = None
for p in (db.parent, db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
    try:
        st = p.stat()
    except PermissionError:
        access = "state-access"
        break
    except OSError:
        continue
    if st.st_uid != os.getuid():
        access = "state-owner"
        break
    if not os.access(p, os.R_OK | os.W_OK | (os.X_OK if p == db.parent else 0)):
        access = "state-access"
        break
if access:
    words.append(access)
elif db.is_file():
    try:
        from agentsync.ops.doctor import manifest_unreadable
    except ImportError:

        def manifest_unreadable(path):
            try:
                conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=0.5)
                try:
                    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
                finally:
                    conn.close()
            except sqlite3.OperationalError:
                return None
            except sqlite3.DatabaseError as exc:
                return exc
            return None

    if manifest_unreadable(db) is not None:
        words.append("state")
    else:
        try:
            conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=0.5)
            try:
                # every table and index (a view or trigger has no b-tree); upper case, as the shell report
                # reads a lower-case word in the strings of this program as one it prints
                trees = conn.execute("SELECT type, name, TBL_NAME FROM sqlite_master WHERE ROOTPAGE > 0").fetchall()
                for kind, name, table in trees:
                    q = "\"%s\"" % name.replace("\"", "\"\"")
                    if kind == "table":
                        q += " NOT INDEXED"
                    else:
                        q = "\"%s\" INDEXED BY %s" % (table.replace("\"", "\"\""), q)
                    try:
                        conn.execute("SELECT 1 FROM %s LIMIT 1" % q).fetchall()
                    except sqlite3.DatabaseError as exc:
                        if getattr(exc, "sqlite_errorcode", 0) & 0xFF in (11, 26):
                            words.append("state-table")
                            break
            finally:
                conn.close()
        except sqlite3.Error:
            pass
sys.stdout.write("".join(w + "\n" for w in words))
' "$CONFIG"
}
# --list-folders: the folders 1-2 levels inside each ~/Library/CloudStorage/<provider>, names only (find reads
# directory entries and their metadata; no file is opened, so nothing is downloaded). Sets NEXT_MSG; returns
# 0 listed, 3 none or a synced folder under CloudStorage whose app is signed out (B13 round 11 M4, round 12
# N41, N46, N61), 4 denied or still asking.
list_folders() {
	local cs="$HOME/Library/CloudStorage" tmp e label rc total providers=0 denied="" asking="" term max=200
	local deadline=$((SECONDS + LIST_TOTAL)) t py why lacks marks synced="" unlisted="" unread="" kept="" built
	local line missing=0 gone=0 gonepaths="" others=0 other="" libs=0 libpaths="" apps="" tokens="" tok="" stuck=0 more=""
	local sn them ungone first one mirror pages vback=0 vother=0 vpaths="" vtail="" vlog="" cerr="" ctail="" clog="" stail=""
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
		# The warning names the cause in fixed words (B13 round 7 H14, H16, H27): an agentsync that is installed
		# and cannot run the reader is not a missing one. Of what the reader wrote, only the name of an import an
		# older agentsync lacks is used: an error line can quote the config.
		why="no agentsync is installed"
		marks="no-tool"
		if [ -n "$py" ]; then
			marks="failed"
			rc=0
			synced_folders "$py" "$tmp/shown" >"$tmp/marked" 2>"$tmp/rerr" || rc=$?
			lacks="$(LC_ALL=C sed -n "s/.*cannot import name '\([A-Za-z_][A-Za-z0-9_]*\)'.*/\1/p" "$tmp/rerr" | LC_ALL=C tail -1)"
			if [ "$rc" -eq 0 ]; then
				synced="$(sed -n 1p "$tmp/marked")"
				unlisted="$(sed -n 2p "$tmp/marked")"
			elif [ "$rc" -eq 124 ] || [ "$rc" -eq 143 ]; then
				why="the reader took over 10 s"
				marks="timeout"
			elif [ -n "$lacks" ]; then
				why="the installed agentsync is older than this installer: it has no $lacks"
				marks="older:$lacks"
			else
				# the class of the error, never its text
				lacks="$(LC_ALL=C tail -1 "$tmp/rerr" | LC_ALL=C sed -En 's/^([A-Za-z_]+[.])*([A-Za-z]+(Error|Exception)):.*$/\2/p')"
				marks="failed${lacks:+:$lacks}"
				# words that blame neither the config nor agentsync: a SyntaxError in agentsync's own module and a
				# config that is not UTF-8 (a UnicodeDecodeError, not a ConfigError) both end here (B13 round 10 L37)
				why="the installed agentsync failed while reading it${lacks:+ and raised $lacks}"
			fi
		fi
		case "$synced/$unlisted" in
		/* | */ | *[!0-9/]*)
			synced=""
			# logged as synced=unreadable, never in $synced, which stays a number; marks= says why in a fixed
			# word, with at most the name an older agentsync lacks or the class of its error (B13 round 7 H8, H16)
			unread=" synced=unreadable marks=$marks"
			warn "could not read which folders $CONFIG already syncs ($why), so none is marked below"
			;;
		esac
		# A source still mirrored whose [[source]] block is gone (B13 round 16 T6, T30): step 2's status FAILs on
		# it, so the NEXT says which, never "none is needed". Read only once the folders were read and with no
		# provider denied or still asking (that exit 4 comes first); a reader that fails changes nothing.
		if [ -n "$synced" ] && [ -z "$denied$asking" ]; then
			tail -n +3 "$tmp/marked" | LC_ALL=C grep -v '^\[' >"$tmp/unmarked" || true
			if vanished_folders "$py" "$tmp/unmarked" >"$tmp/vanished" 2>/dev/null; then
				vother="$(sed -n 1p "$tmp/vanished")"
				case "$vother" in
				'' | *[!0-9]*) vother=0 ;;
				*)
					while IFS= read -r line; do
						vback=$((vback + 1))
						vpaths="${vpaths:+$vpaths; }$line"
					done < <(sed -n '2,$p' "$tmp/vanished")
					;;
				esac
			fi
			# what step 2's config step stops on, in fixed words; a reader that fails says nothing (U12)
			if config_errors "$py" >"$tmp/cerr" 2>/dev/null; then
				cerr="$(LC_ALL=C grep -E '^(policy|governance|state|remote|state-table|state-access|state-owner)$' "$tmp/cerr" | LC_ALL=C tr '\n' ',' || true)"
				cerr="${cerr%,}"
			fi
		fi
	fi
	# Every synced folder is printed with its mark, so the reader can name each: the ones the list does not
	# reach (deeper than it goes, outside ~/Library/CloudStorage, past its cap) come first, then the list. A list
	# that shows no folder (signed out, an empty provider folder, the only provider denied) is not promised: the
	# line is the plain one (B13 round 11 M9). One folder is "1 folder", kept by a re-run as "it" (B13 round 14
	# Q39, Q44); the prefix up to the colon, which the prompt quotes and the report reads, is as before.
	sn="$synced folders" them=them
	[ "$synced" != 1 ] || { sn="1 folder" them=it; }
	if [ "${synced:-0}" -gt 0 ]; then
		if [ "$unlisted" -eq 0 ] || [ ! -s "$tmp/shown" ]; then
			say "already synced on this Mac: $sn (marked $SYNCED_MARK below)"
		else
			# a synced folder gone from disk is never listed, so it is first too, but not "outside the list": it is
			# counted apart, with the same guard as the gone check below, which reads no provider folder while one
			# was denied or is still asking (B13 round 15 S10). Only a folder under $cs: the ones the gone check
			# decides, with a NEXT clause and gone=; one missing elsewhere stays outside the list (B13 round 16 T46)
			ungone=0
			if [ -z "$denied$asking" ]; then
				while IFS= read -r line; do
					line="${line#"$SYNCED_MARK "}"
					case "$line" in "$cs/"*) ;; *) continue ;; esac
					[ -e "$line" ] || ungone=$((ungone + 1))
				done < <(sed -n "3,$((2 + unlisted))p" "$tmp/marked")
			fi
			if [ "$ungone" -eq 0 ]; then
				first="the $unlisted outside the list"
			elif [ "$ungone" -eq "$unlisted" ]; then
				first="the $unlisted not on disk"
			else
				first="the $((unlisted - ungone)) outside the list and the $ungone not on disk"
			fi
			say "already synced on this Mac: $sn (marked $SYNCED_MARK below: first $first, then the list)"
		fi
		tail -n +3 "$tmp/marked"
		# A synced folder under CloudStorage that is not on disk, decided by its provider folder as doctor decides
		# it: that folder gone with no other folder of its app beside it, or there and listing nothing, means the
		# app is signed out (or not set up yet), so this list asks for the sign-in (B13 round 11 M4). A provider
		# folder that lists other folders is signed in: the folder was renamed, moved or deleted there, or is not
		# synced on this Mac, and a sign-in would never bring it back, so step 2's doctor names it (B13 round 12
		# N41, N61). The causes are the wait's and the fixes doctor's: put it back, sync it in its app, or fix the
		# path (B13 round 16 T72); only causes go in the parenthesis, which the prompt's step 1 reads.
		# Not read while a provider was denied or is still asking: its exit 4 comes first, and a read of the
		# provider folder that the list just timed out on could wait on the same prompt, past LIST_TOTAL.
		[ -n "$denied$asking" ] || while IFS= read -r line; do
			case "$line" in "$SYNCED_MARK $cs/"*) ;; *) continue ;; esac
			line="${line#"$SYNCED_MARK "}"
			[ ! -e "$line" ] || continue
			e="${line#"$cs/"}"
			e="${e%%/*}"
			rc=0
			signed_out "$cs" "$e" || rc=$?
			case "$rc" in
			0)
				missing=$((missing + 1))
				label="$(provider_label "$e")"
				case ", $apps," in *", $label,"*) ;; *) apps="${apps:+$apps, }$label" ;; esac
				# the log names the app by a fixed word, never the folder's own text (B13 round 13 O5): its label
				# in lower case with no space (googledrive). Not written out here: a lower-case word on a line of
				# this script that is not a comment is a report word, which report_word would keep in clear
				case "$label" in
				OneDrive | "Google Drive" | Dropbox | Box | Egnyte) tok="$(printf '%s' "${label// /}" | LC_ALL=C tr '[:upper:]' '[:lower:]')" ;;
				*) tok=other ;;
				esac
				case ",$tokens," in *",$tok,"*) ;; *) tokens="${tokens:+$tokens,}$tok" ;; esac
				;;
			2)
				others=$((others + 1))
				other="${other:+$other; }$line"
				;;
			3)
				libs=$((libs + 1))
				libpaths="${libpaths:+$libpaths; }$line"
				;;
			*)
				gone=$((gone + 1))
				gonepaths="${gonepaths:+$gonepaths; }$line"
				;;
			esac
		done <"$tmp/marked"
	else
		cat "$tmp/shown"
	fi
	[ "$total" -le "$max" ] || say "($((total - max)) more)"
	rm -rf "$tmp"
	# The words for those sources: a listed folder one --source-local adds back under the same id, else only
	# the block put back; never "on disk" or "not signed in", the words the prompt's other step 1 cases read.
	# Logged as a count, never an id (B13 round 16 T6, T30). The command names the folder as every NEXT does
	# (shown_arg: a $ or a backtick in a OneDrive name must not expand when pasted; B13 round 17 U88); the
	# listing before it is the list's own text.
	if [ "$vback" -eq 1 ]; then
		vtail="; 1 folder in the list above is still mirrored but has no [[source]] block in $CONFIG: $vpaths. Step 2's status fails on it until it is added again (one --source-local $(shown_arg "$vpaths")) or its [[source]] block is put back, as it was or retired"
	elif [ "$vback" -gt 1 ]; then
		vtail="; $vback folders in the list above are still mirrored but have no [[source]] block in $CONFIG: $vpaths. Step 2's status fails on each until it is added again (one --source-local for each) or its [[source]] block is put back, as it was or retired"
	fi
	if [ "$vother" -eq 1 ]; then
		vtail="$vtail; 1 source that no folder in the list above adds back is still mirrored but has no [[source]] block in $CONFIG. Step 2's status fails on it until its [[source]] block is put back, as it was or retired"
	elif [ "$vother" -gt 1 ]; then
		vtail="$vtail; $vother sources that no folder in the list above adds back are still mirrored but have no [[source]] block in $CONFIG. Step 2's status fails on each until its [[source]] block is put back, as it was or retired"
	fi
	[ $((vback + vother)) -eq 0 ] || vlog=" vanished=$((vback + vother))"
	# A config step 2's config step will stop on (U12): what it is, in fixed words, and whose. An installed
	# agentsync older than this checkout's is not the one step 2 runs (it reinstalls first), so then only if this
	# checkout's rejects it too, as the marks=failed clause below words it.
	if [ -n "$cerr" ]; then
		case ",$cerr," in *",policy,"*) ctail="an invalid [policy] in policy.toml" ;; esac
		case ",$cerr," in *",governance,"*) ctail="${ctail:+$ctail and }an invalid [governance] value in $CONFIG" ;; esac
		# "state-table" is a damaged table or index root page an installed build's probe did not read: this
		# checkout's reads each one (round 19 W41, round 20 X50), so step 2, which reinstalls it, stops at its
		# config step as for "state". The words "at its status or its first sync" were false for an index: a
		# status and a sync with nothing new read none, and step 2 passed (round 20 X1, X25, X71, X72).
		case ",$cerr," in *",state,"* | *",state-table,"*) ctail="${ctail:+$ctail and }agentsync's state file, which cannot be read" ;; esac
		case ",$cerr," in *",remote,"*) ctail="${ctail:+$ctail and }a git remote on the docs repo that [governance] does not allow" ;; esac
		# A state file this account cannot open is no damage, and its stop is the config step's whatever build
		# step 2 installs (it cannot open the file either), so these clauses have no reinstall hedge. Another
		# account's file or folder needs a sudo chown, the person's; this account's own needs a chmod, which
		# the agent runs as it fixes any error step 2 stops on (round 20 X9, X26, X29).
		stail=""
		case ",$cerr," in *",state-owner,"*) stail="; step 2 stops at its config step on agentsync's state file, which another account owns, so this one cannot open it (not damage): fixing that is the person's (a sudo chown; the agent runs no sudo), and ~/.local/bin/agentsync status prints it on its state_dir lines" ;; esac
		case ",$cerr," in *",state-access,"*) stail="; step 2 stops at its config step on agentsync's state file, which this account cannot open (a mode, not damage): the chmod that fixes it is the agent's to run, and ~/.local/bin/agentsync status prints it on its state_dir lines" ;; esac
		git_ro_resolve
		if [ -z "$ctail" ]; then
			:
		elif checkout_is_installed >/dev/null; then
			ctail="; step 2 stops at its config step on $ctail: fixing that is the person's, and ~/.local/bin/agentsync status prints the error"
		else
			ctail="; step 2 reinstalls agentsync first, and if this checkout's agentsync rejects it too, step 2 stops at its config step on $ctail: fixing that is the person's, and ~/.local/bin/agentsync status prints the error"
		fi
		ctail="$ctail$stail"
		clog=" config_err=$cerr"
	fi
	term="$(terminal_app)"
	term="this terminal app${term:+ ($term)}"
	# A config whose folders could not be read is still there: the NEXTs that ask for a folder or a sign-in
	# say so as information, never a count, since none was read (B13 round 8 J17)
	kept=""
	case "$unread" in
	"") ;;
	*" marks=failed"*)
		# A config the installed agentsync failed on: step 2 reinstalls agentsync, which may read it (an older one
		# that rejects a newer key) or may stop at its config step, where neither "keeps" nor "only adds" holds
		# (B13 round 10 L11). status prints the error itself. The clause opens as the others do, the words the
		# prompt's step 1 exception reads. When the installed build is already this checkout's, step 2 does
		# not reinstall it, so the config itself is at fault (B13 round 11 M37).
		lacks="${unread##* marks=failed}"
		lacks="${lacks#:}"
		kept="; this Mac has a config ($CONFIG) whose folders could not be read: the installed agentsync failed while reading it${lacks:+ (it raised $lacks)}, and ~/.local/bin/agentsync status prints why."
		git_ro_resolve
		if built="$(checkout_is_installed)"; then
			stuck=1
			kept="$kept The installed agentsync is already this checkout's build (commit $built), so step 2 (run from this checkout) does not reinstall it: step 2 stops at its config step, and nothing is kept or added until the person fixes the config"
		else
			kept="$kept Step 2 reinstalls agentsync first, and if that version cannot load it either, step 2 stops at its config step and nothing is kept or added until the config is fixed"
		fi
		;;
	*)
		kept="; this Mac has a config ($CONFIG) whose folders could not be read: if it was set up before, $SELF with no --source-local keeps whatever that config syncs, and each --source-local only adds"
		case "$unread" in *" marks=timeout") kept="$kept; or re-run $SELF --list-folders to mark them" ;; esac
		;;
	esac
	rc=0
	if [ -n "$denied" ]; then
		rc=4
		NEXT_MSG="$term was denied access to files managed by $denied: allow it in System Settings > Privacy & Security > Files and Folders (turn on $denied under $term; a click, not a command), then re-run: $RERUN"
		step_end failed "$rc" "denied${synced:+ synced=$synced}$unread"
	elif [ -n "$asking" ]; then
		rc=4
		NEXT_MSG="macOS is asking whether $term may access files managed by $asking: click Allow (or turn it on in System Settings > Privacy & Security > Files and Folders), then re-run: $RERUN"
		step_end failed "$rc" "tcc-pending${synced:+ synced=$synced}$unread"
	elif [ "$missing" -gt 0 ]; then
		# Not "a re-run keeps them": step 2 would stop on each missing folder's [FAIL] lines. A Mac whose synced
		# folders are all there (one syncing only ~/Documents/notes too) keeps exit 0 below (B13 round 11 M4).
		# The app is the one each missing folder's provider folder names, not always OneDrive (B13 round 12 N41).
		rc=3
		# several apps joined as the report joins them, "A, B and C" (B13 round 15 S40); the plural is read off
		# the comma list first, since "A and B" holds no comma
		case "$apps" in *", "*) more="are not signed in on this Mac, so this Mac is" apps="${apps%, *} and ${apps##*, }" ;; *) more="is not signed in on this Mac, so it is" ;; esac
		NEXT_MSG="$apps $more missing $missing of the folders this Mac already syncs: sign in to $apps (or let it finish setting up), then re-run: $RERUN; the folders marked $SYNCED_MARK are kept"
		# which apps, and how many folders are missing, beside how many this Mac syncs: the readers named
		# OneDrive and counted every synced folder as missing (B13 round 13 O5)
		step_end failed "$rc" "not-signed-in apps=$tokens missing=$missing synced=$synced"
	elif [ "${synced:-0}" -gt 0 ]; then # nothing to choose: a run with no folder keeps them (step 4)
		# Before the two exits 3: with nothing listed this Mac is still set up, and the same run keeps it.
		if [ "$providers" -eq 0 ]; then
			NEXT_MSG="this Mac already syncs $sn, and a re-run keeps $them: run $SELF (no folder to add is listed: OneDrive is not signed in on this Mac)"
		elif [ "$total" -eq 0 ]; then
			NEXT_MSG="this Mac already syncs $sn, and a re-run keeps $them: run $SELF (no folder to add is listed: no folders are synced yet in $cs)"
		else
			NEXT_MSG="this Mac already syncs $sn, and a re-run keeps $them: run $SELF, with one --source-local \"<folder>\" for each unmarked folder to add from the list above"
			# none is needed, unless a source still mirrored has no block (T6, T30) or step 2 will stop on the
			# config (U12)
			[ -n "$vtail$ctail" ] || NEXT_MSG="$NEXT_MSG (none is needed)"
		fi
		# A folder gone while its app is signed in: no sign-in (a re-run of this list would stop the same way),
		# and step 2 still runs, so its doctor names the folder and its fix and the report is written. The clause
		# says so, so the keep is not read as a sync (B13 round 12 N41, N61). Only step 2's first sync waits: a sync
		# still mirrors every other source, and deletes nothing of the gone one (B13 round 13 O6).
		# With one synced folder the one gone is the folder itself: "it", and no other folder for a sync to
		# mirror (B13 round 15 S36); the words the prompt keys on, "is (no longer|not) on disk", stay
		one="1 of them" mirror="; a sync still mirrors the folders that are there" pages=", and that folder's pages"
		[ "$synced" != 1 ] || { one=it mirror="" pages="; its pages"; }
		if [ "$gone" -eq 1 ]; then
			NEXT_MSG="$NEXT_MSG; $one is no longer on disk although its app is signed in (renamed, moved or deleted there, or not synced on this Mac): $gonepaths. Step 2's status fails on it and names the fix, so step 2 runs no first sync until the folder is back under that name, synced to this Mac again in its app, or its [[source]] path in $CONFIG is fixed$mirror$pages are kept"
		elif [ "$gone" -gt 1 ]; then
			NEXT_MSG="$NEXT_MSG; $gone of them are no longer on disk although their app is signed in (renamed, moved or deleted there, or not synced on this Mac): $gonepaths. Step 2's status fails on each and names the fix, so step 2 runs no first sync until each folder is back under its name, synced to this Mac again in its app, or its [[source]] path in $CONFIG is fixed; a sync still mirrors the folders that are there, and those folders' pages are kept"
		fi
		# its provider folder gone while another folder of the app is there: not "signed in", as doctor's fix
		# names the sign-in with the account it belongs to, or the path
		if [ "$others" -eq 1 ]; then
			NEXT_MSG="$NEXT_MSG; $one is not on disk because its provider folder is missing while another folder of that app is there: $other. Step 2's status fails on it and names the fix (sign in to that app with the account it belongs to, or fix its [[source]] path in $CONFIG), so step 2 runs no first sync until then$mirror"
		elif [ "$others" -gt 1 ]; then
			NEXT_MSG="$NEXT_MSG; $others of them are not on disk because their provider folders are missing while another folder of each app is there: $other. Step 2's status fails on each and names the fix (sign in to that app with the account each belongs to, or fix its [[source]] path in $CONFIG), so step 2 runs no first sync until then; a sync still mirrors the folders that are there"
		fi
		# a SharePoint library not synced beside its organization's OneDrive folder: doctor names the library's
		# Sync, never a sign-in (B13 round 13 O41, O48, O57; O52 for an empty library folder)
		if [ "$libs" -eq 1 ]; then
			NEXT_MSG="$NEXT_MSG; $one is not on disk because its SharePoint library is not synced on this Mac (its organization's OneDrive folder is there): $libpaths. Step 2's status fails on it and names the fix (sync the SharePoint library to this Mac with Sync on its SharePoint page, or fix its [[source]] path in $CONFIG), so step 2 runs no first sync until then$mirror"
		elif [ "$libs" -gt 1 ]; then
			NEXT_MSG="$NEXT_MSG; $libs of them are not on disk because their SharePoint libraries are not synced on this Mac (each organization's OneDrive folder is there): $libpaths. Step 2's status fails on each and names the fix (sync each SharePoint library to this Mac with Sync on its SharePoint page, or fix its [[source]] path in $CONFIG), so step 2 runs no first sync until then; a sync still mirrors the folders that are there"
		fi
		NEXT_MSG="$NEXT_MSG$vtail$ctail"
		gone=$((gone + others + libs))
		[ "$gone" -eq 0 ] || more=" gone=$gone"
		step_end "done" 0 "listed-$total synced=$synced$more$vlog$clog"
	elif [ "$providers" -eq 0 ]; then
		rc=3
		NEXT_MSG="OneDrive is not signed in on this Mac: sign in to OneDrive, then re-run: $RERUN$kept"
		step_end failed "$rc" "not-signed-in apps=onedrive${synced:+ synced=$synced}$unread"
	elif [ "$total" -eq 0 ]; then
		rc=3
		NEXT_MSG="no folders are synced yet in $cs: sign in to OneDrive (or let it finish setting up), then re-run: $RERUN$kept"
		step_end failed "$rc" "no-folders${synced:+ synced=$synced}$unread"
	elif [ "$stuck" -eq 1 ]; then
		# The installed build is this checkout's and failed on the config: step 2 does not reinstall it, so
		# every --source-local fails at add-source, and the list asks for none (B13 round 12 N29). $kept keeps
		# the words the prompt's step 1 exception reads.
		NEXT_MSG="no folder can be added until the config is fixed: run $SELF with no --source-local, which shows the error$kept"
		step_end "done" 0 "listed-$total$unread"
	else
		NEXT_MSG="choose the folders to sync from the list above (project folders rather than a whole library), then run: $SELF --source-local \"<folder>\" (one --source-local per folder)$vtail$ctail$kept"
		step_end "done" 0 "listed-$total${synced:+ synced=$synced}$unread$vlog$clog"
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
UV="$(find_uv)"
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
# Who started the sync that holds the lock $1, in the words of agentsync's own lines (ops/lock.py _STARTED_BY,
# with its pass worded as _wait_words gives it, the cycle's wait line: B13 round 19 W29, round 20 X32). The lock body ("<pid> <boot_time> <iso> <label>") names no
# starter; the holder sidecar <lock>.json does since B13 round 10 L73, for the body's own pid only. A holder
# from an older build (the usual one during a reinstall: the installed build is the older one), a sidecar of
# another pid or anything unreadable gets the either-or words; an empty body (lockf, a probe, a cycle starting)
# gets none of them, the caller says it (B13 round 19 W31). The environment cannot tell a terminal from cron or an agent's shell, so no line says "terminal".
# The sidecar's host (the Mac's name) is never read. Nothing is imported: sed over the sidecar's fixed layout
# (sorted keys, 2-space indent), LC_ALL=C (B13 round 11 M43, M60).
holder_words() { # LOCK
	local pid="" pass="" spid="" by="" who="the background job, or a sync started another way"
	{ read -r pid _ _ pass; } <"$1" 2>/dev/null || true
	case "$pid" in "" | *[!0-9]*) pid="" ;; esac
	if [ -n "$pid" ] && [ -f "$1.json" ]; then
		spid="$(LC_ALL=C sed -n 's/^  "pid": \([0-9][0-9]*\),\{0,1\}$/\1/p' "$1.json" 2>/dev/null || true)"
		by="$(LC_ALL=C sed -n 's/^  "started_by": "\([a-z]*\)",\{0,1\}$/\1/p' "$1.json" 2>/dev/null || true)"
	fi
	[ "$spid" = "$pid" ] || by=""
	case "$by" in
	launchd) who="the background job" ;;
	other) who="a sync not started by the background job" ;;
	*) pass="" ;;
	esac
	case "$pass" in poll | reconcile | dry_run) who="$who, pass $pass" ;; esac
	printf '%s' "$who"
}
# uv.lock's versions of agentsync's dependencies as a constraints file for `uv tool install` (its path, none
# when the source has no uv.lock or this uv cannot export it, which installs as before). Without them
# `uv tool install --force` resolves every dependency afresh, to the newest version pyproject.toml allows:
# converter libraries no test ran, a pypandoc_binary whose pandoc convert.pandoc does not know, and a re-read of
# the files a converter failed on (B13 round 16 T60, T65; measured with uv 0.12.17). uv keeps them in the
# tool's receipt.
locked_pins() {
	local f
	[ -f "$SOURCE/uv.lock" ] || return 0
	f="$(mktemp)" || return 0
	if "$UV" export --project "$SOURCE" --locked --no-hashes --no-emit-project --no-default-groups --quiet \
		-o "$f" </dev/null >/dev/null 2>&1 && [ -s "$f" ]; then
		printf '%s' "$f"
	else
		rm -f "$f"
	fi
}
AGENTSYNC_PINS=""
uv_tool_install() {
	local rc=0
	[ "$DRY_RUN" -eq 1 ] || AGENTSYNC_PINS="$(locked_pins)"
	uv_tool_install_pinned || rc=$?
	[ -z "$AGENTSYNC_PINS" ] || rm -f "$AGENTSYNC_PINS"
	AGENTSYNC_PINS=""
	return "$rc"
}
uv_tool_install_pinned() {
	local args=(tool install --force --reinstall-package agentsync --python 3.11) locks l
	local held=() busy=0 rc=0 busy_lock="" bare=0 grc
	[ -z "$AGENTSYNC_PINS" ] || args+=(-c "$AGENTSYNC_PINS")
	args+=("$SOURCE")
	AGENTSYNC_NOTE=""
	if [ "$DRY_RUN" -eq 1 ]; then
		run "$UV" "${args[@]}"
		return 0
	fi
	locks="$(sync_locks)"
	if [ -n "$locks" ] && [ -x "$LOCKF" ]; then
		while IFS= read -r l; do
			[ "$busy" -eq 75 ] || { "$LOCKF" -k -n -s -t 0 "$l" /usr/bin/true 2>/dev/null || { busy=$? && busy_lock="$l"; }; }
		done <<<"$locks"
		[ "$busy" -eq 75 ] || busy=0
		# A busy lock with an empty body (only blanks, as ops/lock.py's body.strip() reads it) is held by no
		# sync: install.sh's lockf, a status or doctor check, or a cycle starting, as agentsync's
		# EMPTY_BODY_HOLDER says. Its lines do not say "the other sync", and keep the opening step 2 of the
		# prompt quotes, as cycle.UNRECORDED_LOCK_WAIT_QUIET does (B13 round 18 V40; round 19 W31).
		if [ "$busy" -eq 75 ] && [ -f "$busy_lock" ]; then
			grc=0
			LC_ALL=C grep -q '[^[:space:]]' "$busy_lock" 2>/dev/null || grc=$?
			[ "$grc" -ne 1 ] || bare=1
		fi
		if [ "$busy" -eq 75 ] && [ "$bare" -eq 1 ]; then
			say "agentsync: another sync is running (or install.sh, a status or doctor check, or a cycle starting holds the lock); the reinstall waits for it to be free (at most ${REINSTALL_WAIT}s)"
			ticker "agentsync: reinstalling after the lock is free" &
		elif [ "$busy" -eq 75 ]; then
			# Who started the holder, of the lock that is busy, when its sidecar says (holder_words; B13 round 7
			# H33, round 11 M43, M60). AGENTSYNC_NOTE keeps its machine word below.
			say "agentsync: another sync is running ($(holder_words "$busy_lock")); the reinstall waits for it to end (at most ${REINSTALL_WAIT}s)"
			ticker "agentsync: reinstalling after the other sync ends" &
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
		0)
			if [ "$busy" -ne 75 ]; then :
			elif [ "$bare" -eq 1 ]; then AGENTSYNC_NOTE="waited-for-an-unrecorded-holder"
			else AGENTSYNC_NOTE="waited-for-a-background-sync"; fi
			;;
		64 | 69 | 71 | 73) AGENTSYNC_NOTE="lock-not-taken-rc$rc" ;;
		75)
			if [ "$bare" -eq 1 ]; then AGENTSYNC_NOTE="lock-wait-ran-out-after-${REINSTALL_WAIT}s"
			else AGENTSYNC_NOTE="wait-ran-out-after-${REINSTALL_WAIT}s"; fi
			;;
		esac
		case "$rc" in
		64 | 69 | 71 | 73 | 75) ;;
		*) return "$rc" ;;
		esac
		if [ "$rc" -eq 75 ] && [ "$bare" -eq 1 ]; then
			say "agentsync: the lock is still held after ${REINSTALL_WAIT}s; reinstalling without waiting longer"
		elif [ "$rc" -eq 75 ]; then
			say "agentsync: the other sync is still running after ${REINSTALL_WAIT}s; reinstalling without waiting longer"
		fi
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
if [ "$DRY_RUN" -eq 0 ] && [ "$SOURCE_KIND" = "checkout" ] && install_is_current "$STAMP" "$AGENTSYNC" "$SOURCE" "$COMMIT"; then
	say "agentsync: $AGENTSYNC (current: installed from commit $COMMIT)"
	step_end skipped 0 current
else
	if [ "$DRY_RUN" -eq 0 ] && [ "$SOURCE_KIND" = "checkout" ] && stamp_is_current "$STAMP" "$AGENTSYNC" "$SOURCE" "$COMMIT"; then
		say "agentsync: the bundled pandoc in $TOOL_DIR/agentsync is missing or not the file pypandoc_binary installed; reinstalling"
	fi
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
		# Exit 78 (EX_CONFIG) is a config the module cannot read: the three read the same one, so the other
		# two are not run, their lines say why, and the step is skipped, not done (B13 round 11 M25). An
		# installed build older than that exits 1 there, which is done, as before.
		build_helper() { # MODULE WHAT FALLBACK
			local line rc
			ticker "helpers: building the $2" &
			TICKER=$!
			line="$(AGENTSYNC_CONFIG="$CONFIG" "$TOOL_PY" -I -m "agentsync.convert.$1" </dev/null 2>/dev/null | head -n 1)" && rc=0 || rc=$?
			kill "$TICKER" 2>/dev/null || true
			TICKER=""
			say "${line:-$3}"
			return "$rc"
		}
		# A helper whose build exits non-zero is not built, or built and failing its probe: the step is still
		# done (an optional helper is never a failed step), noted not-built-<names> joined by "+", so
		# install.log and the report's Installer row name it ("not ready: ...") beside the helper's own line,
		# which says why (B13 round 18 V6; round 19 W38).
		nb=""
		if build_helper ocr "OCR helper" "OCR helper: not built (the build did not run)"; then rc=0; else rc=$?; nb="ocr"; fi
		if [ "$rc" -ne 78 ]; then
			build_helper media "media helper" "media helper: not built (the build did not run)" || nb="$nb${nb:++}media"
			build_helper speech "speech helper" "speech: not built (the build did not run)" || nb="$nb${nb:++}speech"
			step_end "done" 0 "${nb:+not-built-$nb}"
		else
			say "media helper: not checked (the config cannot be read)"
			say "speech: not checked (the config cannot be read)"
			step_end skipped 0 config-unreadable
		fi
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
# How many remotes of the docs repo [governance] refuses, as init and add-source read them (cli._refuse_remotes):
# a number only when the agentsync step 2 installed loads the config and its [governance] table and reads the
# repo, within 10 s; empty when it cannot say. -I: nothing from the folder this runs in is imported.
docs_remotes_refused() {
	local n=""
	if [ "$DRY_RUN" -eq 0 ] && [ -x "$TOOL_PY" ] && [ -f "$CONFIG" ]; then
		n="$(with_timeout 10 "$TOOL_PY" -I -c '
import sys
from pathlib import Path
from agentsync import governance
from agentsync.config import load_config
from agentsync.paths import expand

config = load_config(Path(sys.argv[1]))
repo = expand(config.docs_repo)
gov = governance.load_governance(config.config_path)
print(len(governance.remote_policy_findings(repo, gov)) if (repo / ".git").exists() else 0)
' "$CONFIG" 2>/dev/null | head -n 1 || true)"
	fi
	case "$n" in
	*[!0-9]*) n="" ;;
	esac
	printf '%s' "$n"
}
# Whether another account owns agentsync's state folder, manifest, -wal or -shm, as the agentsync step 2
# installed reads the config's state path (doctor's state_dir lines judge the same owners): init and
# add-source then exit 1 ("cannot be opened: Permission denied"), and the fix, a sudo chown, is the person's
# (B13 round 20 X9). A file of this account's own that it cannot open is not: its chmod is the agent's (X26).
# False when it cannot say. -I: nothing from the folder this runs in is imported.
state_owned_elsewhere() {
	local n=""
	if [ "$DRY_RUN" -eq 0 ] && [ -x "$TOOL_PY" ] && [ -f "$CONFIG" ]; then
		n="$(with_timeout 10 "$TOOL_PY" -I -c '
import os, sys
from pathlib import Path
from agentsync.config import load_config
from agentsync.paths import expand

db = expand(load_config(Path(sys.argv[1])).state_paths.db)
owned = 0
for p in (db.parent, db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
    try:
        owned = owned or int(p.stat().st_uid != os.getuid())
    except OSError:
        pass
print(owned)
' "$CONFIG" 2>/dev/null | head -n 1 || true)"
	fi
	[ "$n" = 1 ]
}
# A config agentsync refuses (exit 78, EX_CONFIG: "configuration error: ...") is the person's to fix: a re-run
# fails the same way, and the agent's own mistakes here (a folder that is not there, one inside the docs repo)
# exit 2, which keeps fail's "fix the error above". So the NEXT names the owner and leads with what the agent
# does now (B13 round 11 M23, M39). Whether the config loads is asked of the agentsync step 2 installed, after
# the call (synced_count: a number only when load_config reads it), never read off the exit code or the error:
# init and add-source also exit 78 for a config that loads, a bad [governance] value or policy.toml beside it,
# after they wrote the inbox or the folder's block. That one "loads, but" is refused, as the report's
# Configuration section says, and never "does not load", the words the prompt's step 2 skips the inbox on
# (B13 round 16 T1, T10, T29, T45, T67, T81). Only a config that was there before, and does not load, does not
# load; one this run created that does not load is the generic failure. Exit 65 (EX_DATAERR) is agentsync's
# state file, manifest.sqlite, that is no database or a damaged one: the person's too, since its fix loses
# history the agent must not choose to lose (B13 round 16 T12). The "loads, but" NEXT names policy.toml beside
# the config too, since the error may be that file's and the NEXT never reads it to say which (B13 round 17
# U20). A docs repo with a remote [governance] does not allow is the person's as well: init and add-source exit
# 1 for it (EXIT_FAILED) after the "governance: remote ..." line, whose removal or allowing is theirs, and fail's
# "fix the error above" sent the agent to remove the remote. Exit 1 alone is any failure, so the NEXT is keyed on
# the installed agentsync finding such a remote (docs_remotes_refused), never on the code or the error's words
# (B13 round 17 U2, U16). So is a state folder or file another account owns, which init and add-source exit 1
# on too (state_owned_elsewhere; round 20 X9); this account's own, which they also exit 1 on, keeps fail's "fix
# the error above": the error's fix is a chmod the agent runs, and moving the file aside loses history (X26).
# Every re-run here is $RERUN_ARGS, a folder in double quotes as shown_arg gives it (round 16 T35; round
# 17 U57, U67, U87, U96: the exit-65 line kept %q's $ORIG_ARGS); it still names every folder, since this runs
# before the switch to $BASE_ARGS below.
config_failed() { # MESSAGE (the failed agentsync call's status is $?)
	local rc=$?
	if [ "$rc" -eq 78 ]; then
		if [ -n "$(synced_count)" ]; then
			FAIL_NEXT="the config $CONFIG loads, but agentsync refuses a value in it or in policy.toml beside it (the configuration error above names the file; ~/.local/bin/agentsync status prints it again): fixing it is the person's, not a step for the agent, so show them the error and go on to the report; once it is fixed, the next run is: $SELF$RERUN_ARGS"
		elif [ "$CONFIG_STATE" != "created" ]; then
			FAIL_NEXT="the config $CONFIG does not load with this agentsync (the configuration error above; ~/.local/bin/agentsync status prints it again): fixing it is the person's, not a step for the agent, so show them the error and go on to the report; once it loads, the next run is: $SELF$RERUN_ARGS"
		fi
	fi
	if [ "$rc" -eq 1 ] && [ "$(docs_remotes_refused)" -gt 0 ] 2>/dev/null; then
		FAIL_NEXT="the docs repo has a git remote the [governance] policy does not allow (the governance: line above; ~/.local/bin/agentsync status prints it again): removing it or allowing it in [governance] is the person's, not a step for the agent, so show them the error and go on to the report; once it is settled, the next run is: $SELF$RERUN_ARGS"
	fi
	if [ "$rc" -eq 1 ] && state_owned_elsewhere; then
		FAIL_NEXT="agentsync's state file or its folder is owned by another account, so this one cannot open it (the error above names the file; ~/.local/bin/agentsync status prints the sudo chown that fixes it): fixing its owner (a sudo chown, which the agent does not run) is the person's, not a step for the agent, so show them the error and go on to the report; once it is fixed, the next run is: $SELF$RERUN_ARGS"
	fi
	[ "$rc" -ne 65 ] ||
		FAIL_NEXT="agentsync's state file cannot be read (the error above, with its fix; ~/.local/bin/agentsync status prints it again): fixing it is the person's, not a step for the agent, so show them the error and go on to the report; once it is fixed, the next run is: $SELF$RERUN_ARGS"
	(exit "$rc") || fail "$@"
}
KEPT=0 # the folders synced before this step, and below the ones it added: both in its log line
[ "$CONFIG_STATE" = "created" ] || KEPT="$(synced_count)"
# add-source ends every call on two summary lines, "docs repo ..." and "sources: ..." (cli._ensure_setup).
# With several folders that block came once per folder (field reports 2026-10-06 and 2026-10-07: 16 source
# ids, twice). Call I of N keeps the "docs repo" line only as the first call, the one that can say "created"
# and count the first files written, and the "sources:" line only as the last, the one that lists every
# folder. The first call's "docs repo" line, and its "time machine:" lines, which come between the two, are
# held in the file HELD and printed directly above the last call's "sources:" line: printed where they came,
# they sat between the first and the second folder's lines and read as the first folder's result (B13 round 8
# J13). So is the "added inbox source" line, which the call that creates the inbox prints after its folder's
# block, from any call (B13 round 9 K7). Each line is appended (>>): the file starts empty, each call is an awk
# process of its own, and ">" would empty what an earlier call held. A failed call prints what is held before
# its error, as it was printed before, and the file is emptied
# once printed, so nothing comes twice. With one folder nothing is held or dropped. Every other line passes,
# stderr is not read, and the call's exit status is the pipeline's (pipefail). LC_ALL=C: a byte that is no
# character must not fail the filter. A call that prints "added source " wrote the folder's block, which the
# file MARK records, so a refusal after it is told apart from a call that wrote nothing (B13 round 17 U40).
add_source_filter() { # I N HELD MARK
	LC_ALL=C awk -v i="$1" -v n="$2" -v held="$3" -v mark="$4" '
		/^added source / { print "added" >> mark; close(mark) }
		n > 1 && /^added inbox source / { print >> held; next }
		n > 1 && (/^docs repo / || (/^time machine: / && i == 1)) { if (i == 1) print >> held; next }
		/^sources: / && i < n { next }
		/^sources: / && n > 1 {
			close(held)
			while ((getline l < held) > 0) print l
			close(held)
			printf "" > held
			close(held)
		}
		{ print }'
}
# init and add-source exit 78 for a config that loads (a bad [governance] value, a policy.toml beside it) only
# after add-source appended the folder's block (cli._ensure_setup runs after the write), which stays: the
# person's fix keeps it, and the re-run says "already configured". So a refusal after the block was written
# (the call printed "added source ", never read off the exit code alone: a config that never loads exits 78
# with nothing written) says the folder was added, not that add-source failed, and the step's log line counts
# the add (note=added-then-refused, with kept= and added= when the installed agentsync can count them), which
# the report reads as an add (B13 round 17 U40). Every later folder is not added: the loop stops at the first
# refusal, each would be refused the same way, and the re-run names them. The same holds for exit 1 after the
# block (a docs-repo remote [governance] does not allow: cli._ensure_setup's _refuse_remotes), and for any
# other failure after it: the mark, not the code, says the folder was added; the code and docs_remotes_refused
# only word the cause, and the log line carries the real code (B13 round 18 V47). The call's stderr is held in
# ERRF and printed once the pipeline ends, before the held lines and the error: printed as it came, its
# "configuration error:" line overtook the "added source" block, which awk's piped stdout holds in a buffer
# until it exits, so the error read as if the folder had not been added (B13 round 18 V26). add-source writes
# only short lines to stderr (an error, a governance: line), and no ticker, so holding them delays nothing.
if [ "${#FOLDERS[@]}" -gt 0 ]; then
	nth=0
	HELD=""
	[ "${#FOLDERS[@]}" -eq 1 ] || HELD="$(mktemp)"
	MARK="$(mktemp)"
	ERRF="$(mktemp)"
	for f in "${FOLDERS[@]}"; do
		nth=$((nth + 1))
		: >"$MARK"
		: >"$ERRF"
		HELD_RC=0
		run "$AGENTSYNC" add-source "$f" --config "$CONFIG" </dev/null 2>"$ERRF" | add_source_filter "$nth" "${#FOLDERS[@]}" "$HELD" "$MARK" || HELD_RC=$?
		cat "$ERRF" >&2
		: >"$ERRF" # printed: on_exit prints only what a signal kept from this line
		[ "$HELD_RC" -ne 0 ] || continue
		[ -z "$HELD" ] || { cat "$HELD" && rm -f "$HELD"; }
		WHY="agentsync add-source $f failed (see the error above)"
		if [ -s "$MARK" ]; then
			if [ "$HELD_RC" -eq 78 ]; then
				WHY="the folder $f was added to $CONFIG, but agentsync refuses the config (the configuration error just above)"
			elif [ "$HELD_RC" -eq 1 ] && [ "$(docs_remotes_refused)" -gt 0 ] 2>/dev/null; then
				WHY="the folder $f was added to $CONFIG, but the docs repo has a git remote [governance] does not allow (the governance: line above)"
			else
				WHY="the folder $f was added to $CONFIG, but agentsync add-source failed after adding it (see the error above)"
			fi
			left=$((${#FOLDERS[@]} - nth))
			[ "$left" -ne 1 ] || WHY="$WHY; the 1 folder after it was not added"
			[ "$left" -le 1 ] || WHY="$WHY; the $left folders after it were not added"
			NOW="$(synced_count)"
			if [ -n "$KEPT" ] && [ -n "$NOW" ] && [ "$NOW" -gt "$KEPT" ]; then
				step_end failed "$HELD_RC" "added-then-refused kept=$KEPT added=$((NOW - KEPT))"
			else
				step_end failed "$HELD_RC" "added-then-refused"
			fi
		fi
		rm -f "$MARK" "$ERRF"
		(exit "$HELD_RC") || config_failed "$WHY"
	done
	[ -z "$HELD" ] || rm -f "$HELD"
	rm -f "$MARK" "$ERRF"
	RERUN_ARGS="$BASE_ARGS" # every add-source that failed stopped the run: each folder is in the config now
else
	run "$AGENTSYNC" init --config "$CONFIG" </dev/null || config_failed "agentsync init failed (see the error above)"
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
# Whether status output $1 has [FAIL] lines that stop the run and every one is the person's to fix: a [[source]]
# path or sentinel, a folder renamed back or synced in its app, or a sign-in (ops/doctor.py's source checks and
# _missing_source_fix, read by the first words of their fix). A provider folder's fix starts with its path: one
# missing, or a SharePoint library there but empty beside its organization's OneDrive folder (B13 round 13 O52;
# round 14 Q45, Q54, Q63); Rosetta to install, or the native pandoc copy the operator installed (the pandoc
# check's fixes, B13 round 15 S47, S53); a deleted [[source]] block to put back (config.vanished_sources,
# round 15 S7), or a fix that starts "the person ": a [policy] or [governance] value or a state file that
# cannot be read (round 16 T2, T9, T12); a fix that starts with a [convert] pandoc_path edit, the configured or
# not-absolute pandoc's (round 17 U79, U80; the bundled pandoc's starts with the agent's install.sh re-run and
# stays the agent's). A re-run fails the same way and an edit would
# choose a folder for them, so the NEXT names the owner, as the config error's does (B13 round 13 O11, O30). Any
# other [FAIL] keeps "fix the [FAIL] lines": a check that ran out of time is fixed by the re-run its fix names,
# and a listing held for an Allow click by that click.
persons_fails() {
	local b
	b="$(blocking_fails "$1")"
	[ -n "$b" ] && ! printf '%s\n' "$b" | LC_ALL=C grep -Ev ' \(fix: (fix path in \[\[source\]\]|point \[\[source\]\] |set path in the \[\[source\]\] |restore |sign in to |/.* is (missing|empty) while (OneDrive-|another )|put the \[\[source\]\] block back |the person |Rosetta is yours to install|the native pandoc copy at |set \[convert\] pandoc_path |check or replace the pandoc at \[convert\] pandoc_path )' >/dev/null
}
PERSONS_FAILS="the [FAIL] lines above are the person's to fix (each names its fix: a folder or file to put back or sync, a [[source]] path, a [[source]] block to put back, a [policy] or [governance] value, a docs-repo remote to remove or allow, a state file that is damaged or another account's, a sign-in, a [convert] pandoc_path, Rosetta to install or a native pandoc copy), not a step for the agent, so show them those lines and go on to the report; once fixed"
# The pandoc status is about to check: [convert] pandoc_path, else the one the converters run, the native
# arm64 copy when the operator installed one and the one bundled with the tool otherwise (the rule of
# ops/doctor.py's pandoc check, B13 round 14 Q60), read by the tool step 2 installed. A tool older than
# that rule has no resolved_pandoc, and its status checks the bundled one, which is then the one named.
# The resolver reads the wheel's RECORD and runs nothing. Empty when it cannot say (no interpreter, a
# config that does not load, a bundled pandoc that is missing): status then reports that. -I: see the OCR
# helper above.
status_pandoc() {
	[ -x "$TOOL_PY" ] && [ -f "$CONFIG" ] || return 0
	with_timeout 20 "$TOOL_PY" -I -c '
import sys
from pathlib import Path
from agentsync.config import load_config

pandoc = load_config(Path(sys.argv[1])).convert.pandoc_path
if pandoc is None:
    try:
        from agentsync.convert.pandoc import resolved_pandoc
        from agentsync.errors import ConversionError
    except ImportError:
        import pypandoc

        pandoc = Path(pypandoc.__file__).parent / "files" / "pandoc"
    else:
        try:
            pandoc = resolved_pandoc(None)[0]
        except ConversionError:
            sys.exit(0)
print(pandoc)
' "$CONFIG" 2>/dev/null || true
}
# Start that pandoc once before status does (see step 5 in the header): its first start after an install can
# take longer than the 60 s status gives it, and this wait is the installer's own, with progress lines. At
# most $PANDOC_START_SECONDS, the time a conversion gives pandoc; past that it is stopped and status says
# what it finds. Never fails the run: a pandoc that is missing or broken is the check's to report, with its
# fix. Quiet when the start takes under half a progress interval, which every start but the first does; one
# silent for longer ends with its "started after" line, so the status wait's first line comes a full interval
# later and shows a real count, not "status: still running, 1s" after 14 s of silence (B13 round 20 X36). Its
# clock is $PROGRESS_SINCE (see with_progress): the status wait after it goes on counting from this function's
# last line, or from its start when it printed none, the time status_pandoc took included.
PANDOC_START_SECONDS=300
start_pandoc_once() {
	local pandoc pid wd t0=$SECONDS since rc=0 shown=0 half
	PROGRESS_SINCE=$SECONDS
	pandoc="$(status_pandoc)"
	[ -n "$pandoc" ] && [ -x "$pandoc" ] || return 0
	since=$PROGRESS_SINCE
	"$pandoc" --version </dev/null >/dev/null 2>&1 &
	pid=$!
	# No poll: this shell waits on pandoc and goes on the moment it ends, while two children wait on their own
	# sleeps (an external sleep a poll was five forks a second for up to 300 s; B13 round 14 Q40, as with_progress
	# and with_timeout since load root cause 2026-10-17 X11). The lines are with_progress's ticker; it exits 3
	# when it printed one. The limit is with_timeout's watchdog. on_exit ends both (WAIT_TICKER, WAIT_WATCHDOG).
	# A failed kill in the ticker's trap (its sleep already reaped) is no error under set -e, which exited 1
	# and lost "pandoc: started after" (B13 round 16 T41, T79, the Q40 CI flake); and the ticker ends when
	# pandoc does, so a TERM bash 3.2 dropped leaves no ticker printing for ever (T80).
	(
		s="" turned=0 last=$since said=0
		trap 'kill "$s" 2>/dev/null || :; exit $((said ? 3 : 0))' TERM
		while kill -0 "$pid" 2>/dev/null; do
			if [ $((SECONDS - last)) -ge "$PROGRESS_EVERY" ]; then
				last=$SECONDS said=1
				# one text true of every run: a run stopped in this wait meets the same cold copy unreinstalled, and a
				# later start took 110 s at load 600 (B13 round 15 S17)
				say "pandoc: still running, $((SECONDS - t0))s (a new copy's first start can take a minute, and any start can on a busy Mac)"
			fi
			n=$SECONDS
			w=0.2
			[ "$turned" -eq 0 ] || [ $((last + PROGRESS_EVERY - n)) -lt 2 ] || w="$((last + PROGRESS_EVERY - n - 1)).8"
			sleep "$w" >/dev/null 2>&1 &
			s=$!
			wait "$s"
			s="" # reaped: the trap must not kill a pid the system may have given another process
			[ "$SECONDS" -eq "$n" ] || turned=1
		done
		exit $((said ? 3 : 0))
	) &
	WAIT_TICKER=$!
	watchdog "$PANDOC_START_SECONDS" "$pid" >/dev/null 2>&1 &
	wd=$!
	WAIT_WATCHDOG=$wd
	wait "$pid" 2>/dev/null || rc=$?
	kill "$WAIT_TICKER" "$wd" 2>/dev/null || true
	wait "$wd" 2>/dev/null || true
	wait "$WAIT_TICKER" 2>/dev/null || [ $? -ne 3 ] || shown=1
	WAIT_TICKER="" WAIT_WATCHDOG=""
	if [ "$rc" -ne 0 ] && [ $((SECONDS - t0)) -ge "$PANDOC_START_SECONDS" ]; then
		PROGRESS_SINCE=$SECONDS
		say "pandoc: no answer after ${PANDOC_START_SECONDS}s; status checks it next"
		return 0
	fi
	half=$((PROGRESS_EVERY / 2))
	[ "$half" -ge 1 ] || half=1
	[ "$shown" -eq 1 ] || [ $((SECONDS - since)) -ge "$half" ] || return 0
	PROGRESS_SINCE=$SECONDS
	say "pandoc: started after $((SECONDS - t0))s"
}
status_run() { "$AGENTSYNC" status --config "$CONFIG" | tee "$DOCTOR_LOG"; } # the pipeline's status is status's
step_start status
DOCTOR_RC=0
DOCTOR_TCC_ONLY=0 # every [FAIL] line is the launcher's TCC_PENDING: not a stop; the wait (step 8) asks for the Allow when GO=1
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
		# fail-lines only when status printed one; a status that exited non-zero with none (a traceback, a
		# config or lock error) is status-error, which still blocks the sync (B13 round 14 Q50)
		status_note=""
		if [ "$DOCTOR_RC" -ne 0 ] && grep -q '^\[FAIL' "$DOCTOR_LOG"; then
			status_note="fail-lines"
		elif [ "$DOCTOR_RC" -ne 0 ]; then
			status_note="status-error"
		fi
		step_end "done" "$DOCTOR_RC" "$status_note"
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
# stderr as it comes. AGENTSYNC_QUIET_LOCK_WAIT=1: this step prints its own ticker, and its own wait line when its
# lock probe found the lock busy, so agentsync sends every lock-wait beat to DEBUG, the first too (B13 round 16
# T33), and leaves its skip WARNING to the stderr line beside it (B13 round 7 H31). AGENTSYNC_LOCK_WAIT_ANNOUNCED
# says whether the probe saw the lock busy: when it did not (a sync took the lock after the probe), agentsync says
# the wait once, as a plain line, and marks stdout so the note below books it (B13 round 17 U68). An older
# agentsync ignores both.
first_sync_run() {
	AGENTSYNC_QUIET_LOCK_WAIT=1 AGENTSYNC_LOCK_WAIT_ANNOUNCED="$SYNC_LOCK_BUSY" \
		"$AGENTSYNC" "${FIRST_SYNC[@]}" --config "$CONFIG" >"$SYNC_OUT"
}
# What the first sync printed: its "converted N, deferred M online-only" line(s) after a success, else all of
# it (a failure's detail, or an agentsync that prints no such line).
show_first_sync() { # RC
	local summary
	summary="$(grep -E "$SYNC_SUMMARY_RE" "$SYNC_OUT" 2>/dev/null || true)"
	if [ "$1" -eq 0 ] && [ -n "$summary" ]; then
		printf '%s\n' "$summary" | sed "s/^[[:space:]]*/$SYNC_LABEL: /"
		[ -n "$FIRST_SYNC_NOTE" ] || FIRST_SYNC_NOTE="$(printf '%s\n' "$summary" | awk '
			{ for (i = 1; i < NF; i++) { if ($i == "converted") c += $(i + 1); if ($i == "deferred") d += $(i + 1)
				if (($i == "again" && $(i - 1) == "read") || ($i == "read" && $(i - 1) == "first")) r += $(i + 1)
				# and the first reads among them: the all-first head, or "(F of them a first read", once
				# "F of them read for the first time" (B13 round 12 N35); never "K of them withheld"
				if ($i == "read" && $(i - 1) == "first") f += $(i + 1)
				if ($i == "of" && $(i + 1) == "them" && (($(i + 2) == "a" && $(i + 3) == "first") ||
					($(i + 2) == "read" && $(i + 3) == "for"))) { x = $(i - 1); gsub(/[^0-9]/, "", x); f += x } } }
			END { printf "converted-%d-deferred-%d", c, d; if (r > 0) { printf "-reread-%d", r; if (f > 0) printf "-first-%d", f } }')"
		# the upgrade's one-time retry note (cli, B13 round 19 W18), which a sync that exits 0 printed only into
		# SYNC_OUT: shown after the summary, never fed to the awk above (B13 round 20 X85); other notes keep
		# their own handling
		LC_ALL=C grep -E '^[[:space:]]*note: .* an older build gave up on ' "$SYNC_OUT" 2>/dev/null |
			LC_ALL=C sed "s/^[[:space:]]*/$SYNC_LABEL: /" || true
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
	# waits for it, up to 10 minutes (agentsync's INTERACTIVE_LOCK_WAIT_S): the note names the wait, so
	# install.log and the Summary do not put its seconds on conversion (B13 round 4 Q29). The probe is
	# uv_tool_install's. A line says so before the wait (B13 round 6 G37: it was said only in agentsync's own
	# WARNING lines on stderr, which the prompt never mentions); the setup prompt quotes its words, and it
	# starts with none of the prefixes the prompt hands to the person (NEXT:, WAITING ON YOU:, [warn]).
	SYNC_LOCK_BUSY=0
	l="$(sync_lock "$CONFIG")"
	if [ -n "$l" ] && [ -f "$l" ] && [ -x "$LOCKF" ]; then
		"$LOCKF" -k -n -s -t 0 "$l" /usr/bin/true 2>/dev/null || [ $? -ne 75 ] || SYNC_LOCK_BUSY=1
	fi
	[ "$SYNC_LOCK_BUSY" -eq 0 ] || say "$SYNC_LABEL: another sync is still running; waiting for it to end, at most 10 minutes"
	with_progress "$SYNC_LABEL" first_sync_run || SYNC_RC=$?
	show_first_sync "$SYNC_RC"
	if [ "$SYNC_RC" -eq 0 ]; then
		# a wait the probe missed is booked too, from agentsync's stdout mark (cli.LOCK_WAITED_MARK; U68)
		if [ "$SYNC_LOCK_BUSY" -eq 1 ] || grep -qx "lock wait: this sync waited for another sync's lock" "$SYNC_OUT" 2>/dev/null; then
			FIRST_SYNC_NOTE="${FIRST_SYNC_NOTE:+$FIRST_SYNC_NOTE-}waited-for-the-lock"
		fi
		step_end "done" 0 "$FIRST_SYNC_NOTE"
	elif [ "$SYNC_RC" -eq 80 ]; then
		step_end failed 80 tcc-denied
		LAST_ERROR="the first sync (agentsync sync --once) exited 80 ($(rc_meaning 80)): this terminal app may not read the folders"
		printf 'error: %s\n' "$LAST_ERROR" >&2
		term="$(terminal_app)"
		term="this terminal app${term:+ ($term)}"
		NEXT_MSG="$term was denied access to files managed by $(provider_name): allow it in System Settings > Privacy & Security > Files and Folders (turn on $(provider_name) under $term; a click, not a command), then re-run: $SELF$RERUN_ARGS"
		exit 1
	elif [ "$SYNC_RC" -eq 75 ]; then
		# agentsync's own "skipped: lock held by ..." line above names the holder; this one only closes the step
		# (B13 round 7 H31: a third line said the same news again).
		say "$SYNC_LABEL: skipped, lock busy"
		step_end skipped 75 lock-busy
	elif [ "$SYNC_RC" -eq 78 ]; then
		# A config the sync cycle refuses (a source still mirrored but gone from sources.toml: cycle.py's
		# _refuse_vanished_sources) loads, so the config step passed; a re-run fails the same way, and its fix
		# chooses what to sync, so the NEXT names the owner, as config_failed's does (B13 round 14 Q22). Status
		# FAILs it since B13 round 15 S7 (doctor's config.vanished_sources, the person's fix), which stops this
		# step before it: this branch is the backstop for an older installed build, whose status does not.
		step_end failed 78
		LAST_ERROR="the first sync (agentsync sync --once) exited 78 ($(rc_meaning 78))"
		printf 'error: %s\n' "$LAST_ERROR" >&2
		NEXT_MSG="the first sync refused the config $CONFIG (the configuration error above; ~/.local/bin/agentsync sync prints it again): fixing it is the person's, not a step for the agent, so show them the error and go on to the report; once it is fixed, the next run is: $SELF$RERUN_ARGS"
		exit 1
	elif [ "$SYNC_RC" -eq 65 ]; then
		# A manifest damaged under a table: init, add-source and status read only its schema, so the first sync
		# is where it shows. Its fix loses history, so it is the person's, as config_failed's 65 is; the sync
		# prints it again (status, at such damage, says only "cannot read the state"), and add-source has put
		# every folder in the config, so the re-run names none (B13 round 17 U55, U65).
		step_end failed 65 state-unreadable
		LAST_ERROR="the first sync (agentsync sync --once) exited 65 ($(rc_meaning 65))"
		printf 'error: %s\n' "$LAST_ERROR" >&2
		NEXT_MSG="agentsync's state file cannot be read (the first sync's error above, with its fix; ~/.local/bin/agentsync sync prints it again): fixing it is the person's, not a step for the agent, so show them the error and go on to the report; once it is fixed, the next run is: $SELF$RERUN_ARGS"
		exit 1
	else
		step_end failed "$SYNC_RC"
		LAST_ERROR="the first sync (agentsync sync --once) exited $SYNC_RC ($(rc_meaning "$SYNC_RC"))"
		printf 'error: %s\n' "$LAST_ERROR" >&2
		NEXT_MSG="fix what the first sync printed above ($(rc_meaning "$SYNC_RC")), then re-run: $SELF$RERUN_ARGS"
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
# A local listing the step-5 status warned was held, that this closing status reads as ok, was cleared by this run's
# sync: say so, by source id as the heartbeat lines do, rather than leave the reader to infer it from a WAITING
# line that is missing (B13 round 6 G33). Run only when this run's sync ran and exited 0: a [FAIL] that skipped
# it prints none, and a busy lock's holder may list the folder, which this run's sync did not (B13 round 14 Q35).
# The warn says "; deletions held" since B13 round 12 N17 and "(deletions held)" before: the anchor matches both.
cleared_listings() {
	[ "$SYNC_GO" -eq 1 ] && [ "$SYNC_RC" -eq 0 ] || return 0
	[ -n "$DOCTOR_LOG" ] && [ -s "$DOCTOR_LOG" ] && [ -s "$LOOP_OUT" ] || return 0
	LC_ALL=C awk '
		FNR == NR { if ($0 ~ /^\[warn\] heartbeat\./ && index($0, "deletions held")) held[substr($2, 11)] = 1; next }
		$0 ~ /^\[ok  \] heartbeat\./ && (substr($3, 11) in held) { print substr($3, 11) }
	' "$DOCTOR_LOG" "$LOOP_OUT" | LC_ALL=C sort -u | while IFS= read -r id; do
		printf "heartbeat.%s: this run's sync listed it in full; deletions are no longer held\n" "$id"
	done
}
loop_next() { # sets next, and EXIT_RC to 1 when that status found something that stops the loop
	local fails blocking held waits
	LOOP_OUT="$(mktemp)"
	with_progress "status" loop_status_run || true # its exit status is its [FAIL] lines
	cleared_listings
	fails="$(grep '^\[FAIL' "$LOOP_OUT" 2>/dev/null || true)"
	blocking="$(blocking_fails "$LOOP_OUT")" # the launcher's TCC_PENDING alone does not stop the loop
	held="$(awk '/^WAITING ON YOU: macOS held the listing / { sub(/^WAITING ON YOU: /, ""); print; exit }' "$LOOP_OUT")"
	waits="$(grep '^WAITING ON YOU: ' "$LOOP_OUT" 2>/dev/null || true)"
	next="$(awk '/^NEXT: / { sub(/^NEXT: /, ""); print; exit }' "$LOOP_OUT")"
	[ -z "$fails" ] || printf '%s\n' "$fails"
	[ -z "$blocking" ] || EXIT_RC=1
	[ -z "$held" ] || EXIT_RC=1 # a listing macOS holds for an Allow click in this terminal (field N8b)
	if [ -n "$blocking" ] && persons_fails "$LOOP_OUT"; then
		next="$PERSONS_FAILS, run $AGENTSYNC sync and follow its NEXT line"
	elif [ -n "$blocking" ] || { [ -n "$fails" ] && [ -z "$held" ]; }; then # its NEXT points at a [FAIL] line "below"
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
elif [ "$DOCTOR_BLOCKS" -eq 1 ] && persons_fails "$DOCTOR_LOG"; then
	EXIT_RC=1
	next="$PERSONS_FAILS, the next run is: $SELF$RERUN_ARGS"
elif [ "$DOCTOR_BLOCKS" -eq 1 ] && ! grep -q '^\[FAIL' "$DOCTOR_LOG" 2>/dev/null; then
	# status exited non-zero with no [FAIL] line (note=status-error, B13 round 14 Q50): a re-run fails the same
	# way, and a signal or a timeout prints no error, so the NEXT promises none (B13 round 15 S33, S58, S62)
	EXIT_RC=1
	next="agentsync status exited $DOCTOR_RC ($(rc_meaning "$DOCTOR_RC")) with no [FAIL] line: what it printed above (its error, if it printed one; $AGENTSYNC status prints it again) is the person's to fix, not a step for the agent, so show them those lines and go on to the report; once fixed, the next run is: $SELF$RERUN_ARGS"
elif [ "$DOCTOR_BLOCKS" -eq 1 ]; then
	EXIT_RC=1
	next="fix the [FAIL] lines above (each names its fix), then re-run: $SELF$RERUN_ARGS"
elif [ "$INSTALL_AGENT" -eq 1 ] && [ "$WAIT_RESULT" != "ok" ] && [ "$WAIT_RESULT" != "running" ]; then
	EXIT_RC=1 # --confirm-install-agent asked for background sync: anything short of it is a failure
	if [ "$HAVE_SOURCES" -eq 0 ]; then
		next="choose a folder to sync, then re-run: $SELF$BASE_ARGS --source-local \"<folder>\""
	elif [ "$AGENT_RC" -ne 0 ]; then
		next="read the install-agent error above, then re-run: $SELF$RERUN_ARGS"
	elif [ "$WAIT_RESULT" = "tcc" ]; then
		EXIT_RC=3
		next="macOS is still waiting for Allow for agentsync-launcher: click Allow if the prompt is showing, or $TCC_CLICK, then re-run: $SELF$RERUN_ARGS"
	elif [ "$WAIT_CODE" = "65" ]; then # the person's fix, as the first sync's 65 (B13 round 17 U55)
		next="the first background run exited 65 ($(rc_meaning 65)): agentsync's state file cannot be read (the error in ${WAIT_ERRLOG:-the err log of the job}, with its fix; ~/.local/bin/agentsync sync prints it again): fixing it is the person's, not a step for the agent, so show them the error and go on to the report; once it is fixed, the next run is: $SELF$RERUN_ARGS"
	elif [ "$WAIT_CODE" = "80" ]; then
		next="macOS denied agentsync-launcher access to files managed by $(provider_name) (an earlier \"Don't Allow\"): $TCC_CLICK, then re-run: $SELF$RERUN_ARGS"
	elif [ "$WAIT_RESULT" = "timeout" ]; then
		EXIT_RC=3
		next="the first background run did not exit 0 within ${WAIT_SECONDS}s (last exit: ${WAIT_CODE:-none}); check $AGENTSYNC status, then re-run: $SELF$RERUN_ARGS"
	else
		next="the first background run exited $WAIT_CODE ($(rc_meaning "$WAIT_CODE")); fix what $AGENTSYNC status and $WAIT_ERRLOG show, then re-run: $SELF$RERUN_ARGS"
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
