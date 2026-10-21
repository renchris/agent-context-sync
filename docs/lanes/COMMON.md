## Common brief for every B13 round 22 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 22.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 21 landed" and "B13 round 22" (the v30 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.158 to §16.163.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round22/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round22/*.md`): read the part
about your item. Items that describe one defect from several finders are built once; say so. Round 22's:

THE OWNERSHIP RULE (lead ruling, round 21; every lane builds to it): a state file or folder THIS account owns but
cannot open because of its own mode is the AGENT's to fix: `chmod 600 <file>` or `chmod 700 <folder>` as
`doctor.state_path_fix` names it, once, then the re-run; if the same error comes back, log it and go to the report.
One another account owns, an ACL deny, or an unsearchable parent stays the PERSON's (a `sudo chown`, never run by the
agent). Step 1's tail, step 2's NEXT, status, doctor, the sync's error line, the prompt and both reports say the
same owner in the same case.

ROUND 22 RULINGS (lead; every lane builds to them):
(1) "State file" means every file agentsync keeps under its state folder: the manifest and its -wal and -shm, the
writer lock, the heartbeat, holds.json and the governance files. The agent's chmod covers each of them, and a folder
at 0000 that holds a file at 0000 takes both chmods (folder first) in the one pass before the re-run (Z8 and Z59, the
turns; Z36, Z81). "Once" means one pass, not one command.
(2) A folder ABOVE the state folder that refuses a search is one fault, the person's: state_dir FAILs with the
person's fix, every check that needs the state folder (disk, governance, the manifest's) reads "not checked" with
the reason, nothing raises, and status's NEXT names state_dir's fix, not disk's. The report says the same: one FAIL
filed as the person's, its Configuration section whole (Z3, Z9, Z26, Z34, Z46, Z60, Z73: doctor and cli, lane B;
Z10, Z27, Z40, Z41, Z61, Z74, Z6: the report, lane RA).
(3) Words name what failed: "the state folder" when the folder (or a folder above it) refuses, "the state file"
only for a file (Z4, Z5, Z15, Z31, Z45, Z19).

Clusters: the person's-kinds list in doctor, install.sh and the prompt (Z17, Z48, Z83: doctor, lane B, who sends the
words; Z65: the prompt and install.sh's copy, lane P); a folder where manifest.sqlite belongs (Z11: install.sh,
lane P; Z12: the report, lane RA; Z51, Z53: cli, lane B); a read-only volume (Z47, Z78: cli, lane B; Z79: step 1 and
step 2's words, lane P); a -wal or -shm this account cannot write (Z80; lane B); an outside-CloudStorage
--source-local folder sources.toml no longer configures, in clear in the shell report (Z2, Z13, Z29, Z42, Z77; lane
RB); the shell report's 'origin: none' for a remote on its header line (Z1, Z25; lane RB), a quoted remote name
(Z75; lane RB) and a [remote.origin] section (Z76: setup_report.py, lane RA; install.sh, lane RB, same id); a
configured state_dir folder name in clear (Z21, Z28; lane RA); an organisation named by a product word made public by an earlier site library of the same word (Z86, a Y7 regression; lane RA); a stopped copy's time and folders lines (Z20, Z22,
Z23, Z24; lane RA); the finish's relay of Y23's local-work NEXT and step 3's chmod clause (Z30, Z71, Z38; lane P);
nested brackets in doctor's fixes (Z7, Z49; lane B); dead code after Y28 (Z50, Z57; lane E). Earlier rounds are in
COMMON5.md to COMMON20.md.

Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions and the convert retry clause, lane B the rest, including
`state_path_fix`, `cannot_open`, `PERSONS_KINDS` and the disk and governance checks, which lanes P and RA call and do
not copy. In `scripts/install.sh`, lane P owns config_failed, config_errors, step 1's --list-folders tails, the
status-step NEXT and PERSONS_FAILS (Z8, Z11, Z36, Z65, Z79, Z81), lane RB the rest (the fallback body and its
redaction, the Installer lines, the git readers, the --report-only NEXT). In `cycle.py` and `manifest.py`, lane E
owns the convert-retry record and `retry_settled_failures` (Z50, Z55, Z57), lane B the rest (the purge queue, Z54).

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.158 to §16.163); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: only lane P edits the README setup prompt block. It bumps the prompt to v31 once (README block,
PROMPT digest pin, SETUP_PROMPT_COMPAT, the version pins in tests/test_setup_report.py and every "v30" the installer
prints; round 18's lane P missed the test_setup_report.py pins once). Seven items need prompt words (Z30, Z38, Z59, Z65, Z71, Z81, Z83): lane P
builds all but Z83's, whose words lane B sends. A lane whose fix changes words the prompt quotes sends them to lane P with cc-notify (lane P's session
id is in its fire message) and records that in its note.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.158 to §16.163) begins "Amended
<date>, §16.NN Z<n>". A line that records another round's change reads "Amended <date>, §16.NN Z<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 21's ids are Y<n>; round 22's
are Z<n>. The dated-line scans read a line of any date since round 19's W71.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
Every install.sh probe of doctor (state_path_fix, PERSONS_KINDS, the parent fix) runs under the installed
agentsync: it must fall back when that build lacks the name.

Dated-line test: since round 9 K80 the gate reads every "(Z<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 22's rows the same way.

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round22/
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

Landing (round 22): land with `scripts/ship-land.sh --lock /tmp/b13-r22-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r22-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6). If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. CI takes 21 to 26 minutes a run (one ran 34 on a loaded runner); its limit is 45 (W80, round 21 Y68).

Makes things worse: 13 round 22 items carry "a fix makes things worse? True" (Z3, Z6, Z21, Z26, Z28, Z33, Z34,
Z44, Z51, Z53, Z60, Z75, Z84). For each, the verifier's corrected fix is one that does not: read its "Verifier" part
before you build.

Regressions: round 22's 29 wrongs and two turns sit on round 21's ownership seam: each reader built the rule
for the manifest and the state folder, but not for a folder above it (ruling 2), not for the lock, heartbeat or
governance files (ruling 1), and not for a removed --source-local folder in the shell report. Before you land a new
stop, exit code, wait or person-owned fix, test it on a Mac in the state it is meant for AND on one where it must not
fire, and grep the other readers of its words (install.sh, the report, the guides).

Lane peers (round 20 Y80): once all six lanes are live, the lead messages every lane the six session ids. Send a
seam note straight to the lane that owns the file, and copy the lead only when it changes another lane's scope.

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r22-<lane>.md`.
