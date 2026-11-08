## Common brief for every B13 round 36 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 36.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 35 landed" and "B13 round 36" (the v34 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.235 to §16.239.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round36/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round36/*.md`): read the part
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

ROUND 28 RULINGS (lead; still binding, as extended below):
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

ROUND 29 RULINGS (lead; still binding, as extended below):
(23) A private value never prints in clear in either report, and agentsync's own words are never cut. Both readers'
hold cut takes the `audit` origin, keeping the fixed reason `lost from the hold list` and cutting only the owner;
the lost-hold FAIL's `set by <owner>` reads `(owner not shown)`, cut through the line's last fixed anchor so the
scope and time stay; and every audit.jsonl hold-set and hold-released owner is a value, read from the trail itself,
never `Hold.owner` and never a fixed fallback (`the records owner`, `lost from the hold list`, `hold = true`,
`sources.toml`) (C101, C119, C156, C187: lane RA, FIRST, one build, sending its patterns to lane RB for install.sh's
hold_cut and hold_values). Lane B keeps the FAIL's `set by <owner>[ at <iso>]` frame when ruling 27 renames its
record clause, and sends the new form to RA and RB first. The shell's two-capital test is `[[:upper:]][[:upper:]]`,
tested under en_US.UTF-8 (C160: lane RB, FIRST); a link target is also registered cut before the run's trailing
words (C163: lane RA); every capital-IT phrase in src/agentsync is a fixed phrase, scanned module-wide (C106, C162:
lane RA). Data integrity: a hold owner and a private folder name printed in a public-ready report.
(24) A hold's record outside governance/ survives every move a fix asks for. It leaves heartbeat.json for its own
state file (for example `<state>/hold-record.json`, 0600, written atomically), which the new start's move of
heartbeat.json does not take; set_hold, release_hold and the cycle's end-of-pass re-record each write the union of
the hold list's holds and lost_holds()' scopes, after the audit line, so setting or releasing one hold never drops a
lost one; each read-modify-write takes a short blocking flock on a sidecar lock, never the single-writer lock, so no
hold is refused while a sync runs; lost_holds reads the file, and a heartbeat `holds` value only while no record
file exists and only when it is a list (a dict there is a source named `holds`, which keeps its entry). The
governance folder's fix says a hold stays in force from that record only when the record exists and names a scope
(C199, C200, C171, C170, C175: lane B, FIRST; data integrity: in C199 and C170 a page under a hold on `all` was
erased). Lane B sends the file's name to lanes RB (install.sh's state probe) and E (the new start leaves it alone)
first.
(25) After a new start a queued purge is judged per source, by one split in governance that every reader takes by
name. Not judged yet while no sync since the new start has listed its source in full (no sources row, or no
last_full_run_id; a source not in the config or paused says so and names the by-hand `purge id=<id> --source <src>
--reason <its reason>`): never probed, never run, never invited (C201). Not looked up yet once its source is listed,
the manifest holds nothing for it and no queue run has probed it: a WAITING ON YOU line and a governance.purge_queue
warn invite the dry run before `purge --queue`, and a hold suspends it as it does a queued entry (C102 with C124).
Found nothing once a run probed it: a note and [ok], never an invitation (P112). Lane B builds it FIRST (data
integrity: in C201 a skipped source's current page was erased) and sends the heads' names to lane RA, whose Purge
queue section uses them, never says `not resolvable`, counts each entry once, says `no alias table` only when the
manifest lacks one, and reads a manifest no sync has run over as not looked up (C123, C159, C158, C113, C180, C188,
C214: one build).
(26) A queued purge that finds nothing leaves the queue only by an audited act, never a blind one: a 0-item VERIFIED
purge is P112's blind run and drops nothing. `purge --queue` takes one explicit flag, not a new command (for example
`--forget id=<queued id> --source <src>`, previewed with `--dry-run`), that removes only an upstream-deleted or
label-escalation entry marked found_nothing, refuses any other with its stays-queued reason, and writes a
`purge-forgotten` audit line under the writer lock (a refused audit trail drops nothing, A79). Every by-hand purge
the CLI and the records page print carries the entry's own `--reason` (C193; data integrity: a label-escalation file
synced back). The report's no-trace line, the records page and the guide say a run takes off only what history names
(C173, C189, C193: lane B; the no-trace line: lane RA, in lane B's words).
(27) Ruling 19's governance states are the person's in every reader, named from what the reader finds on disk. Lane
B first, sending its words and names to lanes RA and RB before they build: governance.hold_list_state (unread, lost
or none; hold_list_unread stays its `is not None`), status and doctor saying `not run while a hold the hold list
lost is in force` for a lost hold and `cannot be read` only for an unread list; a sync WAITING ON YOU line for a
lost hold that names status's governance.hold FAIL, with no owner and no `purge --queue`; the lost-hold FAIL naming
the record that keeps it; a heartbeat note for a governance folder that is not one; and loop.next_step reading a
GovernanceUnreadError from partition_purges as no queue (C107 with C174, C130, C152's loop half). Lane RA, one
build: the outcome matches the governance folder's `belongs there` line and a damaged governance file before
`_STATE_NOT_FOLDER_RE`; Doctor buckets for governance/ not a folder, each damaged file, a lost hold and the
held-queue warn key on the report's own read through governance's names (lazy import; a failed read leaves the line
where it is today), never on doctor's words alone; Status evidence maps a GovernanceUnreadError to fixed words by
its role, never its class, detail or path; the unexpected lists fold (C103, C104, C108, C120, C121, C137, C147,
C152, C157, C161, C208). Lane RB, one build: step 1 asks the installed build's lost_holds (silent when it lacks it
or it raises, never re-implemented) for a status-step tail naming no scope or owner, and `a hold the hold list lost`
joins the person's FAIL kinds in every copy (C122, C197, C206).
(28) No governance file's fix is a bare move aside. The suppression list and the purge queue are recovered from the
audit trail as lost holds are: the cycle treats an audited purge's item (its stable-id or path hash) as suppressed
when suppressed.json lacks it, status FAILs governance.suppressed or governance.purge_queue for audited entries a
file lost, sync refuses while a glob or content-hash suppression or a queued purge cannot be rebuilt, and each fix
restores from a copy first and says what its move aside, keeping a copy, leaves to run or queue again (C172: lane B;
data integrity: erased content republished). The hold list's put-back is conditional, one string in every reader:
`then, if it listed any hold, puts the hold list back from a copy they trust` (C151, C142, C126: lane B;
install.sh:967 in lane B's words: lane RB); a shorter form for a Mac that never kept one is built only with C126's
strict judge in both readers. install.sh's governance-folder fix names what is at the path: `they move what is there
now aside, keeping a copy` (C114, C143: lane RB).
(29) A lost manifest is judged once, by manifest.lost_words, and every reader takes that judgment. A heartbeat.json
that is a link to nothing beside a missing manifest is lost-unread (C118); a manifest that is a link to a regular
file under 100 bytes beside a heartbeat that records passes or cannot be read is lost-short, as the file itself is
(C202, whose CONTRACTS edit replaces C190's); a 2 to 99 byte manifest there is lost in every command: one
ManifestLostError (errno EINVAL, one helper for both raise sites), exit 1 with the lost words and a NEXT, one
state_dir.manifest FAIL, no -wal or -shm written beside it (C192 with C203, C179); its readers say short, not
missing, and do not judge Time Machine over it (C128, C129). Lane B, FIRST (data integrity: C118 and C202 start a
new manifest unasked), sends manifest.py's diff to lane RB, which changes install.sh's two inlined lost() copies and
the parity test on it; the two land as one push, as round 28's B and RB did.
(30) Ruling 21's "the heartbeat's fix first" holds only where the heartbeat has a fix of its own: this account's own
regular heartbeat.json on a writable volume whose mode alone refuses it, where the agent's chmod 600 comes first and
the manifest's fix, the person's, after it (`heartbeat.json's chmod first, the agent's, then the file's, the
person's`). Every other unread heartbeat (damaged JSON, a fifo, a link, another owner, an ACL, a read-only volume)
has one owner, the person's, with its two moves (restoring the file, or moving heartbeat.json aside), never a
heartbeat step the agent runs and never "either clears both". The report decides by status's own test (a lazy import
of `cli._heartbeat_chmod_first`; install.out's `so the fix is the agent's first` NEXT as the second source), puts no
bracket inside its `(computed: ...)` (M24), and gives short-unread the unread order while the short file's move
stays `moved aside with heartbeat.json` (C105, C138, C148, C134, C164: lane RA, one build). A manifest that is there
but too short is "lost", never "missing", in step 1, step 2 and the shell report (C195, C165: lane RB);
sync-stops.log records which lost kind stopped a sync (C210: lane B, sending the line's format to lanes RA and RB
first); the guide follows (C209: lane D).
(31) Ruling 22 holds for every conversion reason and every mail label. The converter-unavailable FAILED reason folds
the home folder with no staged file, through one helper that `_pathless` also calls (C182); `_pathless` replaces the
longest form first (C183); a stub an older build settled with a staging or home path is converted once more past its
retries, recorded in its retry entry so it never repeats, with a CORRECTED line in the I39 record (C181). A
msip_labels header is read with email.policy.default, so RFC 2047 encoded-words decode, and a label fragment that
does not parse is an error that refuses under any active label rule, never an unlabelled publish (C144). Lane E,
FIRST (data integrity: a home or staging path and a labelled mail were published).
(32) Ruling 19's last clause holds in every writer: nothing invites `purge --queue` for an entry a hold keeps.
STATE.md splits the queue as status and next_step do, in ruling 27's words (C204); held_purges resolves a sourceless
`docs=` or `path=` entry read-only to the sources it reaches, folder rows left out, and keeps today's split on a
manifest error (C205); held_words names each hold (`the hold on all`, `the hold on source '<id>'`, the sources.toml
hold), never "the hold above", and puts the release before any `purge --queue` (C115 with C132); a held queue logs
one counted WARNING per hold (C176). Lane B, sending held_words' new fixed phrase to lane RA first: RA's held-queue
bucket (ruling 27) keys on its own held_purges read and that phrase, and still accepts round 28's "on the hold
above" from an older doctor.

ROUND 30 RULINGS (lead; still binding, as extended below):
(33) A suppression list that lost part of what a purge suppressed is lost, and only an audited act clears that.
lost_suppressions compares each audited path-glob purge's fingerprint with those of the globs the list holds, by one
helper skipping a malformed entry (never `not sup.globs`), and each purge-summary's new `canonical_sha256s` (the
sha256 of each canonical hash) with the list's; an older line without it skips that test. A rerun clears an earlier
record only when it is verified and re-found content. An older record's way out is one flag, `purge --accept-lost
<at>` (with --dry-run; no free text), writing a `suppression-lost-accepted` audit line under the writer lock; both
FAILs name it beside the copy: it gives up that purge's content guard, while its items stay out by id and path (C383
with C403, C402, C401, C344: lane B, FIRST, one commit series; data integrity: purged content published again).
(34) What governance/ keeps survives the folder's loss, as ruling 24 made holds survive. Purges get
`<state>/purge-record.json` beside hold-record.json (0600, atomic, a union that never shrinks, written after each
purge's audit line and at every pass's end, so older installs backfill), holding hashes only (ruling 33's form),
which the suppression and queue readers union; a record that cannot be read is never "no purges" (C381). The hold
record's own fix sets each hold again first, and `hold` runs while the record is broken (C382). A round 28 record in
heartbeat.json moves to hold-record.json at the start of sync, hold, release and purge, never status or doctor, and
until it has, every fix names the file that keeps it (C351 with C352). Lane B, sending purge-record.json's name and
FAILs to lanes RA and RB first (data integrity: an erased page republished, a page under a hold on all erased, a
hold dropped).
(35) A queued purge the queue lost can be redone from what agentsync prints, and only a purge of that item closes
it. queue_lost names each lost entry's reason, time, selector kind and source, and rebuilds a stable id from the
manifest's rows and aliases by fingerprint into the exact `purge id=<id> --source <src> --reason <reason>`; else it
says the id is in the page's front matter and that a path= or docs= purge does not clear it. The selector text never
enters audit.jsonl. A verified hand purge covers each open lost record its items match, and a 0-item one reopens
every same-selector entry still queued, in both paths; a refused purge-covered line is one error, written before the
queue is rewritten; both dry runs name the lost entry (C370, C312, C350, C389: lane B, with data-governance.md's two
stops in its own commit; data integrity: a lost erasure that could not be redone, or was closed unseen).
(36) Writing the setup report never writes the manifest. governance.partition_queue splits into its opener and one
judge; `partition_queue_ro` and `partition_purges_ro` open through the report's read-only connector, check the
schema version and tables first, and on a mismatch, no connection or a sqlite error return the unjudged split: never
a migration, chmod, WAL switch, cache clear or .pre-v backup. The report's _purge_split, _queue_held and
_governance_kept_lines call only those, and a test proves a report leaves the manifest, its -wal and its -shm byte
for byte (C394: lane B builds the judge and sends the two names to lane RA first; lane RA moves its callers and
writes the unchanged-bytes tests; data integrity: the read-only report migrated and rewrote the manifest).
(37) A page the secret scan stubbed is announced once, by its alarm. _quarantine_secrets keeps none of the first
scan's SECRET findings for a page it stubbed or redacted (a sidecar's hit mapped to its page); the alarm ends with
the rule names, never a line number, and a redacted page keeps its hashed path, so no path that carries a key
reaches stdout, STATE.md or err.log; the alarm drops its `<source>: ` prefix and the log carries the same string, so
`_printed` still matches (C328: lane B; data integrity: a key in a file's name printed). Lane RA adds a test that
`_STUB_NOW_RE` still matches the alarm, and edits nothing else for it.
(38) A mail an older label reader let through is screened again by the new one. policy.py carries
LABEL_READER_REVISION (2, for C144) beside read_eml_labels; the cycle keeps it in its own manifest meta key and,
when the stored value differs while a label rule is on, marks only .eml and .msg rows for re-screen through
_check_policy_change's queued and stub handling, then writes the key whatever the rule, so an install with no rule
never pays; fingerprint() stays as it is (C404 with C360: lane E, FIRST, rewriting C144's CONTRACTS sentence in its
own commit; data integrity: a labelled mail stayed published). The records page says a label header that does not
parse refuses under any label rule, not only under refuse_unlabelled (C379 with C366: lane D).
(39) Ruling 23 holds for every hold value and every one of agentsync's own words. A hold scope that is no configured
or manifest source id (a matter name) is cut as a source label in both reports, read from all three hold files; no
hold is refused for it (C386: lane RA, FIRST, sending its pairs to lane RB for a hold_scopes reader; data integrity:
a matter name printed in a public-ready report). The printed `audit` clauses join _FIXED_VALUE_PHRASES whole,
scanned module-wide as C106 scans `IT` (C302: lane RA, sending both phrase lists to lane RB once final).
install.sh's redact_stream keeps a hold-flag match that stands wholly inside one of those phrases written out in the
line (lists by ENVIRON, kept equal by a test); a folder or org pair is still cut, and a quoted `IT` still makes the
report private (C338 with C397); its private-only line is plural at two distinct cut words (C339: lane RB).
(40) A governance list that lost what the audit trail records (a hold, a queued purge, a suppression) is the
person's in every reader, named by its role, never its reasons, times or selectors. Lane B: status prints no ok
governance.purge_queue line beside the lost FAIL (C345), and the sync's stop record gives a lost list its own kind,
sent with the FAILs' fixed phrases to lanes RA and RB first. Lane RA, one build: a Doctor bucket for each lost list,
keyed on separate reads of suppressions_lost and queue_lost (governance_lost returns only the first) and the FAIL's
phrase; one outcome mechanism for a stop on a lost hold, queue or list; the lost entries counted in the Purge queue
part (C308, C309 with C335, C369). Lane RB: step 1's tail from the installed build's governance_lost, silent when it
lacks it, and the new kind in every person's-kinds copy in one commit, in lane B's words (C311; ruling 6).
(41) The hold record is named as itself in every reader, never as the hold list or the state folder. Lane B:
_stop_role records `file=hold-record` (C367); hold_list_state gains a state for a record that cannot be read, with
its own wait (`not run while the hold record cannot be read`), while status and doctor still list the holds (C349);
STATE.md's extras catch GovernanceError (C348); lane B sends the role, the wait and the noun to lanes RA and RB
first. Lane RA, one build: the record's own report key, mapped back to governance.hold by one helper so a broken
holds.json keeps its words and comes first, in the misplaced, damaged, refused and sync-stop readers and the unread
words, with the new wait in the hold bucket (C334, C347, C368, C385, C395). Lane RB: fallback_sync_stop_line's
`hold-record` case; lane D: the sync-stops.log row. purge-record.json (ruling 34) is read the same way.
(42) Ruling 28's governance fixes name what they mean, from what is on disk. The hold list's put-back has no
pronoun, one string in every reader: `then, if the hold list had listed any hold, puts the hold list back from a
copy they trust` (install.sh: `put`); a shorter form still waits for C126's strict judge in both readers (C316 with
C333). The governance folder's fix never drops its move aside, but when the hold record can be read and names a
scope it says a folder was kept and asks for it back first, naming no scope and keeping the records-owner clause
(C324). A hold only the record keeps has the origin `record`, and with governance/ missing the FAIL names the folder
and its fix puts it back whole (C313). Lane B sends the string and the origin to lanes RA and RB first; their
hold-cut patterns take `record` before or with lane B's land, so no build prints a `(record)` line a report cannot
cut.
(43) Every purge-queue line says only what is true of the entries it counts. The report's split heads name their
column (`- no row, docs history not searched yet: ...`; C304 with C317's half (a)), and its no-trace line splits by
governance._probed, giving a DLP, erasure or operator entry its own words and never --forget (C336: lane RA).
`--forget` refuses a kind it never takes by that reason (C358); the dry run stays read-only, and its note and
forget's refusal say that only `purge --queue` itself looks an entry up (C371); `purge --queue` says `while its file
is listed` only of listed entries, with its own words for a source no sync will list (C346); the clause for entries
from before the new start names the dry run and `purge --queue`, singular and plural (C318: lane B). The records
page names the page's id and the queued id apart (C378: lane D, after lane B's C371).
(44) Both reports name a lost stop's kind one way: the read now first, else the kind sync-stops.log recorded, as
_stop_which does, so a stop's line agrees with the bucket and Doctor (ruling 21). install.sh follows Python, never
the reverse: fallback_sync_stop_line (C337 with C396 and C377, whose Python flip is not built, since C396 measured
that it reopens ruling 21's mismatch; its cross-reader test is), the late block's STOP_LINK and STOP_KIND for the
latest attempt (C303), and lost_kind_now's read of heartbeat.json's content, so damaged JSON, `[]`, `null` or an
empty file is unread (C390): lane RB, amending §16.199 in the same commit. The not-measured cause comes from a read
of the state file now, never from a recorded kind (C341: lane RA).
(45) Ruling 31 holds for every stored reason, every spelling and its one-shot re-read. The scrubber replaces each
path form's repr spelling before its plain one (C364); an older build's converter-unavailable reason is folded in
place once per manifest, its page rewritten only when the plan differs, never swapped for the new reason (C361); a
C181 test reaches _stub_with_a_path (C365). The re-read is said: notes after the legacy notes keep `an older build
gave up on`, so install.sh's grep needs no change, hold no bare `converted`, `deferred` or `read again`, and count
each as current, failed again or waiting for its source (C362's words, not C327's); CONTRACTS and the report say,
with no count or path, that the older page stays in the docs history until compaction, pinned by a test (C319 with
C327, C362: lane E, sending the counts and words to lane RA for the retry sentence and to lane D for the guide).

ROUND 31 RULINGS (lead; still binding, as extended below):
(46) A queued purge the queue lost is always shown, and closes only when the queue's own run would close it. With
purge-record.json unreadable, status judges the lost queue from the audit trail alone, never swallowing the error,
FAILs each entry with its redo and prints no `[ok] governance.purge_queue` line; with nothing lost, one info line
without ` no longer holds ` (C539: lane B, FIRST; data integrity: a lost erasure hidden behind an ok line).
purge-enqueued lines gain the id's and aliases' hashes, never selector text; a lost stable-id record closes only
when a later verified purge carries that hash, the read-only manifest has no row of its source hashing to it and no
id rebuilds; an unreadable manifest keeps it open (C561, makes worse True). _rebuilt_ids fingerprints each id once
per source, no process-wide cache (C538); partition_purges_ro and lost_queue_lines go (C542). Lane B.
(47) --accept-lost and every line that names it say exactly what accepting clears and gives up. The records page
says the act writes one `suppression-lost-accepted` line for each record its time names and clears those only: an
older build's second-precision time can name several, and the dry run then says so (C582 with C553: lane D, FIRST,
landed before lane B's C537; data integrity: more suppressions dropped than meant). accept_lost_words, both FAILs'
fixes, the act's output and help, its docstring, data-governance.md's next sentence (plural) and CONTRACTS say a
copy of content the audit trail fingerprints stays out; only an older build's unhashed purge loses its copy guard,
and a glob purge its glob guard; the sync stop stays (C537 with C577, makes worse True). A purge is named `the
<reason> purge at <time>`, never `the purge <reason> at` (C511). Lane B, its pins in its own commit.
(48) Ruling 45 holds for every stored reason already written and every spelling. Once per manifest, on a run that
covers every live source, the cycle folds a conversion-failed reason holding an escaped spelling of the staging root
or home in place, as C361's fold does (the page rewritten only when its plan differs), and _names_a_path tries every
_spellings form (C546 with C547: lane E, FIRST; data integrity: the local staging path stayed in a page,
QUARANTINE.tsv and the manifest). A fold whose page rewrite failed leaves its key unwritten (C564 with C576).
_spellings gives each form its plain, repr and forced single-quote repr spellings, for _homeless and
_stub_with_a_path too; _pathless cuts a folder named alone only at a path boundary, to a fixed word, never "" (C578,
C564's half); the docstring and §16.206 say only that spellings are replaced longest first (C551).
(49) A typed folder is cut wherever a report prints it, by the rule the source table already uses. The Python report
cuts the typed form of an `added source '...' to <file> (<typed>, stored as <stored>)` note at its first component
that is no placeholder, `~`, generic folder or home folder (one helper shared with _source_local_shown), after the
same-stored collapse; the stored form stays; the cut is not counted (C331), and the C331 sentence and the legend
name an added source's typed folder (C529: lane RA, FIRST, sending the rule to lane RB; data integrity: a project
name behind a link printed in a public-ready report). The shell report pairs each rooted --source-local path's
unquoted spellings, anchored to `(<path>, stored as `, with its cut form, measured by a forced-fallback test (lane
RB, right after its C521).
(50) install.sh's governance-folder fix says a folder was kept when the hold record names a hold, as doctor's does.
misplaced_words' governance case reads hold-record.json when it lexists, else round 28's top-level `holds` list in
heartbeat.json, a regular file only, and takes doctor's words when that is a JSON list holding a string (`all`
included); hold_scopes(), which drops `all`, is not reused; the words name the file doctor names, and a missing,
empty, unreadable or scope-less record keeps today's (C521, C324's install.sh half: lane RB, FIRST, tests for a
scope, `all` and round 28's record; data integrity: the person moves aside the governance folder instead of
restoring the kept one, and its holds are dropped).
(51) The records page's lost-manifest steps keep every hold, and bind each queue rule only to the entries it binds.
Before `Follow status's fix:` it says that while heartbeat.json keeps round 28's hold record and no hold-record.json
is there, status's fix starts with `agentsync sync` once, which moves that record, run before heartbeat.json is
moved aside; line 71 says a real purge moves it too; both pinned phrases stay word for word (C580: lane D, FIRST;
data integrity: a hold dropped with the file). The new-start text binds `kept until its id is found` and --forget to
upstream-deleted and label-escalation entries; DLP, erasure and operator entries run as purges once the manifest is
no longer lost, and --forget refuses them (C581: lane D, after lane B's C565).
(52) Ruling 36 holds for every manifest read a report makes, side files included. manifest.py gains one
read_only_reads() context in which Manifest and _connect never chmod, set WAL, migrate, clear the cache or copy to
.pre-v, but open read-only and raise a typed not-current error that callers map to the schema and busy notes (a
newer schema still FAILs); the report's status hook takes partition_queue_ro's split, never a revived
partition_purges_ro (C560: lane B sends it first; lane RA runs build_report's hooks and threads inside it, its
byte-for-byte test on the real hooks over all seven states). Doctor's manifest probe opens a clean file immutable,
so it makes no -wal or -shm (C526); one manifest helper removes a .ro-copy holding a 0000 side folder, for the sweep
and the report's finalizer (C541: lane B, lane RA's call with a lazy fallback).
(53) A re-screen the mail label reader queued is counted and named as the reader's, row by row. _check_label_reader
keeps the keys it marks in a persisted meta set while no [policy] re-screen is pending (a [policy] re-screen clears
it); a converted row in it adds to a new rescreened_reader with prior=None, so it is never converted_seen or
converted_again; the revision stays out of fingerprint() and the converter options. The sync line says `re-screened
R for a new mail label reader` apart from the rule's share, and the held WARNING counts as printed only then. A
reader marker never overwrites a pending [policy] one, and under its own marker STATE.md and rescreen_left count
mail only (C548, makes worse True, with C540). C505 with C517, C518, C524, C544: lane E, sending the count and words
to lane RA (re-screen bullets, Repeat conversions) and to lane D (the records page, C559).
(54) Ruling 45's re-read says what each row came to, on every upgrade path. The C362 note never says `failed again`
of a row left a stub: it says the file is still a stub, that this sync's counts say why (failed, unreadable or
refused) and that it is not tried again, keeping `an older build gave up on ` and no bare converted, deferred or
read again, with no new count, since the report's `left as a stub` already reads true (C545 with C523: lane E,
CONTRACTS:34290 and :34297 in that commit). A legacy retry that takes a row whose stored reason named a folder is
counted `legacy_pathed` (run record, CycleReport) and its legacy note adds `(its stored reason named a folder on
this Mac)`, with path_reread untouched (C506, makes worse True; lane RA's pathed clause and bullet follow). The
staging re-read test sizes its tmp root to about 160 characters (C549). Lane E.
(55) Ruling 39 holds for every hold word: record, records, legal, label and review. Every printed agentsync clause
holding one joins _FIXED_VALUE_PHRASES whole, never a bare word and none with `|`, found by a C302-style scan
parametrized over the five words that builds each literal into its printed line (the `(record)` origin's three
forms, hold-record.json and purge-record.json among them); setup_report.py:8984 reads `the newest run that says what
its re-read looked for`; a quoted value is still cut and private (C502 with C512, C533, C563: lane RA, one build,
changing install.sh's FIXED_VALUE_PHRASES lines in the same commit, as the parity test reads both). Lane RB adds the
shell probe and _FIXED_PHRASE_LINES rows. A lane that prints a new clause holding one of the five runs that scan
before it lands and sends the clause to lane RA.
(56) The `<source-N>` legend names what C386 registers. Python's reads `<source-N> source id (a configured one, one
the manifest still holds, or a hold's scope that names no source; a number can be missing)`; the shell's keeps its
text and adds `also a hold's scope that no source names, as status's HOLD: line and the lost-hold FAIL print it;`
before its tail, rewrapped to its width; each report's C386 test asserts its legend names a hold's scope (C501 with
C534, C566: lane RA with the §16.203/§16.204 Amended line, sending the shell clause to lane RB).
(57) install.sh's sync-stop kinds are setup_report._SYNC_STOP_KINDS, every one. fallback_sync_stop_line gains
`lost-list) why="which lost a purge the audit trail records" ;;` before the refused default, and one test reads
every single-tab arm and asserts the table equals Python's, `refused` through `*)`, so a later kind cannot fall into
`could not open`; a rendered `kind=lost-list` line is checked too (C531 with C552, C562, C575: lane RB, one commit).
(58) A signal stop is named by its signal in every reader, in two forms: standing alone, R81's `stopped by SIGKILL
(signal 9)`; after an rc or inside a bracket, `stopped by SIGKILL, rc 137` (`stopped by SIGTERM` for 143), never a
nested bracket. Lane RA: one helper beside _closing_stopped feeds _sync_step_words, _step_table, _reached and
_run_end, which adds `, first-sync failed, stopped by SIGKILL, rc 137` when the failed step is not the last logged
(C516, makes worse True, with C514). Lane RB: an rc_signal_words helper gives the closing-status note R81's phrase,
rc_meaning 143 unchanged (C513, makes worse True), and a signal branch before the generic else says the first sync
was stopped before it finished and is safe to re-run once, then the report (C515), CONTRACTS P14 and :21517 amended.
(59) The label-unreadable share of the refused count reads apart from the withheld share, and reaches the report.
K's clause stays; L's (cli.py:692-694) becomes `, L of them under a label rule because its label data did not parse`
(`their label data`), with no `refused`, converted, deferred, read again, first read, `of them a first` or `of them
read for`, and _SPLIT_RE still reads R and K (C525 with C522, makes worse True: lane B, sending the clause to lane
RA first; test_setup_report.py:444-445 and CONTRACTS' three quotes in its commit). Lane RA reads it with its own
regex beside _SPLIT_RE, never a fifth group, into a fact the Summary re-renders after K's clause, and §16.205 says
so (C567 with C583, after lane B's sha); lane D's setup-feedback.md:1203 row follows.
(60) Beside a governance stop, every reader gives that stop's own fix and wait, true of what is on disk. Status's
empty-folder wait under a FAIL the sync's guard stops on (never a lost hold, whose sync runs) says a plain sync
prints the alarm `once the <check> [FAIL] line is fixed`, and keeps the exclude-empty offer (C520: lane B, sending
the words to lane RA first); the report puts that heartbeat warn in the stop's bucket, never `yours` (C519), and its
Loop line says Doctor's governance.hold [FAIL] line gives the fix (C508: lane RA). Step 1 under a hold with
governance/ gone gives the folder's tail, logged `state-governance-lost`, from the build's lost_hold_words (C507:
lane RB). The folder's fix says a purge is lost only `if one was ever run here`, sending the person to the records
owner (C530, makes worse True), and round 28's lead says `first` once (C543): lane B, lane RB the install.sh twin.
(61) The rehearsal kit checks each fixture copy mechanically, and no probe breaks that check. COMMON.md's kit
paragraph adds: right after every `cp -Rp`, right before step 2 and at each act's end, rerun every copy's `shasum -a
256 -c` and stop on a non-zero exit; a probe that needs an upstream deletion deletes a file it made itself, never a
manifest file, or names the one manifest file it moves and checks it where it went (C573's (c) with C504: lane D).
The lead writes C329's sed form, with dirs.txt's full path, and that rule into round 32's brief through
tools/edit.py (C503, C528, C573's (a) and (b)).
(62) Lead rulings on the triage's open questions. C565 is built (lane B): round 30 left C371's optional (4),
`--forget` running its own probe, unbuilt on a scope ruling with no measured harm, and round 31's verifier measured
the cost of keeping it (forgetting one entry forces every runnable queued erasure to run first); the plan's
not-built line is the lead's. C571 raises ci.yml's timeout-minutes from 70 to 80 (lane D, with every pin that cites
70: test_convert_recording.py and the CONTRACTS W70 line move in lane D's commit, sent to lane E first): the slowest
round 30 job took 59:43, and round 29 raised 60 to 70 at the same margin. The kit halves of C503, C504, C528 and
C573 are the lead's, in round 32's script; lane D writes only ruling 61's COMMON.md sentence. CONTRACTS sections: RA
§16.208, RB §16.209, B §16.210, E §16.211, D §16.212.

ROUND 32 RULINGS (lead; still binding, as extended below):
(63) With governance/ gone, every lost-file reader names the folder. While it is missing and purge-record.json
records a purge or hold-record.json names a scope, queue_lost and suppressions_lost still raise (the sync's guard
and the no-hold FAIL stay), their detail opening as lost_hold_words does, `<gov>/ is missing, with the hold list,
...`, crediting purge-record.json, never `the audit trail records`, every `<at>` and redo kept, the fix and the
sync's stop led by the whole-folder put-back; status's NEXT names governance.hold first (C601, makes worse True;
C618). --accept-lost then refuses without `--folder-lost`, the no-copy acknowledgement, which first writes
`audit-trail-lost` to the new audit.jsonl and purge-record.json (C606: lane B, FIRST; data integrity: the old audit
trail lost silently; makes worse True). Lane B sends the words first to lanes RB (one folder tail at step 1:
`,lost-queue-govgone`, `,lost-suppressed-govgone`, `state-governance-lost` beside the file's word) and RA.
(64) Every governance line says what is on disk and what is really lost. The ok lines say `no hold set (no hold list
on disk)` with no holds.json, `(hold record read; the hold list is missing with the governance folder)` in ruling
63's case, and the queue `missing with the folder` there, never `no purge queued` (C624, built once with C618's item
6). The lost-hold wait gets a folder form, LOST_HOLD_WAIT_GOVGONE and its status twin, chosen by one
governance.lost_hold_wait that loop.py:934 appends and cli.py maps, its tail byte-identical (C613; lane RA's
Loop-line case follows). The folder fix's hold half and _purges_rewritten's tail make only the loss conditional and
keep `checks with the records owner whether one was` (C632). The suppressed FAIL's detail names the path guard, and
the hashes or a glob only when lost (C641). Lane B.
(65) Ruling 60's guard answers whether a sync runs, and every reader beside a stop says none runs until it is fixed.
loop._sync_stopped_on also names governance.suppressed when this account cannot open purge-record.json or
governance/suppressed.json, never for holds.json, hold-record.json or audit.jsonl (C639); with the purge record
unread, status, the wait, its summary and STATE.md keep the queued count and the wait and point at the
governance.suppressed line, never `purge --queue` (C643). Under a stop, _named_since and exclude-empty say `once the
<check> [FAIL] line is fixed, ...; until then no sync runs`, once a line (C631); doctor's heartbeat [ok] line says
`no sync lists it again until the person fixes the <check> [FAIL] line`, not `a warn after 3` (C633); the report
files a listing warn beside a lock that is not a file as `not judged` (C637: lane RA). The lost-queue replay is
cached in a contextvar opened only around one read-only build, never module-level (C644). Lane B.
(66) Ruling 58 holds in every bracket and every reader. install.sh's rc_meaning default prints `stopped by
SIG<NAME>` alone when kill -l names the signal and keeps `exit N` for a code none names, never empty; a caller that
already prints the code drops its repeat; rc_signal_words stays R81's phrase; a bash 3.2 test runs every caller over
129 to 192 and finds no `(signal` in a bracket (C655 with C661, makes worse True: lane RB; doctor's pandoc line
takes the git line's colon form, lane E). A re-run whose first sync a signal stopped, after the run before it in
this attempt ended `end rc=1` the same way, offers no further re-run (C615: lane RB). The report says `failed,
stopped by SIGTERM, rc 143` in the outcome (C614 with C635), `(stopped by SIGTERM, rc 143, from outside install.sh)`
in a first-sync retry (C612), the step bracket before the closing clause (C617), and sorts the lock a signal-killed
first sync of the attempt left, matched by its start, as expected (C616): lane RA.
(67) The report's Loop line points only at a line the report prints, and says whose reading it quotes. When the
found NEXT opens `NEXT: the <name> check failed: ` and Doctor's section prints that [FAIL] line, _loop_line rewrites
_fail_step's three `below` forms to `Doctor's <name> [FAIL] line`, from a report-side copy tested equal to
cli._fail_step, cli.py unchanged; otherwise `below` stays (C603 with C656). The lost-hold wait swaps in the Doctor
pointer on that condition only, else keeps the sync's own words naming status (C636). With no install.sh run in the
attempt, the quoted NEXT carries one bracket, `(status when this report was written; this attempt ran no
install.sh)`, merged with the damage clause (C620); status's `last runs: not read` note fills loop_unread only when
status printed no unread words (C638). Lane RA; the C508 paragraph states the condition.
(68) Ruling 55 holds for install.sh's own printed lines, and a new hold-word clause enters both phrase lists in the
commit that prints it. Lane RA adds C634's eight phrases to _FIXED_VALUE_PHRASES and install.sh's
FIXED_VALUE_PHRASES in one commit, never `then review `, and the C502 scan reads install.sh's printed literals (say,
NEXT_MSG with its `NEXT: ` prefix, ctail, stail, lfact, lfix, MISPLACED_FIX, again), never comments or the list
lines (C634, makes worse True: lane RA, FIRST). After it, a lane whose new literal holds record, records, legal,
label or review adds the whole phrase to both lists in that commit and runs the scan before landing (ruling 63's and
C613's words, C607's tail). C609's clause, `, L of them because a label rule could not read its label data`
(`their`), is one: lane B sends it first; lane RA lands _LABEL_UNPARSED_RE reading it and round 31's words, the
re-render and the phrases; then lane B changes cli.py:718 (C609).
(69) purge --help says what --forget and --accept-lost do since round 31. --forget's help reads `with --queue: take
off the queue the upstream-deleted or label-escalation entry id=<queued id> (with --source) whose id reaches nothing
in the docs history: looked up here unless a queue run already found nothing for it, so no run is needed first;
refused while it reaches a page or a hold covers its source; with an audit line; nothing is erased`, a format_help
test asserting `no run is needed first` and no `a run looked up and found nothing for` (C610: lane B, FIRST; data
integrity: the old help sends the person to `purge --queue`, which runs every other queued purge). --accept-lost's
head says `of every purge status names by this time (an older build's purges can share one), with an audit line for
each`, its tail kept (C650), and governance.py:5255 says a --dry-run forget holds no lock (C640). One commit.
(70) A lost purge closes only on a manifest really read, and the records page says when a hand purge clears it.
Under whole=True, _read_manifest_ids returns None when item_aliases exists but cannot be read (an absent table still
reads as no aliases; whole=False keeps its partial read), so a lost record matching only an alias stays open (C667:
lane B, FIRST; data integrity: a lost purge cleared on a failed read). A module-level manifest.check_not_newer is
the one schema check, mapped by _schema_note to UNJUDGED_NOTE (C668). data-governance.md:118-121 says a verified
hand purge of any reason clears a lost purge this build queued only while the item stays gone, judged on each read
and never recorded, an older build's record keeping the upstream-deleted exception, and tells the person to run the
lost purge's own `--reason` (C649 with C671: lane D, FIRST; data integrity; makes worse True: words only). Lane B
makes the preview's words agree, never the clearing code, and moves any page line quoting them.
(71) Ruling 48 holds for every staging form and cut length, also where the fold keys are already set. _fold_staging,
after its four 16-hex substitutions, folds the staging folder named alone to the fixed folder word, then drops
`<staging>/` before any other name, so a Graph source's paths fold (C645: lane E, FIRST; data integrity: the staging
path kept in the stored reason). _names_a_path and _cut_in_staging floor one character past the home's parent for a
root under the home, else 12, and a once-per-manifest `convert-path-fold-v2:done` pass, run only over every live
source and left unwritten after a failed page write, folds each stub whose stored or page reason still names the
staging or home folder (C660, moved to lane E; data integrity: up to 4 letters of the login kept). _PATH_END and
_PATH_GOES_ON add `>`, `\'` and a `.` before the end or whitespace, whitespace kept (C646, makes worse True);
equal-length spellings cannot contain each other, and the backslash test depends on the order (C647). Lane E.
(72) Ruling 49 holds in the shell report for every typed form. After the value pairs run, one awk pass anchored to
`added source '<id>' to <file> (<typed>, stored as <stored>):` and its inbox twin cuts <typed> by _cut_path's rule
and leaves <stored>, its word lists through ENVIRON and every stage under LC_ALL=C, so a relative --source-local
reads as in Python's report (C666: lane RB, FIRST; data integrity: typed folder names printed). install.sh pairs pwd
-P's home with `~` before $HOME's and, only after the cut, collapses a note whose two forms are equal (C658's (a)
and (b): lane RB; data integrity: `/private~/` and both forms shown); Python cuts a collapsed note's one form too
(C658's (c): lane RA, sending its output to lane RB for the forced-fallback test's equality).
(73) Each Repeat conversions line says where the file it names is counted. The rule-change bullet ends `one
re-screened for a secret rule is counted in that column` (C622 with C627, one build); the head ends `nor is a file
re-screened for a label-rule change or for a new mail label reader)` (C623); the waiting line says a run in which a
file's converter cannot start counts it in no column above and a later run that reads it counts it under files
converted, or failed or unreadable (C628): lane RA, its pins and CONTRACTS quotes in its commits, the words sent to
lane D. Lane D then writes the guide's row once (setup-feedback.md:1223): `counted in that column`, and the
folder-path retry clause's two triggers, its `tried once more after an upgrade:` note tied to a re-read, never to
the legacy retry (C651).
(74) A CONTRACTS quote that a later change made stale gets one dated line, never a rewrite of another round's text
and never a second copy. §16.209 C515 and the §16.111 Q22 line each get one giving the first sync's signal branch as
129 to 159 on macOS, 160 to 192 keeping the generic branch, with test row 170 (C652); where round 31's own same-day
line already records the change, it is extended instead: N37's for `not recorded`, with none in K5 (C653); §16.206
C364 gets `Amended <date>, §16.217 C654 (by §16.211 C578):` for the third spelling (C654), and C542 `Amended <date>,
§16.217 C673 (by §16.210 C511):` with `for the <reason> purge at <iso>: <how>` (C673). Lane D.
(75) The rehearsal kit's brief is the lead's, and COMMON.md's kit note says where the kit's lists live. Lane D adds
one sentence to COMMON.md's kit paragraph, outside the backticked commands: dirs.txt and manifest.sha256 are the
fixture's, under ~/.cache/agentsync-bring-back/fixtures/v1/ (or copied beside <root>/fixture/tree), and both checks
run from the folder that holds them; the pinned commands and test_deploy_pack.py:357-358 stay (C664's (1), makes
worse True). The lead writes into round 33's brief through tools/edit.py ruling 61's shasum clause and when the
folder check runs (C664's (2) and (3)), one sandbox root per round that stops if it exists, scenario B's helper
source naming the previous round's root (C605), the KIT RULE's bounded helper copy under its six conditions (C604)
and kitcheck.sh's homes found from the root (C630).
(76) Lead rulings on the triage's open questions. Ruling 63 keeps one FAIL per lost file, each in the folder's
words: C601's verifier measured that dropping a reader's error lets the sync run and, with no hold, leaves status at
exit 0 over a queued purge; one fix is still what the person is told (status's NEXT, the sync's error, step 1's
single folder tail). The no-copy acknowledgement is `--folder-lost`, one name in lanes B, RB and D. Lane B builds
C625 right after C606, C667 and C610 (a credential's converted text kept in the converter cache is close to data
integrity). Ruling 70's code half is words only: the preview's text changes, never _erased_since, _erased_and_gone
or lost_queued. The plan's not-built line for C508's NEXT rewrite is the lead's (ruling 67 builds it). The kit
halves of C604, C605, C630 and C664 (2) (3) are the lead's, in round 33's script; lane D writes only ruling 75's
COMMON.md sentence. Old rehearsal roots under /private/tmp need no cleanup step: macOS's /tmp cleaner removes files
over 3 days old. CONTRACTS sections: RA §16.213, RB §16.214, B §16.215, E §16.216, D §16.217.

ROUND 33 RULINGS (lead; still binding, as extended below):
(77) An audit trail or governance folder agentsync cannot read is never a lost trail. A governance
_audit_read(state_dir) -> (events, read) reads a missing file as read and empty, and OSError, ValueError or
UnicodeDecodeError as not read; audit_trail_lost and audit_trail_loss_said keep folder_lost_case first, then answer
False for a refused, dangling or file-shaped governance/ or an unread trail, while a deleted audit.jsonl or a moved
governance/ still reads as lost. accept_lost_suppression, --dry-run included, first raises that folder's or file's
own error and fix (say `chmod 600 <state>/governance/audit.jsonl`), so no acceptance writes into it; doctor's
governance.audit FAILs a trail that is not UTF-8, and a lost-list FAIL beside an unread trail keeps `the audit trail
records` (C741: lane B, FIRST; data integrity: an `audit-trail-lost` line written into a trail agentsync never
read).
(78) A hold stops `purge --queue --forget` on every branch. forget_queued's found_nothing branch, before its dry_run
return, runs _refuse_if_held(blocking_holds(active_holds(<root>, load_governance(<config>)), <the queued source and
every source among its stored aliases>), "purge") and raises `not forgotten: stays queued: <the hold's refusal>`,
_forget_probe's words; the unmarked branch stays, and a hold list that cannot be read raises as in the queue run.
Forget and forget --dry-run exit EXIT_FAILED, the entry still queued and no purge-forgotten line written, under hold
all, a lost hold and an unreadable holds.json, and succeed after the release. Ruling 69's help (`refused while it
reaches a page or a hold covers its source`) is then true and does not change (C778: lane B, FIRST after C741; data
integrity: a queued purge taken off the queue while a hold covers its source).
(79) Once the audit trail has lost a purge line, each line about a lost record credits what keeps that record. One
governance helper tells whether a lost record's own purge line is in a read trail, counted as audit_trail_lost
counts (an unread trail holds it, ruling 77); suppressions_lost, _queue_lost and accept_lost_words credit a record
missing from it to purge-record.json (ACCEPT_PATH_KEPT_RECORD), one in it to the audit trail (ACCEPT_PATH_KEPT),
both when mixed; audit_trail_lost alone still picks the lead, the put-back, `--folder-lost` and the act's gates
(C742 with C755). accept_lost_suppression returns, decided under the lock, whether it writes the loss line (or
would, on --dry-run) and whether a record it accepts is missing from the trail, and cli's words follow those, never
args.folder_lost or a second read: purge-record.json's path and fingerprints for such a record, FOLDER_LOST_DONE
only when written, else `the old audit trail's loss was already recorded` (C707, C780, C743 with C744). Lane B sends
the words first to lanes RA and RB.
(80) purge --help and accept_lost_suppression's docstring say what --folder-lost does since C606's review. The help
reads `with --accept-lost, your word that no copy is left of an audit trail that has lost purge lines
purge-record.json keeps (the governance folder missing, or re-created by a later write with a new trail): the first
time, it ` + FOLDER_LOST_SAID + `; refused while the trail has lost nothing and records no such loss`, under a
format_help test that it names purge-record.json and lacks `only while agentsync's governance folder is missing`;
the docstring says the same, judged by content, the line written once, a later acceptance needing no folder_lost.
§16.215's clauses at :35918 and :35922 get one dated CORRECTED line in that commit (C784 with C779, C745, C758 and
C766, one commit: lane B).
(81) The heartbeat listing note beside a governance loss says what its FAIL says.
doctor.listing_state_governance_note reads `has lost a purge that purge-record.json records: agentsync's governance
folder is missing` with governance/ gone (C722's `that` only, so the ending and the phrase entry stay; C711's `,
with ... missing` is not built), `has lost a purge that purge-record.json records: agentsync's audit trail no longer
holds it` where the FAIL credits purge-record.json with the folder there (ruling 79's helper; C747, its phrase in
both lists), else the audit trail's words. Lane RA lands _LISTING_GOVERNANCE_RE reading the old and both new forms
first, then lane B changes doctor.py:268. Lane RA files the folder-form note beside a FAIL in _GOV_GONE's lead under
the folder's `not judged` words, keyed on state read before the loop, never on bucket membership (C734).
(82) The report and the installer read the trail-lost state in lane B's words. setup_report gives a
governance.suppressed or governance.purge_queue FAIL that credits purge-record.json with governance/ there
(TRAIL_LOST_HEAD's lead, or ruling 79's credit after the first --folder-lost) its own Doctor bucket, outcome cause
and Purge queue words, matched by alternative phrase sets beside _LOST_LIST_PHRASES (never appended to its tuples)
with the report's own getattr read, the folder-gone branch first; a FAIL without those words stays unexpected (C735
with C740 and C756: lane RA). install.sh's LOST_HOLD_PY adds `-trail` on the same credit after the -govgone test,
with an arm at every lost-queue and lost-suppressed site, never a word no arm matches (lane RB). The person's kinds
read `the audit trail or purge-record.json records` in every copy, in one lane RB commit (C712).
(83) A read the report did not get is `not measured` or `not read`, never a fact. One helper names a runner's
non-answer, rc 124 with the runner's own note (`timed out after ` or `skipped: the report's time budget`), for clt,
_background, devtools, _checkout_facts and _install_source: Background `not measured (<why>; plist present|absent)`
with the advice once on the Summary's line (C720), the checkout's origin, origin/main and uncommitted changes `not
read`, never `no origin/main` or `(git cannot read its .git)`, and the install-source line ending with the advice,
which lane RB's install.sh:7341 grep reads (C702 with C716); the evidence line counts none. _IDLE_AGAIN stays
byte-identical: every advice site goes through _idle_again(r), `this copy is not for sending` in a stopped copy
(C718), and AGENTSYNC_REPORT_TIME_LIMIT, clamped 12 to 60, is named in Run metadata and install.sh's re-run NEXT,
never in _IDLE_AGAIN (C726). Lane RA.
(84) A warn the Loop line could have named is `not judged` when the Loop line was not read. _loop_line records why
on one fact, r.facts.loop_unread_why (the except branch, the no-hook return, the loop_next_text failure), in
_sort_doctor's cache key; after the expected and from_* buckets, a heartbeat.<id> warn opening _LISTING_INCOMPLETE,
a governance.purge_queue warn or a governance.hold warn goes to `not judged (the Loop line was not read: <why>) N:
<check> warn`, before `unexpected`, with `a report written when this Mac is idle sorts it` only for a time-out or a
busy state file. Every other warn stays unexpected, a FAIL never moves, an older CLI's plain-list hook stays
unexpected, and doctor's note and the evidence line do not change (C721 with C703 and C706, one build: lane RA).
(85) Ruling 58's one-bracket form holds in the hold cut, the no-end-line line, the helper lines and step 2's NEXT.
The redacted lost-hold FAIL reads `(owner not shown; kept by <file>)` when no time stands between, both twins and
their pins in one lane RA commit (C710); a run with no end line reads `has no end line (<steps>)`, then `, its
closing status <n>` if any, then `, <state>` last (C764 with C771's F1: lane RA); a helper's failure takes the colon
form, `<label>: not built: <reason>`, in ocr._entry, doctor and the report, `_ended`'s phrase unchanged, under a
bracket-depth test over every helper at signals 1 to 31 (C719: lane E, lane RA's half on its sha); step 2's NEXT
says `also the person's to fix: <fixes>`, with no `))` (C736: lane RB). New installer words that land inside a
bracket carry none of their own (ruling 86).
(86) Every recovery step status and the installer give works when followed, and every stop that follows is named.
Under a hold covering a lost queued purge's source (the hold record's, the state's or sources.toml's), _queue_lost's
no-copy branch, in both forms, names the records owner's release before the hand purge, absent when no hold blocks
it, and install.sh's queue clause ends `by hand once the records owner releases the hold, since setting it again
keeps every purge refused` (C701: lane B; the installer's words lane RB). With both lists lost, LOST_HOLD_PY keeps
governance_lost as the gate and asks the other reader in its own try, and every tail takes one case arm per list,
suppression first, the queue's clause joined `, and run each such purge by hand ...` (C776 with C767: lane RB).
Every config_failed NEXT ending `once it is fixed` names the second stop (C737: lane RB), and a damaged
purge-record.json no longer hides a suppression list's loss (C768: lane B, its install.sh case lane RB).
(87) Every line beside a stop that blocks every sync says no sync runs until its [FAIL] line is fixed, and names no
actor (ruling 65, extended). Doctor's heartbeat [ok] tail reads `no sync lists it again until the <check> [FAIL]
line is fixed`, never `the person` (C746, amending ruling 65's C633 words, whose COMMON.md text stays); a heartbeat
[warn] beside a stop loop._sync_stopped_on names (the lock, governance.suppressed) gets the note `as of its last
walk: no sync lists it again until the <check> [FAIL] line is fixed`, its fix None, while a hold-record.json the
sync runs over keeps the old fix (C729, in C746's words); exclude-empty with folders left beside a stop says `the
next sync that runs lists <id> without that folder` when its pointer already gives the no-sync clause (C731). Lane
B; the §16.51 Q14/Q35, C631 and C633 lines move in its commits.
(88) The records page says which governance loss stops the sync. data-governance.md's missing-folder bullet says the
sync refuses while purge-record.json keeps a purge the suppression list or the purge queue lost, and runs while only
hold-record.json keeps a hold, purge and compaction suspended for its scope, status FAILing governance.hold; every
fix, and the sync's stop when it refuses, leads with the whole-folder put-back (C757 with C783: one sentence, the
pinned phrases byte for byte, one dated line at §16.217). It adds the re-created folder: the lost-list FAILs open
with TRAIL_LOST_HEAD, plain --accept-lost and its --dry-run are refused until the first `--folder-lost` writes
`audit-trail-lost`, after which plain --accept-lost accepts the rest (C759). It never says a lost hold blocks
--accept-lost or the hand purge. Lane D, on lane B's final words.
(89) The setup guide quotes what the report and the installer print now, each rewrite with its condition. The Loop
paragraph (setup-feedback.md:555) adds the stage's `not read: status says ...` and `unknown (no status loop line)`
forms, the `Doctor's <check> [FAIL] line` rewrite only when Doctor prints that line, the no-install bracket with
lane RA's C709 `ran only install.sh --list-folders` variant, and the lost-hold wait's Doctor pointer only on its
condition (C761 with C739, one edit). The Repeat conversions row says `and also under failed or unreadable`, its
test reading the report's whole sentence, with a C-entry rather than a rewrite of :36345 (C760). Step 1's C607
sentence gains the folder form in placeholders (`<what>`, `<fixes>`, `<check>`) and step 2's NEXT, pinned on both
sides, written after lane RB's C776 and C737 land so it quotes their joins (C762). Lane D.
(90) Lead rulings on the triage's open questions. Duplicates are built once in one lane (the notes list them).
Ruling 68 binds every new literal holding record, records, legal, label or review, its whole phrase in both lists in
its own commit, the old kept while an older build prints it (rulings 79, 81 and 86's words, C730's), and C728's
`could not parse or open its label data` goes in its C609 order: lane B sends it, lane RA lands the regex, re-render
and phrases, lane B changes cli.py:734-735. COMMON.md's ruling 65 and 67 text stays; C746 and C709 amend it. C769's
parts go to lanes E (the conftest pandoc warm-up), RB (install_sh's bound) and D (gate.sh's and ship-land.sh's
signal words). The kit items are the lead's, through tools/edit.py: C715's durable helper copy before 2026-11-04,
C713, C724, C725 and C774's brief half. CONTRACTS sections: RA §16.218, RB §16.219, B §16.220, E §16.221, D §16.222.
The lead made C715's copy on 2026-11-01: ~/.cache/agentsync-bring-back/b13/helpers-4a42af9/{ocr,media,speech}/ with
MANIFEST.sha256, each file cmp-equal to round 32 B's home and round 33 B's helpers-pre.

ROUND 34 RULINGS (lead; still binding, as extended below):
(91) A path in another home is cut whole, and an organisation's spellings are cut whichever name registered first:
one rule for both reports. Redactor._compile adds one alternative after the literals, so the home literal wins, as
C822's spec gives it (`/Users/` not right after a word character, `.` or `-`; `Shared`, the registered login and a
`<` left alone; one escape-aware component, then an optional `/` tail up to whitespace, a quote, a backtick, a
bracket or `,;`; no trailing slash needed), and _replace returns `/Users/<path>`, uncounted, the form --source-local
already gets. Never a `<user-N>` prefix: _cut_path walks past it and prints the org it hides today. /var/folders
stays _TMP_RE's. redact_stream makes the same cut after the pairs pass, every stage LC_ALL=C, no lookahead; that
half also leaks an org and a folder, so it goes first in lane RB (C822: lane RA FIRST, lane RB's half FIRST; data
integrity; makes worse True). Redactor.add's known branch takes C870's block, which covers C835: a context, joined
or org value keeps its own table and mode, `setdefault(_norm(value), known)` and its literal, never a bare
_fuzzy.setdefault, so `network` stays whole and `net-work` is cut (C870 with C835: lane RA, right after C822; data
integrity; makes worse True). The legend, the uncounted sentence and setup-feedback.md's table say `a path in
another home` and its limit, an unquoted path with a space keeping its tail after the first space; §16.224 gets its
dated line. Tests assert the value absent in both reports, never the two reports equal, with `/Users/Shared` and a
sandbox `/Users/<user>/` kept.
(92) A lost queue's fix asks the records owner only for a release the hand purge really needs, amending ruling 86.
_lost_queue_holds returns (known, conditional): a sourced record gives blocking_holds(holds, [sid]) to known, a
sourceless one blocking_holds(holds, []), hold all and sources.toml's hold, which always refuse it, and every other
hold in force goes to conditional, never dropped, since a sourceless purge that finds nothing is refused by every
hold (`affected_sources or None`), and dropping them brings back C701. _queue_lost keeps `once the records owner
decides to release ` word for word for known, the substring ruling 93 tests, with `setting any of them again` over
two holds or more (C841). A conditional clause still names each hold and never holds that substring, for example
`after a --dry-run of each purge, which <holds> refuses only if it reaches that source or finds nothing, and then
only the records owner's decision lets it run, `, its phrase in both lists in that commit. No held attribute goes on
GovernanceLostError. queue_lost's cache key is the str `queue_lost, held` when gov.hold is set, with a comment that
any other gov field reaching the words grows it (C842). The `as purge reads them` docstring, :2672's words and
C701's `every hold for an every-source selector` are corrected. Tests: two sources, a hold on beta, a sourceless
alpha record, the page-resolves and nothing-resolves outcomes, the purge running with beta held and the 0-match
purge refused (C874 with C840's governance half, C841, C842: lane B, FIRST; data integrity: a legal hold released
for nothing). Lane B sends the clause to lanes RA and RB first.
(93) Step 1 reads status's own judgment of the hold, never a lost-hold word (C840 with C879, C871 with C859, C803
with C812, one build: lane RB, FIRST after ruling 91's half; data integrity: the words ask for a release nothing
needs, or omit one an intact hold needs; reverses round 33's not-built reason for a held hold). LOST_HOLD_PY reads
gov in its own try, passes it to governance_lost and queue_lost, keeps the queue's error whichever returned it, and,
only when the build has governance_or_none and a queue error exists, appends last, as its own comma item,
`,queue-held` if `once the records owner decides to release ` is in that fix, else `,queue-free`: never a suffix on
lost-queue, so every arm and adjacency still matches. lost_list_tail and folder_gone_words share one three-state
rule: queue-held takes the release words, queue-free the plain words, no marker with a lost-hold word today's (an
older build, C865's test), the joiner chosen by lost-suppressed-govgone apart from the release. A hold lost reads
`once the records owner releases the hold, since setting it again keeps every purge it covers refused`; a hold still
listed or in sources.toml reads C803's `once the records owner releases the hold that covers it, as its status line
says`; the old phrase stays in both lists beside the new (ruling 68). A hold only ruling 92's conditional clause
names reads queue-free, and the purge's own refusal fails closed. Fixed words only: no scope, owner or selector.
second_stop_words keeps its arms. Tests: a hold-list hold, a sources.toml hold with the folder there and gone, a
hold on another source in both forms (plain words, and the purge runs), both lists lost, an older build.
CONTRACTS:36588-36590, :36620-36621, :36723ff and :36730 get dated CORRECTED lines.
(94) With governance/ gone, the words describe the folder and say which line each act fixes. GOVERNANCE_GONE_HEAD
reads `is missing (the folder where agentsync keeps any hold list, its audit trail, the purge queue and any
suppression list)` and install.sh's two tails `agentsync's governance folder, where it keeps any hold list, its
audit trail, the purge queue and any suppression list, is missing, $GONE_WHAT`, never branching on hold-record.json
or purge-record.json, which are best effort and would under-report a legal hold (C810: lane B, after lane RA's
_GOV_GONE reads the new head and the old at every reader, or the report loses the folder's bucket; the tails lane
RB; the old phrase stays in both lists). Where lost-queue-govgone joins lost-hold-govgone under the release words,
folder_gone_words rebuilds GONE_FIX instead of appending: `... from the copy kept; with no copy, the records owner
sets the hold again or releases it, which fixes the hold line only, since setting it again keeps every purge it
covers refused and the stop with it, and once the records owner releases the hold they run each such purge by hand`,
--folder-lost's clause before the run when the suppression list is lost too; the hold-only arm and
GOVERNANCE_GONE_TAIL's pin stay (C805: lane RB, on ruling 93's rule). Only the where-to-read pointers name both
lines when a hold is lost too: GONE_CHECK, plural `lines` in its tails, and doctor's note ending `the
governance.hold and governance.purge_queue [FAIL] lines say what to do first`; `no sync lists it again until the
governance.purge_queue [FAIL] line is fixed` and the wait's until-clause keep the guard's check alone, and a queue
lost alone stays singular (C811: the note lane B, after lane RA's plural in _LISTING_GOVERNANCE_RE; GONE_CHECK lane
RB).
(95) One credit rule in every reader, correcting ruling 82: a list is trail-lost only when every lost record is
credited to purge-record.json alone, lane B's record_only, whatever leads the detail. Lane RA first lands C833:
_outcome_cause reads both lists before it returns, _both_lost_cause joins _lost_cause and _trail_lost_cause in the
Doctor line's order, every disjoint pair joins _LOST_CAUSES, under a test over the five real states. Only then
_trail_form loses its TRAIL_LOST_HEAD branch, `queue-mixed-lead` turns False with a suppressed row and an
all-in-the-trail row beside it, and ruling 82's passage and the C735 paragraph get dated CORRECTED lines;
_GOVERNANCE_TRAIL_LOST_RE and _GOVERNANCE_LOST_RE stay (C836, makes worse True if it lands first). Lane RB's
list_word strips TRAIL_LOST_HEAD by getattr and reads the body: a purge-record.json credit alone adds `-trail`, both
credits add `-mixed`, the trail's alone the plain word; a build before record_only keeps its credit, and a test
holds the word to record_only on every form (C872 with C836's installer half). Each -mixed word has its own arm
before the plain one in lost_list_tail and second_stop_words, with its LIST_LOG and lead, saying `kept by the audit
trail or purge-record.json` and `the audit trail or purge-record.json records as queued and not done`, no bracket in
SECOND, the phrases in both lists. The report's mixed Doctor bucket words are not built; _lost_queue_line already
names both stores.
(96) A read the report did not get says what cut it (ruling 83, extended). git, xcode-select and launchctl print
keep their fixed 3 s and 5 s caps: scaling them cost Status and Loop (65.0 s against 32.9 s on a hung Mac), and
C802's shared allowance still re-cuts a 6 s git, so C802 builds only its early stop: after the first unanswered
read, _checkout_facts, _rewritten_url, _git_keeps_empty_url, rev-list and _last_fetch read no more. A time-out says
its seconds (` (git did not answer within 3.0s)`, `not read (git did not answer within 3.0s)`, ` (commit and origin
not read: xcode-select did not answer within 3.0s)`); a spent budget keeps `the report's time limit` (C834). C873's
reads take that split: _git_keeps_empty_url returns `bool | None`, _rewritten_url `tuple[str | None, bool]`, a cut
prints `origin: not read`, a cut rewrite on a known URL keeps the origin and adds a `URL rewrites: not read` note,
and _git_cut matches on the full argv. A cut Status goes in a new r.facts.status_failed, never loop_unread_why or
loop_unread: the Loop line reads `not read (status did not finish in time)` or `not read (status failed: <why>)`,
`unknown (no status loop line)` kept for a status that answered without one (C801); its section says Doctor's `Not
run:` or `Cut off:`, an errno staying `failed`, and the four probes read `not measured (...)` through _probe_ended
(C820). Background's job lines drop the advice and the Summary's background line ends ` (see the evidence line)`
when that line carries it; _install_source's and the doctor line's stay (C819). _IDLE_AGAIN stays byte-identical and
none of these joins the evidence count. Lane RA, sending lanes RB and D the words first.
(97) The NEXT tells the person a part was cut and names only a remedy that can work, and the prompt relays it.
install.sh's marker grep stays byte-identical; a second grep in the same `if` reads a cut Loop line, _NOT_IN_TIME's
and _BUSY_WHY's forms and ruling 96's `status did not finish in time`, under a pin to setup_report's words and
negatives for `no loop hook` and a crash (C821). The `AGENTSYNC_REPORT_TIME_LIMIT=60` clause stays when a doctor,
evidence or Loop line fired or a line says the budget was spent; when only timed-out git, xcode-select or launchctl
lines fired, the NEXT says to run again once idle and names no variable; an older build's words keep the clause
(C834: lane RB, on lane RA's words). setup-feedback.md:28-29 says which waits the variable lengthens. The prompt
goes to v34 now, so the next round rehearses the prompt the operator will paste (C823, the lead's ruling). Its
Finish reads `... --report-only again once it is idle, or, if that line names AGENTSYNC_REPORT_TIME_LIMIT=60 and
this Mac stays busy, with that in front of the command, before I copy the file back; do not run it again yourself;`,
so the agent never offers the variable where it cannot help. Lane D owns the one commit, on 357dc812f's file list:
the README block, its version and digest, CUT_OFF_TOLD, SETUP_PROMPT_COMPAT, setup_report.PROMPT_VERSION, the
version pins, the issue form and the dated X3 line. Every other lane leaves the block alone.
(98) The records page and the setup guide say what the code does, each with its condition, amending ruling 88.
data-governance.md:162-163 and :111 say a hold in force on the page's source, lost or set again, refuses the hand
run and its --dry-run, so it waits for the records owner's decision to release it, and a hold on another source does
not refuse it: worded as their decision, never an instruction to release, and never resting on the FAIL for a docs=
selector. The page still never says a hold blocks --accept-lost; test_deploy_pack.py:7371-7372's regex narrows to
that, as C851 gives it, with a positive pin that the bullet names the release before or beside the hand run (C851,
on lane B's ruling 92 words). :173-174 says `putting the list's own file (suppressed.json or purge-queue.json) back
from a copy you trust`, its pin moved (C854, built with C851). setup-feedback.md:1301-1306 and §16.222 take the
Command Line Tools line out of `each such line`, the `not measured (xcode-select -p ...)` quote kept, and say it
carries no advice and that read alone asks for no re-run, the code unchanged (C852 with C878, one edit, on lane RA's
C819 and C834 words). Step 2's NEXT loses the log word and held clause only step 1 prints and gains its real
second-stop and -trail words; step 1 gains its config-stop hold leads and the exceptions to `any config-step stop`,
the strings pinned at test_deploy_pack.py:8857-8862 kept (C855, after lane RB's rulings 93 to 95). Lane D.
(99) Ruling 74 holds for the passages edited in place and the stale quotes round 33 left. §16.214's `also the
person's to fix (<fixes>)` and §16.129's U28 sentence come back, each followed by a dated line, `Amended <date>,
§16.229 C856 (by §16.219 C736)` and `(by review backtest pilot-s1/er-13#6)`, claimed by one paragraph, no C712 line,
and the third pin, for the C737 line, is required (C856, makes worse True). §16.147's and §16.140's --report-only
clause get `§16.229 C857 (by §16.219 C726)` lines under a claiming paragraph; §16.34's `failed` row, inside its
cell, the X57 line and §16.35's Doctor bullet get `§16.229 C857 (by §16.221 C719)` lines, pinned in a round 34
_PARAGRAPH_AMENDS table added to the pins tuple; no quote is rewritten (C857, makes worse True). C758's CORRECTED
line is reworded in place with I73's one dated line, --folder-lost's clause alone keeping its condition (C858). Each
line takes §16.229's heading date and is claimed or pinned. Lane D, after the lead's reservation.
(100) A gate re-run clears only what it ran and convicts nothing a signal stopped. rerun_alone returns a status
above 128 through part_stopped, statuses 1 to 128 staying plain red, and part_once and run_part call it as `rc=0;
rerun_alone ... || rc=$?`, never the `&& ...; then rc=0` form that drops the status; ship-land.sh already reads it
and stays (C877). tests/_gate_journal.py journals no teardown skip, only a call, a failure or a setup skip, so a
failed test stays in the retry set and last-record-wins keeps resume boundaries (C876, which replaces C866's step
3). A collection error is journaled apart and the `failed` verb exits 1 on it, gate.sh staying red by its existing
path (C866's steps 1 and 2: optional in the spec, built under the operator's 2026-10-10 ruling). Tests through
_rerun_part in land and lane modes, SIGKILL and SIGTERM on the second run, a failed call with a skipped teardown
beside a flake, a collection error beside a flake, and a ship-land case; §16.223 cites them by a dated line. Lane D,
FIRST: every land gate runs this code.
(101) Lead rulings on the triage's open questions. Duplicates are built once in one lane (the notes list them): C812
with C803, C859 with C871, C852 with C878, and items 1 to 4 of C863 and C865's defect under ruling 93. C809: `that`
goes only where two nouns meet, setup_report.py:10006, :10017, :14060-14063 and :14200, so the item moves to lane
RA; TRAIL_LOST_HEAD, cli.py:414, _TRAIL_LIST_PHRASES and governance.py:2693 stay, grammatical as written, and a
changed head adds a second form to every reader of a recorded stop for no gain. C830 is the contract line alone:
lane D's one dated line scopes `names no actor` to doctor's [ok] tail and C729's note, which is C863's item 6 too,
and doctor's notes keep the true owner. Ruling 68 binds every new literal holding record, records, legal, label or
review, its whole phrase in both lists in the commit that prints it, the old kept while an older build prints it.
COMMON.md's rulings 84, 86 and 88 stay as written; C818 and rulings 92, 93 and 98 amend them, each by its CONTRACTS
dated line. C845: the lead reserves §16.225 to §16.229 after reading the highest `### 16.N` on origin/main, before
any lane starts: RA §16.225, RB §16.226, B §16.227, E §16.228, D §16.229. The kit items are the lead's, through the
next round's tools/edit.py, never round34-workflow.js: C807, C814, C815, C816, C824, C825, C828, C867, the note and
neardup halves of C865, and C868, whose allow rule stays the operator's.

ROUND 35 RULINGS (lead; still binding, as extended below):
(102) A path in another home is cut whole however it is spelled, completing ruling 91: one rule for both reports.
Kept: this home's literal (`~`), `/Users/Shared` and the whole component `<user>` (an already-cut `<path>` as it
is); every other component is cut, one an earlier pass made a placeholder or that opens with one included
(`<value>`, `<org-1>`, `<name>`, `<user>.old`, `<user>\ 1`): install.sh:4098's `substr(comp, 1, 1) == "<"` becomes
`comp == "<user>" || comp == "<path>"`, never C941's `^<[a-z]+...>$`, which keeps `<value>` (C901 with C992). This
home's literal wins only where the home ends: the two home pairs at install.sh:2778-2779 take flag t, Python's trail
(C939), and for a home under /Users/ only, both trails refuse a `\` escape not followed by `"` (`(?!\\[^"\n])` in
Redactor._compile, a pair flag in the shell; C941, makes worse True), so `/Users/dana.old`, `/Users/dana-1` and
`/Users/dana\ 1` are another home. A home behind `/System/Volumes/Data` or `/Volumes/` at any depth (blanks allowed,
up to the first `/Users/` not after a blank or `/`), with `(?i)Users` and a non-possessive `/+`, is cut: a /Volumes
match prints `/Volumes/<path>`, any other `/Users/<path>`, uncounted; the login stays exempt after
/System/Volumes/Data, not under /Volumes, and the shell, whose home pair has already written that one case
`/Volumes/<vol>~/`, states it in its legend (C947, C979). Python takes a registered value holding a blank as path
characters, as the shell's pairs pass does, and in both a component after `/CloudStorage/` or opening `OneDrive - `
runs to the next `/`, never past a quote or backtick (C912, makes worse True: its token rule is not built); after an
unquoted blank the cut goes on over each space- or tab-separated word that holds a `/`, has no `=` or `://`, does
not open with `-`, `/` or `~` and is not digits and `/` alone (C981). Both legends, the comments and
setup-feedback.md:94 say both reports cut it, every stop (blank, quote, backtick, bracket, comma, semicolon) and the
limit left (C946, makes worse True: words only; C945 with C966). Tests assert each value absent in each report, with
C950's five byte classes, `/Users/Shared`, `/Users/<user>/`, `~/x`, a URL and `3/14` kept and `GET /users/me/` an
accepted over-cut; test_report_redaction.py:121 reads `/Users/<path>`. Lane RA builds the Python halves FIRST (C912,
C947: privacy, data integrity) and lane RB the shell halves FIRST (C901, C992, C939, C941: privacy, data integrity),
each including the other lane's items' halves; lane RA sends the cut, its cases and the limit words first.
(103) A source's path never enters the docs repo's commit history (C934: lane B, FIRST; data integrity).
cycle._commit passes each alarm through one helper as it builds `notes`, before commit_body collapses whitespace:
each spelling of every configured source's `path` and of its cloud_provider_root, longest first, becomes `<source
folder>`, then _homeless; convert._common._spellings goes public in that commit. acc.alarms, the terminal, STATE.md,
the carried alarms and cli.py:849 keep the full path. The one site covers cycle.py:1208, :1373-1398, :1415, :1423,
:1428, :4247 and any later alarm. Tests read `git log --format=%B` for neither str(Path.home()) nor the provider
folder's name (`OneDrive-Contoso`), a home-only pin being too weak, and STATE.md for the full path. Bodies already
committed stay: rewriting the person's docs history is the operator's call, not this round's.
(104) Step 1's queue word tells two more states, amending rulings 92 and 93; status still judges and the reader
reads its judgment. Ruling 92's `no held attribute` gives way to one: GovernanceLostError.kept_hold (bool, default
False), set in _queue_lost to `any(h.origin in ("config", "state") for h in blocked)`; LOST_HOLD_PY reads it by
getattr, so an older build keeps today's words, and appends `,queue-held-kept` in place of `,queue-held` when the
hold is lost and another still covers the purge; its queue_hand_words arm, ahead of the lost-hold arms, gives C803's
`once the records owner releases the hold that covers it, as its status line says`, and folder_gone_words keys its
two rebuilt arms on the lost form of HAND, never `?*` (C967: lane B's half right after C934, lane RB's right after
ruling 102; data integrity; no new phrase). LOST_HOLD_PY then tests the release marker first (`,queue-held`, so a
known and a conditional hold stay held), then `or finds nothing, and then only the records owner\x27s decision lets
it run` (`,queue-maybe`), else `,queue-free`; the queue-maybe arm, after queue-free and before every lost-hold arm,
reads `after a --dry-run of it, which a hold on another source refuses only if it reaches that source or finds
nothing, and then only the records owner's decision lets it run`, and folder_gone_words' two queue-maybe arms
precede the `:?*` rebuilt arms, so it never carries release words; the phrase enters both lists in that commit (C993
with C951, makes worse True each: C993's form; a sibling test beside :13472, never rows in that table, with C951's
known-plus-conditional row). Lane B builds C874's step 3 as promotion only: _lost_id_sources resolves a sourceless
stable-id record read-only under its own except, a hold on its resolved source moves to known, every sourceless
record stays conditional for the rest (C980, makes worse True: the drop half is not built, since the id can sit in
another source's history, gap I91).
(105) A doctor the report's call gave up on stops at that deadline, its last named check is the one it was in, and
its waits end (C804's regressions; lane B, after C934 and C967's half). One helper, stop_past_deadline(), raises
TimeoutError once RUN_DEADLINE has passed, never TimeoutExpired, since _doctor catches only TimeoutError and the
raise can reach box first; it runs in run_checks' loop and in cli._extra_checks' loop before saw_group, outside each
try, and in checks_once after _status_checks, before `seen.append("")` and `fixes[:]` (C952, C953, C978; C978 makes
worse True: no margin, RUN_DEADLINE stays the call's limit). saw_group tells nobody past the deadline and
checks_once's `seen.append("")` goes through it, so the name never depends on which thread wakes first (C921 with
C925, makes worse True each: the raise above is a cut, never a short result handed back as whole). _run's time-out
kills the group, then `proc.wait()`, never communicate(), then a bare raise (C954). A program the deadline left
unstarted raises _NotStarted, a TimeoutExpired, and _timed_out says `<what> was not run: the report's time limit had
already passed, so this check says nothing either way`, WARN, with no why or fallback; a wait the deadline shortened
says `did not answer within the report's time limit`, an unshortened one its seconds; _importable lets _NotStarted
skip (C917). The docstrings and C804's passage say a passed deadline stops run_checks; standalone doctor, with none,
is unchanged. Tests are deterministic, through the real cli._report_hooks() (C952's patched _CHECKS and a delayed
_read_cut_off): the hung check named, later checks never run, no `within 0s`, no Loop NEXT naming an unprinted
[FAIL], and two hung programs leaving nothing alive in a fresh process; lane RA writes the report-level cases on
lane B's sha.
(106) Every child the report's call starts ends with that call, and with agentsync (C804 completed: lanes B, E and
RA). Lane B moves RUN_DEADLINE, the pid registry and stop_past_deadline into a new leaf module named in §16.232,
doctor re-exporting the same RUN_DEADLINE object, with a run() wrapper: with no deadline exactly subprocess.run;
with one, timeout=min(timeout, deadline-now), TimeoutExpired before any spawn once it has passed, the pid
registered, the child left in the caller's group and ended by os.kill, never killpg; gitops._run, governance._git
and launchd._run_launchctl call it, and none gets process_group=0 (C955, makes worse True). doctor._run starts
C956's pipe watcher (`echo; read -r said; [ "$said" = done ] || kill -KILL 0`) in its own group, waits for its first
line, starts the program with process_group=watcher.pid, registers and ends the watcher's group, and in a finally
writes `done` with OSError suppressed, closes the pipe and reaps it; no signal handler (C906 with C956, makes worse
True each: about 20 ms a run). Lane E gives ocr.py a START_BY ContextVar, a start gate in _run_helper before any
spawn, Popen under the helper's own limit, a locked _LIVE of Popen objects and end_helpers(), the helper kept in the
job's group (C961, makes worse True); lane RA's _Run.call sets ocr.START_BY beside RUN_DEADLINE and
_end_doctor_children calls ocr.end_helpers() by getattr, on lane E's sha. Tests: SIGTERM, SIGHUP and SIGKILL to the
process and to its group leave no child; a hung helper behind a blocked doctor hook starts nothing.
(107) A read the report did not get, or never started, says which, and never reads as a value (ruling 96, extended;
lane RA). One _cut_words(r) gives `git did not answer within 3.0s` or, for a spent budget, `git did not answer
within the report's time limit`; a cut rev-list prints inside the one fetch bracket, `on origin/main: yes (how far
behind not read: <words>; no fetch since the clone)`, `ahead or behind` on `no`, never two brackets side by side
(round 13 O14); a cut _last_fetch reads `as of the checkout's last fetch, date not read: <words>` unless
install_source_cut already said it (C940 with C965 and C984: C940's words; C965's rev-list, --git-path and
--git-common-dir rows with a linked worktree; C984's no-variable assertion). A call the spent budget never started
says `not started: the report's time budget was used up`: the Loop line `not read (status not started: ...)`, its
NEXT half `not read (not started: ...)`, each keyed on that call's own limit, _loop_unread_head keeping the idle
advice for both (C904 with C919, C904 makes worse True: its form, C919's tests). Environment's _probe_ended says
`not measured (<cmd> did not answer within <n>)` or `not measured (the report's time limit was used up before <cmd>
ran)`, clt() going through it, a real exit 124 with other text staying `exited 124: <text>`, rc 124 inside _Run
unchanged (C905); gitleaks' cut version read goes through it (C918). Lane RA sends lane RB the not-started words
first.
(108) install.sh's NEXT names AGENTSYNC_REPORT_TIME_LIMIT=60 only where more time can help (ruling 97, extended;
lane RB). report_time_helps opens with `! LC_ALL=C grep -qE '^- time limit: 60s[,:] ' "$1" || return 1`, pinned to
setup_report._TIME_LIMIT_MAX_S, with C929's rows (C922 with C929). Its doctor grep counts only `^- doctor: (cut
off|not started)` and the evidence line, so a helper's --version time-out, which lane E makes a WARN and lane RA
files under `not judged (<helper> did not answer in time: ...)` before unexpected, never names the variable and
never holds the bring-back (C915, makes worse True: convert untouched). REPORT_LOOP_CUT keeps every alternate and
adds `|(status )?not started: the report`, with no apostrophe (C904's words). A cut rev-list or fetch date needs no
install.sh change (C940's rows, in lane RA's commit). The older-build sentence narrows to `an older build's install
source words name no seconds and keep the clause` at install.sh:7421-7422, CONTRACTS:37508,
setup-feedback.md:1367-1368 and test_install_oneshot.py:5051 (C976).
(109) A NEXT, note or wait points only at a line its reader's own output prints, and at the stop that comes first
(lane B). Status maps QUEUE_REFUSED_NOTE at its render, as the lost-hold waits are mapped, to
QUEUE_REFUSED_NOTE_STATUS (`... the governance.purge_queue [FAIL] line below says why`) when that check prints, else
the bare clause; sync, curate and STATE.md keep the constant (C959). The report swaps _PURGE_YOURS_NOTE for one
_PURGE_YOURS_REPORT_NOTE in checks_once beside C818's listing swap (C903, amending ruling 84 by a dated line). A
[governance] that fails to load prints governance.config's FAIL and the not-ok rows of _governance_lines over a
default GovernanceConfig, never remote, compaction or time_machine, and its NEXT goes to the chmod first (C958).
curate puts `no sync runs until the <stop> [FAIL] line is fixed: run `<BIN> status` and do what its NEXT says` first
when _sync_stopped_on names a stop, loop.py:972 untouched (C957, makes worse True). _sync_stopped_on reads the lock
first, then load_suppressions before recorded_purges, its X_OK test only after a refused read (C933, makes worse
True: no os.access ahead of the reads; a wait naming both lines is not built). One test asserts every `<check>
[FAIL] line` pointer that status, curate and the report print resolves to a printed [FAIL].
(110) The guides and the contract say what the code prints, each with its condition; a quote a later round made
stale gets a dated line, never a rewrite (rulings 74 and 99 hold; lane D, after the lanes' words, citing each sha).
setup-feedback.md:1354-1356 says a hung xcode-select on a checkout is named by the install source's line with the
advice, with dated lines under §16.222 and §16.229 (C968); C820's evidence-line sentence gets a CORRECTED line and
setup_report.py:11532-11533 its comment (C969); `unknown (no status loop line)` also names status not run (C970);
:35-36 gives pandoc's own time-out words (C971), and a sentence after C852's says the macOS, MDM, uv and pandoc cuts
are ruled the same and ask for no re-run (C923, makes worse True: docs only), both on lane RA's C905 words; the
failed-run Items paragraph follows lane RA's C949 order (C975). Each stale C810 and C817 quote gets `Amended <date>,
§16.234 C972 (by §16.227 C810)` or `(by §16.225 C817)` in C857's form, pinned in `_ROUND_35_D_PARAGRAPH_AMENDS`
(C972, makes worse True); C850's re-read gets one claimed dated line under :36998 (C995).
(111) Lead rulings on the triage's open questions. Duplicates are built once in one lane (the notes list them). C902
over C908: `which is missing (the folder where agentsync keeps ...)`, status's GOVERNANCE_GONE_HEAD, the tails regex
unchanged, a dated line for ruling 94's words. C987 over C977 on the timing variable: a re-run clears its part only
when the count of failed ids in its passed set equals the failed count, failing closed and returning 1, with no
AGENTSYNC_TIMING=1 (operator ruling 2026-10-20: a timeout we impose is unknown, retry, never a verdict), C977's land
and lane cases beside FLAKY added to C987's four (lane D, FIRST: every land gate runs it). C991's one-trap stub over
C963's, the production _STOP_S and margin unchanged; C963's monkeypatched _STOP_S of 60 s in the two late tests and
its words at ocr.py:668-669 and CONTRACTS:14890 are built too, since a stall past 9.5 s is the other measured cause
and the pin costs a passing run nothing. C988 over C983 for COMMON.md:994 (`moves to `<root>/trash/<probe>/``, the
pin moved), C806's shasum lines going to the brief; the pair moves to lane D, COMMON.md being a repo doc. C904 over
C919, C940 over C965 and C984, C922's grep with C929's pin. C911 adds the chmod-first step at _state_stop_first's
tail only; _manifest_first's D43 order stays, recorded. C958's NEXT moving to the chmod is accepted. C945 and C966
move to lane RA, since ruling 102 rewrites the same clause. The prompt stays v34. The lead reserves §16.230 to
§16.234 on origin/main before any lane starts: RA §16.230, RB §16.231, B §16.232, E §16.233, D §16.234. The kit
items are the lead's, through round 36's tools/edit.py: C920, C931, C989 (its allow rule the operator's, backlog
8ed9dae0fbc0), and the brief halves of C907, C983 with C988, and C990. Ruling 68 binds every new literal holding
record, records, legal, label or review.

ROUND 36 RULINGS (lead; every lane builds to them; all code-only but ruling 113, which takes the prompt to v35):
(112) A path in another home is cut whole whatever byte sits in it, completing ruling 102 (which completed ruling
91): one table for both reports, and a property test as its proof (privacy; data integrity: lanes RA and RB, FIRST).
`_CUT_BYTES` in setup_report.py gives every character its class: a blank is a space or a tab only (the cut goes on
over a next word that holds a `/` and no `=`, C981, the legend now naming the `=`, C1019); `\r`, `\f` and `\v` end a
cut; a stop is a quote, a backtick, a bracket `()[]{}`, a comma or a semicolon; every other character, a no-break,
ideographic or other Unicode space and every control byte included, is a path character, so `_PATH_END`,
`_PATH_STOP` and the two `(?<![\s/])` lookbehinds become ASCII, built from the table, as install.sh's `STOP` already
is under LC_ALL=C (C1017). Behind `/Volumes/` the cut runs back over every byte on the line to the first `/Volumes/`
that no letter, digit, `_`, `.` or `-` precedes: brackets, commas and semicolons (C1016 with C1064, makes worse
True: volume() loses its character class, the shell moving to Python's over-cut, never Python to the shell), and
quotes and backticks too, since `/Volumes/Olga's Passport/Users/olga/...` prints whole in both today; a path that
opens with a quote still ends at its closing one. `/Volumes/`, `/System/Volumes/Data` and `Users` match in any case,
in sysdata() and in setup_report.py:2078's `/Volumes/<path>` choice too (C1018). This home's literal ends only where
the table ends a component, so `/Users/dana<U+00A0>old` is another home in both trails; a blank right after it stays
this home's end, the one limit left, stated in both legends. A provider folder after `/CloudStorage/` or opening
`OneDrive - ` runs over brackets, commas and semicolons to the next `/`, stopping only at a quote, a backtick, `):`
or `, stored as ` (C1069, makes worse True: so `OneDrive-Contoso (UK) Ltd` stays cut), and agentsync's own
`~/agent-context/inbox` typed form prints uncut in both (C1123). The proof is not one more row: for every code point
from 1 to 127 but `/` and the newline, every `str.isspace()` one, U+200B and U+FEFF, in each slot (a volume name, a
provider folder's name, right after this login, inside another login, between two words after it), with each prefix
as written, lowercase and uppercase, both reports run the same lines (`_shell_cut_at_home` once, the same home in
both) and print the same line, with `olga`, `Fabrikam` and `Contoso` absent except where the table makes that byte a
stop, an end or a blank right after this login, where the line equals the documented cut; a tie test holds
install.sh's `STOP` to the table and volume() to no class. The specs' rows stay as named rows, the combined row at
test_install_oneshot.py:4209-4210 taking Python's result. Lane RA lands the table, the Python half and
`_separator_lines()` (in test_setup_report.py, which test_report_redaction.py already imports) and sends lane RB the
table, the cases and the legend words first; lane RB lands the shell half with the both-reports test in
test_report_redaction.py, in its own commit. The legends, the comments, setup-feedback.md's `<path>` row and the
CONTRACTS lines that quote them move in each lane's commit.
(113) A folder row step 1 shows escaped is marked, equals no other row and can never add another folder (data
integrity: lane RB, right after ruling 112; the prompt goes to v35). A row with no control character prints byte for
byte as today; an escaped row opens with one fixed mark beside SYNCED_MARK, INSIDE_MARK and CONTAINS_MARK, never a
suffix, which listed_folder_names would read as part of the name, in words such as `[name holds a control character:
rename it to sync it] `, so no raw row can equal it and the `?` stand-ins clash with nothing (C1001 with C1116,
makes worse True each: the mark, never a `\` escape alone); list_esc puts the same words beside each such path in a
NEXT: or vpaths line, and the lost-[[source]]-block command is built from list_real through shown_arg, never from
the shown text. --source-local, after its -d check, refuses a pasted row that carries the mark and a path whose
escaped form equals another folder's in the same parent, either way round (find -print0 over the parent, each name
through the same esc), with a usage error that names the clash and says to rename the folder in its sync app; it
never takes an escaped spelling silently and offers no `$'...'` form (C1072; C1002, whose `$'...'` clause is not
built, since the prompt tells the person to rename). A folder with a control character and no look-alike is still
added as given. `_LIST_MARKS` and its parity test take the mark in lane RB's commit, as the phrase lists do. Lane RB
sends lane D the mark's exact words first; lane D's one commit, on a6b3a0d2e's file list, adds one sentence to the
README prompt's step 1/2 (a row with that mark cannot be chosen: tell the person to rename that folder in its sync
app, then run step 1 again) and moves the prompt to v35: its version and digest, CUT_OFF_TOLD, SETUP_PROMPT_COMPAT,
setup_report.PROMPT_VERSION, the version pins, the issue form and the dated CONTRACTS line. No other lane touches
the README prompt block.
(114) install.sh reads the report's helper words as the report writes them, and the report's words stay (C1071, the
round's one turn, with C1015, C1032, C1039, C1063 and C1106: one build, lane RB, after its data-integrity items;
amends ruling 108's quote). `_helper_late_head` has written `not judged (the <helper>[ and the <helper>]... did not
answer in time: ...)` in live and stopped copies since C915, and no build wrote the form install.sh greps, so the
reader moves, never the writer: report_time_helps' helper grep becomes C1039's `'^- doctor: .*not judged \((the
)?(OCR|media|speech) helper( and the (OCR|media|speech) helper)* did not answer in time'` (the optional `the` costs
nothing; the chain takes two and three helpers), kept after the cut, Loop and budget checks, so a helper beside a
real cut still names AGENTSYNC_REPORT_TIME_LIMIT=60, and its comments quote the real words. `_HELPER_WARN` is built
from `setup_report._helper_late_head` on a minimal _Run, never typed, with rows for one, two and three helpers and a
stopped copy (no clause) and for two helpers beside _DOCTOR_CUT (the clause); a tie test matches install.sh's
alternation against `_helper_late_head` for every non-empty subset of the helpers, as the REPORT_LOOP_CUT test does,
and HELPLATE2's NEXT names no variable. Lane RA changes no helper word, so nothing is sent first; lane D corrects
the bucket's quotes at setup-feedback.md:1411-1412 and CONTRACTS §16.230, §16.231 and §16.233 on lane RB's sha,
ruling 108's text staying as written.
(115) A cut read says ruling 107's words wherever it is printed, quoted or rewritten, and a wait the spent budget
shortened names the report's time limit (ruling 107, extended; lanes RA, RB and D). _Run.run and _git_ro_err decide
on the float limit against the cap, never on the printed seconds (2.96 s prints `3.0s` and was shortened), and a
shortened wait reads `did not answer within the report's time limit` through _within's None in _probe_ended,
_git_not_answered, _cut_words, the xcode-select source and Background's `launchctl print`, which report_time_helps
takes as one more alternate, its last `within [0-9]` grep kept (C1040: lane RA; the install.sh half lane RB's). The
guide's generic form is `not measured (<command> did not answer within <n>s)`, <n> the probe's own cap (3.0s, 5.0s
for `profiles`) or less on a nearly spent budget, and C918's dated line names the probe's own 3 s cap (C1041, C1045:
lane D, on lane RA's sha). gitleaks' version state folds into the one path bracket, `gitleaks (version not measured,
<words>; <where>; ...)` or `gitleaks (version unknown; ...)`, never `) (` (C1020; round 13 O14). The doctor bucket
says what did not start, `not judged (the Loop line was not read: its NEXT was not started: the report's time budget
was used up)`, `_NOT_STARTED` and Status's words unchanged since install.sh keys on them (C1109). A stopped copy an
older build rendered is brought to today's words by exact-string rules in older_report_rewrite only, under `cp &&
!det && !fence`, with no new head-note trigger: the bare `exited 124` Xcode and launchd lines become `did not answer
in time`, no seconds (C1022); `exited 124: skipped: the report's time budget is used up` becomes the not-started
_probe_ended form, and under the exact Status TimeoutError line, Status, the Loop line, the gitleaks bracket
(C1020's form, never `gitleaks, version not measured (`) and the heartbeat warns (C1109's words) take today's words
(C1108, makes worse True); `this attempt ran no install.sh` becomes `this attempt made no install run` in the Loop
bracket and the Installer details summary as well (C1107). Lane RA sends lane RB C1040's Background words, C1020's
form and C1109's words before lane RB writes those rules, and lane D the guide's words.
(116) No line invites a purge that a hold could refuse, and a sync's wait names every line it waits on (data
integrity: C1026, lane B FIRST; amends ruling 109). A [governance] that does not load judges no queue:
_governance_checks' ConfigError path hands _governance_lines gov=None, keeps the refusal and [info] not-checked
lines, turns the queued-count warn into an ok=False [info] line that keeps the count with no fix and no held split
(`N purge request(s) queued (...); not judged: [governance] does not load; the governance.config line says why`),
keeps a lost-queue FAIL's detail with its fix at governance.config, and gates the WAITING ON YOU and `queued
purges:` invitations on the table loading; a test asserts that no status line under a refused table names `purge
--queue` as a step (C1026). C933's wait naming both lines, which ruling 109 left unbuilt, is built:
`_sync_stops(config)` keeps C933's order and its X_OK-only-after-a-refused-read rule, the three sufficiency writers
print `the A and B [FAIL] lines are fixed`, and `_sync_stopped_on` stays the first stop for its readers (C1048);
QUEUE_REFUSED_BARE's docstring says a refused [governance] prints that FAIL since C958 (C1027). The report names the
governance folder, not `a file in the state folder`, when governance/ itself is shut, in its own evidence clause
(C1121: lane RA). The records guide gives a no-source purge status's conditional words, refused by a hold on another
source only if it reaches that source or finds nothing, so its --dry-run comes first, never a release worded as an
instruction, with a dated line on §16.229 C851 (C1068); setup-feedback.md says a purge by id that the manifest
resolves to the held source takes that hold's release (C1047, by C980): lane D, words only.
(117) A child the report's call starts ends with it even when it starts between the deadline check and the spawn or
after a Ctrl+C, completing ruling 106 (lanes B, E and RA). Each spawner checks the deadline, spawns and registers
under its one lock, as ocr._start does: deadline.run in one `with _LIVE_LOCK` block, its timeout recomputed after
the spawn; doctor._run's watcher spawned and registered under the lock, its first-line handshake outside it (never
the lock across readline()), then a second locked check before the program starts in the watcher's group (C1065).
One gate shape serves both registries: a per-report object with a `closed` bool in a ContextVar, deadline.START_GATE
in lane B's leaf module and ocr.START_GATE in lane E's ocr.py (two, as ruling 106 kept two deadlines, so neither
lane waits), which _Run puts in context fresh for each report; end_children and end_helpers(gate) close it under
their lock before they copy, and a spawn after that raises its cut error; never a module-wide flag, which would
refuse every later report and sync (C1067). net._run_scutil, cli._launchctl_env (stopping at its first
TimeoutExpired), arm_local._mount_point and _diskutil_volume_uuid go through deadline.run, governance._CatFile
staying, a write path (C1049). checks_once publishes fixes and manifest_first together under one lock unless the
report gave up, and an optional ReportHooks.doctor_abandoned, called in _doctor's TimeoutError branch under
suppress, clears both (C1030: lane RA lands the field first, lane B sets it); doctor's _LIVE_CHILDREN alias goes,
the tests reading deadline's and CONTRACTS:37748 naming RUN_DEADLINE and end_children (C1031). §16.233 states the
limit left by a dated line: a signal to agentsync's pid alone does not reach a running helper, while a group signal,
launchd's cleanup and end_helpers do; no signal handler and no per-call watcher is built (C1035: lane D).
(118) A file's class follows its bytes, completing C927 and C930 (lane E; lane D's sentence on its sha). Zero bytes
are `empty` in every converter: pdf, eml, pandoc's odt branch only, teams and vtt each raise
UnreadableSourceError(EMPTY_OUTPUT_REASON) on zero bytes inside the converter, never at screen_file's or
PandocConverter.convert's top, which would stub a zero-byte .rtf or .html; a whitespace-only .eml keeps `empty
message file`, the label refusal still runs first, and the vtt test's b"" case moves (C1050 with C1117, one build).
A Word, Excel or PowerPoint package named .vsdx or .vsdm is one settled stub through `_check_ooxml_family(src,
"visio", "a Visio drawing")`, the wording narrowed to `a .vsdx that is not a ZIP, or an Office package of another
family` (C1033). A blank or BOM-only .drawio stays `not the type its name says` by choice, only zero bytes being
empty-output: no code change, one CONTRACTS sentence (C1034 with C1118, makes worse True: a loose strip would class
a NUL-only or garbage file empty). The C927/C930 paragraph takes lane E's dated lines in its own commits and lane
D's sentence after.
(119) Lead rulings on the triage's open questions. Duplicates are built once in one lane (the notes list them). The
prompt goes to v35 for C1072 alone (ruling 113), so round 37 rehearses the prompt the operator will paste. C1004
over C1120 (makes worse True each): an UNKNOWN_EMPTY branch before root_unlisted's generic else, its wait following
doctor's _empty_root_fix in order and condition through one shared helper, an inbox without the pause,
`source.<id>.listable` moved into it, the generic else and its EPERM words untouched (lane B). Ruling 103's last
sentence is amended, with no packet and no rewrite offered (C1051, makes worse True): bodies committed before C934
keep their text until scheduled compaction squashes them, within history_days plus compaction_slack_days (30 + 7 by
default), sooner by `agentsync compact-history --keep-days N`, never under [governance] archive or a hold; lane D
gives the C934 section that dated line. The kit is the lead's, through round 37's script: a re-fired slot gets a
fresh root, a harness-chosen attempt number in its name and the old root kept read-only (C1008 over C1009's first
free `-2` name, which C1011 rejects as agent-chosen; one defect with C1005, C1011, C1012 and C1014; Codex's `cannot
tell` disputes are about the export lacking the kit and change nothing), with C1006, C1007, C1010, C1111, C1114,
C1115, C1124 and C1113's brief half; and the bookkeeping: C1052 with C1055, C1053, C1057, C1059 and C1060 (makes
worse True: no agent fix, the agy-review.sh allow rule being the operator's, backlog 8ed9dae0fbc0; a clean
Codex-only round goes to the operator before the loop closes). The lead reserves §16.235 to §16.239 on origin/main
before any lane starts: RA §16.235, RB §16.236, B §16.237, E §16.238, D §16.239. Ruling 68 binds every new literal
holding record, records, legal, label or review.

Clusters: the report's other words (an error line's told copy redacted without counting, C1021, makes worse True;
the partly-unread status head pinned to cli's, C1024; one issue link in a public report, C1112; lane RA); the
installer's other lines (step 2's config phase printing a progress line every 15 s, C1013; a dead early return in
the pairing test, C1023; Run metadata naming the build that rendered a report, C1103; a reinstall that finished
before a SIGTERM recorded as done, C1119; lane RB); the CLI's and the cycle's other words (an image waiting on a
broken OCR helper said so, not `no converter`, C1101; the commit body's source spellings built lexically, C1025; a
check the deadline never started given no fix, C1028; Severity.OK in two tests, C1066; lane B); the converters'
tests (the helper test's 60 s pin and its finally, C1036; a docstring assert retired, C1037; lane E); docs and CI
(CONTRACTS' §15 snapshot, a moved citation, a missing blank line, V10's dated line and C972's --list-folders forms,
C1038, C1042, C1043, C1044, C1046; every new signal test resets what it sends, C1054; round 35's CI figures, C1056;
Outlook's change token, C1070; the zsh expansion-flag kit sentence, C1113; lane D). Lead: round 37's kit (a re-fired
slot's fresh root, C1008 with C1005, C1009, C1011, C1012 and C1014; C1006, C1007, C1010, C1111, C1114, C1115 and
C1124; the brief half of C1113), the bookkeeping (C1052 with C1055, C1053, C1057, C1059, and C1060, makes worse
True), the reservation and the open questions (ruling 119). Earlier rounds are in COMMON5.md to COMMON34.md.

Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`scripts/install.sh` and `tests/test_install_oneshot.py`, lane RB owns everything this round, the other lanes'
install.sh halves included (ruling 112's shell halves of C1017, C1018, C1069 and C1123 on lane RA's table; C1040's
Background alternate and ruling 115's rewrite rules on lane RA's words), except: the FIXED_VALUE_PHRASES lines,
which a lane changes in the commit that prints or retires a clause (lane RA's C1112, ruling 68), with the
stopped-copy `left out` counts that phrase moves; and SETUP_PROMPT_COMPAT and the version pins, in lane D's v35
commit. In `setup_report.py`, `tests/test_setup_report.py` and `tests/test_report_redaction.py`, lane RA owns
everything, the other lanes' report halves included (C1030's doctor_abandoned field and call, the per-report gates
of C1065 and C1067 with C1067's pthread_kill test, the pin on C1101's phrase), except: `_LIST_MARKS` and its parity
test, changed with the printing lane in lane RB's C1072 commit; the both-reports separator test in
test_report_redaction.py, in lane RB's ruling 112 commit; and PROMPT_VERSION and its pins, in lane D's v35 commit.
`cli.py`, `cycle.py` and `publish.py` outside lane E's parts, `loop.py`, `governance.py`, `ops/deadline.py` and
`ops/doctor.py` outside lane E's pandoc, convert and speech lines are lane B's, with `net.py`, model.py's C1101
counter and arm_local.py's two C1049 calls in lane B's commits (lane E edits no arm_local line this round);
`convert/ocr.py` is lane E's, ocr.START_GATE and the OCR-state function lane B's C1101 reads included; lanes B and E
build one gate shape and rebase on each other's land. Only lane D touches the README prompt block, its version or
its digest, and only for C1072. Who sends first: lane RA sends lane RB ruling 112's table, cases and legend words,
and C1040's Background words, C1020's form and C1109's words before lane RB writes C1108's rules; lane RB sends lane
D C1072's mark words and lands `_LIST_MARKS` with them; lane RA lands ReportHooks.doctor_abandoned before lane B
sets it; lane E sends lane RA ocr.START_GATE and end_helpers' signature and lane B the OCR-state function's name;
lane B sends lane RA deadline.START_GATE and tells it when ruling 117 lands; lanes RA, RB, B and E send lane D their
final words. A CONTRACTS line that quotes words a lane changes, and a test or guide line that pins them, move in
that lane's own commit, and lane D cites the sha: the legend's `<path>` row at setup-feedback.md:100,
CONTRACTS:38160-38162, :38318-38321 and :38330, Q81 and C918, setup-feedback.md:1402 and :1413-1414 and
CONTRACTS:28277 (lane RA); C916's and C910's paragraphs, C932's `cannot be pasted` sentence and the step=agentsync
notes' section (lane RB); §16.45 R102, §16.232 C917 and C933, CONTRACTS:37748 and setup-feedback.md:1269-1273 (lane
B); the C927/C930 paragraph's dated lines (lane E). Lane D writes the rest once, after the lanes land. COMMON.md's
rulings 84, 103, 106, 107, 108 and 109 stay as written; rulings 112 to 119 amend them, each by its CONTRACTS dated
line. docs/plans and the kit are the lead's.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.235 to §16.239); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: one item needs prompt words this round, C1072 (ruling 113), and the prompt goes to v35 in lane D's
one commit, so round 37 rehearses the prompt the operator will paste. No other lane edits the README setup prompt
block. If another fix seems to need new prompt words, message the lead before you build it.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.235 to §16.239) begins "Amended
<date>, §16.NN C<n>". A line that records another round's change reads "Amended <date>, §16.NN C<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 35's ids are C901 to C997; round
36's are C1001 to C1124. The dated-line scans read a line of any date since round 19's W71.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
Every install.sh probe of doctor (state_path_fix, PERSONS_KINDS, the parent fix) runs under the installed
agentsync: it must fall back when that build lacks the name.

Dated-line test: since round 9 K80 the gate reads every "(C<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 36's rows the same way. A quote in a §16 paragraph counts as a dated line only with a real date, a numeric home
of §16.92 or later and no `C<n>` template, and test_every_dated_line_from_round_11_on_is_held_by_a_test then calls it
loose unless it is claimed or pinned: quote the form with `C<n>` or `<date>`, as each section's "A dated line this
round writes reads" sentence does, or pin it (round 35 C982).

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round36/
scratch-<lane>/`, not in /tmp. Only the land lock stays in /tmp. To damage a manifest for a test or probe, write the
junk with `python3 -I`, not `dd` (`Bash(dd:*)` is denied).

Rehearsal kit and macOS's /tmp cleaner (round 29 C149, C150; the kit itself is the lead's): `com.apple.tmp_cleaner`
runs `/usr/libexec/tmp_cleaner` daily at local midnight (`StartCalendarInterval` hour 0), and it deletes under /tmp
every empty folder whose mtime is more than 3 days old (`-type d -empty -mtime +3`) and every file whose atime, mtime
and ctime all are. `cp -Rp` keeps the fixture's folder mtimes, so at round 29's first midnight it deleted `Project
Alpha/Empty Subfolder` from the rehearsal roots' fixture copies and homes, and step 2 printed no empty-folder wait.
Right after every `cp -Rp` of the fixture and every snapshot restore, run `/usr/bin/find <copy> -type d -exec touch
{} +` (the shell's `find` here is bfs, which refuses the cleaner's `-dx`); never `cp -R`, which resets the file
mtimes agentsync reads. One touch keeps a copy's folders for 3 days, so run it again at the start of each act. It
keeps no file: the cleaner also deletes every file whose atime, mtime and ctime are all more than 3 days old
(`-type f -atime +3 -mtime +3 -ctime +3`), `cp -Rp` keeps each file's atime and mtime and gives it a fresh ctime,
and neither a folder touch nor a read renews them (on APFS here a read does not move the atime), so a copy under
/tmp keeps its files for 3 days after its copy or restore and no longer (round 30 C380). A rehearsal root or home
kept longer lives under ~/.cache, which the cleaner never visits, or is copied again with `cp -Rp` (and the folder
touch) at the start of each act; never `touch -a` its files, which renews their ctime, so the next sync reads and
hashes every file again and an inbox root holds each back for its 60-second quiescence. Right before step 2, before
reading a role run's state and at each act's end, run `(cd <home>/Library/CloudStorage/OneDrive-Contoso &&
/usr/bin/find . -type d | LC_ALL=C sort | LC_ALL=C sed 's#^\.#tree#') | diff - dirs.txt` on each home's copy, and the
same command with <root>/fixture/tree in place of the home's folder, and stop on a non-zero exit: dirs.txt holds
`tree`, `tree/Documents`, ..., so the sed maps find's leading `.` to `tree` (round 30 C329). Right after every `cp
-Rp`, right before step 2 and at each act's end, rerun every copy's shasum check,
`(sed 's|  tree/|  <copy>/|' manifest.sha256 | shasum -a 256 -c -)`, and stop on a non-zero exit. A probe that needs
an upstream deletion moves to `<root>/trash/<probe>/` a file it made itself (a `cp -p` of a fixture file under a
made-up name, synced before it goes; round 35 C983), never a manifest file; one that must move a manifest file names that one file, and its copy's check passes
only when that file is the one line missing, its hash checked where it went against its manifest line (round 31 C573,
C504). shasum reports a moved file on two lines, `shasum: <copy>/<rel>: No such file or directory` (on stderr, which
the kit reads with 2>&1) and `<copy>/<rel>: FAILED open or read`, then `shasum: WARNING: 1 listed file could not be
read`, so that copy's check passes only when those three are its only lines not ending `: OK` (round 34 C806). A
probe that adds a made-up folder (for example `Project Alpha/Contoso Empty 2`) names each one in its report, and that
home's folder check passes only when those lines are the only extra lines; any other extra or missing line stops the
run (round 33 C774). Both dirs.txt and manifest.sha256 are the fixture's, under
~/.cache/agentsync-bring-back/fixtures/v1/ (or copied beside <root>/fixture/tree), and both checks run from the
folder that holds them (round 32 C664). A rehearsal that crosses local midnight checks every step after it again: an
empty-folder alarm or wait gone just after 00:00 local (the parent folder's mtime says when) is the cleaner's doing,
not agentsync's. The Bash tool's shell here is zsh, while sb and the kit scripts are /bin/bash. Outside sb,
`${PIPESTATUS[0]}` is empty and a pipe's `$?` is its last stage's, so to keep a command's exit code, send its output
to a file and read `$?` straight after: `sb '<cmd>' > logs/x.log 2>&1; rc=$?; echo "rc=$rc"` (not `status=`, which
zsh holds read-only). Quote any word that starts with `=` (`echo '====='`, never `echo =====` or `[ a == b ]`): zsh
reads it as a command name, stops with `= not found`, and skips the rest of the command. The diff and shasum checks
above end in the checker, so their `$?` is right as written (round 35 C907). Build any file list inside a kit script
(/bin/bash) and run that script by path. Never use zsh parameter-expansion flags in the Bash tool, such as
`${(f)"$(...)"}` or any `${(...)...}`: Claude Code's removal check refuses them as `could not be checked for dangerous
rm operations`, even with no rm (round 36 C1113). Do not use other zsh-only forms such as `${=var}` either; sb and the
kit scripts read them differently. zsh does not split an unquoted `$var`, so never pass a list to a script as one
unquoted variable: let the script build the list itself, or pass one argument per file. This is what to do first, not
a way around a refusal: a refused command is still not reworded but recorded as a finding.

Tools CI lacks (round 18): lane RB's census test passed here only because this Mac has gitleaks; CI does not, and
its land turned trunk red for 90 minutes. A test that reads a tool's output (gitleaks, pandoc, swift, tesseract)
must also pass with that tool absent: check `shutil.which` or stub it.

Gate: the repo's `scripts/gate.sh`, never a hand-copied script. While you build, pre-gate with
`scripts/gate.sh --affected` (every static step, then the tests your diff can turn red; `scripts/gate.sh --lane`
is the full set, resumed where a killed run of the same tree stopped). Before every push run the land gate,
`scripts/gate.sh`: the full CLAUDE.md list, the full test set (the whole-tree scanners first), and it ends
`GATE GREEN at <sha>`. A fresh worktree has no node_modules (it is gitignored); scripts/gate.sh runs `npm ci` once when
it is missing. Run a lint and the commit it guards as one `&&` chain, never `;`, with no pipe on the lint: `ruff
check x | tail -3 && git commit` reads tail's exit, not ruff's (round 33). A new def, test or helper, placed before a
decorated test goes above that test's decorators (round 33, bb9286d52).

Landing (round 36): land with `scripts/ship-land.sh --lock /tmp/b13-r36-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, reads trunk's CI once without waiting (a run
already ended red stops it there, exit 6, before the gate), rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r36-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).
If its line says `no CI run for <sha>`, CLAUDE.md's "CI is part of the gate" says when to dispatch `ci` and when
never to (round 35 C982). A land run in the background can end while you think it is queued (round 34: exit 4, two
hours lost): while you wait, read its log, and check that your own worktree's path shows in `ps -ax -o command | grep
'<worktree>/scripts/ship-land'`, since other lanes' lands look the same otherwise.

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6); since round 27 P140 it
also reads trunk's runs once before the gate, so a trunk already red stops the land before a 35-minute gate. If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. Pass `--allow-red-trunk` only for a run that
ended red on tests your diff does not touch and your lane does not own, with the run id in your commit body or result
note and the owner messaged; never for exit 9: a run that lost its hosted runner is rerun (`gh run rerun <id>
--failed`) and waited for, never landed past (round 31 C569). The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. CI takes 18 to 62 minutes a run (the slowest, 61:22, in round 34), and a run can wait over an hour for a macOS runner first (63:27 in round 31); its limit is 80 (W80, round 21 Y68, round 22 Z67, round 23 A74, round 24 B98, round 25 D83, round 28 I99, round 29 C207, round 30 C372, round 31 C571, round 32 C662, round 35 C985).

Makes things worse: 11 round 36 items carry "a fix makes things worse? True" (C1001, C1004, C1021, C1051, C1060,
C1064, C1069, C1108, C1116, C1118, C1120). For each, the verifier's corrected fix is one that does not, and the
rulings adopt it (C1001 and C1116: a prefix mark with the raw rows unchanged, since escaping only the backslash
leaves the ? clashes; C1004 and C1120: a dedicated branch for the empty root, never a rewrite of the generic else,
which would change the EPERM words; C1021: Redactor.quiet(), never scrub() or a raw cut; C1051: no packet and no
rewrite, since compaction, which respects holds, already clears the bodies; C1060: no agent-side fix, the allow rule
stays the operator's (backlog 8ed9dae0fbc0); C1064: the shell moves toward the Python report's cut, never the
reverse, which leaks a volume name with a comma; C1069: the cut stops only at `):` and `, stored as `, so an org
name with a comma or bracket stays cut; C1108: exact strings only, never a head-note trigger keyed on the evidence
line; C1118: docs only, since the loose strip would class a NUL-only file as empty): read its "Verifier" part before
you build.
Dated lines: every round 36 dated line reads 2026-11-08, its heading's date (lane D's round 25 test).

Regressions: round 36 found 5 turns and 10 wrongs. Four of the turns are one kit defect, a re-fired rehearsal slot
that reused its sandbox root (C1008 with C1005, C1009, C1011, C1012 and C1014: the lead's, in round 37's kit); the
fifth is real: install.sh's helper time-out advice recommends a report run that cannot help, because its grep never
matches the report's words (C1071 with C1015, C1032, C1039, C1063 and C1106, ruling 114). 8 items are data
integrity, 4 of them privacy: a path in another home kept its tail when a volume name held a comma or a semicolon
(C1016 with C1064, the shell) or the path held a no-break or ideographic space (C1017, C1018, the Python report), so
ruling 102 was incomplete again and ruling 112 completes it with one table and a property test, not one more case; a
folder row shown escaped could add another folder (C1001 with C1116 and C1072, ruling 113); and a [governance] that
failed to load printed a purge-queue warn (C1026, ruling 116). A word another reader greps for is a contract: when
you change a printed phrase, grep install.sh, both reports, status, doctor and the guides for it, and test the
reader against the writer's own constant, never a copy. Before you land a new stop, exit code, wait or person-owned
fix, follow its words on a Mac in the state it is meant for AND test it on one where it must not fire. A test marked
timing is skipped above a load of 4 per core: run any one you change with AGENTSYNC_TIMING=1 before you land.
scripts/gate.sh re-runs a part's 1 to 20 failed tests once alone and convicts only on a second failure, so read a
red land's log: a test that failed alone is yours or trunk's, never load. A background ship-land can end while its
lane believes it is queued: while you wait on the lock, check with ps that your ship-land still runs. A test that
sends SIGHUP or SIGINT to a child makes the child take that signal's default first, because a pytest started under
nohup passes SIGHUP on ignored and one started with `&` passes SIGINT on ignored (C869 for two tests; b781e539 for
C956 and C961). A new signal test must do the same, so a detached gate passes it (round 36 C1054). Send the words another lane waits on first, pre-gate with --affected and land once; two lanes that
share a chain may land jointly under one gate, the shared commit with one owner. A whole-tree test one lane owns can
turn another lane's gate red: ruling 68's phrase scan reads install.sh's literals too, so a lane that prints a new
clause adds it to both phrase lists in that commit and runs the scan before it lands. A citation of an off-trunk sha
fails test_a_cited_commit_is_on_the_trunk: cite only what is on origin/main. A hook asks before any command holding
the word "restore" and before `git push --delete`: leave the word out of -k filters, and never push a share branch
you would have to delete. If the classifier refuses a command, do not reword it: message the lead. A trunk run that
lost its hosted runner is rerun, never landed past (ship-land exit 9, round 31 C569); pass --allow-red-trunk only
after reading a red run's log and finding no test failed. A fresh worktree needs `npm ci` before ship-land. The line
numbers in the specs are b781e539's; at 798fa709 they have moved, so find a function by name.

Lane peers (round 20 Y80): once all five lanes are live, the lead messages every lane the five session ids. Send a
seam note straight to the lane that owns the file, and copy the lead only when it changes another lane's scope.

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r36-<lane>.md`.
