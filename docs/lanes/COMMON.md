## Common brief for every B13 round 46 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 46.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 45 landed" and "B13 round 46" (the v42 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.285 to §16.289.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round46/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round46/*.md`): read the part
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

ROUND 36 RULINGS (lead; still binding, as extended below):
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

ROUND 37 RULINGS (lead; still binding, as extended below):
(120) Both reports cut the same spellings, and a report an installed agentsync renders is cut again by this
checkout, never left to the installed Python (privacy; data integrity: lanes RA and RB, FIRST; completes ruling
112). The Python report splits install.out and the friction log on `\n` alone, one trailing `\r` stripped, in
_tail_cut, parse_friction, Friction's line count and the friction listing together, so U+2028, U+2029, U+0085 and
`\x1c` to `\x1e` stay inside the path they sit in; `_CUT_BYTES` and install.sh's `STOP` stay as they are (C1249,
makes worse True: those bytes in `STOP` would end a cut at `é` or `Å`). The provider folder after `/CloudStorage/`
or opening `OneDrive - ` matches in any case, `(?i:...)` inside the fixed-width lookbehind and tolower() in
foreign_home_cut, and both legends and setup-feedback.md's `<path>` row say `in any case` and name C1069's stops at
`):` and `, stored as ` (C1285 with C1274; C1203). install.sh's own `is not an existing folder: ` diagnostic is cut
in _redact_lines.plain through _cut_path and counted, the name running to ` (see --help)` or the line's end (C1299
with C1250), and every --source-local refusal sets USAGE_ERROR_REPORTED with `<path>` (C1278). The volume look-back
becomes one linear pre-pass with the same cuts, never a bound (C1254), and the shell's scan keeps skipped text with
an offset reset at each cut, never looking back over `out` (C1253; makes worse True each: a bound leaks the login, a
look-back over `out` re-cuts a used volume or its placeholder). Right after any agentsync render, before
bring-back.md, the issue link and the stopped copy read it, install.sh passes $REPORT_PATH through this checkout's
foreign_home_cut and a fixed-word cut of its own diagnostic (the text after `is not an existing folder: ` becomes
`<path>` unless it already ends in `<path>`), in place (mktemp beside it, chmod 600, cmp, mv), with no build-differs
condition and never the whole redact_stream; the pass also cuts on past a `/Users/<path>` or `/Volumes/<path>` an
older cut left followed by a path character, and a current render comes back byte for byte. A pass that fails falls
back to write_fallback_report, and so does a render by another build (rendered_by_note's test, or no readable
install stamp) when install.out or friction.md holds one of C1249's six code points, since an older build splits
that line where no shell cut can follow its tail (C1210 with C1237). The proof is ruling 112's: lane RA adds the
provider prefixes as written, lowercase and uppercase, and the six code points as `_separator_lines()` slots run
through build_report, never Redactor.redact alone, and sends lane RB the cases and the legend words first; lane RB
lands the shell half with the both-reports rows in test_report_redaction.py (C1253's `_RULING_102_LEAKS` rows among
them) and an install.sh run with a missing --source-local folder rendered by this checkout's agentsync and by a stub
older one, in its own commit.
(121) A step 1 row is choosable exactly when step 2 takes it, and no mark's words are false for its row, amending
ruling 113 (the round's turns C1242 and C1308, with C1239, C1201, C1213 and C1229, one build; C1202 with C1236;
C1251 with C1276; C1214; C1300, data integrity: lane RB, after ruling 120; the prompt goes to v36). Ruling 113
marked only a twin pair's control-character member, so a plain look-alike (a literal `\n` or `?`) listed unmarked
and step 2 refused it with words false for it. Twins are found over the full sibling set by escaped_twin's own dir
test (find over the parent, the test step 2 runs) for each row about to print that holds `\` or `?`, never over the
rows inside the cap alone, and in dir mode two find rows with one transported text are twins too, so a newline name
beside an RS name is refused both ways (C1300). Both rows of a pair are marked: the escaped one keeps ESCAPED_MARK
and the plain one opens with TWIN_MARK, words such as `[name reads as another folder there: rename one of them to
sync it]` with no apostrophe, bracket or slash inside, in the first mark's slot. No kept row ([synced] or [inside a
synced folder]) is told to rename: an escaped kept row opens with ESCAPED_KEPT_MARK, `[name holds a control
character]`, which shares ESCAPED_MARK's opening words, as do the escaped paths in a NEXT's gone, library and other
clauses, and a plain kept look-alike carries its kept marks alone (C1202 with C1236, C1251, makes worse True each:
the mark stays first, never after [synced], and list_plain serves only the one no-twin lost-block add-back clause,
the twin clause and vback>1 keeping the marked vpaths, C1276). --source-local refuses a pasted row with any of the
three marks (the kept one saying the folder already syncs, so paste nothing for it) and words each refusal by
direction: a folder whose own name holds a control character keeps today's words; a plain look-alike says its name
is step 1's escaped form of another folder there; a path with no folder says it is how step 1 shows a folder whose
name holds a control character, never `another folder` (C1239). Each says to rename the folder in its sync app, then
run `<this installer> --list-folders` again, never step 1's whole command (C1275's install.sh half), and takes a
USAGE_NEXT that leaves the rename to the person and names no folder (C1214). Every reader learns every mark in lane
RB's commit: listed_folder_lines' first sub() as one alternation, the log scrub, the unmarked-folder comparison,
`_LIST_MARKS` and its parity test. This regression is evidence on decision ef4a5985f497's course (fail closed with a
prefix mark for an escaped folder row): the course stands, amended to mark both rows of a pair, keep rename words
off kept rows and word each refusal for its own row. Lane RB sends lane D the marks and the refusals word for word
first; lane D's one commit, on lane RB's sha and 553a4724's file list, moves the prompt to v36: step 1/2 names both
rename marks as rows that cannot be chosen and says to run `--list-folders` again once the person says the folder is
renamed (C1201, C1242, C1308, C1275, with C1275's test that no text but the closed-attempt prompt-copy path reruns
step 1 after --log-start), a row with the kept mark as a kept folder, and a NEXT's own --source-local command for a
marked folder as the fix to run as given if the person names that folder, never the marked row pasted (C1309, makes
worse True for its installer option: the add-back stays, since a rename can lose the source id); with its version
and digest, `prompt v36` in step 1's --log-start, CUT_OFF_TOLD, SETUP_PROMPT_COMPAT, setup_report.PROMPT_VERSION,
the version pins, the issue form and the dated CONTRACTS line. No other lane touches the README prompt block.
(122) An image waiting on a failed OCR helper is told so only where it will be read, and QUARANTINE.tsv and the
report take its class from its page, completing C1101 (lane B, with lane RA's report half; C1286 data integrity).
ocr_waits takes `local`, false for a Graph source or an online-only row, at _plan_stub and at cycle.py's count,
through one helper shared with _no_converter and _stub_settled, so those stubs keep `no converter for <suffix>` and
count in refused_unread as when the helper works (C1257 with C1265). write_quarantine follows the published page,
never this run's ocr_down (round 7 H52): for a REFUSED image row whose stored reason is `no converter for <suffix>`,
with no label rule, it reads the stub's `reason:` through the one-line decode _withheld_on_page uses, _stub_says
generalized, and prints OCR_DOWN_REASON when the page says it, the stored reason, none@0 and the re-read key
unchanged and the page read measured against J55 (C1211 with C1204, C1238, C1259 and C1269; C1204 makes worse True
for the run-state option). While the helper fails, an unchanged image whose mtime moved keeps its image-ocr page: a
download case keeps it unread, a local image is read through arm.fetch and kept only when classify_content says
TOUCHED_NOT_CHANGED, a changed hash or a label rule falling through to the stub (C1286). A pending recording waits
on the OCR helper, in OCR_DOWN_RECORDING_REASON, only when media.probe (lane E's, read only) finds the media helper
ready (C1268), and `failed` has one wording in comments, docstrings and §16.237 (C1271). The report follows the page
too, never its own probe (J52, J54): read_image_pages also reads no-converter image stubs with no label rule, within
_PAGES_S and its quarter of the time, and a page whose `reason:` is OCR_DOWN_REASON takes the class `waiting for the
OCR helper` in QUARANTINE_CLASSES, reason_class and refused_type and an _IMAGE_OUTCOMES entry counted in `waiting`,
§16.235 corrected and its empty pin replaced by a build_report test over a manifest row and its page (C1267 with
C1273, makes worse True for the probe-keyed option only); its first-sync line copies `N waiting for the OCR helper`
between withheld and refused (C1266 with C1260). Lane RA builds its half without waiting, since it reads only
publish.OCR_DOWN_REASON; lane D gives §16.26 its dated line (C1270).
(123) A stopped copy an older build rendered is rewritten only on proof in its own text, completing ruling 115 (lane
RB, after ruling 121). Every rule in older_report_rewrite stays an exact-string rule under `cp && !det && !fence`
with no new head-note trigger, and keys on a flag the first pass sets from that copy's own lines: spentst as now;
cutst, Status's anchored `TimeoutError: did not finish within <n>s` form, never an errno TimeoutError (C1223);
envskip, an Environment probe before the rewritten one ending ` exited 124: skipped: the report's time budget is
used up`, or 4e0e8604's not-measured form, reset per file (C1224 and C1225, makes worse True each: spentst does not
prove those probes were skipped, so the Xcode line and gitleaks' `version unknown` change only under envskip and a
real time-out keeps its words); loopspent, set when the Loop line is rewritten (C1222). One `nsw` string, `its NEXT
was not started: the report's time budget was used up`, serves hb_move and every doctor-bracket rule, hb_move taking
its bracket words as a parameter (cutst passes `it did not finish in time`), and b781e539's subject-less `not
started:` bracket is rewritten under cp alone (C1221). The install line counts step 1's --list-folders runs from
install.log through older_report_facts and is rewritten only when 0 < L <= N, in setup_report._install_line's words
(C1226); the earlier-link line (`issue link (an earlier run's, not shown`, passed by -v as gone is) counts in left()
and goes with the Send-it lines, EARLIER_LINK unchanged (C1248); rendered_by_note runs after stopped_report_note in
both stopped branches (C1215). Each rule has a test with its build's shape and a current render that comes back byte
for byte, and its CONTRACTS line moves in lane RB's commit.
(124) A sync's wait names every stop the cycle meets, and a [governance] that does not load still names what it
holds, completing ruling 116 (lane B). doctor.state_stops yields each independent stop in the guard's order (sides,
manifest, lock, heartbeat, then each governance file, governance_not_a_file in an every-file form and the damaged
and lost reads only on regular files), state_not_a_file becoming its first; _sync_stops takes all of them, then the
lock probe, then the refusals the cycle exits 78 on (`policy`, `governance.config`), then the refused reads,
deduplicated, so a folder at heartbeat.json beside one at purge-record.json waits on both lines and a refused table
never promises that a plain sync prints the alarm (C1301 with C1246). Under gov=None each state hold is named as an
[info] line ending `_HOLD_NOT_JUDGED`, with no fix and no `[ok  ]` line (C1247). A remote_url_prefixes entry that
raises ValueError becomes a ConfigError naming the prefix through _redact_url, as the `is not a remote URL` message
does too, so `see governance.config` points at a printed line and no password prints (C1261, makes worse True:
_parse_remote never swallows it into the raw-prefix message).
(125) A usage error is decided before any config loads or any state is written (data integrity: C1262, lane B FIRST,
with C1303). `hold` with --reason or --owner and no scope exits with EXIT_USAGE, `hold: <SCOPE> ('all' or a source
id) is required`, and writes no holds.json, so no one reads a listing as a hold set; `hold` alone still lists
(C1262). A bare `purge` with no selector, --queue or --accept-lost fails in _purge_usage after its more specific
checks, so it exits 2 on a malformed config and writes no hold-record.json, the late selector check kept only as an
assert (C1303). The words take C1263's `<SELECTOR>` and `<SCOPE>`, and lane B builds this after rebasing on the CLI
conventions wave's land, whose queue rewrites that late check.

ROUND 38 RULINGS (lead; still binding, as extended below):
(126) A folder's name prints in no spelling in either report and never raw on a terminal, and both reports cut the
same spellings at the same edges, completing rulings 112 and 120 (privacy; data integrity: lanes RA, RB and B,
FIRST). install.sh's `source-local:` line prints list_name's spelling, doctor's source.<id>.listable details print
the list's escaped spelling of the root, and its fix commands (`open ... in Finder once`, `chmod u+rx`) write such a
root in install.sh's `$'...'` form, never `?` (C1414). The report aliases each folder literal's list spelling, its
`$'...'` word and the Cf-stripped form safe_text prints, beside the JSON-escaped one, in _alias_escaped, and the
shell's pairs take the same spellings (C1414, C1439). Doctor text becomes report lines split at `\n` alone, `""`
giving [], at both of cli.py's doctor splitlines() sites and in _with_evidence_damage, so no break str.splitlines()
honours (`\r`, `\v`, `\f`, `\x1c` to `\x1e`, U+0085, U+2028, U+2029) splits a name (C1431). agentsync's own verbs,
read from the commands cli.py registers, hidden ones included, are held whole after `agentsync ` in both reports'
fuzzy match, with keep_path's `unless` guard and the fuzzy edges unchanged (C1425, makes worse True: an edge rule
leaves `empty-project-2026` in clear). A `--source-local <name>: <head>` refusal is cut to the last `: ` before one
of _REFUSED_HEADS (C1432), and the not-a-folder name ends at the last ` (see --help)` only before the line's end,
`;,.:`, ` | ` or ` · ` (C1436, makes worse True). report_recut's fixed-word cut runs only on another build's render
(report_build_other, which fails closed), so a current render comes back byte for byte (C1453); there it keeps a
name only when its last part is `<path>` and every earlier part is one _cut_path knows, from install.sh's existing
copies of the three sets passed through ENVIRON, a provider folder only as `<Provider>-<placeholder>`, and _cut_path
takes that provider rule too (C1480, C1481 and C1435, makes worse True each: a faithful port of _cut_path passes an
unregistered tenant). The other-home cuts agree (C1440), and a recut that changed another build's render says so
under its Redaction section with the passes' own count (C1406). Lane RA lands the Python half first and sends lane
RB the spellings, _REFUSED_HEADS, the verb source, the provider rule and the legend words; lane B's C1414 and lane
RB's `source-local:` line land only on lane RA's sha, since a spelling no alias knows prints in clear. The proof is
ruling 112's: a name holding each C0 control, DEL, each of those breaks, each character _LIST_ESCAPED_RE matches, a
blank and a hyphen beside a verb, in each line form (the source-local line, doctor's detail and fix, each refusal
head, the not-a-folder line, a friction row), and both reports print the same line with no fragment of it; the
both-reports rows and the recut harness's current and older rows land in lane RB's commit.
(127) A recording waiting on a failed OCR helper is classed by its page wherever it is counted, as an image is,
completing ruling 122 (lanes RA and B; C1442 data integrity). The report half is lane RA's (C1433 with C1451, C1487,
C1490, C1465 and C1454): read_image_pages also reads `no converter for <suffix>` rows for `_RECORDING_SUFFIXES`,
held equal to convert's RecordingConverter.extensions by a test as `_IMAGE_SUFFIXES` is, after the image rows so
they never spend the image pages' time; a row waits only when its page's `reason:` equals
publish.ocr_down_reason(its suffix); a recording row never enters image_pages, pages_unread or the withheld test,
label rule or not; page_waits takes both sets, while withheld_for and the OCR part's images line stay image-only.
The guide's Summary row (`O waiting for the OCR helper`: images whose OCR helper fails this run, and recordings
while the media helper is ready, in C1271's words) and its Quarantine row, the _OCR_WAITING_RE comment, the dated
lines under J39's and §16.240's C1266 lines and at C1267, and C1271's module list move in that commit, never before
its code (C1452). The cycle half is lane B's: _keeps_ocr_page keeps a recording's page under the image's gates
(C1442) and an OCR-made unreadable stub, a QUARANTINED output on a QUARANTINED row, beside an OK page (C1403),
§16.242's C1286 sentences in its commit; QUARANTINE.tsv already follows the page. Lane RA waits on no lane (it reads
publish and convert only) and proves its half with a build_report test over manifest rows and pages the real
publisher stub writes, label rule off and on, OCR probe ready and not.
(128) Under --json, stdout holds one document, stderr only the error document (with -v, one JSON object per log
record), and every `next` runs as given against the install the person chose (lane B, last in its list; C1486 data
integrity; the CLI conventions wave's landed --json code, built on trunk at that wave's lead's request of
2026-11-13). offboard's recovery command, text and `next` alike, is built once with `--config` through one
_config_tail shared with _inspect (C1486). `graph login --json`, with or without --device-code, is a usage error
before MsalAuth is built: one error line, empty stdout, exit 2, a `next` without --json that keeps --device-code;
logout keeps its document (C1441 with C1485, makes worse True: sign-in text on stderr breaks the one-error-line
contract). In JSON mode no text log handler is attached: WARNING records go into the document's and error_doc's
`warnings`, and a sync the land gate blocks takes the outcome `blocked` with the blocking findings in curate's
shape, recorded in §16.300.6 (C1484). A command with a `<placeholder>` has argv [] (C1444), a governance file's stop
is governance_unreadable wherever it is met (C1446), and status's document points at checks entries by id, its text
view byte for byte (C1448, makes worse True: the sync forms are never substituted). That wave's queued U-C3 and U-C5
rebase over lane B.
(129) A mark's words are true for every row it opens, and the prompt quotes each mark whole on one line, so the
prompt goes to v37, amending ruling 121 (lanes RB and D). Since 8528ea2a4 the escaped form also covers bidi,
zero-width and separator characters, so ESCAPED_MARK and ESCAPED_KEPT_MARK read `[name holds a hidden or control
character: rename it to sync it]` and `[name holds a hidden or control character]` in install.sh, its header,
arm_local.ESCAPED_KEPT_MARK and every quote, while listed_folder_lines' sub() and _LIST_MARKS (new before old) still
read the old words, proven by one install.out holding both (C1447, makes worse True: old rows stay registered). A
render by another build whose install.out holds a new-wording row falls back to write_fallback_report, as ruling 120
does for C1249's code points, since that build's _LIST_MARKS cannot strip it. Lane RB lands this in one commit with
_LIST_MARKS, the arm_local constant, the guides' and CONTRACTS' quotes of the marks and every parity pin, and sends
lane D the two marks word for word first. Lane D's one commit moves the prompt to v37: step 1 quotes the new marks,
each quoted mark whole on one block line, held by a test over every bracketed mark the block quotes, [synced] and
[inside a synced folder] included (C1402), with its version and digest, `prompt v37` in step 1's --log-start,
CUT_OFF_TOLD, SETUP_PROMPT_COMPAT, setup_report.PROMPT_VERSION, the version pins, the issue form and the dated
CONTRACTS line; if a pin ties the block to install.sh's marks, that commit rides on lane RB's branch and both land
in one run. Then lane D widens both guides' escape mapping on lane RB's sha (C1455 with C1492). No other lane
touches the README prompt block.
(130) An empty source folder outside every File Provider domain is a [warn] with a fix that the report files as
expected, completing C1205 (lane B with lane RA's bucket; C1401 with C1408 and C1427). Doctor keeps the WARN (C1401,
makes worse True: an [ok] or [info] hides an empty mount point or a wrong path, so C1427's [ok] is not taken) and
gives it, for a cloud and a local non-inbox root alike, `if <root> is meant to be empty, set state = "paused" in
[[source]] id = "<id>" in <sources.toml>; else check that <root> is the folder you meant`, from one helper that
loop.empty_root_fix's pause words share, the root one shell word through _shell_path, with no grant and no Full Disk
Access step; the in-domain and inbox branches stay. The report files a source.<id>.listable [warn] of a configured
live non-inbox source whose detail ends in the empty-folder words doctor exports as a constant under expected, `an
empty folder chosen as a source: pause it or fill it`, keyed on the check and that detail, never the fix, so
unexpected is 0 (C1408's listing key not taken: the report follows what doctor printed, J52). Lane B sends lane RA
the constant first; §C1205's line moves in lane B's commit.
(131) A pointer names a line only where that line is, extending C1243 with C1258 (lanes RB and RA; C1438 with C1426,
and C1437). install.sh's loop_next turns status's `the source.<sid>.listable [warn] line below` back into
loop.EMPTY_ROOT_GRANT_STATUS on WAITING lines, through one LC_ALL=C sed whose two strings a test holds to
loop.EMPTY_ROOT_GRANT_BELOW and EMPTY_ROOT_GRANT_STATUS, a no-op where an older build prints neither; never an
`above` form, which the report does not read and install.out's order does not promise (C1426's form not taken). The
report's Loop line takes Doctor's form when Doctor prints that warn, no pointer and no `()` when Doctor ran in full
and printed none, and status's form only when Doctor was cut, did not run or had no hook (C1437). §16.242's dated
line moves in lane RB's commit.
(132) A zero-byte file is never served an older build's cached answer, completing C1304's step 5 (lane E; C1450 with
C1488, and C1466). _GuardedConverter.options adds `empty_screen: 1` only when the wrapped converter claims an OOXML
suffix or `.msg`, so pandoc's, xlsx's, pptx's, diagram's and msg's keys move once and every other converter keeps
its own (five emitter bumps not taken: they also move outdated_key and the versions pages show); convert_file skips
cache get and put for a staged file of size 0, after the label refusal, an OSError reading as not empty (C1466).
KEY_SCHEMA_VERSION stays, no outdated rule reads options and _republish_from_cache is unchanged, so a settled row
keeps its class (C930's ruling). The test seeds an older build's answer for .docx, .xlsx, .pptx, .potx, .vsdx and
.msg; C930's dated line and C1304's `Not built here` sentence move in lane E's commit.
Amended 2026-11-14, §16.253 C1646: built in `convert._identity`, keyed on the name, not in `_GuardedConverter.options`
(with an OCR engine pandoc is two converters, §16.248); C1466's zero-byte skip is what keeps the older answer away,
and the option is a second guard that changes no result (C1645).

ROUND 39 RULINGS (lead; still binding, as extended below):
(133) Both reports cut the same spellings by construction, from one table both read, completing ruling 126 (privacy;
data integrity: lanes RA, RB and B, FIRST; C1625, C1626, C1627 with C1671 and C1681, C1628 with C1672, C1629, C1634,
C1635, C1601 with C1656, C1633, C1640, C1675). Ruling 126's halves were ported by hand and drifted, so every value
they cut by lives once, in a new module src/agentsync/report_cuts.py (lane RA; data only, standard library only):
agentsync's verbs (agentsync_verbs(), hidden ones included), the provider apps and the provider folder forms kept
after CloudStorage (`<App>[-SharedLibraries]` then placeholders only, or a domain _org_of reads as naming no
organization: Personal, Business or Work in any case, or the app's own name), the generic folders, _AGENT_WORDS, the
separator class (blank, tab, `_`, `\`, `-`, every other C0 control and DEL, never LF or CR), the format characters
(every Cf in Unicode 14 to 15.1, Python 3.11 to 3.13, as code-point ranges), the alias forms in the order both
builders walk them, and bash 3.2's octal bytes (ruling 134). setup_report builds _SEP_RE, _FUZZY_SEP, _JOINED_SEP,
_PROVIDER_KEPT_RE and its aliases from it. install.sh carries a block report_cuts.shell_block() generates, between
`# BEGIN report_cuts` and `# END report_cuts`, which every shell reader takes (AGENTSYNC_VERBS, CUT_DRIVE and the
agent words move into it; each code point one UTF-8 alternative; the provider forms spelled out, since awk has no
backreference), held byte for byte to the generator by a test; the shell never reads the table from the installed
agentsync, which may be older (C1655). The rules both apply: the verb hold takes keep_verb's one per-position guard,
no verb held where a pair whose value begins with any verb matches right after `agentsync ` (C1625); the provider
rule runs in _cut_path, the recut's known(), typed_note_cut's cut() and a pass after the pairs in redact_stream's
tail over each `--source-local ~/Library/CloudStorage/...` word, never a guess in the pair loop, so
OneDrive-Fabrikam and OneDrive-Personal-Fabrikam read `~/Library/CloudStorage/<path>` while Box-Box,
OneDrive-Personal and a registered `OneDrive-<org-N>` stay (C1626, C1634); a folder or library whose Cf-stripped
spelling differs, has 2 or more characters and is one word is never aliased, leaves the report public when that word
is a generic folder or product word, checked first (C1635), and makes it private only when it is any other report
word, which the shell decides in fallback_redaction's own loop over `<folder-` pairs through a top-level safe_shown,
never by a flag out of the redaction_pairs subshell or a marker pair (C1627 with C1671 and C1681, whose
generic_folder step gives way to C1635); safe_shown drops the table's Cf set (C1628 with C1672); Python's separators
take the control bytes the shell already splits at (C1629); a --source-local argument under CloudStorage that is not
a configured source prints `~/Library/CloudStorage/<Provider>-<org-N>/<path>` on install.log's args= line and every
refusal line in both reports, the shell numbering no list-only name `<folder-N>` there (C1601 with C1656). Doctor
prints every folder or file name it shows through list_spelling, the spelling both reports alias, and its fixes
through _shell_path: the four tcc.<id> details and the launcher line (C1640) and the sentinel's ok line (C1675);
lane B lands that alone, first. The shell test harnesses load the block whole, with a guard test over every
ENVIRON-fed global the extracted functions read (C1633). The proof is one property test in test_report_redaction.py
(lane RB) that runs both reports over one hostile name set in every line form, under LC_ALL=C and en_US.UTF-8, and
requires identical lines, the same private-only verdict and no fragment of a name, with safe_shown over every Cf the
running Python knows equal to safe_text. Lane RA builds the table and its reader first and sends lane RB its branch
sha; lane RB builds the shell half, the harnesses and the proof on that branch and lands both halves in one
scripts/ship-land.sh run, so trunk never carries one half.
(134) The report knows what macOS bash 3.2's `printf %q` writes, measured with /bin/bash 3.2.57 on this Mac,
completing ruling 126's `$'...'` spellings (lanes RA and RB; C1670 data integrity, with C1636 and C1649, and C1648).
Under en_US.UTF-8, %q tests each byte, writes 0x80 to 0x9F and 0xAD as `\ooo` and every other byte raw, and
dollar-quotes the word when any byte is escaped (U+FEFF, all raw, is not quoted); under LC_ALL=C every non-ASCII
byte is octal. The table carries that byte set. folder_spellings adds the body bash 3.2 writes, built as bytes and
decoded with errors="replace" as the report reads install.out and the friction log, in `\E` and `\e` forms, beside
the C-locale body and the existing UTF-8 body, which stays (C1649, makes worse True: it is the only spelling of Café
with an ESC or a BEL); its docstring and emit_pair's comment say what each body matches (C1636). Both reports then
cut every `$'...'` word whole to `<path>` after their aliases: install.sh:4187's stage stays (C1656, makes worse
True: without it a body no pair covers prints in clear), setup_report takes the same cut, which is C1670's backstop,
and doctor's fix line reads `open ~/<path> in Finder once` in both, both legends' `<path>` items naming the cut.
_printf_word runs /bin/bash under LC_ALL=C and en_US.UTF-8 (LANG too), captures bytes and decodes them with
errors="replace", over U+2028, U+200B, U+202E after an accent, U+0085, É with a tab and Café with an ESC, skipped
unless darwin's /bin/bash is 3.2; §16.245's sentence, in lane RA's commit, says which body matches which name and
why a byte-wise miss is safe (install.sh writes %q only after `--source-local `, cut by structure). Lane RB's
legend, setup-feedback.md:103 and the pin at test_install_oneshot.py:3744 say `a decomposed accent or many
precomposed ones, such as É, ß, í or ł`, with no parenthetical before the closing one, and §16.46 R7 and §16.246
C1493 each take a CORRECTED line naming the byte rule, all in one commit (C1648). Both halves land in ruling 133's
run.
(135) Another build's render is kept only when that build can read every marked row install.out holds, completing
ruling 129's fallback (lane RB; C1655 data integrity, with C1630 and C1604). report_new_marks falls back to
install.sh's own report for a row opened by any of ESCAPED_MARK, ESCAPED_KEPT_MARK, TWIN_MARK (bab9deb6c, round 37),
ESCAPED_MARK_OLD and ESCAPED_KEPT_MARK_OLD, each matched at the start of a line as listed_folder_lines reads them,
so a warning copied into install.out is no row, and SPLIT_WHY names the mark it found, the longer before the kept
(C1630); a render this checkout's build wrote is still kept. When a kept render already held `<path>` before the
recut, the recut note scopes this pass's `<path>` to the two kinds it names, says any other is the installed
agentsync's own cut, and still ends `these N cut(s) are not counted above.` (C1604; CONTRACTS' quote near :41023 and
the c1406 case in that commit). The proof is C1655's reproduction (a twin past `--list-folders --limit 2`, the plain
twin renamed, a stub older renderer: no folder word in setup-report.md or bring-back.md, `shell fallback` on stdout)
and the c1447 test parametrized over the five marks, the same build's render kept. It lands in ruling 133's run.
(136) --fields and log records follow the document the command prints (lane B, last in its list; the CLI conventions
wave's landed --json code, built on trunk at that wave's lead's request of 2026-11-14; C1638 with C1673, C1639 with
C1674, C1643). _RESULT_FIELDS lists every key a command's document can carry: sync and accept-deletions take
`findings`, one `logout` row ("outcome",) serves graph logout and the top-level alias, whose document goes through
_emit (C1638 with C1673), `warnings` joins every row but status's, doctor and policy take ("status",), graph takes a
union row read only by _help_doc, help takes ("commands",) with --fields refused on a help page unless --all or
search, login takes none, and _cmd_help's inner parse goes through _parse, so `help bogus --json` is one usage line
(C1643, makes worse True: a bare help row lets `help status --json --fields commands` print the whole page). Log
records join the document's own warnings (clio.emit's warnings_at: status.warnings for status) only when the
selection keeps that field, after the filter; otherwise they stay for flush_log_warnings, one JSON object each on
stderr after the document (C1639 with C1674, makes worse True each: a filter after the merge loses records C1484
keeps). A test holds every top-level key of each document the tests capture to its row; §16.300.6 and §16.300.25
move in lane B's commit. That wave's queue (U-C4, U-C10, U-C5, U-C11, U-C13 and its plan record) touches neither
_RESULT_FIELDS nor clio.emit and rebases over lane B, keeping lane B's code in a conflict.

ROUND 40 RULINGS (lead; still binding, as extended below):
(137) Every argument form either report parses is a row of ruling 133's table that both read, and a case of its one
property test, completing ruling 133 (privacy; data integrity: lanes RA and RB, FIRST, lane B keeping its writer;
C1835, C1831 with C1881, C1832, C1833, C1882 with C1834, C1862, C1890 with C1837, C1836, C1840 with C1887, C1802,
C1889). Ruling 133's table held the values both reports cut by but not the forms they parse, so the shell's
hand-kept heads and splitters drifted again. src/agentsync/report_cuts.py (lane RA) gains the path heads, each with
its rule, generated into install.sh's block and read by typed_note_cut's slcut and by setup_report's
_SOURCE_LOCAL_RE and refusal reader: `--source-local `, `--source-local=` (C1831 with C1881) and `is not an existing
folder: ` keep a word under CloudStorage whole only when every part after a kept or registered provider folder is
fixed, a generic or home folder, or a configured source's placeholder, and otherwise print
`<Provider>-<org-N>/<path>`, as ruling 133 says, so Python's _source_local_shown and _not_a_folder_shown take a
`configured` set mirroring the shell's cfgph while typed notes keep _cut_path (C1882 with C1834: Python moves, the
shell stays); `add-source ` keeps every placeholder and cuts at its first unknown part, the shell printing the
provider folder and cut() of the unquoted word (C1832); `--cwd `, `--cwd=` and `debug: cwd ` cut their value as
_cut_path does, the word --cwd kept, and `debug: source` and `debug: config` cut only what lies under that run's
--cwd (C1835); the refusal heads move into the table from _REFUSED_HEADS (one holds an apostrophe, so shell_block()
writes it in double quotes, under its byte-for-byte test), and a refused name runs to the last head on its line,
never to its first blank (C1833); DEL's two escaped spellings, `\u007F` from config.toml_string (held to that writer
by a test) and `\u007f`, which _alias_escaped and add_forms both alias for a name and its shown spelling (C1840 with
C1887). ALIAS_FORMS and CUT_ALIAS_FORMS, which neither builder reads, go (C1889). Every shell splitter reads
CUT_SEP: fuzzy(), the pairs sort key, norm(), the org key, agent_protect's rx and sep, and agent_words_name's IFS,
built from the block, never typed; the run between words leaves out agent_protect's marker bytes \004, \005 and \016
to \035 (C1890 with C1837). redaction_pairs registers each configured source's tenant from sources.toml after the
disk walk, in config order, as _build_redactor does, and every `~/Library/CloudStorage/<App>-<word>` word on any
line goes through the provider rule in both reports, an unkept, unregistered one reading
`~/Library/CloudStorage/<path>`, never numbered; a tenant written without that prefix stays the documented residual
(C1862). agent_words_name and own_word_folder open no process substitution (own_word_folder keeps its own split
class): the verifier measured with /bin/bash 3.2.57 at 7253d365 that 255 of them inside one command substitution
segfault (254 do not) and that 250 to 450 listed folders crashed about 45 of 50 report runs and leaked names at 270
and 400; redaction_pairs ends `return 0`, its three readers fail closed through REDACT_FAILED_FILE, and
write_fallback_report refuses the report on that marker (C1836). Step 1's list spells each escaped byte but \n, \t
and \r as `\ooo` per UTF-8 byte, in LIST_ESC_AWK, arm_local and doctor.list_spelling alike, and folder_spellings
keeps the old `?` spelling as an extra alias (C1802). The proof is ruling 133's property test, whose line forms come
from the table's rows, so a row without a case cannot exist: both reports, under LC_ALL=C and en_US.UTF-8, identical
lines, no fragment of a name and the same private-only verdict, save ruling 138's one divergence. Lane RA builds the
rows, the generator, the Python reader and the test's cases and Python half, and sends lane RB its branch sha; lane
RB builds the shell reader, C1836, C1802 and the test's shell half on that branch and lands both halves in one
scripts/ship-land.sh run, so trunk never carries one half. Lane B keeps add-source's TOML echo and
config.toml_string as they are; a change to either is a table row first.
(138) One divergence between the reports' lines is kept, pinned and named wherever the cut is described (lanes RA,
RB and D; C1801 with C1857, C1839, C1863 with C1864, C1855). A bare `$'...'` word right after `--source-local `,
`--source-local=` or `add-source ` is cut whole to `<path>` by the shell report (ruling 134: the shell never decodes
a body no pair covers) and decoded, cut by its head's ruling 137 rule and shown in double quotes by the agentsync
report (round 17 U10; ruling 49, one folder one way inside a report); neither prints a name, and neither report's
output changes (C1801 and C1863, makes worse True each). Ruling 137's property test pins the pair instead of leaving
it out: install.log's real args= form (`printf %q` of the whole path), the = form and a dry run's add-source word,
under LC_ALL=C and en_US.UTF-8, for names bash 3.2 dollar-quotes (a C0 control, DEL, a tab, U+2028, an accent with
an ESC), under an unregistered, a registered and a generic-domain provider: the shell line ends `<path>`, the
agentsync line is the decoded cut, neither holds a name fragment, `$'`, an octal escape or U+FFFD, and the verdicts
match; a word %q writes with backslash escapes stays in the equality half (C1839, C1863). The agentsync legend's
`<path>` item adds the exception after "once", its pinned words kept (C1801 with C1857, lane RA); the shell legend's
dollar-quote sentence stays and adds one clause naming the agentsync report's decoded form (lane RB, in ruling 137's
run); §16.251's divergence paragraph names the = form and add-source (lane RA); setup-feedback.md's `<path>` row
says both reports cut every `$'...'` word, the agentsync report all but this one (C1855, lane D, from lanes RA's and
RB's words). The lead records the exception on packet 8ac14056ab04's call 3 as a dated recheck (C1864's option b).
(2026-11-17) The exception is recorded in the text of round 40 packet d24c7a8ee6bc, not as a recheck on
8ac14056ab04. That packet closed expired-actioned at its 2026-11-16 12:00Z deadline, before round 40's triage was
filed, so it takes no recheck. The plan's round 40 entry says the same (round 41 C2069).
(139) A help page and a version run refuse --fields as every command does, through b74445af's one pre-parse scan
(lane B; the CLI conventions wave's landed code, built on trunk at that wave's lead's word of 2026-11-16; C1856 with
C1808, C1844 and C1865, and C1845). argparse ends a --help or --version run before _select_fields runs, so _main
scans the tokens before `--` once, beside b74445af's _version_fields, for `--fields` or `--fields=`: with --json and
-h or --help (either order, top level, a graph action, `help --help`) it refuses with exit 2 and C1643's line
(`error: --fields: a help page is no result to select fields from; ...`), from one constant _select_fields shares,
so `<cmd> --help --json` and `help <cmd> --json` print the same bytes; `--version --fields x` without --json refuses
with _select_fields's `add --json` line; `--version --json --fields` keeps b74445af's _check_fields. Without --json,
help's text page still wins, --fields or not (R086), and `--version --json --help` keeps its text page.
tests/test_cli_json.py covers `status --help --json --fields bogus` and `--fields path`, the `--fields=` spelling,
--fields before --help, `-h`, top-level `--help --json --fields x`, `help --help --json --fields commands`,
`--version --fields version` and `--fields=bogus --version`, with plain `--version` and `status --help --fields x`
unchanged; §16.300.25's --fields bullet names `<cmd> --help --json` beside `help <cmd>`, its Tests list takes the
new tests, and the --fields paragraph says help's text page wins, all in lane B's commit.
(140) Every page a converter writes stays within max_page_bytes at every cap the config accepts, completing round
39's C1676 (lane E; C1848, C1886 with C1888, C1851 and C1860, C1849, C1807 with C1822). A [convert] max_page_bytes
under 8 KiB is a ConfigError at load naming the minimum (MIN_PAGE_BYTES = 8192 in config.py, lane E's this round);
the default 1,000,000 and `8KiB` load as before, and the 1,024-byte floors in text.py's _table and xlsx.py's two
budgets become max(0, ...), so a ConvertConfig built below the minimum, as unit tests build 400, gets a shorter
head, and at any cap of 2,048 or more the floor change alone moves no byte (C1886 with C1888 and C1851). xlsx cuts
each value before escaping it (the index's headers through _join_limited at about 600 characters, a streamed
first-row value at about 200, each ending `…`) and budgets the index and streamed Schema tables against the running
total, a note past the budget naming the sheet's own page, or the sidecar on the streamed path only (C1848). A cut
CSV page's note reads `[table past the page cap: the raw head above shows its start; every row is in the sidecar
`full-table.csv`]`, with no count (C1849). _parse_delimited raises csv.field_size_limit to at least len(text) + 1
and never lowers it or uses sys.maxsize, strict kept, so a well-formed CSV with a long field reaches the table path
(C1807 with C1822). text.py's and xlsx.py's _EMITTER_VERSION each move once, to 1.0.2. The tests hold every unit's
body to the cap through Registry.default at 8192 (an oversized header, an oversized first row, a TSV, four sheets of
thirty long headers, the streamed path) and the refusal at load; tests/test_review_fixes.py's two 2000-byte caps
move to 8192 with bodies still past it, in that commit. The C1676 paragraph in §16.253, the ConvertConfig row near
CONTRACTS:1413 and _table's docstring say the page stays within any cap the config accepts (8 KiB or more), in lane
E's commit (C1851, C1860).
(141) A zero-byte file is empty in every reader that meets one, completing round 39's C1647 (lanes B, RA, E and D;
C1820 with C1806, C1821, C1850, C1852, C1853). In _process, a recording on this Mac listed at 0 bytes (not dataless,
not a Graph source) is fetched with a probe budget, ByteBudget(0, 1) as _reread_source uses, before any defer; only
a copy that staged 0 bytes goes on, converted through the full registry, never _converting() or without_ocr, so
RecordingConverter's empty-head check (unchanged) classes it `empty` with no RECORDING_WAITS row and no WAITING ON
YOU line; a fetch that raises or stages any byte is discarded and deferred as today, so no interactive sync reads a
recording's bytes (spec S0 rules 4 and 5), and the HYDRATION_REFUSED line stays keyed on the name (C1820 with C1806,
makes worse True each; lane B). The report's `converter cache:` line leaves out rows whose canonical hash is the
empty file's (governance._EMPTY_SHA256), while manifest.record_cache keeps writing them for converted_seen and
converted_again (C1821, makes worse True; lane RA), and the OCR section counts a zero-byte image as `empty stub`,
after `not-readable stub`, with a dated line under §16.28's OCR images bullet (C1850, lane RA). CONTRACTS:41867 and
test_convert_image.py's docstring say fourteen typed names (C1852, lane E), and §16.28's C1647 line says a zero-byte
image or recording is `empty` where a converter reads it and `no converter` where none does, from lane B's words
(C1853, lane D).

ROUND 41 RULINGS (lead; still binding, as extended below):
(142) Each head in ruling 137's table carries one rule that both reports apply to any path, wherever it lives,
completing rulings 133 and 137 (privacy; data integrity: lanes RA and RB, FIRST, one joint run, lane D after it;
C2062 with C2031 and C2085, C2084, C2060, C2063 with C2083, C2029, C2028, C2050 with C2032, C2030, C2027, C2071,
C2033, C2051). Ruling 137 made every argument form a row, but the shell report applied the provider and
first-unknown rules only to words under ~/Library/CloudStorage/ while the agentsync report cut every word, so a name
outside CloudStorage, or a tail after a provider, printed in clear in the public shell report and the two reports
diverged. A head's rule now reads any path: under CloudStorage or not, the home folder, a relative path resolved
under --cwd, and the config path. slone stops skipping a word outside CloudStorage and cuts it as _cut_path does,
through cut() of the unquoted word, a CloudStorage word keeping today's provider arm; a bare word with no slash,
backslash or quote after `--source-local `, `--source-local=` or `add-source ` stays prose, as _source_local_shown
leaves it, while a not-a-folder or refused name is always cut; cut() reads a listed-only placeholder as Python does
(C2062 with C2031 and C2085, makes worse True each for the unguarded cut). _cut_path runs the first-unknown walk
before any provider shortcut and tests the provider tail inside it (C2084). Every occurrence of a head on a line is
cut: dbcwd and dbunder loop as slone does, and debug_cwd reads every `debug: cwd` match (C2029). Every config path
but the default ~/agent-context/sources.toml is a table row, cut as _cut_path cuts its ~-form on every line that
echoes it (C2063 with C2083). A folder step 1 listed but the config does not name is decoded from its `\ooo`
spelling and registered under its <folder-N> (C2060). The free-text provider part runs across blanks to the next `/`
before a quote, backtick, `):` or `, stored as ` (FREE_PART_STOP, generated as CUT_FREE_PART_STOP); a tenant with no
`/` after it is a named residual beside the prefix-less one (C2050 with C2032). `--cwd is not a folder this run can
enter: ` is a whole-name head, and only install.sh's own usage phrases after `--cwd ` stay whole, by phrase (C2030).
A quoted refused or not-a-folder name keeps its quotes and the rest of its line (C2027). Python reads a
GoogleDrive-<address> tenant as <org-N>, as the shell does, and a 2-letter id or folder part takes the hold-value
rule in both reports (C2071, makes worse True for registering every short id). report_recut and every older build's
render read the same heads: an install.out holding a --cwd run header or a `debug: cwd` line falls back to the shell
report (C2028). Both legends name these forms, every pinned clause kept (C2033). The property test of rulings 133
and 137 gains, per head, a path outside CloudStorage, a path under --cwd, two heads on one line, a quoted name with
a blank and a tenant with a blank, plus prose rows that pin the guard, the config arms, a listed-only arm with a
0x1E name and C2071's rows, its fragment check testing each raw split word and safe_text apart: both reports, both
locales, identical lines, no name fragment, the same verdict, save ruling 138's one pinned divergence. Lane RA
builds the rows, the generator, the Python reader and the test's cases and Python half, and sends lane RB its branch
sha first; lane RB builds the shell reader, the fallback and the test's shell half on that branch and lands both
halves in one scripts/ship-land.sh run, riding on lane RA's branch, so trunk never carries one half. The CONTRACTS
lines these words move land in that run; lane D writes setup-feedback.md's `<path>` and `<email-N>` rows once, from
both lanes' final words (C2051).
(143) Every page a converter writes stays within max_page_bytes, measured on the guarded page's real bytes,
completing ruling 140 (lane E, lane B for the re-read; C2042 with C2087, C2043 with C2088, C2064 and C2046, C2044,
C2022, C2045, C2047, C2055 with C2091, C2021). Ruling 140 held the table budgets but not what surrounds them, so
fences, the guard's lines and the xlsx sheet page's lists still passed the cap. A budget counts the bytes it will
write, never a guess: _capped and _cap_body recut a head whose closing fence must outgrow a backtick or tilde run by
that excess only, so a page with no such run keeps its bytes (C2042 with C2087); one GUARD_RESERVE in _common (the
banner, the sidecar line and its digest) comes off every fit test (C2044); xlsx's sheet page renders its comment and
object sections first, gives them their real bytes up to cap // 4, budgets the table on the rest with no
200-per-item guess and no floor, fits each list with _fit/_listed in the room left, ends a cut list with a note
naming its sidecar, and writes every comment and object whole to `<NN>-<sheet-slug>.comments.txt` and
`.objects.txt`, never colliding with the rows CSV; a chart title or pivot name is cut to 200 characters and escaped
(C2043 with C2088, C2064 and C2046). An oversized first row is shown with its cells cut, never dropped (C2022); the
index's cut note names the sheet's page or its CSV sidecar for headers (C2047); the per-cell cuts stay, scaled with
the cap, and the docs say a longer cell ends `…` (C2045, makes worse True for dropping them). text.py and xlsx.py
move _EMITTER_VERSION once each, to 1.0.3, a _cap_body caller whose bytes change moves one step, and no
_REREAD_BELOW moves. With every list bounded, §16.258's residual goes, and config.py's comment, the ConvertConfig
row and §16.258 say every page stays within any cap the config accepts (C2055 with C2091, lane E). An edit of
[convert] max_page_bytes re-reads existing local and inbox pages through an options-drift target in _reread_targets
under the re-read budget, committing only a page whose body changes, a Graph page changing with its bytes, and
status counts pages built under other options (C2021, lane B, which sends lane E the comment's words first). This
amends ruling 140: its "at any cap of 2,048 or more the floor change alone moves no byte" holds for text.py only.
Tests through Registry.default at 8,192 and 20,000, and the default cap for the backtick runs, hold every guarded
page to the cap and every comment's full text to its sidecar.
(144) A recording that waits is one state every reader names alike, and the zero-byte probe reads no byte,
completing ruling 141 (lanes B, RA and D; C2038 with C2086, C2039 with C2007, C2041, C2006, C2054, C2090, C2057 with
C2089). _empty_recording calls a no-read probe, LocalArm.stage_if_empty (InboxArm inherits it), in place of
arm.fetch: it opens with O_NOFOLLOW, requires a regular, not-dataless file of size 0 with the same inode on the
descriptor, stages an empty file, checks size, inode and mtime again, and otherwise returns None having read no
byte, so a recording that grew after the walk is never copied or hashed and the docs' "discarded unread" stays true
(C2038 with C2086; lane B writes the method in arm_local.py this round, lane E leaves it). A local REFUSED recording
whose reason is `no converter for <recording suffix>` is re-planned once a cycle's registry holds the recording
converter, a 0-byte one through _empty_recording and any other to RECORDING_WAITS through _recording_defer, which
may now replace that reason; the planner writes a manifest marker when it gives a recording the OCR-down page, and
_unpublished counts a REFUSED recording row only with that marker, never by its reason alone, so C1841's note covers
it during the outage, the usual wait follows recovery, and a no-media Mac's stub stays uncounted (C2039 with C2007).
last_run_counts passes over a run crash recovery closed `aborted` (C2041). The report's Quarantine table leaves out
a RECORDING_WAITS row, by exact match on cycle.RECORDING_WAITS, so its legend stays true (C2006, lane RA). Lane D
writes from lane B's words: §16.28's C1647 line and its amendments say a zero-byte local recording is `empty` where
the recording converter is registered (an OCR and a media engine, [convert] recordings on), also when the OCR helper
stops mid-cycle, and `no converter` without it (C2054, C2090); README:246 says an interactive sync reads none, but a
recording on this Mac with no bytes gets a page saying it is empty, neither counted nor waiting, while an
online-only or Graph one still waits (C2057 with C2089); lane B's commit corrects the _recording_pass docstring.
(145) Step 2 tells the person about an inbox only when one syncs, so the prompt goes to v38 in lane D's one commit,
as ruling 129 took it to v37 (lane D; C2092). Step 2's condition reads a kind = "inbox" source that is neither
paused nor retired, on the folder named inbox beside the docs repo, matched as that folder, never by string (the
inbox line ~/agent-context/docs/AGENTS.md writes from live sources is the anchor, never status's `inbox on`, which
counts every live inbox folder); if that folder's source is paused or retired, step 2 says files dropped there do
not sync until the person sets it back to live, an edit the agent never makes; if setup printed `inbox: not added`,
it says so and shows that line; any other live inbox source is a folder the person added. The same commit moves the
block's version line and digest, `prompt v38` in step 1's --log-start, SETUP_PROMPT_COMPAT in install.sh,
setup_report.PROMPT_VERSION and its docstring's version list, the issue form's placeholder, setup-feedback.md's
version lines and the old-copy refusal list (v37 joins the versions an older copy reports), a K62 row, every test
that pins the block or a version, a new test pinning step 2's condition to is_live, and §16.264's dated line;
CUT_OFF_TOLD names no version and stays. Lanes RA and RB never touch those lines, so lane D waits on no lane for
this commit. No other lane edits the README setup prompt block, its version or its digest; a lane whose fix changes
words the prompt quotes sends lane D those words first. C2053 stays out of the prompt, as its verifier says. Round
42 rehearses v38, the prompt the operator will paste, with v37's block as the old copy.

ROUND 42 RULINGS (lead; still binding, as extended below):
(146) The free-text provider scan judges the whole run a tenant can hold, the same in both reports, completing
ruling 142's free-text part (privacy; data integrity: lanes RA and RB, one joint run; C2285 with C2235 and C2271).
The keep test ran on the part up to its first blank, so a tenant whose first word is a kept generic word
(`OneDrive-Personal Secret/Plans`) printed whole, while a run to the next `/` swallowed a later path. The long
candidate now runs across blanks from the part's start to the first `/`, FREE_PART_STOP, blank before `~/` or `/`
(an independent path start, which no tenant name holds) or line end, and never stops at a blank before `-`
(`Northwind - EMEA`). A part whose first word is not kept is cut whole to that bound, _FREE_MORE_RE following only a
`/` and FREE_WORD_TRAIL's give-back kept, so `…/OneDrive-Northwind Traders then ~/Documents/x` reads
`~/Library/CloudStorage/<path> ~/Documents/x` and `OneDrive-Tailspin Toys` at a line end is cut whole: this reverses
ruling 142's no-`/` residual (C2271) and leaves the head-less tenant the only one of its kind. A kept first word is
judged on its whole run only when the run reaches its `/`, so `OneDrive-Personal and ~/Documents/x` stays whole and
`OneDrive-Personal Secret then ~/x` is the second named residual (C2235, makes worse True for bounding the not-kept
arm at its blank word, which printed `Traders`). setup_report._free_provider_cuts and install.sh's freecut with its
report_cuts-generated block change in one commit, with parity rows for every example here and C2050's, in both
locales. Lane RA builds the Python halves and both-reports rows of rulings 146 to 149 first and sends lane RB its
branch sha; lane RB builds the shell halves on that branch and lands both in one scripts/ship-land.sh run, so trunk
never carries one half.
(147) Every spelling of a config path or name a report holds is cut to its end, whichever build wrote or renders it
(privacy; data integrity: lane RB first and alone for the fallback, lanes RA and RB in the joint run; C2230 with
C2284, C2290, C2231, C2320 with C2239). An older installed build renders a non-default config path in clear, and
install.sh kept that render (C2230 with C2284): write_report's fallback gains report_config_lines after
report_cwd_lines, behind `other -eq 1`, firing when $CONFIG's ~-form is not CUT_CONFIG_DEFAULT and config_shown
would cut it, or when install.out holds a `debug: config ` head whose value is neither empty nor the default; the
C2028 'none' control moves to the default config; never a recut port. The whole-config cut runs to the end of its
path token on every line, Redactor._sub through one compiled regex per literal and the shell's config pairs through
a new awk flag, keeping a sentence-final `.` and every terminator (C2290, makes worse True for a boundary-only
match). Python cuts the config's unredacted ~-form, as config_shown does (C2231). _toml_spellings builds the old
writer's base (json.dumps with ensure_ascii off, DEL in its escaped form) beside the current one, never by reversing
escapes, and replaces every TOML_BREAKS character in every form, so a pre-af21e6339 add-source echo of a name
holding a line break and a tab, control byte or DEL is cut as the shell cuts it; the loop C2239 called dead is live
again and stays, its docstrings corrected, and the legacy rule is one named helper in setup_report.py citing
config.toml_string (C2320 with C2239).
(148) A head's value has one bound in both reports: an empty value, a quoted name, the `=` form and the count read
alike (privacy; data integrity: lanes RA and RB in the joint run; C2286 with C2242, C2234, C2236, C2321 with C2237,
C2265, C2275). A value is empty only before `| ` and another `debug: ` head, or in the double-space form: Python's
head regex takes an empty value only there, its cwds list takes non-empty values only, and dbcwd, dbunder and
dbconfig guard the same way with `v != ""` kept, so an empty cell neither eats the next one nor replaces the carried
cwd (C2286 with C2242, makes worse True for allowing every empty value); _CWD_NAME_RE and _NOT_A_FOLDER_RE take an
empty name as written. A name that runs to install.sh's own ` (see --help)` end is taken whole, quotes included,
slone through nfend, and only an end-of-line name opening with a quote ends at its matching quote, the rest of its
line kept (C2234, makes worse True for ending every quoted name there). install.sh's --cwd usage words stay whole
only after a head ending in a blank, never after `--cwd=` (C2236). The --cwd usage-error cut is not counted, as the
shell counts none, the not-a-folder and refused --source-local names are, and the legend, setup_report's :1194
docstring and CONTRACTS' C331 passage name exactly those counted cuts (C2321 with C2237, makes worse True for
uncounting them). install.log's relative --source-local word keeps its `./` or `../` head in the agentsync report
and is cut whole in the shell report: a second kept divergence beside ruling 138's, declared in both legends,
report_cuts.py:139, §16.255 and setup-feedback.md:103 and pinned by a both-reports test, with no output changed;
COMMON41.md's ruling 138 stays as written and this ruling amends it (C2265, makes worse True for converging). The
property test gains C2275's two `--cwd=` rows outside CloudStorage, never the form whose quote wraps the whole
argument.
(149) A two-character source id, and each id's and folder's number, take one rule in both reports at every entry
point (privacy; data integrity: lane RB with lane RA's half in the joint run, lane D for the words; C2287 with
C2232, C2233, C2202, C2240, C2261, C2262 with C2312). A 2-character source id is a whole word in any case in both
reports: Redactor.add sets ignore_case for a 2-character source id not in capitals, the shell's short_pair stays as
it is, and the vanished-source and hold-scope loops call short_pair after add_pair with the same <source-N>; never a
flag-w source pair, which C2071 rated worse, and a word id such as `it` keeps the report Private only where its
placeholder shows (C2287 with C2232). The shell's id loop finds the placeholder that hides an id (an org key, a
fuzzily registered folder key, a two-letter capitals word exactly) before it counts, skips an id under 2 characters,
decodes TOML escapes, numbers only on a miss and always registers the pair (C2233). install.sh registers
install.log-only names after every value the agentsync report numbers, so configured and listed folders share their
<folder-N> and a product word such as `Copilot` stays unnumbered (C2202, makes worse True for numbering it in both).
The C2071 test says what it pins (C2240). Lane D writes from both lanes' final words: §16.260 names short_pair's
flags (w for a two-letter word in capitals, i for any other short id) and fallback_redaction's judgment (C2261), and
setup-feedback.md:102 keeps its pinned phrase and makes a report Private only where a two-letter word's placeholder
shows, `HR` and `QX` leaving it public (C2262 with C2312).
(150) C2060 is gated on the operator, so C2263 is built only on "Allow it" and nothing else writes its change
(privacy; data integrity: lane RB, GATED; lane D for C2259; C2263, C2259). The auto-mode classifier refused lane
RB's round 41 C2060 edit, the operator's class C decision 1476293d00a3 is still open, and a refused command is never
re-run or reworded. Lane RB reads `cc-decide show 1476293d00a3` at start and before its last land. On "Allow it" it
applies round 41's text as rebased in round42/verify-c2060-rebased.diff (list_decoded before redaction_pairs, the
decode block after short_pair, short_pair kept), drops the strict xfail on
test_both_reports_cut_a_folder_step_1_listed_but_not_configured_ruling_142 and rewrites §16.261's C2060 paragraph as
built, in one commit of its own that the joint run never waits for; a second refusal stops it, and it messages the
lead. Otherwise round 41's strict xfail stays and §16.266 says C2263 stays unbuilt. No lane writes an equivalent
install.sh change another way. Lane D's guide says what is true at its land: while C2263 is unbuilt, the folder row
names the listed-only gap in a caveat pinned to that xfail, with §16.264 C2051's dated line (C2259); if C2263 lands
after it, lane RB's commit drops the caveat and its pin, the one docs/deploy edit outside lane D.
(2026-11-23) The lead's recheck superseded decision 1476293d00a3 with class C decision c0777e12a523: read this ruling's
1476293d00a3 as c0777e12a523, as ruling 190 does, and the strict xfail's reason names c0777e12a523 (round 45 C2882).
(151) The inbox is one folder, known by identity, and every reader says what sits on it (data integrity: lane B
first, then lanes RB, RA and D; C2318, C2309, C2207, C2258). One paths.same_folder (lexical equality, then the
collision_key gate, then samefile, OSError False; cli._same_folder kept as an alias) answers "is this the inbox
folder" for config.ensure_inbox, Publisher.root_guide, add-source's inbox-first branch and the report's overlap
pairing, so `Inbox` and `inbox` on a case-insensitive Mac are one source (C2318). add-source's inbox-first branch
asks whether any source is already on the kept folder, never whether an inbox source exists elsewhere, and
_ensure_inbox prints `inbox: <path> is source '<id>' (<kind>, <state>), not an inbox source` when another kind sits
there (C2309). _ensure_inbox words a failure by cause, a file or dangling link with the move-aside advice and any
other OSError with its strerror and no advice, still exit 0; install.sh's config line drops its `(inbox ensured)`
claim, init's own line saying what happened, and its note logs `inbox=not-added`; the report's Configuration
`inbox:` line reads sources.toml and the folder from disk through same_folder (`live`, `paused`, `retired` or `not
added (<reason>)`), never "any live inbox source" (C2207). Lane D writes "Setup keeps an inbox" at README:54 and
docs/deploy/README.md:127, with the paused, retired, not-added and another-kind cases, and CONTRACTS :5097 and
:9037; lane RB moves the string pins (test_install_oneshot.py:1313 and :12938, test_launcher.py:690) in its own
commit (C2258).
(152) A record ends only at the byte its writer ends it with: a reader splits on that byte, never with
str.splitlines() or strip(), and a writer escapes or quotes every line break inside a record (data integrity; lane
E; C2299, C2302, C2301, C2304, C2252). pandoc's _own_lines splits stderr bytes on LF and tests `pandoc:` on bytes,
so a warning holding CR, VT, FF, 0x1C to 0x1E, U+0085, U+2028 or U+2029 before `pandoc:` copies no warning text into
an error (C2299, privacy); a conversion run's non-zero exit names its first record that is not `[WARNING]`, `[INFO]`
or indented, through _plain, `--version` gives _ended its whole stderr through _plain, and the crash branch keeps
_own_lines (C2252). _csv_bytes writes a row with a CR in a field through a QUOTE_ALL writer, every other row's bytes
unchanged, tested on 3.11 (C2302). The xlsx comment sidecar escapes backslash, newline, CR and tab and strips
nothing, one record per line, and §16.263's sentence says so (C2301, makes worse True for raw text).
speech._git_config reads `git config -z --type=path --get-urlmatch http <url>`, split on NUL bytes, decoded with
surrogateescape, unstripped, an empty proxy kept (C2304; test_deploy_pack.py:6087's quote in lane E's commit).
text.py and xlsx.py move _EMITTER_VERSION once each (ruling 153); no _REREAD_BELOW moves, so a sidecar already
written takes these fixes when its file next changes, and §16.268 says so.
(153) A capped page shows all the cap leaves room for, keeps whole what a cut drops, and the words say what the cap
bounds, completing ruling 143 (lane E; data integrity for C2300; C2208, C2272, C2254, C2268, C2300, C2220 with
C2306, C2269, C2314, C2255, C2201 with C2209). The text cap's head grows line by line while it and its own fence fit
(C2208, makes worse True for a cut at the run's line start). _line_head cuts mid-line at a word, else a character,
boundary when the last line end keeps under half the budget, never inside a `\` escape, and a fence longer than half
the budget gives way to a short one (C2272; CONTRACTS:11040 and the _cap_body docstring). When the keyframe footers
leave a window no room, _cap_body works the room out again without them, so the window keeps a keyframe-free page's
text and passes the cap by its footer lines only (C2254, makes worse True for returning the body uncut). xlsx's cut
row takes the largest length that fits once the later rows that fit uncut keep their bytes, searched on rendered
bytes, and the streamed path reserves the real bytes of what follows, not a flat 4,096; a recording's index stays a
named exception (C2268, makes worse True for filling the room). A cut row cuts value and formula apart, never
splitting the code span, and every formula cut or left out rides whole in `NN-<slug>.formulas.txt`, counted in the
footer reserve and named by the row-cap note, with no formula column in the CSV (C2300). config.py's comment, the
ConvertConfig row and §16.258's and §16.263's sentences say the cap bounds the guarded body and the front matter, up
to about 1 KB, sits outside it, never budgeted (C2220 with C2306, makes worse True each); §16.258's C2055 line names
the two exceptions with its prefix kept (C2269); the xlsx header formula names the 1,024-byte reserve and the
200-byte row (C2314); the C2045 test checks its 258-character value (C2255). text.py and xlsx.py each move
_EMITTER_VERSION once, to 1.0.4 (an empty text file's 0 lines in text.py's move, C2201 with C2209), each other
_cap_body caller whose bytes change moves one step, and no _REREAD_BELOW moves.
(154) A [convert] edit re-reads every local and inbox page converted under other options after the first recorded
sync, and every reader says exactly that, completing ruling 143's re-read (lane B, lanes E, RA and D for the words;
C2243 with C2291, C2292, C2270, C2246, C2210 with C2221, C2256 with C2253, C2297, C2308, C2257). A page is dated by
the run that converted it: _REREAD_SQL compares COALESCE(c.created_run, o.built_run), rewrite_frontmatter's
built_run stays, since the report reads it, and a rename or a repair from cache no longer exempts a page (C2243 with
C2291, its three tests). A converter that cannot start keeps its drift targets waiting, under a count check, counted
in reread_waiting and never `sync again` (C2292); only a converter that made a page for the source is asked whether
it starts (C2246); _REREAD_OPTIONS reads "have pages made under other converter options", its tested prefix kept and
"[policy]" gone, and the edit's hash set is stored with its fingerprint, so a BANNER_VERSION or
SIDECAR_DIGEST_VERSION bump re-reads nothing (C2270). No code opens drift for an edit made before the first recorded
sync, and recordings and cloud files are not re-read for an options edit (C2297, makes worse True for routing
recordings through drift and for an upgrade-time re-read; C2256's optional code is not built). Lane B sends its
words first; lane E's config comment and ConvertConfig row and lane D's README [convert] paragraph, §16.262 C2021
and §16.264 R41-D1 dated lines carry them once, word for word where they quote each other: those two exceptions, a
recording keeping its cap until it is read again for another reason (C2256 with C2253), and the re-read's note
starting `sync again:` only while another sync reads more, the options note after it (C2308, C2257); lane B's
test_cycle.py tests each limit. The report's Configuration shows `- [convert]: default` or `- [convert]: set
(<option names>)`, max_page_bytes's number shown and pandoc_path's value never (C2210 with C2221, lane RA).
(155) A recording's page, mark and saved pieces follow the state it is in now, and only a settled row is re-planned,
completing ruling 144 (lanes B and E, lane D for the words; C2205, C2244, C2245, C2293, C2249, C2294, C2296 with
C2251, C2219, C2298 with C2307). An outage stub a read replaces becomes a `unit-removed` tombstone with a neutral
heading and superseded_by naming its first new page, its status, reason and ChangeOp kept and lints.py's heading
tuple moved in the same commit, and it leaves the `d` count (C2205). The OCR-down mark is re-judged only in a
non-dry cycle whose registry holds no recording converter, through _mark_ocr_down (C2244, makes worse True for
clearing it whenever recordings do not wait on OCR). A re-planned stub is rewritten through _plan_stub with the real
wait's reason, reading no byte and written only when the reason changes, its page naming the recording line, not
QUARANTINE.tsv (C2245). Withheld ids join the replan's and the re-read's skip set (C2293). Each re-planned row has a
savepoint inside its batch, so a raising row rolls back alone and the rows before it commit (C2249, makes worse True
for a plain catch). stage_if_empty opens with O_NONBLOCK (C2294, lane E). Picture pieces key on their own
_PIECE_REVISION without max_page_bytes or the emitter version, reading a 1.1.0 or 1.1.1 key once and saving it
again, the emitter not stepped back (C2296 with C2251, lane E). This amends ruling 132 for C930's settled rows,
once: a zero-byte LocalArm stub whose reason is a converter's own no-bytes type mismatch is re-read one time through
stage_if_empty under a `zero-bytes-v1` outdated_key token, never a label, refused, no-converter, withheld,
hydration-refused or path-length row (C2219, makes worse True for an unbounded re-read; lane E). Lane D writes
README:254's condition in plain words (OCR on, the media helper built, recordings not turned off) with the
missing-helper case and no internal name, its test tied to Registry.default and _Cycle._process (C2298 with C2307).
(156) The setup prompt reads the paths and folder states its installer printed, never a default it assumes, so it
goes to v39 in lane D's one commit, as ruling 145 took it to v38 (lane D, after lanes B's and RB's printed lines
land; C2316, C2317, C2212, C2310 with C2319). Whatever step 2's exit status, a NEXT reading `no folder is synced
yet` or `no folder to sync yet` sends the agent to step 1's last sentences and never to step 3, and step 1's trigger
and the report exception read "step 2, or the first sync of step 3" (C2316; install.sh's exit 0 stays). Step 2 reads
the inbox source in the config the installer's `config:` line names, on the folder its `inbox:` line names, step 3
reads and writes under its `docs repo:` line's folder, and the final inventory comes from the report's Configuration
or a status listing, ~/agent-context the example default only; lane B prints status's resolved `inbox:` line and
lane RB the config step's `docs repo:` and `inbox:` lines, each sending lane D its words first (C2317). A source of
another kind on the inbox folder is told as that source, its id, kind and state from C2309's line, its kind the
person's edit (C2212). The label sentence matches README:61, and a removed file queues a purge by default only
(C2310 with C2319). The same commit moves the block's version line and digest, `prompt v39` in step 1's --log-start,
SETUP_PROMPT_COMPAT in install.sh, setup_report.PROMPT_VERSION and its docstring's version list, the issue form's
placeholder, setup-feedback.md's version lines and the old-copy refusal list (v38 joins the versions an older copy
reports), a K62 row, every test that pins the block or a version, and §16.269's dated line; CUT_OFF_TOLD names no
version and stays. No other lane edits the README setup prompt block, its version or its digest; lane RB adds
C2316's exit-0 case after it lands. Round 43 rehearses v39, the prompt the operator will paste, with v38's block
(4c1fd00de) as scenario C's first old copy.

ROUND 43 RULINGS (lead; still binding, as extended below):
(157) Every path a report prints is cut in every spelling its printer writes, in both reports (privacy; data
integrity: lanes RA and RB, one joint run; C2497 with C2431, C2432, C2430). _add_pandoc_cut also registers
doctor._shell_path of each absolute form of a configured pandoc_path (as written, expanded, resolved) to
_add_config_cut's own `~/<path>`, home_only kept so /opt/homebrew stays (C2497 with C2431); install.sh's
redaction_pairs reads [convert] pandoc_path (basic or literal string), expands `~` and, under $HOME or its pwd -P
form only, pairs the raw, `~/`, debug_path, `~/'…'`, `~/"…"` and `'\''` forms with flag c, LC_ALL=C at every stage
(C2432). With a docs repo outside the home, both reports register `<its parent>/inbox` whole after the four own
folders, never for a default inbox, never under a `/` parent (nor the docs path there), never the parent itself
(C2430). Lane RA builds the Python halves and both-reports rows of rulings 157 and 158 first and sends lane RB its
branch sha; lane RB lands both halves in one scripts/ship-land.sh run, so trunk never carries one half.
(158) A cut token ends at the same byte in both reports, whatever head or quote opens it, amending ruling 148
(privacy; data integrity: lanes RA and RB in ruling 157's run; C2526 with C2435, C2496, C2466, C2468, C2436). The
config-path terminator is the shell's ASCII set plus `\n`, one named constant _CUT_TOKEN_END is built from, so NBSP
and U+3000 sit inside a token (C2526 with C2435); a printf-%q `\x` whose x is not `"` sits inside it, in the regex
and the awk cf loop alike, and `\"` or a line-final `\` ends it (C2496). A head right after `'` or `"` (`'--cwd=`,
`'--source-local=`, their blank forms, add-source) runs to its closing quote or the line's end, split at any later
PATH_HEADS head after a blank, each piece cut by its own head's rule, CUT_HEADS_* regenerated from PATH_HEADS;
ruling 148's "never the form whose quote wraps the whole argument" clause is lifted (C2466). install.sh runs
foreign_home_cut before the head cuts, as Python redacts other homes first, with no more() in slone (C2468). The
not-kept free-text arm gives back trailing blanks and tabs through a new FREE_CUT_TRAIL, FREE_WORD_TRAIL and its
--source-local reader unchanged (C2436). Every form is a _r142_lines or R146_FREE_ROWS row in both locales.
(159) A source id and a folder take one number and one length rule in both reports, the shell moving to Python's
(privacy; data integrity: lane RB alone, setup_report.py unchanged; C2501 with C2433, C2434, C2471). One id_known
helper (an org key, a fuzzy key of 3 or more, a folded value), declared before the configured loop, serves all three
source-id loops; the vanished and hold-scope loops skip an id under 2 characters before they count and number only
on a miss, and the vanished add-back folder loop runs before the hold-scope loop, as Python registers (C2501 with
C2433). For source ids only, the 2-character test reads the separator-free key (fuzzy_key), with add_forms flag i
and two_letter_word on that key, short_pair and its folder-part callers unchanged (C2434, makes worse True for
moving Python, which stopped cutting `Q-3`). A OneDrive-SharedLibraries library is `<library-N>` in the shell too,
its own counter and key map at the configured, listed depth-2 and vanished cloud roots, the first registration
winning across both maps, a legend item only when shown; the C2071 carve-out goes, with a same-name
library-and-folder test (C2471).
(160) A render by another build gets every cut this build makes, or the shell report replaces it (privacy; data
integrity: lane RB; C2498, C2499, C2500). freecut and its BEGIN inputs become one shared awk function, and
report_provider_lines, run only when OTHER=1 over $REPORT_PATH, joins write_report's fallback condition, so a render
whose provider words the scan would change gives way to the shell report (C2498, its option A). report_config_lines
reads a value after `| ` as empty only when another debug head follows, through one empty-cell function shared with
dbval (C2499). report_words_cut's not-a-folder block ports _quoted_cut's end-of-line branch through a closeq helper,
cutting only a non-empty unknown name and keeping its opening quote (C2500). Each case runs _report_recut_harness
against setup_report._redact_lines, and other=0 stays byte for byte.
(161) C2060 is still gated on the operator, so C2469 (round 42's C2263) is built only on "Allow it" and nothing else
writes its change (privacy; data integrity: lane RB, GATED; C2469). Ruling 150 stands. Lane RB reads
`cc-decide list --open` for 1476293d00a3, and any packet the lead files to replace it, at start and before its last
land. On "Allow it" it checks that round41/scratch-RB/refused-1-full.txt, as rebased in
round42/verify-c2060-rebased.diff, applies at its base, applies it, drops the strict xfail on
test_both_reports_cut_a_folder_step_1_listed_but_not_configured_ruling_142, rewrites §16.261's C2060 paragraph and
§16.271's line as built and drops lane D's C2259 caveat with its pin, in one commit the joint run never waits for; a
second refusal stops it, and it messages the lead. Otherwise the strict xfail stays and §16.271 says C2469 stays
unbuilt. No lane writes an equivalent install.sh change another way, and a refused command is never re-run or
reworded.
(162) The inbox's state is decided once, by folder identity and the disk, and every reader prints that answer,
completing ruling 151 (lane B first, then lanes RA and RB; C2410, C2411, C2441, C2525 with C2503, C2513, C2537,
C2440). One helper in config.py beside ensure_inbox takes the folder named inbox beside the docs repo: an entry
there that is a file, a link to one or a dangling link reads `not added (<folder> is not a folder)` whatever
sources.toml lists; otherwise sources match canonical_source_root(folder) through paths.same_folder (live, paused,
retired, another kind's source, or no source), the folder printed as typed, and setup_report._inbox_line prints it
(C2525 with C2503, C2513, C2537). status and step 2 keep `inbox: <path>` byte for byte and, only when no live inbox
source sits there, print `inbox state: <the helper's words>` after it; install.sh's -I snippet calls the helper when
the installed build has it, else answers inline (samefile, then a lexical match), and prints the bare line on any
failure (C2410). loop's inbox() matches by same_folder (C2411); add-source that adds an inbox source on the kept
folder reports `created` with one line (C2441); install.log's inbox=not-added also fires on another kind's
`is source '<id>' …, not an inbox source` line (C2440).
(163) No reader calls a Mac synced, or asks for a draft, while nothing from a folder is converted (lanes RB, RA and
B; C2401, C2402, C2424). A bare step 2 re-run on an inbox-only config keeps exit 0 and ends
`NEXT: no folder is synced yet:` with the --source-local re-run form, in place of the skill-missing NEXT and after
the [FAIL], held-listing and doctor checks (C2401, lane RB). LOOP_STAGES gains `inbox synced` between installed and
synced, chosen from live_sources by loop.py's own non-inbox test, never from status's words, a drafted baseline
keeping its stage; CONTRACTS :5478 and :5551, setup-feedback.md:583 and the issue form's Loop stage text move in
lane RA's commit (C2402). With no converted page in the mirror, loop.next_step's rule 4, and rule 5 and docs_waits
for an empty draft, say `nothing to draft yet`, why (from what next_step holds) and the move (a folder that holds
documents, or files dropped in it or the inbox, then sync), then session done, under rule 4's stage (C2424, lane B).
(164) A unit-removed tombstone is a change that names its successor, wherever it is read (lane B; C2507 with C2444,
C2445, C2409, C2510). Manifest.run_changes derives unit_removed by a LEFT JOIN on tombstones (the same output_path
and deleted_run, reason unit-removed), with no schema change, so a run carried after a crash counts it m, not d
(C2507 with C2444, over a new column). The CHANGELOG prints the counted letter and the word,
`- M <path> (part no longer produced)`, MirrorChange.op staying D (C2445). The page keeps status: deleted and
deleted_at:; curate, skill.py and MIRROR_CLAUDE_MD read reason: and superseded_by:, a new SOURCE-SUPERSEDED verdict
naming the replacement, outside CHECKPOINT_VERDICTS as SOURCE-DELETED is (C2409, makes worse True for a superseded
status). _tombstone_again and repair derive superseded_by from the item's live outputs through superseded_page,
re-rendered when those move, and repair records its page and rendered digests (C2510, makes worse True for storing
the successor).
(165) A recording that waits is told, counted and rolled back as waiting, and a withheld file is read by no stage,
completing ruling 155 (lane B; data integrity for C2509; C2509, C2508 with C2443, C2511, C2407, C2413). _sync_source
drops held ids from an unforced work queue, and _recording_candidates passes self._withheld's ids as skip and drops
them from its queue, so no poll reads an inbox recording inside its quiescence window (C2509).
_replan_recording_stubs keeps each row's page bytes and `.files` folder before its savepoint and puts them back on
rollback, best effort and never hiding the first error, undoing acc's deferred, settle and queue entries;
replan_waiting_recording returns early only when the page says the wait and its page_sha256 matches the file (C2508
with C2443). Clearing the OCR-down mark re-plans the stub with this run's reason in the same savepoint (C2511). A
page made with no byte read is a re-screen only when its id is in the persisted re-screen queue (C2407). A waiting
stub reads `# [WAITING]` with a `waiting:` summary, status refused kept, STUB_RENDER moving once (C2413).
(166) The zero-byte re-read is counted, rolls back one row at a time, and reads each row once whatever its scope,
amending ruling 155 (lane E; C2422, C2442, C2512, C2470). _zero_byte_reread adds one to _reread_n before
_after_fetch, so the sync line's `read again`, the run tally and install.sh's note count it, never converted
(C2422). _reread_zero_byte_stubs takes _replan_recording_stubs' per-row savepoint shape and re-raises, its token
left unset (C2442). Each probed row gets a `zero-bytes-v1:<source>:<stable_id>` marker, the source token is set only
when no qualifying row is left unmarked, out-of-scope rows included, and the row markers then go (C2512, moved from
lane B). Ruling 155's amendment of ruling 132 extends once to a one-shot `zero-bytes-v2:<source>` pass for zero-byte
`.drawio` stubs whose reason starts with conversion failed but not converter unavailable, v1's set and token
unchanged (C2470). (2026-11-22) The lead declines call 5 of packet 767198a7eec3: C2470 is not built, so this sentence's
v2 pass does not exist. The reason is lane E's own measurement (e-z1, r43-E.md), not a verifier's: only builds from
f94ee8693 to 18ba64ffa stored a `conversion failed` stub for a zero-byte `.drawio`, and the field build 4a42af93 has
no diagram converter. The plan's round 43 entry and §16.273's C2470 sentence point here (round 44 C2673).
(167) A record stays one record from its writer to its last reader, and a page line is flattened before it is
escaped, completing ruling 152 (lane E; data integrity for C2519; C2519 with C2530, C2452, C2531, C2528 with C2518).
pandoc spaces out every str.splitlines separator but LF in each chosen record, the exit branch's first cause and
each own record before the crash join, so _plain sees one line; whole=True and ocr._plain stay (C2528, over C2518's
split of _plain). The xlsx sidecars keep a comment author, chart title and pivot name raw through _record_escape,
the presence test and the page using the stripped value, and a page value, a pivot location and _label flatten
whitespace before _escape_line (C2519 with C2530, C2452). _code flattens CR and CRLF too, and a formula holding a
line break is listed in `.formulas.txt` even on a shown row, with a note of its own when no row cap is set (C2531).
CONTRACTS :43765 (with 13056 and 43237) and the C2300 and C2301 lines move in lane E's commit.
(168) A capped page shows the largest cut that fits on both xlsx paths, and a cut fence always closes, completing
ruling 153 (lane E; data integrity for C2516; C2516 with C2529, C2476, C2450, C2451). _cut_row searches the segments
between breakpoints (1 and each cell length over plain, formula and values) from the top, binary-searching inside
the first whose start fits, and _table_lines' floor is the cheapest breakpoint (C2516 with C2529). The full path's
_sheet_body budgets the real head, the widest row-cap note, its own uncached note and the guard and sidecar footers,
dropping _SHEET_RESERVE and keeping the lists' min(tail, cap // 4) (C2476, reversing round 42's flat 4,096).
_short_fence's info string is what follows the whole run (C2450), and its content stops at the block's own closing
line (C2451). xlsx.py's _EMITTER_VERSION moves once, to 1.0.5, for rulings 167 and 168 together, each other
_cap_body caller whose bytes change one step, and no _REREAD_BELOW moves.
(169) A [convert] edit's tests fail without their fix, every reader parses the options meta as the cycle writes it,
and every reader names the recording exception, completing ruling 154 (lane B; lanes RA and D for the words; C2446,
C2447, C2448, C2504 with C2522, C2521, C2459, C2461). The C2270 test patches _build_digest in the bump run and
proves _drift_targets ran (C2446); _drift_since refills an empty or other-run edit-hash record in a later non-dry
cycle with OCR up and the same fingerprint, the test failing OCR in the edit's run (C2447); the C2292 test's last
step puts pandoc back at the broken path and reruns that config, its options meta unchanged (C2448).
setup_report._convert_changed_run (lane RA) and cycle._drift_since (lane B) take only two fields and ASCII digits, a
bad value read as unrecorded (C2504 with C2522). The report's changed-in-run clause names meeting recordings'
exception, its guide row and pins moving in lane RA's commit (C2521); lane D's words show every number option's
value (C2459) and the options note only while a converter that can run has such files left (C2461).
(170) The setup prompt reads only lines setup prints in every branch, so it goes to v40 in lane D's one commit, as
ruling 156 took it to v39 (lane D, after lane RB's printed lines land; C2406, C2408, C2419 with C2524, C2420, C2523,
C2535, C2536, C2538, C2541, C2463's prompt half). Step 2's other-kind sentence says "no drop folder" only when no
other kind = "inbox" source in that config is neither paused nor retired, naming each that is as a folder the person
added (C2406), and the Finish leaves out kind = "inbox" sources only (C2408). The Finish reads the config the
report's Configuration `config:` line names, with no status pointer (C2419 with C2524), and step 2 reads "its
config: lines (each names the same file)", the header kept (C2420, makes worse True for relabelling it). Lane RB's
config_failed prints `docs repo:` and `inbox:` whenever the config loads (C2463, makes worse True for saying nothing
of an inbox init made), and step 2 says nothing of the inbox when it printed no inbox: line (C2536). A no-folder
NEXT from any sync of step 3 or after any step 1 branch goes to one general recovery that never re-asks a person who
answered none (C2523, C2538); a list ending on an Allow click or a denial is relayed and listed again first (C2535);
the final message names the bring-back.md this run's NEXT: line names (C2541). The same commit moves the version
line and digest, `prompt v40` in --log-start, SETUP_PROMPT_COMPAT, setup_report.PROMPT_VERSION and its docstring
list, the issue form, setup-feedback.md's version lines and the old-copy refusal list (v39 joins), a K64 row, every
pin and §16.274's dated line; CUT_OFF_TOLD stays. Round 44 rehearses v40 with v39's block (1433dd16d) as scenario
C's first old copy.

ROUND 44 RULINGS (lead; still binding, as extended below):
(171) Every spelling a printer can write of a configured pandoc_path is cut in both reports, completing ruling 157
(privacy; data integrity: lanes RA and RB, one joint run; C2716 with C2713, C2697 and C2641, C2698, C2671).
_printf_q_forms keeps its ASCII word and, for any absolute path with no C0 control, DEL or newline, also returns bash's
UTF-8-locale word (each _PRINTF_Q_ESCAPED character backslashed, every non-ASCII character raw, NBSP included, never
gated on str.isprintable()) with its `~` form, whether or not bash 3.2 would write it; its docstring and
_PRINTF_Q_ESCAPED's say the `$'...'` rule is the C locale's, and _raw_pandoc_path strips before it expands (lane RA).
install.sh's agentsync_dir_values reads [convert] pandoc_path, and the [agentsync] paths it also serves, in every TOML
spelling (a quoted table header or key, a dotted key, a one-line inline table, triple-quoted strings, a multi-line value
read to its close or recorded unmeasured), keeps the raw value for the echo pairs and trims as str.strip() does before
path_norm; a flag-c pair matches a TAB or a space at each blank, with no raw TAB in any pair, and debug_path is
registered as both a UTF-8 run and a C run write it (lane RB). Rows in both locales: a non-ASCII leaf with an NBSP, a
`Zephyr Quarry` leaf whose blank is a TAB (a basic-string escape and a literal TAB), and each TOML spelling, the two
reports matching. Lane RA builds the Python halves and rows of rulings 171 to 173 first and sends lane RB its branch
sha; lane RB lands both halves in one scripts/ship-land.sh run, so trunk never carries one half.
(172) Every place a private value comes from is read by both reports, under one skip rule and to one verdict, completing
ruling 159 (privacy; data integrity: lanes RA and RB in ruling 171's run; C2668, C2669, C2637, C2609, C2670). The shell
lists ~/Library/CloudStorage as Python does: each provider under with_timeout, 3 seconds in all, `find -mindepth 1
-maxdepth 2` with dot names pruned, no -L, NUL-separated, numbered in Python's (provider, depth 2, depth 3) order under
LC_ALL=C up to 2,000 names, through listed_folder_names' keep and skip, and an unfinished listing with no printed list
makes it Private only (C2669). Python registers, after every value it numbers now, each part below the provider of every
install.log --source-local word under CloudStorage, and install.sh's late mode skips what it skips:
report_cuts.GENERIC_FOLDERS and the agent words (C2668, makes worse True). Both register the typed `<parent>/inbox`
whenever the typed inbox lies outside the home, wherever it resolves, ruling 157's guards kept (C2637). A sources.toml
that exists and does not load is read unvalidated, its ids (SOURCE_ID_RE, not _GENERIC_IDS) and paths sent through the
loaded case's own registration with its `own` exclusions, and an unparseable file makes the report private-only (C2609,
lane RA). A two-letter id or folder that lands on a `<library-N>` takes Python's number and keeps the shell report
private (hidden_by's library flag and lib_short; C2670, lane RB).
(173) A quote that opens anywhere before a path head ends that head's value at the nearest same-kind quote after it,
amending ruling 158 (privacy; data integrity: lane RA, its shell copy in ruling 171's run; C2666). For each one-word
head (`--source-local`, `add-source`, `--cwd`, blank or `=` form), when a single or double quote stands earlier on the
line, the value runs to the next same-kind quote after the head (_closing_quote's rule, so a backslash-escaped double
quote never ends it) or to the line's end, split at a later head after a blank as _LATER_HEAD_RE does, each piece cut by
_head_value_cut with its guards; a %q value holding a backslash-escaped blank keeps the one-word read, and today's
head-right-after-the-quote path stays the special case. Lane RB regenerates install.sh's copy from
report_cuts.shell_block byte for byte. Rows: both finding rows, `Dana's run: '...--cwd=~/<outside>/<shown>' first`, a
double-quoted whole command, and a prose row with an apostrophe and no path that stays whole; the unquoted prose form
stays a residual that CONTRACTS names by a dated line.
(174) C2060 is still gated on the operator, so C2667 (round 43's C2469, round 42's C2263) is built only on "Allow it"
and nothing else writes its change (privacy; data integrity: lane RB, GATED; C2667). Rulings 150 and 161 stand. Lane RB
reads `cc-decide list --open` for 1476293d00a3, and any packet the lead files to replace it, at start and before its
last land. On "Allow it" it rebases round 41's text,
~/.cache/agentsync-bring-back/b13/round41/scratch-RB/refused-1-full.txt, onto trunk itself, since round 42's rebase no
longer applies: the decode gated on `[ "$only" = listed ]`, its pairs on `"$m"`, the block after the short_pair and
add_forms library branch beside hidden_by, the `dec` local and the newline-to-0x1E twin pair kept; it drops the strict
xfail, records §16.261's C2060 paragraph and §16.271's line as built by dated lines, and drops lane D's C2259 caveat
with its pin, in one commit the joint run never waits for; a second refusal stops it, and it messages the lead.
Otherwise round 41's strict xfail stays and §16.276 says C2667 stays unbuilt. No lane writes an equivalent install.sh
change another way, and a refused command is never re-run or reworded.
(175) A zero-byte re-read row that fails puts back its page, its books and its counters with its manifest rows,
completing ruling 166 (data integrity: lane E; C2703 with C2650). Before each row's savepoint, _reread_zero_byte_stubs
takes `_page_files(src.id, row.stable_id)`, the books (the row in acc.deferred_ids, acc.counted_as's entry, the
recording queue's length) and `(_reread_n, _reread_s)`; in its except branch, before `del self.changes[mark:]`, it calls
lane B's _put_back as it stands with them and restores both counters, the token left unset, so the next sync re-reads
the row as now. Lane B's replan is not edited this round. The C2442 test asserts, right after the failing sync, that the
failing row's page in HEAD and on disk has its manifest page_sha256 and still says not-ooxml, and that read_again is 1,
its "one sync ahead" sentence dropped; both docstrings name _put_back's one residual (a page write_pages wrote with no
change recorded yet stays on disk).
(176) Every text sidecar opens with the banner line and an empty line, and a reader drops only those, amending §16.274's
C2415 (data integrity: lane E; C2723 with C2712). registry._with_sidecar_digests prepends the banner line and an empty
line to every `.txt` and `.md` sidecar, the `not raw.startswith(banner)` skip gone. The reader rule, a dated line under
C2415 moved in lane E's commit with the pin at tests/test_deploy_pack.py:4145-4150, reads: drop line 1 when it is the
banner, then line 2 when it is empty; that gives back every sidecar written from now on byte for byte, a source opening
with the banner or an empty line included. No BANNER_VERSION bump: it moves every banner-wrapped conversion's action key
while re-reading nothing (C2270), so it would refresh no sidecar on disk; the contract says a sidecar written under the
skip is rewritten when its source next changes. Tests: a source whose line 1 is the banner and line 2 content, one with
the banner then an empty line, and a capped markdown source opening with the banner then `# heading`, each read back
exactly.
(177) install.sh and the CLI read AGENTSYNC_CONFIG by one rule, and its refusal says whose step the fix is (lanes RB and
B, lane D's sentence under ruling 187; turn C2728 with C2610 and C2606, C2699 with C2729). paths.py strips a named
CONFIG_BLANKS, the six ASCII blanks (space, tab, LF, CR, VT, FF), and install.sh trims that literal set, never
`[[:space:]]`, so no locale and no Unicode table decides which file step 2 and a bare sync read; the comments at
install.sh :200 and :1743 say so (lane B lands the constant first). The relative and ~user refusal stays before PARSED=1
and writes no file, and prints one NEXT: line on stdout before its usage error (none under JSON_RUN=1): setting
AGENTSYNC_CONFIG to the absolute path, or unsetting it, where the person starts their shell is the person's, not a step
for the agent, so the agent shows the error and goes to the report, and the person pastes the prompt again in a new
session; never an export or an env prefix for the agent (C2728, makes worse True; C2610). Tests: rel.toml and a ~user
value exit 2 with exactly one NEXT: line holding "not a step for the agent" and nothing under HOME; a parity test feeds
space, tab, VT, FF, 0x1C, 0x1F, U+0085, U+00A0 and U+3000, leading and trailing, under LC_ALL=C and en_US.UTF-8, and
asserts install.sh's step 2 path equals paths.default_config_path().
(178) A none is recorded by install.sh, never inferred from an exit code, and the report scores it as the person's
choice (lanes RB, RA and D; C2658 with C2605, C2604). install.sh takes `--no-folder`, refused beside --source-local,
which does a bare step 2's work, logs the flag in its start line's args, exits 0 and ends on a NEXT that names the
person's choice and never says "no folder is synced yet" or "no folder to sync yet", so the prompt's folder rule never
fires on it (lane RB; its words to lane D first). compute_outcome reads install.log, never the agent's friction lines: a
last install run carrying --no-folder is `stopped` at step 1, why "the person chose no folder"; C2605's no-folder first
run (config `created`, first-sync skipped `no-sources`, `end rc=2`, no failed step) followed by a logged step 1
deviation is `stopped` at step 1, why "no folder answer came"; a bare exit-2 run with neither, and a usage error's rc 2,
stay failed, each a test (lane RA). Lane D's v41 runs `install.sh --no-folder` after a none to the which-folders
question (the exceptions' add-a-folder none and an unanswered question do not count), on a fresh none in step 1's last
paragraph too, then tells the person this Mac syncs no folder and goes to step 3's report (C2604); setup-feedback.md
section 4 and §16.279 say how a none is scored.
(179) Every reader of step 2's lines says what install.sh prints on that run, completing ruling 163 (lanes RA, RB and D;
C2601 with C2638, C2700, C2701, C2643, C2661, C2660, C2663). setup_report._loop_line replaces a NEXT that starts with
the skill-missing words by install.sh's no-folder NEXT only when install.sh would: the config loads with no source whose
kind is not inbox, in any state (install.sh's HAVE_SOURCES count), and the latest install run ended rc 0 with no doctor
FAIL and no sign-in need; the words come from one constant that a test pins install.sh's literal to, and a doctor-FAIL
control and a live-folder control keep their NEXT (C2601 with C2638, lane RA, the ruling-163 paragraph's dated line in
its commit). install.sh keeps its state-blind count, pinned by a bare re-run over a live inbox beside one paused and one
retired local source (C2700); a refused or failed config step keeps `inbox=not-added` on its closing line, a failed init
or add-source that saw the warning closing its own step before config_failed (C2701); the header says a refused step 2
prints `docs repo:`, `inbox:` and `inbox state:` with no folders line (C2643, a comment; lane RB). The guides say the
same: docs/deploy/README.md names the C2401 exception (C2661), K64, §16.274 and the test docstring say a run with no
--source-local prints two config: lines (C2660), and setup-feedback.md:14's install.log row names `inbox=not-added`,
read by no report (C2663, makes worse True; lane D).
(180) The inbox's state is read from its one helper before anything that claims it, and an unread state is said as
unread, completing ruling 162 (lanes B, RA and D; C2644 with C2636, C2611, C2718 with C2731 and C2657, C2608).
config.inbox_state guards its one read of the entry and returns `not read (<why>)` on an OSError, the words carrying no
path and never `is not a folder`, so status keeps `inbox: <folder>`, prints `inbox state: not read (Permission denied)`
and every other line, and the report's `- inbox:` line and install.sh's -I snippet print the same answer (C2644 with
C2636, makes worse True; a link into a mode-000 folder in tests/test_cli.py and in lane RA's report test, skipped as
root). _ensure_inbox's line reads `inbox: not added: <typed> is not a folder; move it aside and run step 2 again` for a
file, a link to one, a dangling link or a fifo, install.sh's `^inbox: not added` grep unchanged (C2611, lane B; its
words to lane D first). Lane D's v41 step 2 reads the `inbox state:` line before sources.toml: none printed, a live
inbox source; paused or retired, today's words; `not added (<folder> is not a folder)`, the inbox could not be added
whatever sources.toml lists, shown with step 2's `inbox: not added:` line, moving it aside the person's and nothing said
of dropping files there; another kind's source or no source, today's words (C2718 with C2731 and C2657, README:61 naming
the case); and in each case but live, other kind = "inbox" sources neither paused nor retired are named as folders the
person added, else "no drop folder" (C2608, makes worse True).
(181) Rule 4's `nothing to draft yet` says whose move it is and names an inbox only when one is read, and rule 5 points
only at lines status prints, completing ruling 163 (lanes B, RA and D; C2630 with C2603, C2602 with C2649 and C2719,
C2707, C2631, C2659). _nothing_to_draft keeps its opening `nothing to draft yet: <why>` (NOTHING_TO_DRAFT unchanged, so
every reader that matches it still does) and words the move as the operator's: `adding a folder that holds documents
(~/.local/bin/agentsync add-source "<folder>") or putting files in a synced folder or the inbox (<inbox>) is the
operator's choice, not a step for the agent: show them this line; session done`. The inbox clause names the folder
inbox_state answers only when its words start with INBOX_LIVE, else a live kind = "inbox" source elsewhere as
publish.root_guide names it, through one helper both use, else no clause, and an OSError drops it; the online-only
branch offers no folder move (`<N> online-only file(s) wait for a later sync to download them; nothing for the agent to
do now; session done`). One _draft_suppressed helper serves docs_waits and next_step, rule 4 takes an empty draft over
an empty mirror whether or not topic pages exist, and rule 5 runs only once docs_waits returned its line (C2707). Lane B
moves the C2424 paragraph's dated line in its commit and sends lanes RA and D the words first. The report's Loop line
shows the kept default inbox as `~/agent-context/inbox`, kept whole, and any other as `the inbox folder beside the docs
repo`, never `(inbox)` and never a non-default path uncut (C2631, lane RA). Lane D's v41 step 3 says that on this NEXT
the agent shows it, chooses no folder, moves or makes no file, runs no add-source, asks whether to add a folder from
step 1's list and runs step 2 again with one --source-local per folder named, or on none or no way to ask logs nothing
and goes on to the report syncs (C2659).
(2026-11-23) Step 3's question whether to add a folder stands after step 1's exceptions' none, since `nothing to draft
yet` is a fact that question lacked, and v42 asks it once a session (round 45 C2831, ruling 197).
(182) A tombstone's successor, unit-removed flag and title follow what they derive from, completing ruling 164 (lane B;
C2646 with C2706, C2704 with C2648, C2613, C2672). Publisher.tombstone adds `self._successors_again(rows)` to its
changes after its loop and before the `if changes:` guard, so a kept unit-removed whole tombstone whose item is deleted
upstream, moved out of scope or retired drops its successor in place, one M change, its deleted_at, reap_after and
last_commit kept (C2646 with C2706). The unit-removed body names a live successor, `This page was replaced by
<superseded_by> on <deleted_at>: ...`, its "source still exists" clause printed only while the item is not a tombstone,
derived alike in tombstone(), _tombstone_again and _repair_outputs (C2613, makes worse True: status: deleted,
deleted_at:, reason:, superseded_by: and the `# [NO LONGER PRODUCED]` heading kept). run_changes gains a nullable
`unit_removed INTEGER` column, added on open by the manifest's additive path with no schema version bump, written by
record_run_changes and read when not NULL, the same-run join kept, never loosened, for an older build's rows; a
unit-removed change carried through two crashed runs still reads m, and the deleted-upstream control d (C2704 with
C2648, amending ruling 164's "no schema change" and round 43's call 1). _repair_outputs titles a repaired tombstone from
the committed tombstone's own source_title (HEAD, then last_commit or _content_commit's commit), else item.name, the
redacted path first whenever the item is hidden and never a git read for it (C2672, reversing round 43's call 16; the
not-built reason gets a dated line).
(183) A file a person names is read whatever its settle hold, and every waiting recording stub is rendered again once,
completing ruling 165 (lane B; C2645 with C2705, C2647, C2651). _recording_candidates applies self._withheld's ids only
when no paths are forced, as the source queue does, so `materialise PATH` reads a settling inbox recording it names, the
comment and §16.272's C2509 paragraph (a dated line) saying so; a test beside the C2509 one, over a recording and a
regular file and over a settled stub and a pending row, runs materialise inside a 600-second window and asserts the
fetch, rec.worked and a state_reason no longer recording-waits (C2645 with C2705). _rerender_stubs also passes each LIVE
or DATALESS row whose state_reason is RECORDING_WAITS and whose page already says the wait through
replan_waiting_recording, which writes only when the page differs, with no STUB_RENDER bump and a public publisher
helper in place of _stub_says; a test with STUB_RENDER patched to 6 and the label to REFUSED shows `# [WAITING]` after
one sync and no commit after the next (C2647). replan_waiting_recording's docstring names each case it writes (C2651).
(2026-11-23) Ruling 195 amends this one: "with no STUB_RENDER bump" was false for a home that ran 7 before this loop
existed, which never rendered its waiting stubs again; publish.STUB_RENDER is 8, its 8 clause naming the waiting
recording stubs and every tombstone again (C2646, C2613), and _rerender_stubs writes the marker only once no stub was
left unrendered (round 45 C2851 with C2917, C2918; lane B).
(184) An xlsx cut is costed as it renders and a list-sidecar record parses one way, completing rulings 167 and 168 (lane
E; C2710 with C2652 and C2721, C2711 with C2694). _CODE_RUNS takes CR and CRLF as its last run (a CR with an optional
LF), the ASCII run no longer taking a CR, so kinds 1 to 5 keep their numbers, and _code_steps charges a CR or a CRLF one
byte and the LF nothing; one exactness property asserts at every length that _RowCuts' bytes are the rendered cut's line
bytes, over plain and formula texts holding CRLF, CR, LF, CR CR LF, pipes, backtick runs, blanks, tabs, backslashes and
2-, 3- and 4-byte characters, and probe 2's row shows within the header plus 179 bytes (C2710 with C2652 and C2721;
§16.273's dated line). A comment author escapes `)`, a pivot name and a chart title are quoted with their double quotes
escaped, the location is the last field after ` at `, and the decoder (the author to the first unescaped `)` before `:
`, a quoted field to its first unescaped double quote, then the backslash escapes undone) is stated by a dated line
under §16.268 and the C2301 amendment, with tests for both colliding pairs and a title holding a double quote, each read
back (C2711 with C2694). xlsx's _EMITTER_VERSION moves once, to 1.0.6, for both, and no _REREAD_BELOW moves.
(185) The Office type check and the .potx retype read the parsed content types, the byte read only a fallback (lane E;
C2708 with C2653, C2722 with C2709). _ooxml_family takes families only from content-types-namespace Override and Default
elements of content_types_tree, `len(families) == 1` kept, and uses the byte regex only when the part is over the 1 MiB
cap or does not parse; _CONTENT_TYPES_NS moves to _common.py and pptx imports it, and the docstrings say comments and
other elements never count (C2708 with C2653). _deck_types runs _decoded_deck_types on the original bytes with three
answers: parsed with hits, only Override and Default retyped; parsed without one, None; not parsed, today's byte and
UTF-16 replace (C2722 with C2709, makes worse True). Dated lines amend C781's and C2517's byte sentences (a normal
template's in-memory part is re-serialized by lxml; an over-cap or unparseable part is still retyped by a literal
replace), and pptx's _EMITTER_VERSION stays once plain, raw, escaped and decoy templates convert to the same pages
before and after in all three codecs. Tests: a decoy per codec (a potx or pptx with a Word literal in a comment, an xlsx
with a deck literal, a docx through pandoc with a workbook literal), an over-cap or unparseable part reaching the
fallback, and the Notes' and Override's spellings, literal and escaped, in UTF-8 and UTF-16 LE and BE.
(186) A change to another round's CONTRACTS or COMMON.md text is a dated line beside it, never a rewrite and never a
second copy, restoring ruling 74 (lanes D and E; C2662, C2654, C2682, C2673). Lane D puts round 42's three passages back
word for word from f9e96ccd1^ (§15 :1417, §16.263 :43243-43244, §16.268's C2220 paragraph), makes each 2026-11-21
§16.273 C2532 dated line carry the new words itself, adds a dated line under §16.273's C2532 paragraph saying the old
lines keep their words, and moves tests/test_convert_page_cap.py's c2220 assertions to the §15 C2532 line, config.py's
comment kept (C2662). Lane E gives each of §16.273's three "stays at" clauses a dated line: that change moved no
version, and C2450's step took pandoc to 1.0.3, recording to 1.1.3 and pptx to 1.0.4 (C2654). Lane D gives §16.271's
C2469 paragraph a dated line citing §16.266's C2060 paragraph as the record and naming what §16.271 adds (ruling 161 and
the C2259 caveat), the paragraph's words kept (C2682), and puts a dated line right after ruling 166 in
docs/lanes/COMMON.md recording the lead's decline of call 5 (C2470 not built, lane E's measured reason), its position
pinned in the C2069 style and §16.273's "by the lead's ruling" pointed at it by a dated line (C2673; the plan's half is
the lead's, C2674).
(187) The setup prompt reads only what setup prints and what this shell holds, so it goes to v41 in lane D's one commit,
as ruling 170 took it to v40 (lane D, after lanes B and RB land the lines it quotes; turn C2727 with C2656 and C2717,
C2718 with C2731 and C2657, C2608, C2604, C2659, C2619 with C2634 and C2664, C2606's sentence, C2658's flag). Step 3 and
the Finish name the config as the file step 2's `config:` line named if step 2 printed one, else AGENTSYNC_CONFIG as
`printenv` reads it before the report, resolved as install.sh --report-only resolves it, else
~/agent-context/sources.toml; never a report path holding `<path>`; exists, unknown and none come from the report's
`(exists)` or `(missing)` mark and its loads: line, `loads: not checked` and `loads: no:` first, and `(exists)` with
`loads: yes` never gives none (C2727 with C2656 and C2717; §16.49's and §16.274's dated lines, K64, and
test_deploy_pack.py:4052's source-text assert made a behavior test over a custom AGENTSYNC_CONFIG). Step 2 reads the
inbox by ruling 180, a none by ruling 178, and a step 2 that prints no NEXT: line as a step 2 error whose usage line it
shows before the report (C2606); step 3 takes ruling 181's sentence (C2659). Line 614 is rewrapped at the block's width
with no word changed and no line opening on the digit 2, and the width test measures each line with its code spans
removed (C2619 with C2634 and C2664, makes worse True). The same commit moves the version line and digest, `prompt v41`
in --log-start, SETUP_PROMPT_COMPAT, setup_report.PROMPT_VERSION, its docstring list and setup_report.py's two v40
mentions, the issue form, setup-feedback.md's version lines and the old-copy refusal list (v40 joins), a K65 row, every
pin and §16.279's dated line; CUT_OFF_TOLD stays. Round 45 rehearses v41 with v40's block (5cf1b5407) as scenario C's
first old copy.
(2026-11-23) Ruling 197 amends this one: v42's Finish gives none for a config marked (exists) with loads: yes only
when that file, read as above, holds no kind = "local" source that is neither paused nor retired, as after --no-folder,
never because a path could not be opened or a report path held `<path>`, and its AGENTSYNC_CONFIG rule reads a bare `~`
as the home and a `~name` value under the report's folder, as install.sh's case arms do (round 45 C2938, C2950, C2859,
C2804, C2821, C2871, C2949).

ROUND 45 RULINGS (lead; still binding, as extended below):
(188) Both reports reach one verdict on a sources.toml that does not load or is spelled unusually, and cut every value
it names, completing rulings 171 and 172 (privacy; data integrity: lanes RA and RB, one joint run; C2840 with C2915,
C2839, C2913, C2815, C2934). Lane RA: _build_redactor takes the docs repo's remote from the raw config (`cfg.docs_repo`)
whether or not the config loads, and registers the pandoc cut from _raw_pandoc_path's value, stripped then expanded,
when it does not (C2839); a sources.toml that does not parse has its [[source]] id lines (basic and literal strings)
line-scanned through SOURCE_ID_RE, _GENERIC_IDS and report_words() and registered as source values, so an id is cut
whole to `<source-N>` and no listing word is cut inside it, the private-only line kept (C2815, makes worse True). Lane
RB: fallback_redaction checks an existing $CONFIG with tomllib under with_timeout through an interpreter install.sh
already resolves (TOOL_PY, else a uv-held python3.11 or later), deciding from fixed exception names and never printing
stderr: TOMLDecodeError, UnicodeDecodeError or ValueError prints setup_report.py's private-only sentence word for word
and sets PRIVATE_ONLY=1, an OSError stays public, and only where no interpreter has tomllib does a structural check (a
`[` line that is not a whole header, an id or path line the awk readers do not read as a basic string) decide, never a
blanket private-only (C2840 with C2915); agentsync_dir_values' basic() joins a line-ending backslash followed by blanks,
and its inline-table skip consumes quoted values whole (C2913); non_inbox_sources counts with TOOL_PY's tomllib,
state-blind, the awk kept as the fallback (C2934). Rows in both locales: an unterminated path beside an `id = = ` line,
a `[[source]` header and a garbage line (one private-only line, no issue link in either report), a well-formed config
with no Python keeping its link, the multiline and inline-table spellings, `[[ "source" ]]` and `[['source']]` counted
as the report decides, and C2839's config with and without an unknown key printing alike. Lane RA builds its halves and
rows first and sends lane RB its branch sha; lane RB lands both halves in one scripts/ship-land.sh run with ruling
189's, and records the shell half under ruling 172's C2609 by a dated line.
(189) A word ends where bash ends it and a listed name is read whole, so both reports number and cut it alike (privacy;
data integrity: lanes RA and RB in ruling 188's run; C2939 with C2912, C2877, C2880 with C2914).
_LOGGED_SOURCE_LOCAL_RE's bare word ends only at a space, tab or newline (`(?:\\.|[^ \t\n\\])+`), never at a Unicode
space and never through shlex, and _logged_cloud_folders numbers in install.sh's order: the start lines' args sorted by
code point, their bare words first, then the `$'..'` words deduplicated and sorted by their sed form (lane RA; the C2668
paragraph and setup-feedback.md:102 say the word ends at a space or tab, as bash splits it). listed_folder_lines is
awk-only, the CloudStorage root passed through ENVIRON, fallback_redaction reads listed_folder_names through `$()` and a
here-string, and listed_folder_names prints its rows once after its loop (C2877); agentsync_dir_values takes an opt-in
escaped output for the pandoc caller alone (`\\`, `\t`, `\n`), decoded once by awk or parameter expansion, never printf
%b, the true value registered by debug_path under both locales and the folded copy for every plain and quoted spelling,
its seven other callers unchanged (C2880 with C2914; lane RB). Rows: a leaf with an NBSP and a deeper part, two runs
whose log order and sorted order differ, `Zephyr\tQuarry Ledger` and `Zephyr Quarry\tLedger` as literal and basic
strings, each with shell == python under C and en_US.UTF-8, and 3000 listed rows read whole under induced SIGCHLD.
(190) C2060 is still gated on the operator, now under class C decision c0777e12a523, and nothing else writes its change
(privacy; data integrity: lane RA builds C2873 with C2882 ungated; lane RB, GATED, C2889 and the port). Rulings 150, 161
and 174 stand. C2873 is Python only, not the refused edit, so lane RA builds it ungated (lead's call 1): setup_report.py
decodes only rows that opened with an escaped mark, `\ooo`, `\t`, `\r`, and `\n` read as both LF and 0x1E, registering
the decoded name as the folder's own value and the escaped literal as its alias under one number, the old `?` form left;
the C2667 test splits so the agentsync report is asserted unconditionally and only the shell half keeps the strict
xfail, whose reason reads `C2060 unbuilt: install.sh's decode of a listed part awaits operator decision c0777e12a523` on
one line, test_deploy_pack.py:14609's docstring moving in that commit and lane D adding a dated line beside COMMON.md
:1742-1743 (C2882). Lane RB reads `cc-decide list --open` for c0777e12a523 at start and before its last land, takes an
"Allow" on it as round 44's "Allow it", reads any answer on 1476293d00a3 through its superseded recheck (C2889), and on
"Allow" builds the port at C2667's three porting points (round44/items/C2667.md) on top of lane RA's Python half,
dropping the shell half's strict xfail; a second refusal stops it, and it messages the lead. Otherwise the shell half's
strict xfail stays and §16.281 says so. No lane writes an equivalent install.sh change another way, and a refused
command is never re-run or reworded; the lead adds C2873's dated line to c0777e12a523's receipt.
(191) install.sh honors the person's none at every NEXT that would ask for folders again, and never over a failed step,
completing ruling 178 (lane RB; turn C2910 with C2945, C2931, C2838 and C2860, C2909, C2849, C2844). After loop_next,
with EXIT_RC 0, install.sh keeps C2401's skill-missing swap under HAVE_SOURCES=0 (the person's-choice NEXT with
--no-folder, else the bare `no folder is synced yet: list them ...`), then, keyed on NO_FOLDER=1 alone whatever
HAVE_SOURCES counts, turns a NEXT opening `no folder is synced yet` or `no folder to sync yet` into the person's-choice
NEXT; a bare run keeps status's rule-2 NEXT (C2316, C2700), and [FAIL], held-listing, chmod, sign-in and every other
NEXT pass through; the comment and the header near :416 say so. The --no-folder created-config branch and the bare fresh
branch move behind the DOCTOR_TIMEOUT and DOCTOR_BLOCKS branches, so a status timeout or [FAIL] exits 1 on its own NEXT,
whose re-run keeps --no-folder (C2909). The shell variable becomes NO_FOLDER_CHOSEN_NEXT, the name of the report's
constant (C2849). Tests: the rule-2 stub at STUB_STATUS_RC 0 and 2, a real_env --no-folder, sync, --no-folder, and a
paused- and retired-only config, each exit 0 on the person's-choice NEXT with none of NONE_CHOSEN_NOT; a bare re-run
still ending on `no folder is synced yet`; STUB_INIT_RC=78, a [FAIL] and a status timeout under --no-folder exiting 1; a
real_env fresh bare run still exiting 2 on `no folder to sync yet`; each refusal row pinned to its exact usage error,
the --list-folders row named as the generic guard (C2844). §16.276's wording is widened by a dated line in lane RB's
commit; lane RB sends lane RA its literals and order and lane D its words first.
(192) The report reads, scores and words a none as install.sh made it, completing rulings 178 and 179 (lanes RA and D;
C2946 with C2801, C2836 and C2932, C2802, C2947 with C2837 and C2861, C2933 with C2911 and C2846, C2842, C2803, C2848
with C2863, C2865). InstallRun.chose_no_folder splits args with shlex, skips the operand of --source-local, --cwd,
--launcher, --timeout and -n/--limit, and is true when a bare `--no-folder` word remains; on ValueError it scans
%q-aware (`\x` one character, `'..'` and `$'..'` spans skipped, only top-level words tested); _NO_FOLDER_ARG_RE goes
(C2933 with C2911 and C2846). compute_outcome scores such a run stopped at step 1 (the person chose no folder) only when
it ended rc 0 with no failed step and no blocking status note (fail-lines, status-error, timeout); any other falls
through to its failure, signal or no-end scoring (C2947 with C2837 and C2861, and C2909's report half).
_install_sh_said_no_folder returns words or None from install_runs_only's latest run (C2842): NO_FOLDER_CHOSEN_NEXT when
that run chose none, on the skill-missing or either no-folder NEXT as ruling 191 swaps them, NO_FOLDER_NEXT for a bare
run on the skill-missing NEXT, NO_FOLDER_FIRST_NEXT for C2605's no-folder first run, each only with no doctor FAIL and
no non-inbox source in any state, and each one constant a test holds install.sh's literal to by content (C2946 with
C2801, C2836 and C2932; C2802); a both-reports test compares one real `install.sh --no-folder` run's NEXT with the Loop
line. form_label gives `Stopped at step 1 (preflight)` for every stopped outcome with a step, FORM_OUTCOME_OPTIONS and
the issue form's option moving in lane RA's commit, G23 and H42 by dated lines (C2803). Lane D writes from lane RA's
final words: setup-feedback.md's Loop-line paragraph names three report changes (C2848 with C2863), section 4 and both
ruling-178 passages say "ended rc 0 with no failed step" and "holds --no-folder as a shell word of args=", the field
table the new option, and docs/deploy/README.md a new sentence after its exit-2 sentence for --no-folder (C2865, makes
worse True: the list the ruling-162 test pins is untouched).
(193) A non-absolute AGENTSYNC_CONFIG is refused or resolved by one rule wherever it is read, and shown as a person can
copy it, completing ruling 177 (lanes B and RB, lane D's words under ruling 197; C2822, C2816, C2944, C2824).
paths.expand, and so default_config_path, refuses a value that starts with `~` and is neither `~` nor `~/...` with a
typed error the CLI maps to exit 2, worded as install.sh's refusal and naming --config when the flag gave it, with no
traceback and no "This is a bug" (C2822, lane B). install.sh --list-folders appends to every NEXT branch, when CONFIG is
still not absolute after the ~ expansion, the step-2-stop tail in C2728's words for the relative and the ~user forms,
its path built with shown_arg, `(none is needed)` dropped beside it (C2816); --report-only keeps reading such a value
under the folder it runs in, so the report still runs (rows: `~contoso/sources.toml` read there and marked (missing), a
bare `~` read as the home). shown_arg re-quotes a `$'..'` form under LC_ALL=C exported in a subshell, the double-quote
and `!` branches kept (C2944; its U+3000 and U+0085 parity row in tests/test_cli.py rides lane RB's commit). The shell
fallback report computes `failed at step N (computed: no install run; step N logged an error)` from the latest attempt's
last unresolved error when no install run ran, as compute_outcome's no-install-run branch does, its link filling `Failed
at step N (<step name>)`, and the refusal still writes nothing (C2824, makes worse True; one test runs both reports on
the same files).
(194) Every reader tells the inbox's state as config.inbox_state reads it, and a read that did not finish says so,
completing ruling 180 (data integrity: lanes B, RB, RA and D; C2807 with C2937, C2936, C2808, C2809, C2814, C2948 with
C2868 and C2935, C2810, C2869). config.live_inbox decides once whether the kept entry is a folder (lexists and not
is_dir, an OSError counted as not) and, when it is not, drops every candidate same_folder with it from both branches, so
AGENTS.md and rule 4 never name a file, a dangling link or a link's target there, a link to a real folder still named as
kept (C2807 with C2937). inbox_state reads the entry with os.lstat and os.stat: ENOENT or ENOTDIR on the entry is
absent, a non-link non-folder or a link whose target is missing, not a folder or loops is `not added (<folder> is not a
folder)`, any other OSError `not read (<strerror>)` (C2936). Doctor shows the source on a not-a-folder kept inbox as
typed, with one fix opening on a word PERSONS_FIX_RE reads as the person's (`restore <inbox> as a folder: ... move the
file or link at that name aside, then run step 2 again`; C2809, makes worse True); the listable EACCES branch uses
_parent_fix(root), the volume row prints `volume UUID not checked: ...` with no raw errno, status drops the loop's
"cannot read inbox" warning when those lines say it, and _ensure_inbox prints `inbox: not read: <typed>: Permission
denied (inbox state says what to do)` (C2808); LocalArm records a not-a-folder kind for its root and a sync prints a
note, never a wait, naming the typed path with step 2's remedy (C2814; lane B). install.sh's config_folder_lines prints
`inbox state: not checked (<why>)` whenever its inbox line printed and no state came back (the exception's type name,
`it did not finish within 10 s` on rc 143 even after a third record, `no state was printed`), the line absent only for a
read live state (C2948 with C2868 and C2935, makes worse True for an explicit live line); inbox_not_added() also matches
`^inbox: not read` and logs `inbox=not-read`; the shell report prints `- inbox: not checked ($why)` after its loads
line, never a guess from the disk (C2810, makes worse True; lane RB). README:61 and docs/deploy/README.md say "a file, a
link to a file or a dangling link" and name both unread forms in new sentences after the pinned list (C2869, C2865; lane
D).
(195) Every home renders its stubs again once for rulings 182's and 183's fixes, and the marker moves only after every
page rendered, amending ruling 183 (lane B; C2851 with C2917, C2918). publish.STUB_RENDER goes to 8, its docstring's 8
clause naming the waiting recording stub and every tombstone (C2646, C2613) and its 7 clause losing "with no bump"
(C2851 with C2917); ruling 183's "no STUB_RENDER bump" takes dated lines (CONTRACTS :45541 in lane B's commit,
docs/lanes/COMMON.md beside ruling 183 by lane D). _rerender_stubs calls says_recording_wait inside the try that holds
replan_waiting_recording, logs and marks pending on PublishError or OSError and goes on, and writes the meta only when
nothing is pending, the REFUSED and QUARANTINED loop taking the same flag (C2918, makes worse True). Tests: a meta at 7
with the label REFUSED shows `# [WAITING]` and a re-rendered superseded tombstone after one sync and no commit after the
next; a waiting stub at chmod 0 logs the warning, leaves the meta at 7 and renders once it is readable.
(196) A kept answer is keyed on, and put back with, everything that decides it, completing rulings 175 and 185 (lane E;
C2923, C2924, C2925, C2929). _reread_zero_byte_stubs snapshots, beside `reread`, _reread_first, _reread_kept, acc's
per-result scalars, _tally's conversion keys and the row's membership in _converted_ok and _waiting_ids through a
_row_books and _restore_row_books pair, and restores them in its except branch, acc.alarms and the OCR tallies kept
(C2923); lane B's _replan_recording_stubs takes the pair in lane B's commit only if one twin test, a failing row through
both passes, shows it keeps a book the pass restores. convert_file checks an OOXML_SUFFIXES file's container after the
label check and before cache.get and returns screen_file's answer (empty-output, encrypted, not-ooxml) with no
cache.put, and cycle.py's `same` beside _relabelled gains the container verdict (C2924; both cache orders and both
touched directions). _identity adds `family_parse: 1` for OOXML_SUFFIXES, no _EMITTER_VERSION moving, and a one-time
meta-token re-read modeled on zero-bytes-v1 reads, under the new key, each Office row whose stored reason has the
family-mismatch form, never NOT_OOXML_REASON (C2925). with_banner keeps an existing banner only as the first line after
leading blank lines; BANNER_VERSION goes to 2 only if lane E measures that a sync after the bump commits nothing on a
home with guarded pages (ruling 176's C2270 claim), else the first-line rule lands alone and the note gives the measured
reason (C2929).
(197) The setup prompt reads a none, a config path and an unread inbox state as setup gives them, so it goes to v42 in
lane D's one commit, as ruling 187 took it to v41 (lane D, after lane RB lands the lines it quotes; C2938 with C2950,
C2859 and C2804, C2821 with C2871 and C2949, C2868's case, C2869, C2870, C2831). The Finish gives none for a config
marked (exists) with loads: yes only when the file named above, read as above, has no kind = "local" source that is
neither paused nor retired, as after --no-folder, and never because a path could not be opened or a report path held
`<path>` (then "unknown: <path> could not be read"), the two unknown cases first (C2938 with C2950, C2859 and C2804,
makes worse True for dropping the guard). Its AGENTSYNC_CONFIG rule reads as install.sh's case arms: the six blanks
trimmed, empty as ~/agent-context/sources.toml, exactly `~` or `~/...` under the home, any other value not starting with
/ (a `~name` one included) under the folder the report command ran in; the printenv step says it prints nothing and
exits 1 when unset, no failure and nothing logged, the command unchanged so C2862's allow rule holds (C2821 with C2871
and C2949, makes worse True for "/ or ~/" alone). Step 2 tells `inbox state: not checked (<why>)` as agentsync not
telling whether the inbox is live, shown with a pointer to status, nothing said of dropping files and left out of the
no-drop-folder sentence (C2868); its not-a-folder parenthetical reads "a file, a link to a file or a dangling link"
(C2869); the no-NEXT branch loses " (an export it names is mine)" (C2870). Step 3 asks whether to add a folder once a
session, the question standing after step 1's exceptions' none (C2831, makes worse True for an already-answered guard).
The same commit moves the version line and digest, `prompt v42` in --log-start, SETUP_PROMPT_COMPAT,
setup_report.PROMPT_VERSION, its docstring list and setup_report.py's v41 mentions, the issue form, setup-feedback.md's
version lines and the old-copy refusal list (v41 joins), a K66 row, INBOX_NOT_A_FOLDER, every pin, a behavior test
(--no-folder then --report-only gives none; a paused-only config too; the sentence pinned to install.sh's case arms),
§16.49's, §16.279's and §16.284's dated lines and one beside ruling 187; CUT_OFF_TOLD stays. Round 46 rehearses v42 with
v41's block (9acf0c54b) as scenario C's first old copy.
(2026-11-24) Ruling 205 amends this one: v43 tells the none only when the --no-folder run exits 0 on the person's-choice
NEXT, step 1's none branches running it as step 2 says and step 3's sync loop never started after it, and gives
`inbox state: not read (<why>)` a branch of its own after the no-drop-folder sentence, beside C2868's not-checked one,
with step 2's `inbox: not read:` line shown (round 46 C3102, C3112, C3113, C3013).

ROUND 46 RULINGS (lead; every lane builds to them; the prompt goes to v43 in lane D's one commit, ruling 205):
(198) A config value whose spelling a reader refuses is skipped per field and cut, and a redactor that could not be
built never yields a public report, completing ruling 193, whose `~name` refusal now raises where these callers expected
none (privacy; data integrity: lanes RA, RB and B; C3099 with C3033, C3037 and C3110, C3038, C3039). Lane RA:
_unloaded_config's folder() and [[source]] path read and C2839's pandoc branch each catch ConfigError around expand()
and go on (folder() its default, a source path None, pandoc None), never dropping the whole config; the stripped raw
value is registered whole with red.add_cut, its `~name` head as a user-like value and the folder parts after the head
through the normal folder cut, so the report stays public; build_report's except branch sets a new Redactor flag,
redactor_failed, that private_only reads, with its own line (`Private only: redaction facts could not be read, so names
may show in clear`), and registers login, full name, serial and host through a _register_identity helper _build_redactor
shares. Lane RB registers a `~name`-headed config value (a source path, pandoc_path, docs_repo, the state, cache and log
dirs) the same three ways in redaction_pairs, the home-only rule at :3786 kept for every other path. Lane B: config.py's
_expand_value turns the AccountHomeError of a value read from the file (_path, a source path, pandoc_path) into a plain
ConfigError naming the file, table and key, exit 78, the AccountHomeError arm for --config, AGENTSYNC_CONFIG and CLI
paths kept at exit 2 (C3039); skill.py's _config_dir catches AccountHomeError only, so skill_paths leaves a refused
CLAUDE_CONFIG_DIR copy out and write_skill logs one warning naming it and never raises (C3038). Rows: a `~contoso`
source path, pandoc_path and docs_repo, each beside a valid source, through both reports in both locales (no `contoso`,
`Zephyr` or `Quarry`; `<name>`, `<user>`, `<host>`, `<serial>` and `<org-1>` still cut; one verdict), a forced raise in
_build_redactor (private only, no issue link in the report or in cli's printed link), and a sync under that
CLAUDE_CONFIG_DIR ending ok. Lane RA may land the redactor_failed flag alone first; the cuts land with lane RB's half in
ruling 199's joint run.
(199) Both reports read sources.toml's sources, the docs repo and the inbox as tomllib and the agentsync report read
them, so neither prints a value the other cuts, completing rulings 188 and 189 (privacy; data integrity: lanes RB and RA
in one joint run with ruling 198's cuts; C3034 with C3083 and C3056, C3054, C3055, C3084, C3085, C3104, C3087 with
C3105). Lane RB: when config_toml_state's interpreter answers loaded, that interpreter (-I, with_timeout, stderr
discarded) prints each [[source]] table as index, id and path, string values only, a record holding a TAB or LF dropped,
and the registry loop at :4262 reads those lines in place of its awk, its filters and config-order numbering unchanged;
with no interpreter the awk reads `[[ "source" ]]` and `[[ 'source' ]]`, quoted keys and literal id and path values raw
(\047 only, no apostrophe in a comment), config_path_values gains the literal `path = '...'` rule, and
config_toml_state's structural check accepts exactly those spellings (its qsrc arm dropped only with the reader) and
calls a multi-line string or array still open at end of file unparsed (C3084); agentsync_dir_values and hold_values
strip a record-ending CR first (C3085); the source count strips kind as config._str does, the awk allowing blanks inside
the quotes (C3087 with C3105); git_remote_entries reads the configured docs repo (own_docs, `~` expanded, trailing slash
dropped) in place of ~/agent-context/docs, never beside it (C3054); own_folder_values registers the inbox link's target
as lane RA does. Lane RA: the unparsed scan pairs each [[source]] id with its path (basic unescaped, literal raw) as
_RawSource (C3056); the kept inbox's resolved target is registered only when it lies outside ~/agent-context, after the
four own folders and the C2430 block, through _own_folder_values, in _build_redactor and _unconfigured_roots (C3055,
makes worse True for _project_values); _logged_cloud_folders splits args as bash's read -a does and collects `$'..'`
words with install.sh's grep pattern (C3104). Lane RA builds its halves and the shared rows first and sends lane RB its
branch sha; lane RB lands both halves in one scripts/ship-land.sh run. Rows, each shell == python in both locales with
one verdict: {bare, "source", 'source'} headers by {basic, literal} id and path by {loaded, unparsed} by {tomllib,
none}; a moved docs repo beside a leftover default of another host; an inbox linked into ~/Locked Contoso/inner (cut,
public) and to ~/Documents/inbox (`inbox` not registered, public); a CRLF continuation; `kind = " inbox "`; a launcher
named `Project --source-local`.
(200) C2060 is still gated on the operator under class C decision c0777e12a523, and the agentsync report reads an
escaped listed row by row and in every reading the row allows (privacy; data integrity: lane RA ungated, C3082, C3114
with C3036; lane RB GATED, C3081 with C3057). Rulings 150, 161, 174 and 190 stand. Lane RA, Python only: _add_listed
carries each entry's escape flag (or a decoded map keyed by row) and decodes only flagged rows, every plain row
registering its literal name fuzzy before any escaped row's printed spelling is aliased, the numbering in install.sh's
order (C3082); listed_name_decodings returns the all-decoded reading first, then each reading that keeps some escapes
literal, for both readings of `\n`, deduplicated and without the written spelling, bounded at 6 matches, above which it
adds only the all-literal reading and the single-literal variants, the cap named in its docstring (C3114 with C3036,
makes worse True for the unbounded form); LIST_ESC_AWK and esc() stay as they are. Rows: a plain and an escaped row with
one printed spelling under two providers in both orders; `Wombat\\tQuokka\x7f`, `Wombat\\177Quokka\x7f` and
`Q3\\101\\tPlans` in the C2873 test and its strict-xfail shell twin; a name past the cap answering fast. Lane RB reads
`cc-decide list --open` for c0777e12a523 at start and before its last land and, only on "Allow", builds the port at
C2667's three porting points (round44/items/C2667.md) mirroring lane RA's row flags and readings, dropping the shell
half's strict xfail and test_deploy_pack.py's pin of its reason; a second refusal stops it, and it messages the lead.
Otherwise the strict xfail stays with its one-line reason and §16.286 says so. No lane writes an equivalent install.sh
change another way, and a refused command is never re-run or reworded.
(201) The STUB_RENDER marker moves only when every stub and tombstone the pass meant to render was rendered, and its
test proves a real tombstone, amending ruling 195 (lane B; C3106 with C3092 and C3040, C3041 with C3095).
_rerender_tombstones returns (rewritten, pending): pending on PublishError or OSError from rerender_tombstone and on a
regular, non-link page whose digest cannot be read; a page that is not a tombstone, has no row, is due for reaping, has
no page_sha256, is a link, is missing, was hand-edited (digest differs) or renders None stays a silent skip.
_rerender_stubs folds that flag into pending (no local named `done`); for REFUSED and QUARANTINED items _stub_again
raises, or returns a sentinel, when the live non-link page is a file whose read fails, so the caller logs `a stub could
not be rendered again` and sets pending, while UnicodeDecodeError and FrontmatterError on a page matching its row stay
None with pending unset. The C2851 test seeds a real superseded tombstone (a single page republished as a `.d/` folder),
ages it to `# [NO LONGER PRODUCED] ` with its new page_sha256 and rendered_sha256 stored, records the spy's counts and
asserts their sum at least 1, the `# [REPLACED] ` heading, status deleted, the same superseded_by and the successor
named in the first body sentence, then no commit and no walk on the next sync. New rows over meta 7: rerender_tombstone
raising OSError once, a tombstone and a refused stub at mode 000, each leaving the meta at 7 and writing 8 on the next
readable sync, and hand-edited pages still letting 8 through. The CONTRACTS paragraph at :46314 takes a dated line
naming both pending sources; test_deploy_pack.py's `self._rerender_tombstones(sid)` substring still matches.
(202) A kept Office answer is read again only from the bytes and the fetch that were read, completing rulings 185 and
196 (data integrity: lane E; C3107 with C3097, C3046, C3096, C3047, C3098, C3048 with C3101). _family_reread sets the
family_parse mark only after arm.fetch returns, inside _read_rows_once's savepoint, so a failed fetch leaves the row
unmarked and the token open as a dataless row does; the _reread_family_stubs, _family_reread and _FAMILY_PARSE_META
docstrings say each row read is marked, CONTRACTS' "whatever the answer" sentence takes a dated line, and
_zero_byte_reread stays as it is (C3107 with C3097). _CONTAINER_REASONS gains ENCRYPTED_PDF_REASON, the _container_moved
and _container_screen docstrings and the C2924 paragraph corrected (C3046), and _container_moved reads a family-mismatch
stub (FAMILY_MISMATCH_RE, never NOT_OOXML_REASON) on non-ZIP bytes as a move, no other stub reason added (C3096).
_ooxml_family reads only <Types>' direct Override and Default children in <Types>' own namespace, or in none when it has
none (C3047), and parses before it decodes, decoding only in the fallback, UTF-32 marks before UTF-16 (C3098);
family_parse stays 1, and a dated line says a hand-built package's cached stub stays until its bytes change.
registry.py's module doc, _common.py:188 and CONTRACTS section 7 say an Office name's container rejection is given
before the cache and never stored, while an encrypted PDF and a family-mismatch stub are cached (C3048 with C3101).
Rows: the decoy .potx with its fetch raising BudgetExhaustedError once (token None, then one try, LIVE, done); a clean,
polyglot, clean run; junk put before a family-mismatch .potx and taken away, with no loop; foreign-namespace and
xmlns="" decoys in a deck and a .vsdx; UTF-32LE and UTF-32BE parts.
(203) Every word install.sh writes or prints is valid UTF-8 and reads back as its path, completing ruling 193's C2944
(lane RB; C3080 with C3089 and C3103). One helper before ALL_ARGS, q_word, gives printf %q's word and re-quotes a
`$'..'` result under LC_ALL=C exported in a subshell; shown_arg calls it and keeps its double-quote and `!` branches.
q_word replaces plain %q in record_flush (ORIG_ARGS, ORIG_SHOWN and BASE_ARGS, both branches), the start line's source=
field, install.out's run line (ALL_ARGS) and SELF's rendering, `~/` kept outside the quote. Only words whose %q is
already `$'..'` change; every plain and `\ ` word stays byte for byte, so the args= equality tests, _again_words and the
`--launcher <path>` scrub pairs hold, and no a: word and no SELF goes through shown_arg's double-quote form. Both
reports keep registering the UTF-8 and the C spellings, so older logs still cut. Tests under /bin/bash 3.2 and
en_US.UTF-8 with U+3000, 日本 and U+0085 in a wheel path, a --source-local folder, a positional SOURCE, a --launcher
bundle and a checkout folder: install.log, install.out and the --no-folder, failed and BASE_ARGS NEXTs decode as strict
UTF-8, bash reads each word back to its path, a spaces-only path renders as before, and both reports cut the logged
folder alike (tests/test_cli.py's C2944 row extended in lane RB's commit); CONTRACTS :46191 takes a dated line.
(204) Every reader names an inbox entry that is not a folder, or one it cannot read, as config.inbox_state does,
completing ruling 194 (lanes B, RB and D; C3007, C3094 with C3009, C3044, C3043, C3008, C3088, C3051). Lane B: cycle.py
decides by loop._inbox_root_not_a_folder's test when a source sits on the kept inbox whose entry is not a folder and
sets LocalArm.shown_root, and arm_local.py's _walk_scan root branches then print, in the alarm and the log, `source root
is not a folder: ~/agent-context/inbox is a file, a link to a file or a dangling link (walk skipped; nothing is deleted;
inbox state says what to do)`, root_missing, root_notdir and every other source's text unchanged (C3007). Doctor keys on
typed_inbox, never on ELOOP alone: the listable row's generic OSError branch reads `is not a folder` with
inbox_not_a_folder_fix and skips the volume probe, and the volume row for a dangling or looping link reads `volume UUID
not checked: <folder> is not a folder` with that fix, never `is missing` (C3094 with C3009); the sentinel row keeps the
TCC fix for EPERM only, points to the listable line with no fix when the root itself is unread, and gives
_parent_fix(target) for a locked folder under a listable root (C3043). doctor._prose_path writes a folder in prose
unquoted unless arm_local._LIST_ESCAPED_RE matches (then the `$'..'` form), command words keep _shell_path and the ACL
fix words stay (C3008; lane RA's row with a blank and a control character checks both reports still cut the plain form).
loop.py's not-a-folder note keeps step 2's remedy for the kept inbox only and gives any other inbox source the
put-back-or-fix-path words, a note, never a wait (C3044). Lane RB: install.sh's older-build fallback inlines inbox_entry
with os.lstat and os.stat, stdlib only, printing `not read (<strerror>)` for any errno but ENOENT and ENOTDIR (and ELOOP
on a link's target) (C3088). Lane D: a dated line under §16.282 C2808 says no report reads step 2's `inbox:` lines, and
lane B rewords cli.INBOX_NOT_READ's docstring in its own commit (C3051). Lane B sends lanes RA, RB and D the new alarm,
doctor and note words first; the CONTRACTS lines that quote them (ruling 130, C2809, C2814) take dated lines in lane B's
commits.
(205) The setup prompt tells a none, an unread inbox and a step 2 or step 3 error only from how the run ended, so it
goes to v43 in lane D's one commit, as ruling 197 took it to v42 (lane D, with lane RA's report halves; C3113 with
C3013, C3102 with C3112, C3116, C3018, C3086, C3029). Step 1's two none branches run step 2's --no-folder command as
step 2 says (its exit status and NEXT: line first), then go to step 3's report; step 2 says that if it exits 0 and its
NEXT: line says I chose no folder, the agent tells me this Mac syncs no folder, and any other result is handled by step
2's rules, a re-run it names keeping --no-folder, the folder question not asked again, and step 3's sync loop never
started after my none (C3102 with C3112). `inbox state: not read (<why>)` leaves the no-drop-folder sentence for a
branch of its own: agentsync could not read what is on my inbox folder, so whether files I drop there sync is unknown;
show that line and step 2's `inbox: not read:` line; name each kind = "inbox" source that is not paused or retired by
its path as a folder that may take dropped files, never as one I added; say ~/.local/bin/agentsync status tells it again
once it can be read; never say I have no drop folder (C3113 with C3013). Step 3 reads a sync or curate run until it
exits; a usage or configuration error (exit 2 or 78, a line opening `error:` or `configuration error:`) prints no NEXT:
line and is logged as a step 3 error with its exit code, shown, not followed, and then the report, the lock-held retry
kept (C3116). The Not used rule covers a setup stopped before step 2 and a step 2 stopped on an error before agentsync
ran, such as its AGENTSYNC_CONFIG refusal, H26 by a dated line (C3018). Lane RA, with no prompt words: compute_outcome
scores a chose_no_folder run that ended rc 0 without completed_none as failed at the install step in its own record's
words (the failed step's rc; `its status step reported [FAIL] lines (rc N)`; `its status step exited N with no [FAIL]
line`; `its status step was stopped at its time limit (rc 143)`), saying an older install.sh ended 0 and that is no
success (C3086); _expected_turns_line, once an attempt reached step 3, adds `step 3 asks at most once whether to add a
folder (not logged)`, and a dated line beside rulings 181 and 197 says that question is no turn beyond the unavoidable
ones and never changes the outcome (C3029, makes worse True for logging it). The same commit moves the version line and
digest, `prompt v43` in --log-start, SETUP_PROMPT_COMPAT, setup_report.PROMPT_VERSION with its docstring list and
setup_report.py's v42 mentions, the issue form, setup-feedback.md's version lines and the old-copy refusal list (v42
joins), a new K row, test_deploy_pack.py's INBOX_NOT_READ pin and every other pin, behavior tests for each new branch,
H26's and §16.289's dated lines and one beside ruling 197; CUT_OFF_TOLD stays. v43 quotes only lines already on trunk,
so it waits on no lane. Round 47 rehearses v43 with v42's block (97049c792) as scenario C's first old copy, a step 2
refused on AGENTSYNC_CONFIG among its plays.

Clusters: the report (a config path literally ending ` exists` compared both whole and stripped, so it is never labeled
an earlier run's config, C3115; Outcome.title_label's and compute_outcome's docstrings naming STOPPED_FORM_OPTION for a
stopped attempt, C3035 with C3053; lane RA); the shell installer (the shell fallback's Configuration section marking a
config path that exists but is not a file `(exists)` with a `loads: no:` reason and no table count, `(missing)` only
when nothing is there, its parity assert in lane RB's commit, C3019; on a bare run with EXIT_RC 0, a NEXT opening
status's rule-2 `no folder is synced yet: ask the operator` replaced by install.sh's own `--source-local` form, keyed on
the NEXT text, with a clause naming each paused or retired folder and its state as the person's edit of sources.toml,
the --no-folder swap after it and loop.py's rule 2 unchanged, a dated line amending ruling 191, lane RA mirroring it in
_install_sh_said_no_folder, C3003; the first sync's rc 75 under a holder that recorded nothing logged
`lock-busy-unrecorded-holder`, lane RA's key landed first beside the old one, C3024; the C2890 test's outcome assert
given a str message holding the written-by, outcome and setup-report-failed lines, gate.sh's re-run rule unchanged,
C3064; lane RB); the CLI (a dry run's withheld-while-settling alarm built in _scan_and_classify after the scan's alarms,
the forced paths filtered there and the late block deleted, C3093 with C3045, and a forced materialise that read the
only settling file adding a note saying why the listing stays INCOMPLETE, the mark and the cursor hold kept, C3010,
makes worse True; every STATE.md and CHANGELOG run read through _run_value, publish's index with an equal local check,
C3042; the trailer format with `unfold` and each whole line validated, C3090; _newest_run_trailer reading matching
commits newest first in batches of 32 behind a case-blind prefilter and returning the first valid value, C3091; step 2's
`sources:` line marking a paused or retired source `(paused)` or `(retired)`, live ids bare, C3002; the cycle's own
corrupt-heartbeat read logged at DEBUG so write_heartbeat's one warning remains, install.sh's stderr untouched, C3004,
makes worse True; lane B); conversion (a capped workbook index's where-phrase naming each sheet's .objects.txt sidecar
as the full list, C3025; speech.py's three CA summaries keeping the pinned phrases and adding that a shell SSL_CERT_DIR
beside its own CURL_CA_BUNDLE gives way to git's CA folder, the C2930 test split into three cases, and dated lines under
CONTRACTS :15152 and the round 45 summary, C3049 with C3100 and C3109; lane E); docs and tooling (setup-feedback.md's
shell numbering order as install.sh calls it, its test, docstring and a call-order pin, dated lines under §16.284 C2864
and C2669, C3050; G12 and H43 dated lines giving a stopped attempt the Stopped option, C3052; data-governance.md's
banner sentence scoped to pages this build writes, with the pre-C2929 heading exception, C3108; round 45's eight ci jobs
and 006e48a6's 80:39 recorded, timeout-minutes 112 and the head 92 moved together in ci.yml, CLAUDE.md and COMMON.md,
test_convert_recording.py:57's docstring and §16.144 W70's chain in the same commit, C3060; gate.sh's ceiling comment
and a dated line under C2876 naming the part stopped at 13,200 s as a censored lower bound, GATE_CEILING_S 23400 and its
test unchanged, C3065; lane D). Lead: round 47's kit (kit/run.sh refusing a status read outside statusread.sh, C3005,
and a --wrapper word holding a blank, with the working loop form in the brief, C3031; the retired-state probe writing
retired_reason and loading the config first, C3006; crun.sh's summary doctor row quoting the copy, C3014;
check-wrapper.sh passing a wrapper that sets AGENTSYNC_CONFIG on purpose, C3021; names-all.sh cutting the shell report's
legend phrase, C3022; final.sh sweeping sub-homes' fixture copies, C3023; fixcheck.sh's per-home allowance list, C3030
with C3068; the slot procedure's copy, chmod, syntax check, edits and hashing before the first run, C3032), the brief
(the shell report's two inbox sentences matched to ruling 194, C3012; the end-of-run list built from ls, C3015;
checksums through kit/sha.sh and kit/run.sh never sent to /dev/null, C3016 with C3027; no bare glob, find with -print,
printenv only when step 2's log has no config: line, C3017 with C3026), the harvest (the bare-glob scan over tool
results after every slot has finished, C3066, makes worse True; the no-ls-l and mktemp rules in text every slot
receives, C3067; each lead session's missed Yours: lines, non-close messages excluded, C3061; recycle-brief.sh's marker
ahead of the standing lines and recycle-check.sh's 2000-character check, C3062, makes worse True), the lead's kit checks
(notes-check.sh's Twins block check from round 46 on, C3059, makes worse True), and the record (round29/lead-notes.md's
late hand-typed times and the 93-minute claim corrected by dated lines, C3063; Codex-only review until backlog
8ed9dae0fbc0's allow rule, Gemini not run for an eighth round, C3077). Earlier rounds are in COMMON5.md to COMMON44.md.

Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`scripts/install.sh` and its tests (test_install_oneshot.py, test_install_help.py, test_install_bounds.py,
test_install_next_line.py, test_install_list.py, test_install_json.py, test_install_signals.py) and test_launcher.py's
install.sh pins, lane RB owns everything this round, the other lanes' install.sh halves included (ruling 198's `~name`
cut, ruling 199's shell halves with C3055's own_folder_values target, C3088's older-build reader), except
SETUP_PROMPT_COMPAT's line and the version pins, which lane D's v43 commit moves. In `setup_report.py`,
`report_cuts.py`, `tests/test_setup_report.py` and `tests/test_report_redaction.py`, lane RA owns everything, the other
lanes' report halves included (C3003's _install_sh_said_no_folder mirror, C3024's `lock-busy-unrecorded-holder` key,
C3008's row with a blank and a control character), except the shell halves and both-reports rows lane RB's items need
(rulings 198 and 199's shared rows, C3019's folder-config parity assert, C3054's moved-remote row), in lane RB's
commits, and PROMPT_VERSION, its docstring list and setup_report.py's v42 mentions, which lane D's v43 commit moves;
tests/test_cli.py's C2944 parity row, which ruling 203 extends, rides lane RB's commit. `cli.py`, `cycle.py` and
`publish.py` outside lane E's parts, `loop.py`, `paths.py`, `config.py` (but its max_page_bytes comment), `skill.py`,
ops/lock.py and doctor's inbox, listable, volume and sentinel rows are lane B's, and this round so are arm_local.py's
_walk_scan root-branch alarm and LocalArm's shown_root (C3007). Lane E owns convert/, cycle.py's family re-read (C3097
with C3107, moved from lane B), _CONTAINER_REASONS and _container_moved (C3046, C3096), and speech.py's docstrings with
the CONTRACTS lines that quote them (C3049 with C3100 and C3109, moved from lane D); lane B's cycle.py work
(_rerender_stubs and _rerender_tombstones, _scan_and_classify's withheld alarm, the run readers, source_statuses)
touches none of those functions, and each lane rebases on the other's land. `scripts/gate.sh` with
`tests/test_gate_journal.py`, `scripts/ship-land.sh`, CLAUDE.md and ci.yml are lane D's (C3060, C3065), and so is
tests/test_convert_recording.py:57's docstring (C3060). Only lane D touches docs/deploy, the README and .github, each
line written once from the lanes' final words, except a doc line a test holds to a lane's own words, which moves in that
lane's commit: cli.INBOX_NOT_READ's docstring rides lane B's commit (C3051), and on an Allow the pin of C2060's xfail
reason in test_deploy_pack.py rides lane RB's port. Only lane D touches the README prompt block, its version and its
digest (ruling 205). Who sends first: lane RA sends lane RB its branch sha with the Python halves and shared rows of
rulings 198 and 199, and lane RB lands both halves in one scripts/ship-land.sh run on that branch, which never waits on
ruling 200 (lane RA may land ruling 198's redactor_failed flag alone before it); lane RA lands C3024's report key before
lane RB's note, and lane RB sends lane RA C3003's NEXT words and paused clause before lane RA's mirror; lane B sends
lanes RA, RB and D ruling 204's alarm, doctor and note words, and lane D ruling 201's pending words; lane RA sends lane
D C3086's outcome words and C3029's turns words; lane D's v43 commit waits on no lane. A CONTRACTS line that quotes
words a lane changes, and a test that pins them, move in that lane's own commit as a dated line (ruling 186), and lane D
cites the sha. COMMON.md's rulings 188 to 197 stay as written; rulings 198 to 205 amend them, each by its CONTRACTS
dated line, and lane D adds a dated line beside ruling 195 (ruling 201), beside ruling 191 (C3003) and beside rulings
181 and 197 (C3029, ruling 205). The lead reserves §16.285 to §16.289 on origin/main before any lane starts: RA §16.285,
RB §16.286, B §16.287, E §16.288, D §16.289. Lanes land with plain `scripts/ship-land.sh`, the repo's default lock; no
lane names a lock of its own, and no lane runs git fetch, pull or remote update, in the operator checkout or any
worktree (C2486, C2874): trunk reads go through round46/tools/trunk.sh. docs/plans and the kit are the lead's.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.285 to §16.289); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: C3013, C3018, C3102, C3112, C3113 and C3116 need prompt words, so the prompt goes to v43 in lane D's one
commit (ruling 205): v42 told `this Mac syncs no folder` before it read the --no-folder run's exit status (C3102 with
C3112), put `inbox state: not read (<why>)` in the no-drop-folder sentence and showed only that line (C3113 with C3013),
waited for a NEXT: line a usage or configuration error never prints (C3116), and kept "no part was used" for a stop
before step 2 alone (C3018). v43 quotes only lines already on trunk (`inbox: not read:`, `error:`, `configuration
error:`, the person's-choice NEXT), so lane D's commit waits on no lane, and round 47 rehearses the prompt the operator
will paste: v43, with v42's block (97049c792) as scenario C's first old copy. C3029 needs none, though it was marked:
its corrected fix changes the report only (the expected-turns line and a dated ruling), since logging step 3's answer
would count a question the prompt asks as a turn. C3086's scoring, C3003's bare-run NEXT (its `no folder is synced yet`
opener kept), C3002's state marks, C3007's alarm, C3044's note and C3039's `configuration error:` need none: the prompt
quotes none of the words they change. No other lane edits the README setup prompt block, its version or its digest; a
lane whose fix changes words the prompt quotes sends lane D those words first, and lane D's prompt commit lands after
them. If another fix seems to need new prompt words, message the lead before you build it.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.285 to §16.289) begins "Amended <date>, §16.NN
C<n>". A line that records another round's change reads "Amended <date>, §16.NN C<n> (by §16.NN <item>)"; never stamp
another round's item id as if it were yours (round 5 F85). Round 45's ids are C2801 to C2950; round 46's are C3001 to
C3116. The dated-line scans read a line of any date since round 19's W71.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
Every install.sh probe of doctor (state_path_fix, PERSONS_KINDS, the parent fix) runs under the installed
agentsync: it must fall back when that build lacks the name.

Dated-line test: since round 9 K80 the gate reads every "(C<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 46's rows the same way. A quote in a §16 paragraph counts as a dated line only with a real date, a numeric home
of §16.92 or later and no `C<n>` template, and test_every_dated_line_from_round_11_on_is_held_by_a_test then calls it
loose unless it is claimed or pinned: quote the form with `C<n>` or `<date>`, as each section's "A dated line this
round writes reads" sentence does, or pin it (round 35 C982).

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round46/
scratch-<lane>/`, not in /tmp. The land lock is the repo's own, in its git directory. To damage a manifest for a test or probe, write the
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
folder that holds them (round 32 C664). So `<copy>` in the shasum check is the copy's absolute path (`cd <copy> &&
pwd -P`), never one relative to the root, which the check would read from the fixture's folder and report every file
missing, and never one holding `|`, `&`, `\` or a newline, which the sed replacement takes unescaped (round 37 C1231). A rehearsal that crosses local midnight checks every step after it again: an
empty-folder alarm or wait gone just after 00:00 local (the parent folder's mtime says when) is the cleaner's doing,
not agentsync's. The Bash tool's shell here is zsh, while sb and the kit scripts are /bin/bash. Outside sb,
`${PIPESTATUS[0]}` is empty and a pipe's `$?` is its last stage's, so to keep a command's exit code, send its output
to a file and read `$?` straight after: `sb '<cmd>' > logs/x.log 2>&1; rc=$?; echo "rc=$rc"` (not `status=`, which
zsh holds read-only). Quote any word that starts with `=` (`echo '====='`, never `echo =====` or `[ a == b ]`): zsh
reads it as a command name, stops with `= not found`, and skips the rest of the command; to mark the parts of a
read's output, use dashes (`echo '--- notes ---'`), which neither shell expands (round 37 C1207). Brace any variable a colon follows (`git show "${S}:src/agentsync/cli.py"`, never `"$S:src/..."`): zsh reads
the letter after `$var:` as a modifier (`:s`, `:t`, `:h`, `:l`, `:u`, `:a`, `:r`, `:e` and others), so `$S:tests/x`
silently becomes `<sha>ests/x` and `$S:src/x` stops with `bad substitution`, skipping the rest of the command; double
quotes do not prevent it, and bash reads `${S}:` the same way (round 40 C1823). The diff and shasum checks
above end in the checker, so their `$?` is right as written (round 35 C907). Build any file list inside a kit script
(/bin/bash) and run that script by path. Never use zsh parameter-expansion flags in the Bash tool, such as
`${(f)"$(...)"}` or any `${(...)...}`: Claude Code's removal check refuses them as `could not be checked for dangerous
rm operations`, even with no rm (round 36 C1113). Do not use other zsh-only forms such as `${=var}` either; sb and the
kit scripts read them differently. zsh does not split an unquoted `$var`, so never pass a list to a script as one
unquoted variable: let the script build the list itself, or pass one argument per file. This is what to do first, not
a way around a refusal: a refused command is still not reworded but recorded as a finding.
No probe creates, touches, holds or removes a lock file by hand (touch, a redirect, ln, mv, flock or rm on any
.git/*.lock, index.lock, refs/*.lock or the land lock; the locks git and agentsync take for themselves inside a
throwaway home or sandbox clone are fine), no probe runs rm in any form, and no probe runs git fetch, pull or remote
update. In a rehearsal it binds every probe and check of the rehearsal's own making, and leaves alone only these: the
prompt's own commands, played as written in a sandbox clone (its fetch and pull); the bare mirror's fetch inside the
root; the sandbox's own agentsync.lock held with /usr/bin/lockf, as scenario E and kit/sigterm.sh do; and the FIFO
rules' rm -f of a FIFO or socket. A file a probe would remove is moved to <root>/trash/<probe>/ instead, and scratch
is cleared by making a fresh `mktemp -d` folder, never by removing one. A race or lock question is answered by
reading code and existing logs, and a probe that would need one is recorded as unmeasured (round 40's verify:71 hung
twice at a permission prompt nobody can answer, on a hand-built probe that touched a .git/refs lock file and ran rm
-f). The rule in a brief is not enough by itself: in round 41 a review slot ran rm -f and a verify slot rm -rf with it
in their briefs, and the classifier and the hook refused each in seconds (round 41 C2077).

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

Landing (round 46): land with `scripts/ship-land.sh`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the repo's one land lock (`/Users/chrisren/Development/agent-context-sync/.git/land.lock`, shared with the CLI conventions wave's lands), fetches, reads trunk's CI once without waiting (a run
already ended red stops it there, exit 6, before the gate), rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf
/Users/chrisren/Development/agent-context-sync/.git/land.lock scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).
If its line says `no CI run for <sha>`, CLAUDE.md's "CI is part of the gate" says when to dispatch `ci` and when
never to (round 35 C982). A land run in the background can end while you think it is queued (round 34: exit 4, two
hours lost): while you wait, read its log, and check that your own worktree's path shows in `ps -ax -o command | grep
'<worktree>/scripts/ship-land'`, since other lanes' lands look the same otherwise.

No fetch (round 43 C2486): a lane never runs git fetch, pull or remote update, in the operator checkout or in any
worktree of it, its own lane worktree included. Every worktree shares refs/remotes/origin/main, the object store and
the land lock, and FETCH_HEAD is written in the checkout that fetched, so a lane's fetch races another lane's land
(round 42: lane RB fetched in the operator checkout 11 times, six of them inside another land's gate). The only
fetches are the ones `scripts/ship-land.sh` makes. To read trunk, run `bash
~/.cache/agentsync-bring-back/b13/round<n>/tools/trunk.sh [<full sha>]` or that folder's lane-status.sh (git ls-remote
and cat-file, which write nothing). To wait for another land, read the local `git log origin/main` (every ship-land
fetch moves it for all worktrees) or trunk.sh <sha>, at most once a minute, never in a fetch loop. A STOP is reported
and looked at, never fetched.

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6); since round 27 P140 it
also reads trunk's runs once before the gate, so a trunk already red stops the land before a 35-minute gate. If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. Pass `--allow-red-trunk` only for a run that
ended red on tests your diff does not touch and your lane does not own, with the run id in your commit body or result
note and the owner messaged; never for exit 9: a run that lost its hosted runner is rerun (`gh run rerun <id>
--failed`) and waited for, never landed past (round 31 C569). The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. CI takes 18 to 92 minutes a run (the slowest, 91:40, in round 45, a failed first attempt; 89:37 green), and a run can wait over an hour for a macOS runner first (63:27 in round 31); its limit is 112 (W80, round 21 Y68, round 22 Z67, round 23 A74, round 24 B98, round 25 D83, round 28 I99, round 29 C207, round 30 C372, round 31 C571, round 32 C662, round 35 C985, round 37 C1290, round 38 C1460, round 40 C1866, round 45 C2878, round 46 C3060).

Makes things worse: 8 round 46 items carry "a fix makes things worse? True" (C3004, C3010, C3029, C3036, C3055, C3059,
C3062, C3066). For each, the verifier's corrected fix is one that does not, and the rulings and clusters adopt it
(C3004: the cycle's own read of a corrupt heartbeat.json logs at DEBUG so write_heartbeat's one `rewriting it` warning
remains, install.sh's stderr passthrough untouched, where keeping the syncs' warnings off their output would hide the
stderr lines install.sh's NEXT points to (the first sync's error, the configuration error, the lock holder's line) and
the only copy of a foreground sync's warning; C3010: a note says why the listing stays INCOMPLETE when materialise read
the only settling file, the INCOMPLETE mark and the cursor hold kept, where marking the forced pass complete recorded a
listing that held a file back as complete, against C2813's ruling; C3029: the report's expected-turns line names step
3's at-most folder question and a dated line says it is no turn beyond the unavoidable ones, the prompt and the friction
log untouched, where logging the answer as a question line counted a question the prompt asks as a turn, demoted `fully
one command` and needed a prompt bump; C3036: the decoder adds the readings that keep some escapes literal, bounded at 6
matches with the all-decoded reading first, in Python only, where reading every backslash both ways is exponential (2^86
readings for 85 `\t`s, a report that hangs) and escaping a backslash in LIST_ESC_AWK changes every escaped row's shell
spelling, reaches into C2060's gated half and leaves old rows ambiguous; C3055: the inbox link's target is registered
only when it lies outside ~/agent-context, after the own folders, through _own_folder_values, where registering every
source's resolved path through _project_values registered the bare `inbox` of a link to ~/Documents/inbox, made the
report private only and turned every `inbox` word into a placeholder; C3059: notes-check.sh reads the whole Twins block
from round 46 on and passes one test_ token or `none, <reason>`, splitting no pairs, where a test name per pair failed
the one compliant note, twins that share one implementation and the gated C2873 twin; C3062: recycle-brief.sh writes the
guard line and the marker before the standing lines and recycle-check.sh fails a prompt whose marker is past 2000
characters, the harvest's 2000-character window kept on top-level .jsonl files only, where reading the whole first
record or recursing matched workflow subagent transcripts as leads; C3066: the harvest scans tool results for `(eval):N:
no matches found` after every slot has finished, skipping records that quote it, with no new brief prose, where a
kitcheck grep for `no matches found` flagged all 93 transcripts, since the brief itself holds the phrase): read its
"Verifier" part before you build.
Dated lines: every round 46 dated line reads 2026-11-24, its heading's date (lane D's round 25 test).

Regressions: round 46 found 0 turns and 40 wrongs. That is the finders' count; the verifiers class C3099 (the report's
redactor falling back open) a turn and 27 items wrongs. 18 items are data integrity, 17 of them privacy (C3107 the
other), and the wrongs share five root causes. A new refusal in one lane crashed callers in another: ruling 193's
`~name` refusal in paths.expand raised inside the report's raw readers, so the redactor fell back to the home cut and
the report went out public with the login, name, serial, host, org and folders in clear (C3099, C3033, C3037, C3110),
write_skill broke its never-raises contract and failed every sync (C3038), and a `~name` value in sources.toml exited 2
naming no file (C3039; ruling 198). A report cut a value from one reader or one spelling and the other printed it: the
shell's id reader skipped literal ids and quoted headers (C3034, C3083, C3056), the shell read only the default docs
repo's remote (C3054), the agentsync report missed an inbox link's target (C3055) and a logged operand behind an escaped
flag word (C3104), and the shell's TOML readers passed an open multi-line value, kept a CR and counted a padded kind
(C3084, C3085, C3087, C3105; ruling 199); the agentsync report decoded an escaped listed name one way and keyed it by
name (C3082, C3114, C3036), and C2060's shell half stays gated (C3081, C3057; ruling 200). A fix stopped at its first
twin: the STUB_RENDER marker moved past a tombstone or stub it could not render, and its test passed with no tombstone
(C3040, C3092, C3106, C3041, C3095; ruling 201); the family re-read marked a row before its fetch, the container check
missed the encrypted-pdf and family-mismatch answers, a decoy namespace and a UTF-32 part decided a family, and two
docstrings kept the cache claim (C3097, C3107, C3046, C3096, C3047, C3098, C3048, C3101; ruling 202); the C-locale
quoting covered only the folder word (C3080, C3089, C3103; ruling 203); and a dry run lost the withheld alarm (C3093,
C3045), the run readers beside the trailer took any digits or trusted one commit (C3042, C3090, C3091), and C2803's and
C2930's old words stayed in docstrings and dated lines (C3035, C3053, C3052, C3049, C3100, C3109). A reader told the
inbox other than inbox_state does: a sync named a link's target, doctor called a dangling link missing and a loop a
Finder problem, a second inbox source got step 2's remedy and the older-build reader read denied as absent (C3007,
C3009, C3094, C3044, C3088; ruling 204). A fact was told before it was checked: v42 said `this Mac syncs no folder`
before reading the run's exit status, told an unread inbox as no drop folder and waited for a NEXT: line some errors
never print (C3102, C3112, C3113, C3116), the report scored an older --no-folder run whose status failed as the person's
choice (C3086) and counted no turn for step 3's folder question (C3029; ruling 205), the shell report called a folder
config missing (C3019), the guide gave the shell's numbering order wrongly (C3050), and the report labeled a config
named `... exists` an earlier run's (C3115). A fix that runs in an older installed build is not a fix: test the shell's
cut over an older render. A word another reader greps for is a contract: when you change a printed phrase, grep
install.sh, both reports, status, doctor and the guides for it, and test the reader against the writer's own constant,
never a copy. Before you land a new stop, exit code, wait or person-owned fix, follow its words on a Mac in the state it
is meant for AND test it on one where it must not fire. A test marked timing is skipped above a load of 4 per core: run
any one you change with AGENTSYNC_TIMING=1 before you land. scripts/gate.sh re-runs a part's 1 to 20 failed tests once
alone and convicts only on a second failure, so read a red land's log: a test that failed alone is yours or trunk's,
never load. A background ship-land can end while its lane believes it is queued: while you wait on the lock, check with
ps that your ship-land still runs. A test that sends SIGHUP or SIGINT to a child makes the child take that signal's
default first, because a pytest started under nohup passes SIGHUP on ignored and one started with `&` passes SIGINT on
ignored (C869 for two tests; b781e539 for C956 and C961). A new signal test must do the same, so a detached gate passes
it (round 36 C1054). Send the words another lane waits on first, pre-gate with --affected and land once; two lanes that
share a chain may land jointly under one gate, the shared commit with one owner. A whole-tree test one lane owns can
turn another lane's gate red: ruling 68's phrase scan reads install.sh's literals too, so a lane that prints a new
clause adds it to both phrase lists in that commit and runs the scan before it lands. A citation of an off-trunk sha
fails test_a_cited_commit_is_on_the_trunk: cite only what is on origin/main. A hook asks before any command holding the
word "restore" and before `git push --delete`: leave the word out of -k filters, and never push a share branch you would
have to delete. If the classifier refuses a command, do not reword it: message the lead. A trunk run that lost its
hosted runner is rerun, never landed past (ship-land exit 9, round 31 C569); pass --allow-red-trunk only after reading a
red run's log and finding no test failed. A fresh worktree needs `npm ci` before ship-land. The line numbers in the
specs are 006e48a6f's; the CLI conventions wave has closed (its last land 63deb83d, its queue empty), so trunk moves
only by the lanes' lands: find a function by name and rebase on each land. A ruling for both reports lands both halves
in one run, proven by one shared table both read: two halves ported by hand drift, so generate the second copy from the
first, hold it byte for byte to its source, and run one property test over both reports. A shared table protects only
the values it holds: every argument form a report parses (the = form, nested paths, names with blanks, echoes) must be a
row in it and a case in its one property test. A rule keyed to where a path lives (under CloudStorage or not) leaks
every path outside that place: the rule of a head applies to any path, and a test case outside the expected place proves
it. A record ends only where its writer ends it: a reader of another program's output splits on the byte that program
ends a record with (LF, or NUL under -z) and tests its prefix on bytes, never with str.splitlines() or strip(), a writer
escapes or quotes every line break inside a record, and a test puts CR, VT, FF, 0x1C to 0x1E, U+0085, U+2028 and U+2029
inside one record and expects one record back. A test proves its fix only if it fails without it: before you land, run
each new or changed test once with the fix's source hunk reverted in a scratch copy of your branch, see it fail for the
reason it names, and say so in your note; a test that builds its expected value with the code under test, matches a
substring, or changes a second input beside the one it tests passes either way (C2446, C2448, C2453, C2514). A fix has
twins, and the twin left behind is the next round's wrong: the code that renders a value and the code that costs, cuts,
quotes or renders it again (_code and _code_steps, the replan's put-back and the zero-byte pass's, install.sh's NEXT and
the report's Loop line, the live render and the older-render rewrite, an unforced run and a forced one). Before you
land, grep for every function and phrase you changed, name each twin in your note on a line that opens `Twins named:`
(or `Twins named (<rule>):`), listing each changed function or phrase with its twin and the test that feeds both, or
`Twins named: none, <reason>` (for example "docs-only items" or "no second renderer: grepped <names>") (round 45 C2887),
and add one test that feeds both twins the same input and asserts the same answer (round 44: C2601, C2639, C2645, C2652,
C2703). A choice the person makes is honored on every route its run can end on: when a flag records an answer
(--no-folder), list every NEXT, exit code and report line that run can end on (the skill-missing and the rule-2 routes,
a created and an existing config, a status [FAIL] and a timeout), and add one test per route with the flag set,
asserting the answer where the run ended clean and never where it failed (round 45: C2909, C2910, C2945). A function
that starts to raise changes every caller's contract: when you make a function raise a new error, list its callers in
every lane (`git grep -n '<name>(' src`), and for each add a test that feeds it the input that now raises and asserts
the caller still keeps its own promise (the report stays redacted, write_skill never raises, a config error names its
file and exits 78); and a fallback that catches a broad exception where names are at stake fails closed (private only,
no issue link), never open (round 46: C3099, C3033, C3037, C3110, C3038, C3039).

Lane peers (round 20 Y80): once all five lanes are live, the lead messages every lane the five session ids. Send a
seam note straight to the lane that owns the file, and copy the lead only when it changes another lane's scope.

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r46-<lane>.md`. It carries a section headed exactly `For later
lanes and the rehearsal`, after its Items and Corrections, which says `none` when the lane has nothing for a later lane
or the rehearsal; a word for one lane, lane D included, goes inside that section, never under a heading of its own
(C2215). Only a Landed or CI read-back section, written after the land, may follow it (C2465). An item under Items
that a later lane or the rehearsal needs is repeated inside that section, or pointed to there by its id (round 44
C2625). The rehearsal reads each note's section, from its heading to the next `## ` heading, and where a note has none,
every section of the note above its Landed or CI read-back section, so no closing section is skipped (round 44 C2681).
Before the rehearsal reads the notes, the lead checks each one mechanically and sends a note that fails back to its
lane, without holding up its land: exactly one line `## For later lanes and the rehearsal`, only a Landed (or Land) or
CI read-back heading after it, and a section that is not empty (`none` or at least one line) (round 44 C2615, C2620,
C2625).
