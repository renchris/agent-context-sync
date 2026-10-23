## Common brief for every B13 round 24 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 24.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 23 landed" and "B13 round 24" (the v32 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.171 to §16.177.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round24/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round24/*.md`): read the part
about your item. Items that describe one defect from several finders are built once; say so. Round 23's:

THE OWNERSHIP RULE (lead ruling, round 21; every lane builds to it): a state file or folder THIS account owns but
cannot open because of its own mode is the AGENT's to fix: `chmod 600 <file>` or `chmod 700 <folder>` as
`doctor.state_path_fix` names it, once, then the re-run; if the same error comes back, log it and go to the report.
One another account owns, an ACL deny, or an unsearchable parent stays the PERSON's (a `sudo chown`, never run by the
agent). Step 1's tail, step 2's NEXT, status, doctor, the sync's error line, the prompt and both reports say the
same owner in the same case.

ROUND 22 RULINGS (lead; still binding): (1) the agent's chmod covers every file agentsync keeps under its state
folder (the manifest and its -wal and -shm, the lock, the heartbeat, holds.json, the governance files), and a 0000
folder holding a 0000 file takes both chmods, folder first, in the one pass before the re-run; (2) a folder ABOVE the
state folder that refuses a search is one FAIL, the person's, with every check that needs the state folder "not
checked" and nothing raised; (3) the words say "the state folder" when a folder refuses, "the state file" only for a
file.

ROUND 23 RULINGS (lead; still binding): (4) anything that is not a folder where the state folder or a folder above it
belongs is the PERSON's ("the person moves <path> aside (keeping a copy)"), never a chmod; (5) a heartbeat this
account cannot write is reported once, and an interactive sync exits 1 with its fix and a NEXT to status; (6) lane P
lands every person's-kinds copy (doctor.PERSONS_KINDS, setup_report._PERSONS_KINDS_COPY, install.sh PERSONS_FAILS
and its header, the prompt's list, the test literals) in ONE commit, in lane B's words.

ROUND 24 RULINGS (lead; every lane builds to them):
(7) A LOST manifest (manifest.sqlite missing while heartbeat.json beside it records an earlier pass; lane B's round
23 A29 refusal, `agentsync.manifest.manifest_lost`) is the PERSON's: "they restore the file from a copy they trust,
or move heartbeat.json aside, keeping a copy, so the next sync starts a new manifest". Every reader says so in the
same words: install.sh's step 1 predicts the stop (a config_errors word keyed on the filesystem, never on an
error's words; inline the check, as the probe runs on the installed older build) and step 2's config_failed gives
it as the person's NEXT (B3, B24, B31, B33, B41, B48, B69, B83, B85: lane P); status, doctor and the sync's stop
record name it as its own kind, not "state-folder" and not a refusal (B27, B36, B37, B62, B82, B100: lane B); the
report files it as the person's, never "unexpected", and says the parts it could not measure were not measured
(B4, B9, B14, B18, B25, B26, B32, B34, B35, B44, B49, B99: lane RA). Lane B sends lanes P and RA its exact words
first and cites the sha; if the new kind joins the person's-kinds list, lane P lands every copy in one commit
(ruling 6).
(8) Something that is not a file where manifest.sqlite belongs (a folder): the person's fix moves it AND
heartbeat.json aside in the one step, worded as doctor.MANIFEST_FIX words it ("(keeping a copy, they put the file
back, or move the folder aside and heartbeat.json with it, so the next sync starts a new manifest)"), so following
it leads to no second stop on ruling 7's guard (B7, B21, B86: install.sh, lane P; the two turns of round 24). Test
it by following the words: moving the folder alone stops on the lost-manifest guard, moving both does not.
(9) A step 3 sync stop is recorded twice by design: agentsync writes sync-stops.log, and the agent still logs the
error (its line carries the exit code and the chmod it ran, and a "skipped: lock held" sync or a run with no setup
folder leaves no sync-stops.log line). Prompt v33 keeps the instruction to log and corrects only its reason
(B1, B8, B22, B40, B50, B60, B79, B96: one build, lane P). The report pairs them one to one, by order, not by a time
window: each recorded stop whose later sync ended ok claims the first unclaimed step 3 error at or after it and
before the next recorded stop; a paired stop is one Items line ("F<n>: the agent's account of it"), the outcome
counts only unpaired errors, and "none of these changes the outcome by itself" is said only when no listed line
feeds the outcome (B2, B23, B78, B97: lane RA). The shell report reads sync-stops.log too (B95: lane RB).

Clusters: install.sh's other state tails and status-step NEXT (B5, B10, B12, B13, B15, B52, B54, B59, B81, B105;
lane P); the report's other words (B16, B20, B53, B55, B56, B74, B101, B102; lane RA); status, doctor, cycle and
governance seams after round 23 (B11, B17, B28, B43, B58, B61, B63, B64, B65, B66, B67, B70, B71, B88, B89, B90;
lane B); convert and the timing marker (B39, B42, B72, B73, B75; lane E); the remaining #! execs (B91, B92, B93,
B103; lane K); the guides, CONTRACTS and CLAUDE.md (B76, B77, B80, B84, B98; lane D). Earlier rounds are in
COMMON5.md to COMMON22.md.

Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions, the convert retry clause, the convert imports and the speech
build's check (B42), lane B the rest, including `state_path_fix`, `cannot_open`, `MANIFEST_FIX`, `PERSONS_KINDS`
(whose copies lane P lands, ruling 6), the heartbeat, governance and time_machine checks. In `scripts/install.sh`,
lane P owns config_failed, config_errors, mode_refuses, state_open_fix, step 1's --list-folders and state tails, the
status-step NEXT and PERSONS_FAILS, lane RB the rest (the fallback body and fallback_next_line, its redaction, the
git readers, the --report-only NEXT), and lane K the hashing lines and how tests run launcher/build.sh (B103). In
`cycle.py`, lane E owns the upgrade's retry note, legacy counts and `_stub_settled` (B73, B75), lane B the rest. In
`tests/test_install_oneshot.py`, lane P owns the state-tail tests it changes, lane K the stub helpers, lane RB the
rest.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.171 to §16.177); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: only lane P edits the README setup prompt block. It bumps the prompt to v33 once (README block,
PROMPT digest pin, SETUP_PROMPT_COMPAT, the version pins in tests/test_setup_report.py and every "v32" the installer
prints; round 18's lane P missed the test_setup_report.py pins once). Eight items need prompt words, one build (ruling 9, lane P's), and ruling 7's new kind joins the
prompt's list in lane P's one commit if it joins PERSONS_KINDS. A lane whose fix changes words the prompt quotes sends them to lane P with cc-notify (lane P's session
id is in its fire message) and records that in its note.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.171 to §16.177) begins "Amended
<date>, §16.NN B<n>". A line that records another round's change reads "Amended <date>, §16.NN B<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 23's ids are A<n>; round 24's
are B<n>. The dated-line scans read a line of any date since round 19's W71.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
Every install.sh probe of doctor (state_path_fix, PERSONS_KINDS, the parent fix) runs under the installed
agentsync: it must fall back when that build lacks the name.

Dated-line test: since round 9 K80 the gate reads every "(B<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 24's rows the same way.

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round24/
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

Landing (round 24): land with `scripts/ship-land.sh --lock /tmp/b13-r24-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r24-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6). If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. CI takes 18 to 33 minutes a run (one ran 34 on a loaded runner), and a run can wait up to about 48 minutes for a macOS runner first; its limit is 45 (W80, round 21 Y68, round 22 Z67, round 23 A74, round 24 B98).

Makes things worse: 13 lane items of round 24 carry "a fix makes things worse? True" (B12, B17, B22, B28, B36,
B40, B60, B67, B72, B75, B92, B93, B101). For each, the verifier's corrected fix is one that does not: read its
"Verifier" part before you build.

Regressions: round 24 found 2 turns and 31 wrongs. Both turns and most wrongs sit on round 23's A29 lost-manifest
refusal, which landed in cli and doctor with no installer or report half (rulings 7 and 8), and on the step 3 stop
round 23's A9 began recording while v32 still says no record is kept (ruling 9). Before you land a new stop, exit
code, wait or person-owned fix, follow its words on a Mac in the state it is meant for (does doing what it says
lead to a clean run, or to the next stop?) AND test it on one where it must not fire, and grep the other readers of
its words (install.sh, both reports, status, doctor, the guides). A test marked timing is skipped above a load of 4
per core: run any one you change with AGENTSYNC_TIMING=1 before you land. The load ran 15 to 25 per core for all of
round 23: a land gate takes about 40 minutes, so pre-gate with --affected and land once.

Lane peers (round 20 Y80): once all seven lanes are live, the lead messages every lane the seven session ids. Send a
seam note straight to the lane that owns the file, and copy the lead only when it changes another lane's scope.

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r24-<lane>.md`.
