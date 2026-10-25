## Common brief for every B13 round 28 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 28.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 27 landed" and "B13 round 28" (the v33 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.193 to §16.197.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round28/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round28/*.md`): read the part
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

ROUND 25 RULINGS (lead; still binding, ruling 11 as refined again below):
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

ROUND 26 RULINGS (lead; still binding, rulings 13 and 14 as extended below):
(11, refined again) A person-owned step 3 stop claims the first unclaimed step 3 error line at or after it and
before the stop after next, whatever its chmod words ("no chmod was run", the person's own chmod quoted, "agentsync
sync returned 1" all pair); only a "skipped: lock held" line is never claimed. An agent-owned stop still claims only
a chmod-naming line. Both reports pair alike, fractional-second stamps included (E8, E33, E53, E67, E68: lane RA;
E66, E77: lane RB).
(13) Anything that is not a regular file where ANY state file belongs (agentsync.lock, heartbeat.json,
governance/holds.json and the other governance files, manifest.sqlite and its -wal and -shm): a folder, a fifo, a
socket, a link to nothing, a link loop, a link through a file. It is the PERSON's, as ruling 12 says for the side
files: status FAILs it (never "free", never a warn that promises the next pass rewrites it, never a passing link),
every command that opens it (sync, curate, init, hold, release, purge) refuses with status's own words, its fix and a
NEXT, exits 1, never blocks on a fifo and never tracebacks; doctor reads a link's own mode with lstat where the link
is the defect and its target's where the target is (E22, E38, E39, E40, E43, E45, E46, E62, E63, E65: lane B;
install.sh's inlined copies, E31, E56: lane RB; the report's words, E3, E11, E14, E28: lane RA).
(14) With the manifest lost (ruling 7), no command deletes or rewrites a mirror page: `purge --queue` and every
governance write refuse with the lost manifest's own fix and a NEXT, and status and the sync never invite them (E25:
lane B, FIRST, before its other items; data integrity). The person's new start (heartbeat.json moved aside) that
removes orphan mirror pages counts each removal in the commit and the report, and its runs do not restart at run 1
in the CHANGELOG (E9, E10: lane B; E1, E24, E27: lane RA, whose D26 half never fired).

ROUND 27 RULINGS (lead; still binding, ruling 15 as extended below):
(15) Ruling 13 holds for every kind at every governance file and at the heartbeat: a link to nothing, a link
through a file and a link loop at holds.json, purge-queue.json, suppressed.json or audit.jsonl, and a loop or a link
through a file at heartbeat.json, are the PERSON's, like a folder or a fifo. A hold list that cannot be read is never
an empty hold list: status FAILs it with the person's fix, and hold, release, purge and `purge --queue` refuse, exit
1, never traceback and never say "a fault in agentsync to report". Build it at the shared readers first (P130's
guidance): `ops/lock.read_regular_bytes` maps ELOOP and ENOTDIR at a path that is itself a link to "not a regular
file", so the next pass rewrites a heartbeat link; `doctor.state_file_kind` names ELOOP ("a symlink loop") and
ENOTDIR ("a symlink to nothing"); a governance-only judge calls a link to nothing the person's, while a link to a
working regular file stays ok (never newly FAIL a working setup). A 0000 folder at a state file takes the person's
move aside with its chmod first (`doctor._opens_first`), and status judges it by lstat before the open's EACCES.
reconcile, accept-deletions, materialise and checkpoint run sync's guard (P131 to P136, P43: lane B, FIRST, before
its other items; data integrity: a page under a hold on `all` was erased). Lane B sends lanes RA and RB its words
first.
(16) A step 2 stopped by a lock, heartbeat or governance file that is not a file gives the person's fix in its NEXT,
and step 1 predicts that stop with a config-step tail. install.sh asks the installed agentsync's own
`doctor.state_not_a_file(load_config(cfg), manifest=False)` under `-I` and a timeout, prints nothing when that build
lacks it or it raises, and never re-implements the kinds (P22 and P124, the round's two turns; P57, P110, P113,
P125, P142: one build, lane RB, after lane B's ruling 15 words). A folder at the -shm: step 1's tail says step 2
stops at its config step when step 2 runs the flagless init, and at its status step only on the --source-local
path, measured both ways (P15, P56, P114: lane RB; the guide's words for it: lane D, citing RB's sha).
(17) After the person's new start, a purge queued before the manifest was lost is "not judged yet" until the next
sync builds a manifest, then "not resolvable" when the new manifest holds nothing for its source and id. Status,
doctor and step 2 never invite a `purge --queue` that would act blind or on nothing, and the queued-purge words name
the FAIL that is there, once (P40, P112, P24, P46, P118: lane B, right after ruling 15). A lost manifest beside a
heartbeat.json that cannot be read (damaged JSON or a fifo) is still lost, never a first start (P111: lane B;
install.sh's inlined copy of the check: lane RB, in lane B's words).
(18) Beside a linked manifest, the -wal and -shm are judged beside the link's target, where sqlite keeps them, by one
helper (for example `doctor.manifest_sides`) that doctor, status, the report and install.sh all use; a side link
beside a linked manifest is skipped before any stat follows it (P42: lane B, first; P35: lane RB's install.sh, and
lane RA's report half after lane B's sha).

ROUND 28 RULINGS (lead; every lane builds to them; all code-only, so the prompt stays v33):
(19) A hold is never lost to a reader. governance/ itself (a link to nothing, a loop, a file, a fifo) is the
PERSON's like any governance file; a hold list of the wrong shape or damaged JSON is never an empty list and never a
traceback; holds the audit trail records as set and not released count as held even when holds.json lacks them, so
following status's own fix for an unreadable hold list never leaves a page under a hold unprotected; a working link
at holds.json or purge-queue.json is written through to its target, never replaced by a local file; and under a hold
on `all`, nothing invites `purge --queue` (I87 to I91, I94, I107: lane B, FIRST, before its other items; data
integrity: in both I87 and I88 a page under a hold on `all` was erased).
(20) No blind purge from a manifest that knows nothing: an empty manifest that add-source, init or a dry-run sync
created after the new start, and a 0-byte or too-short manifest beside a heartbeat that records passes, are judged
as the new start's, never as a manifest that holds no such file (I61, I92: lane B); a "not resolvable" purge whose
stable id still reaches the page's history runs its own source and id purge in the queue run and verifies, and is
kept only when that targets nothing (I93, I109: lane B).
(21) With P111 live, a lost manifest beside a heartbeat.json that cannot be read is said as that in every reader:
never "heartbeat.json records earlier passes" and never "the next completed pass rewrites it", with its own fix
(the heartbeat's chmod or move aside first, then the manifest's) in doctor, status, step 1, step 2, the report's
outcome, Summary and buckets, and the guides (I62, I32, I42: lane B, first, sending its words to lanes RA and RB;
I95, I1, I28, I41, I53, I64, I76: lane RA, one build; I5, I33, I56: lane RB; I11, I69, I78, I101: lane D).
(22) A secret in a file's text is never published: a .url whose URL carries `user:password@` is withheld by a
built-in secret rule, as any other password is, and a conversion error never prints an absolute staging path (I40,
I39: lane E, FIRST). One name for each governance file in every reader: the hold list, the purge queue, the
suppression list, the audit trail, in doctor's words (I3, I15, I34, I44, I57, I86, I100: lane RB, one build; the
report already says them). The ownership rule at a linked manifest's target: a -wal or -shm this account owns but
cannot open is the agent's chmod at its resolved path, as doctor's state_dir.files FAIL says, in step 1, step 2 and
the report (I52, the round's turn: lane RB with doctor's `own_path_fix` from lane B and the report's half from lane
RA).

Clusters: the report's purge queue section and sync line over a new start (I2, I13, I46, I18, I45; the settling
pages, I54, I63) and its other words (I55, I7, I8, I9, I10, I19, I27, I31, I37, I38, I51, I58, I59, I68, I74; lane
RA); status, doctor and the commands (I16, I25, I43, I47, I49, I50, I65, I67, I70; lane B); install.sh's other tails
and the shell report (I4, I17, I14, I6, I20, I21, I29, I30, I35, I36, I60, I96, I106; lane RB); the new start's run
numbers and kept pages (I22, I23, I108), the empty folders' Finder order (I24, I48, I105) and the OCR URL cut (I72,
I104; lane E); the guides, CONTRACTS records, ship-land's header and the contracts test (I75, I77, I71, I73, I79 to
I84, I99, I102, I103; lane D). Lead: I97 (the harvest's umbrella note, built as I87 to I96). Earlier rounds are in
COMMON5.md to COMMON26.md.

Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions, the convert retry clause, the convert imports and the speech
build's check, fix and docstrings, lane B the rest. In `scripts/install.sh` and `tests/test_install_oneshot.py`, lane
RB owns everything this round. In `cycle.py` and `publish.py`, lane E owns the new start's parts (I22, I23, I108) and
its earlier parts, lane B the rest. In `lints.py`, lane E owns the new secret rule (I40), lane B the rest. In
`scripts/ship-land.sh`, CLAUDE.md and `tests/test_contracts.py`, lane D (I80, I99, I103).

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.193 to §16.197); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: no item needs prompt words this round, and v33 stays: rulings 10 to 22 are code-only so the
operator still pastes v33. No lane edits the README setup prompt block. If a fix seems to need new prompt words,
message the lead before you build it.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.193 to §16.197) begins "Amended
<date>, §16.NN I<n>". A line that records another round's change reads "Amended <date>, §16.NN I<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 27's ids are P<n>; round 28's
are I<n>. The dated-line scans read a line of any date since round 19's W71.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
Every install.sh probe of doctor (state_path_fix, PERSONS_KINDS, the parent fix) runs under the installed
agentsync: it must fall back when that build lacks the name.

Dated-line test: since round 9 K80 the gate reads every "(I<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 28's rows the same way.

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round28/
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

Landing (round 28): land with `scripts/ship-land.sh --lock /tmp/b13-r28-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, reads trunk's CI once without waiting (a run
already ended red stops it there, exit 6, before the gate), rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r28-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6); since round 27 P140 it
also reads trunk's runs once before the gate, so a trunk already red stops the land before a 35-minute gate. If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. CI takes 18 to 37 minutes a run (the slowest, 36:24, in round 24), and a run can wait up to about 48 minutes for a macOS runner first; its limit is 50 (W80, round 21 Y68, round 22 Z67, round 23 A74, round 24 B98, round 25 D83).

Makes things worse: 16 round 28 items carry "a fix makes things worse? True" (I10, I14, I17, I23, I40, I49,
I50, I51, I57, I70, I72, I77, I84, I89, I90, I108). For each, the verifier's corrected fix is one that does not: read
its "Verifier" part before you build.
Dated lines: every round 28 dated line reads 2026-10-25, its heading's date (lane D's round 25 test).

Regressions: round 28 found 1 turn and 26 wrongs. Most sit on round 27's own new paths: hold-list readers
that ruling 15 did not reach (governance/ itself, a damaged or wrong-shaped hold list, a hold the audit trail records;
twice a page under a hold on `all` was erased: ruling 19), empty manifests the new start's judge does not see
(ruling 20), and P111's lost-unread state, which every reader still words as "records earlier passes" (ruling 21).
Before you land a new stop, exit code, wait or person-owned fix, follow its words on a Mac in the state it is meant
for (does doing what it says lead to a clean run, or to the next stop?) AND test it on one where it must not fire,
and grep the other readers of its words (install.sh, both reports, status, doctor, the guides). A test marked timing
is skipped above a load of 4 per core: run any one you change with AGENTSYNC_TIMING=1 before you land. A land gate
took about 1 h 50 min at round 27's load (270 to 1,000), so pre-gate with --affected and land once. A hook asks
before any command holding the word "restore" (round 27: a pytest -k filter held lane E for two hours): leave it out
of -k filters.

Lane peers (round 20 Y80): once all five lanes are live, the lead messages every lane the five session ids. Send a
seam note straight to the lane that owns the file, and copy the lead only when it changes another lane's scope.

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r28-<lane>.md`.
