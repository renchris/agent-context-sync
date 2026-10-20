## Common brief for every B13 round 20 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 20.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 19 landed" and "B13 round 20" (the v28 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.146 to §16.151.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round20/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round20/*.md`): read the part
about your item. Items that describe one defect from several finders are built once; say so. Round 20's:
a manifest this account cannot open, which round 19's W64 opened as a seam (X9, X29 and X26: install.sh treats it as
an ordinary error and step 1 says nothing, lane P; X12, X15, X27, X16, X30, X47, X49, X73, X74, X77 and X78: doctor's
Time Machine warn, the heartbeat warn, status's raw WARNING and lost reason, the access fix for every OSError, a state
folder this account cannot search, a sudo chown handed to the agent, a 0400 manifest's -wal and -shm, lane B; X28 and
X35: the report calls the person's FAIL 'unexpected', lane RA; and the sqlite code: X48 in cli.py, lane B, X42 and X60
in setup_report.py, lane RA, both to SQLITE_CANTOPEN (14), never 23); a damaged index root (X1, X25, X71 and X72: step
1's state-table tail promises a step 2 stop that never comes, lane P; X14 and X50: status and the heartbeat clause,
lane B; X59: the guide, lane D); the Repeat conversions note's waiting files (X2, X10; lane RA); the one-line
`[url "x"] insteadOf = y` rewrite left in clear (X8 lane RB, X41 lane RA); W17's agent words (X38, X39 lane RA; X40
and X81, the shell twin, lane RB); a sync that stops on the state file leaves staging without its Time Machine
exclusion (X4, X13; lane B); a plain install.sh after a stopped old copy reported "fully one command" (X19; lane RB);
W3's NEXT says the time limit cut the report when it did not (X37; lane RB); a folder name holding a TAB in clear in
the shell report (X75; lane RB); and the upgrade's retry note (X11, X53 lane E; X85, install.sh's first sync, lane RB).
Earlier rounds are in COMMON5.md to COMMON18.md.
Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions and lane B the rest. In `scripts/install.sh`, lane P owns
config_errors and step 1's --list-folders tails (X1, X9, X25, X29, X71, X72) and the step 2 config stop's NEXT (X26),
lane RB the rest (the fallback body and its redaction, the legend, git_rewrite_bases, on_exit, holder_words,
with_progress, FRICTION_AWK, show_first_sync, the W3 NEXT). In `cycle.py`, lane E owns the convert-retry record and
the upgrade's retry note, lane B the rest (staging, the error isolation). The sqlite code set appears in cli.py (lane
B) and setup_report.py (lane RA): both use 14, and the second to land checks the first.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.146 to §16.151); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: only lane P edits the README setup prompt block. It bumps the prompt to v29 once (README block,
PROMPT digest pin, SETUP_PROMPT_COMPAT, the version pins in tests/test_setup_report.py and every "v28" the installer
prints; round 18's lane P missed the test_setup_report.py pins once). Three items need prompt words (X3, X24, X26), all
lane P's own. A lane whose fix changes words the prompt quotes sends them to lane P with cc-notify (lane P's session
id is in its fire message) and records that in its note.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.146 to §16.151) begins "Amended
<date>, §16.NN X<n>". A line that records another round's change reads "Amended <date>, §16.NN X<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 19's ids are W<n>; round 20's
are X<n>. The dated-line scans read a line of any date since round 19's W71.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
X71 (step 1's state-table tail under an older installed build) is this seam again.

Dated-line test: since round 9 K80 the gate reads every "(X<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 20's rows the same way.

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round20/
scratch-<lane>/`, not in /tmp. Only the land lock stays in /tmp. To damage a manifest for a test or probe, write the
junk with `python3 -I`, not `dd` (`Bash(dd:*)` is denied).

Tools CI lacks (round 18): lane RB's census test passed here only because this Mac has gitleaks; CI does not, and
its land turned trunk red for 90 minutes. A test that reads a tool's output (gitleaks, pandoc, swift, tesseract)
must also pass with that tool absent: check `shutil.which` or stub it.

Gate: the repo's `scripts/gate.sh`, never a hand-copied script. While you build, pre-gate with
`scripts/gate.sh --affected` (every static step, then the tests your diff can turn red; `scripts/gate.sh --lane`
is the full set, resumed where a killed run of the same tree stopped). Before every push run the land gate,
`scripts/gate.sh`: the full CLAUDE.md list, the full test set (the whole-tree scanners first), and it ends
`GATE GREEN at <sha>`.

Landing (round 20): land with `scripts/ship-land.sh --lock /tmp/b13-r20-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r20-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6). If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. CI takes 19 to 25 minutes a run; its limit is 40 (W80).

Makes things worse: 9 round 20 items carry "a fix makes things worse? True" (X4, X6, X26, X33, X44, X46, X47, X49,
X53). For each, the verifier's corrected fix is one that does not: read its "Verifier" part before you build.

Regressions: round 20's 21 wrongs sit on seams round 19 left: W64's unopenable manifest, which reached
install.sh, doctor, status and the report unevenly (X9, X27, X28, X35, X73, X74); W41's table probe, which leaves an
index root to step 1's tail alone (X1, X25, X72); W52/W68's Repeat conversions note (X2, X10); W17's agent words (X38,
X39, X40); the one-line URL rewrite (X8, X41); the staging exclusion on exit 65 (X4); W3's NEXT (X37); the stopped
copy's outcome (X19); a TAB in a folder name (X75); and the guide's step 1 tail (X59). Before you land a new stop, exit code, wait or person-owned fix, test it on a Mac in the state
it is meant for AND on one where it must not fire, and grep the other readers of its words (install.sh, the report,
the guides).

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r20-<lane>.md`.
