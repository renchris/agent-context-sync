## Common brief for every B13 round 25 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 25.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 24 landed" and "B13 round 25" (the v33 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.178 to §16.182.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round25/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round25/*.md`): read the part
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

ROUND 24 RULINGS (lead; still binding):
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

ROUND 25 RULINGS (lead; every lane builds to them; all code-only, so the prompt stays v33):
(10) A step 3 that ENDS on the person's FAIL (v33: the agent stops and goes to the report; no later sync ended 0)
is not "worked with help": compute_outcome returns failed at the report step, naming the last such stop in its kind
words and that its fix is the person's. A stop whose record could not be measured needs a doctor FAIL in a person's
bucket at report time to count; agent-owned stops and step 3 errors with no stop record stay "worked with help"
(D3, D19, D27; lane RA). This reverses round 22's A36 "never failed" for this one case only.
(11) Pairing covers every recorded stop, not only one a later sync ended 0 after: each stop claims one step 3 error,
one to one in time order; an agent-owned stop claims only an error line that names a chmod, a person-owned stop
claims only one that names the sync's non-zero exit and no chmod (refined mid-round with lane RA: v33's person's-FAIL
path runs no chmod), and a "skipped: lock held" line is never claimed and stays a reason; stop k may claim up to the stop after next, since v33 logs the line after the re-sync; an
error in the same second as a stop is at or after it. A paired error of a stop no later sync fixed still counts
among the outcome's reasons (D1, D7, D18, D24, D28, D35, D57, D58, D69, D75; lane RA; the shell report reads the
same pairing, lane RB).
(12) Anything that is not a file where manifest.sqlite-wal or -shm belongs, and a 0000 folder or a dangling link
at the manifest, is the PERSON's, as install.sh already says (round 24 B52): sync, curate and init catch the sqlite
open error, print status's own state_dir.files result as `error: state: <detail> (fix: <fix>)`, record
file=manifest-wal or manifest-shm with owner person, give the misplaced-state NEXT (none under --mode or with NEXT
hints off) and exit 1; doctor prints one state_dir.files line per side, not a FAIL and an ok; every fix keeps
"keeping a copy" and the same words as install.sh's (D2, D6, D8, D10, D21, D22, D70, D71, D72, D73; lane B; the
report's buckets for it, D4, D9, D20, D23, D78, lane RA).

Clusters: the report's other words (D5, D15, D16, D32, D34, D36, D39, D74, D80; lane RA); status, curate, governance
and lock seams after round 24 (D11, D12, D25, D26, D29, D40, D41, D43, D44, D45, D46, D47, D48, D49, D77, D81; lane
B); install.sh this round (D31, D33, D38, D42, D66, D76, D79; lane RB owns the whole file, lane P's functions
included, since lane P has no items); the speech and OCR reasons and the timing pin (D50, D51, D53, D54, D55, D56,
D65, D84, D85; lane E); the guides, CONTRACTS and CLAUDE.md (D59 to D64, D67, D68, D83; lane D). Earlier rounds are
in COMMON5.md to COMMON23.md.

Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions, the convert retry clause, the convert imports and the speech
build's check and fix (D53, D55, D65, D85), lane B the rest. In `scripts/install.sh`, lane RB owns the whole file
this round. In `cycle.py`, lane E owns the upgrade's retry note, legacy counts and `_stub_settled`, lane B the rest.
In `tests/test_install_oneshot.py`, lane RB owns everything this round.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.178 to §16.182); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: no item needs prompt words this round, and v33 stays: rulings 10 to 12 are code-only so the
operator still pastes v33. No lane edits the README setup prompt block. If a fix seems to need new prompt words,
message the lead before you build it.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.178 to §16.182) begins "Amended
<date>, §16.NN D<n>". A line that records another round's change reads "Amended <date>, §16.NN D<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 24's ids are B<n>; round 25's
are D<n> (C has §16.11 C15). The dated-line scans read a line of any date since round 19's W71.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
Every install.sh probe of doctor (state_path_fix, PERSONS_KINDS, the parent fix) runs under the installed
agentsync: it must fall back when that build lacks the name.

Dated-line test: since round 9 K80 the gate reads every "(D<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 25's rows the same way.

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round25/
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

Landing (round 25): land with `scripts/ship-land.sh --lock /tmp/b13-r25-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r25-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6). If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. CI takes 18 to 37 minutes a run (the slowest, 36:24, in round 24), and a run can wait up to about 48 minutes for a macOS runner first; its limit is 50 (W80, round 21 Y68, round 22 Z67, round 23 A74, round 24 B98, round 25 D83).

Makes things worse: 15 round 25 items carry "a fix makes things worse? True" (D1, D5, D6, D7, D10, D15, D18,
D22, D28, D32, D50, D57, D64, D79, D84). For each, the verifier's corrected fix is one that does not: read its
"Verifier" part before you build. D57's verifier also offers a v34 prompt change: do not build it (ruling 11 is the
code-only half).

Regressions: round 25 found no turn and 19 wrongs. Most sit on v33's new step 3 path for a person's FAIL (the
report pairs and counts it wrong, rulings 10 and 11) and on side files of the manifest that are not files (ruling
12); the rest are seams on round 24's own fixes (B37, B39, B51, B63, B64, B101). Before you land a new stop, exit
code, wait or person-owned fix, follow its words on a Mac in the state it is meant for (does doing what it says lead
to a clean run, or to the next stop?) AND test it on one where it must not fire, and grep the other readers of its
words (install.sh, both reports, status, doctor, the guides). A test marked timing is skipped above a load of 4 per
core: run any one you change with AGENTSYNC_TIMING=1 before you land. A land gate takes about 40 minutes at this
load, so pre-gate with --affected and land once.

Lane peers (round 20 Y80): once all five lanes are live, the lead messages every lane the five session ids. Send a
seam note straight to the lane that owns the file, and copy the lead only when it changes another lane's scope.

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r25-<lane>.md`.
