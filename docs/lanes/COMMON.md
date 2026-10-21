## Common brief for every B13 round 21 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 21.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 20 landed" and "B13 round 21" (the v29 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.152 to §16.157.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round21/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round21/*.md`): read the part
about your item. Items that describe one defect from several finders are built once; say so. Round 21's:

THE OWNERSHIP RULE (lead ruling, round 21; every lane builds to it): a state file or folder THIS account owns but
cannot open because of its own mode is the AGENT's to fix: `chmod 600 <file>` or `chmod 700 <folder>` as
`doctor.state_path_fix` names it, once, then the re-run; if the same error comes back, log it and go to the report.
One another account owns, an ACL deny, or an unsearchable parent stays the PERSON's (a `sudo chown`, never run by the
agent). Step 1's tail, step 2's NEXT, status, doctor, the sync's error line, the prompt and both reports say the
same owner in the same case.

Clusters: the report calls this account's chmod the person's (Y3, Y9, Y21, Y25, Y30, Y34; lane RA); step 2's NEXT is
generic and step 1's tail and the prompt disagree on who runs the chmod (Y24 turn, Y4, Y59: the prompt; Y10, Y14,
Y57: install.sh; lane P); the sync's and curate's error give a generic fix (Y31, Y69; lane B, from
doctor.state_path_fix); a shut state folder adds a second FAIL and hides a legal hold (Y11, Y26, Y44, Y51; lane B);
a lock, heartbeat or holds.json this account cannot open (Y43, Y45, Y47; lane B); the graph check missing from X73's
'not checked' list (Y42; lane B); a git proxy or remote written on its section's header line left in clear or read
as 'not set' (Y1, Y36 in setup_report.py, lane RA; Y64 in install.sh, lane RB); a folder name with a TAB in
add-source's escaped form (Y2; lane RA); the cannot-open sqlite codes defined twice and dead interim code (Y48,
Y71, Y39, Y50, Y63, Y78; lane RA reads doctor's); exit 71's meaning (Y35, Y46, Y56; lane RA, and install.sh's
literal); and the guide's Repeat conversions row (Y41, Y52, Y58, Y74; lane D). Earlier rounds are in COMMON5.md to
COMMON19.md.
Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions and the convert retry clause (Y54), lane B the rest, including
`state_path_fix` and `cannot_open`, which lanes P and RA call and do not copy. In `scripts/install.sh`, lane P owns
config_failed, config_errors and step 1's --list-folders tails (Y10, Y14, Y24, Y57), lane RB the rest (the
fallback body and its redaction, the report header, on_exit, the Private-only loop). In `cycle.py`, lane E owns the
convert-retry record and the rescreened stub (Y73), lane B the rest.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.152 to §16.157); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: only lane P edits the README setup prompt block. It bumps the prompt to v30 once (README block,
PROMPT digest pin, SETUP_PROMPT_COMPAT, the version pins in tests/test_setup_report.py and every "v29" the installer
prints; round 18's lane P missed the test_setup_report.py pins once). Three items need prompt words (Y4, Y24, Y59), all
lane P's own. A lane whose fix changes words the prompt quotes sends them to lane P with cc-notify (lane P's session
id is in its fire message) and records that in its note.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.152 to §16.157) begins "Amended
<date>, §16.NN Y<n>". A line that records another round's change reads "Amended <date>, §16.NN Y<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 20's ids are X<n>; round 21's
are Y<n>. The dated-line scans read a line of any date since round 19's W71.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
Y10 and Y24's install.sh probe runs under the installed agentsync: it must fall back when that build lacks
`doctor.state_path_fix`.

Dated-line test: since round 9 K80 the gate reads every "(Y<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 21's rows the same way.

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round21/
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

Landing (round 21): land with `scripts/ship-land.sh --lock /tmp/b13-r21-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r21-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6). If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. CI takes 21 to 26 minutes a run (one ran 34 on a loaded runner); its limit is 45 (W80, round 21 Y68).

Makes things worse: 9 round 21 items carry "a fix makes things worse? True" (Y7, Y18, Y27, Y35, Y38, Y56, Y57,
Y68, Y79; the lead built Y79). For each, the verifier's corrected fix is one that does not: read its "Verifier" part
before you build.

Regressions: round 21's 15 wrongs and one turn sit on round 20's unopenable-state seam, where each reader picked
its own owner for this account's chmod (the ownership rule above settles it), and on round 20's one-line git config
reads (X8, X41), which missed a value written on the section's header line (Y1, Y36, Y64). Before you land a new stop,
exit code, wait or person-owned fix, test it on a Mac in the state it is meant for AND on one where it must not fire,
and grep the other readers of its words (install.sh, the report, the guides).

Lane peers (round 20 Y80): once all six lanes are live, the lead messages every lane the six session ids. Send a
seam note straight to the lane that owns the file, and copy the lead only when it changes another lane's scope.

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r21-<lane>.md`.
