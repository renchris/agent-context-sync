## Common brief for every B13 round 23 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 23.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 22 landed" and "B13 round 23" (the v31 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.164 to §16.169.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round23/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round23/*.md`): read the part
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

ROUND 23 RULINGS (lead; every lane builds to them):
(4) Anything that is not a folder (a file, a symlink to a file) where the state folder or a folder above it belongs
is the PERSON's: "the person moves <path> aside (keeping a copy)", never a chmod. doctor's state_dir FAILs with that
fix and names the first such path; heartbeat, governance and the manifest's checks read "not checked: the state
folder is not a folder (see state_dir)"; nothing raises. install.sh's step 1 tail and step 2's NEXT, status, the
sync's error and both reports say the same (A2, A6, A21, A32, A80: install.sh, lane P; A7, A22, A33, A58: doctor
and cli, lane B; A23: the report, lane RA). mode_refuses hands a chmod only to the kind it fixes (700 for a
folder, 600 for a regular file).
(5) A heartbeat this account cannot write is reported once, not per source: an interactive sync (no --mode, NEXT
hints on) prints `error: state: <path> cannot be opened: Permission denied (fix: <doctor.state_path_fix>)`, its NEXT
leads to status, and it exits 1; a --mode (LaunchAgent) run keeps today's exit; a corrupt heartbeat keeps today's
warning (A1, A34, A84, one build; lane B).
(6) The person's-kinds list: lane P lands every copy (doctor.PERSONS_KINDS, setup_report._PERSONS_KINDS_COPY,
install.sh PERSONS_FAILS and its header, the prompt's list, the test literals) in ONE commit, in lane B's words for
the new kind ("a state folder that is not a folder"); lane B sends the words and cites the sha. Four files pin them
to each other, so any split goes red between two lands.

Clusters: the report's Items and outcome words after the one-pass chmod and for an ACL on the state folder itself
(A4, A5, A12, A13, A16, A38, A43, A45, A69, A88; lane RA; lane B's A54 is cli's side of the same prefix); a heartbeat
at 0000 (A1, A34, A84; lane B); step 1's tail for a lock, heartbeat or governance stop and step 2's NEXT for a -wal
or -shm at 0000 (A17, A83, A78; lane P); a stopped copy's installer, sync and install.out lines (A18, A19, A20; lane
RA); an origin with two url keys and no rewrite (A24, A50, A90: the report, lane RA; install.sh's half, lane RB,
same id A24); the <proxy-N> legend and a remote URL's path (A25: both reports, RA and RB); the local-work NEXT's
words (A27: install.sh, RB; the report's copy, RA); the shell report's own moved folders and a --source-local folder
beside the docs repo (A41, A42; lane RB); a rewrite base with an escaped quote (A82; lane RB); the Z76 guard (A48,
A76, A89, one build; lane RB); the purge queue's words after round 22's Z54 (A52, A55, A56, A57; lane B); rotate_logs
outside the lock's try (A53, A81, one build; lane B); §16.162 Z55's stale "no run-record count" (A64, A68, A95, one
build; lane E). Earlier rounds are in COMMON5.md to COMMON21.md.

Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions, the convert retry clause and the convert imports (A67), lane B the
rest, including `state_path_fix`, `cannot_open`, `PERSONS_KINDS` (whose copies lane P lands, ruling 6), the
heartbeat, governance and time_machine checks. In `scripts/install.sh`, lane P owns config_failed, config_errors,
mode_refuses, state_open_fix, step 1's --list-folders and state tails, the status-step NEXT and PERSONS_FAILS, lane
RB the rest (the fallback body and fallback_next_line, its redaction, the git readers, the --report-only NEXT). In
`cycle.py`, lane E owns the upgrade's retry note and legacy counts (A62, A63), lane B the rest (the heartbeat, the
purge queue, rotate_logs). In `tests/test_install_oneshot.py`, lane P owns the Z81 test (A51), lane RB the rest,
including the oneshot helper's timeout (A96 part 5).

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.164 to §16.169); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: only lane P edits the README setup prompt block. It bumps the prompt to v32 once (README block,
PROMPT digest pin, SETUP_PROMPT_COMPAT, the version pins in tests/test_setup_report.py and every "v31" the installer
prints; round 18's lane P missed the test_setup_report.py pins once). One item needs prompt words (A36, lane P's), and ruling 6 adds the new kind to the prompt's
list in lane P's one commit. A lane whose fix changes words the prompt quotes sends them to lane P with cc-notify (lane P's session
id is in its fire message) and records that in its note.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.164 to §16.169) begins "Amended
<date>, §16.NN A<n>". A line that records another round's change reads "Amended <date>, §16.NN A<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 22's ids are Z<n>; round 23's
are A<n>. The dated-line scans read a line of any date since round 19's W71.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
Every install.sh probe of doctor (state_path_fix, PERSONS_KINDS, the parent fix) runs under the installed
agentsync: it must fall back when that build lacks the name.

Dated-line test: since round 9 K80 the gate reads every "(A<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 23's rows the same way.

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round23/
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

Landing (round 23): land with `scripts/ship-land.sh --lock /tmp/b13-r23-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r23-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6). If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. CI takes 18 to 32 minutes a run (one ran 34 on a loaded runner); its limit is 45 (W80, round 21 Y68, round 22 Z67, round 23 A74).

Makes things worse: 15 round 23 items carry "a fix makes things worse? True" (A5, A9, A13, A24, A25, A27, A28,
A29, A38, A43, A46, A52, A60, A63, A75). For each, the verifier's corrected fix is one that does not: read its
"Verifier" part before you build.

Regressions: round 23 found no turn and 25 wrongs. Most sit on two states round 22's rulings did not name:
something that is not a folder where the state folder belongs (ruling 4) and a heartbeat this account cannot write
(ruling 5); the rest are seams between the report's words and lane B's new doctor fixes, and the purge queue round
22 changed. Before you land a new stop, exit code, wait or person-owned fix, test it on a Mac in the state it is
meant for AND on one where it must not fire, and grep the other readers of its words (install.sh, the report, the
guides). A test marked timing is skipped above a load of 4 per core: run any one you change with AGENTSYNC_TIMING=1
before you land (round 22's red trunk).

Lane peers (round 20 Y80): once all six lanes are live, the lead messages every lane the six session ids. Send a
seam note straight to the lane that owns the file, and copy the lead only when it changes another lane's scope.

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r23-<lane>.md`.
