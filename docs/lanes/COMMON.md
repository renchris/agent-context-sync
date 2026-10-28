## Common brief for every B13 round 31 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 31.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 30 landed" and "B13 round 31" (the v33 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.208 to §16.212.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round31/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round31/*.md`): read the part
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

ROUND 31 RULINGS (lead; every lane builds to them; all code-only, so the prompt stays v33):
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

Clusters: the report's other words (the Purge queue's no-row head after a dry run, C510, makes worse True; the
Repeat conversions note for one file, C527; lane RA); the shell report's late-block dedupe on one snapshot of
sync-stops.log (C536; lane RB); the purge queue's two ids named apart in unresolved_purges and the report (C557) and
`--forget` looking an entry up itself, which replaces round 30's not-built line for C371's (4) (C565; lane B); the
dead label_fingerprint (C550 with C579; lane E); the guides' own words (the lost lists' order, C532; the Purge queue
row's two new lines, C535; the Repeat conversions row's pointer, C554; the two records' not-a-file tails, C556; the
two records in Where copies live, C558), CI's times with the 70-minute limit kept (C571) and a lost hosted runner in
ship-land, rerun and never landed past (C569; lane D). Lead: the kit's brief for round 32 (C503, C528, C573's (a)
and (b), C504's brief half; ruling 61) and the plan's not-built line for C371's (4), which C565 replaces once the
lead confirms it. Earlier rounds are in COMMON5.md to COMMON29.md.

Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`scripts/install.sh` and `tests/test_install_oneshot.py`, lane RB owns everything this round, the other lanes'
install.sh halves included (C529's bare-path pairs and ruling 56's legend clause in lane RA's words, ruling 55's
shell probe, C543's twin at :8189 and :9394 in lane B's), except the FIXED_VALUE_PHRASES lines, which lane RA
changes in ruling 55's one commit with setup_report's tuple, since the parity test reads both. In `setup_report.py`,
`tests/test_setup_report.py` and `tests/test_report_redaction.py`, lane RA owns everything, the other lanes' report
halves included (lane E's rescreened_reader and legacy_pathed words, lane B's read_only_reads around the hooks, the
.ro-copy helper's call, C557's queued id, C520's wait), but a pin there that only quotes another lane's changed
words moves in that lane's commit (:17950 for C511, :444-445 for C525). `governance.py`, `manifest.py`, `loop.py`
and `ops/doctor.py` outside lane E's parts are lane B's. In `cli.py`, lane E owns this round the sync line's
re-screen clause (:700-706) and _printed's reader record beside its path re-read and legacy notes, lane B the rest,
the label-unreadable clause at :692-694 included; the two rebase on each other's land. In `cycle.py`, lane E owns
the reader re-screen whole (_check_label_reader, _queue_rescreen, _rescreen_lines, _rescreen_left, the tally's
reader share), the stored reason folds, _names_a_path and the legacy retry's mark; `model.py`'s rescreened_reader
and legacy_pathed, `policy.py` and `convert/` are lane E's. A CONTRACTS line that quotes words a lane changes, and a
test or guide line that pins them, move in that lane's own commit, and lane D cites the sha:
setup-feedback.md:1197's head, test_deploy_pack.py:6779-6785 and §C304 for C510, and setup-feedback.md:1200 with
test_deploy_pack.py:8655 for ruling 53's report words (lane RA); test_deploy_pack.py:6788 and the Purge queue row's
forget and C371 sentences for C557 and C565, data-governance.md:115-117 and CONTRACTS:33812 for C537 and C577,
CONTRACTS:33847-33848, ~33886, the three label-clause quotes and §16.205's residual (lane B);
setup-feedback.md:946-950's tail and test_deploy_pack.py's word map for C507, CONTRACTS P14 and :21517 (lane RB);
CONTRACTS:34290, :34297, §16.28/§16.206 and the §15 label_fingerprint stub (lane E). Lane D writes the rest of the
guides' words once, after the lanes land: the Purge queue row's two new lines (C535), the Repeat conversions row's
pointer (C554), the :1203 clause (ruling 59), the lost lists' order (C532) and the records page's queue scope and
reader sentence (C581, C559). In CLAUDE.md, `docs/lanes/COMMON.md`, `.github/workflows/ci.yml`,
`scripts/ship-land.sh` and `tests/test_ship_land.py`, lane D (C569, C571, C573, C504); COMMON.md's ruling 36 text
stays as written, and ruling 46 amends it (partition_purges_ro is gone).

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.208 to §16.212); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: no item needs prompt words this round, and v33 stays: rulings 10 to 62 are code-only so the
operator still pastes v33. No lane edits the README setup prompt block. If a fix seems to need new prompt words,
message the lead before you build it.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.208 to §16.212) begins "Amended
<date>, §16.NN C<n>". A line that records another round's change reads "Amended <date>, §16.NN C<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 30's ids are C301 to C407; round
31's are C501 to C583. The dated-line scans read a line of any date since round 19's W71.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
Every install.sh probe of doctor (state_path_fix, PERSONS_KINDS, the parent fix) runs under the installed
agentsync: it must fall back when that build lacks the name.

Dated-line test: since round 9 K80 the gate reads every "(C<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 31's rows the same way.

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round31/
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
hashes every file again and an inbox root holds each back for its 60-second quiescence. Right before step 2, and
before reading a role run's state, run `(cd <home>/Library/CloudStorage/OneDrive-Contoso && /usr/bin/find . -type
d | LC_ALL=C sort | LC_ALL=C sed 's#^\.#tree#') | diff - dirs.txt` and stop on a non-zero exit: dirs.txt holds
`tree`, `tree/Documents`, ..., so the sed maps find's leading `.` to `tree` (round 30 C329). A rehearsal that
crosses local midnight checks every step after it again: an empty-folder alarm or wait gone just after 00:00 local
(the parent folder's mtime says when) is the cleaner's doing, not agentsync's.

Tools CI lacks (round 18): lane RB's census test passed here only because this Mac has gitleaks; CI does not, and
its land turned trunk red for 90 minutes. A test that reads a tool's output (gitleaks, pandoc, swift, tesseract)
must also pass with that tool absent: check `shutil.which` or stub it.

Gate: the repo's `scripts/gate.sh`, never a hand-copied script. While you build, pre-gate with
`scripts/gate.sh --affected` (every static step, then the tests your diff can turn red; `scripts/gate.sh --lane`
is the full set, resumed where a killed run of the same tree stopped). Before every push run the land gate,
`scripts/gate.sh`: the full CLAUDE.md list, the full test set (the whole-tree scanners first), and it ends
`GATE GREEN at <sha>`.

Landing (round 31): land with `scripts/ship-land.sh --lock /tmp/b13-r31-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, reads trunk's CI once without waiting (a run
already ended red stops it there, exit 6, before the gate), rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r31-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6); since round 27 P140 it
also reads trunk's runs once before the gate, so a trunk already red stops the land before a 35-minute gate. If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. Pass `--allow-red-trunk` only for a run that
ended red on tests your diff does not touch and your lane does not own, with the run id in your commit body or result
note and the owner messaged; never for exit 9: a run that lost its hosted runner is rerun (`gh run rerun <id>
--failed`) and waited for, never landed past (round 31 C569). The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. CI takes 18 to 52 minutes a run (the slowest, 51:10, in round 29), and a run can wait up to about 48 minutes for a macOS runner first; its limit is 70 (W80, round 21 Y68, round 22 Z67, round 23 A74, round 24 B98, round 25 D83, round 28 I99, round 29 C207, round 30 C372).

Makes things worse: 9 round 31 items carry "a fix makes things worse? True" (C506, C510, C513, C516, C525, C530,
C537, C548, C561). For each, the verifier's corrected fix is one that does not, and the rulings adopt it (C506:
path_reread stays untouched and a separate legacy_pathed count marks the legacy retry; C510: the head names the real
queue run as not yet searched and never says `not looked up`; C513 and C516: rc_meaning 143 is unchanged and the
signal form carries no nested bracket; C525: K's clause and _SPLIT_RE stay, so install.sh's awk still reads R and K;
C530 and C537: words only, and the sync stop stays; C548: the reader never overwrites a pending [policy] marker;
C561: a record closes only under all three conditions): read its "Verifier" part before you build.
Dated lines: every round 31 dated line reads 2026-10-28, its heading's date (lane D's round 25 test).

Regressions: round 31 found 0 turns and 19 wrongs, 7 of them data integrity. Most sit on round 30's own new paths: a
lost erasure hidden behind an ok line (ruling 46), the staging path kept in a page, QUARANTINE.tsv and the manifest
(ruling 48), a project name behind a link printed in a public-ready report (ruling 49), install.sh's
governance-folder fix moving a kept folder aside and dropping its holds (ruling 50), round 28's hold record lost
with heartbeat.json and more suppressions accepted than meant (ruling 51 and 47). Before you land a new stop, exit
code, wait or person-owned fix, follow its words on a Mac in the state it is meant for AND test it on one where it
must not fire, and grep the other readers of its words (install.sh, both reports, status, doctor, the guides). A
test marked timing is skipped above a load of 4 per core: run any one you change with AGENTSYNC_TIMING=1 before you
land. Round 30 took 17 hours from the fire to the last land, most of it the land queue (gates of 50 minutes to 2
hours), so pre-gate with --affected and land once. A whole-tree test one lane owns can turn another lane's gate red
(round 30: lane B's new printed words failed lane RA's C302 scan, nothing pushed): ruling 55's five-word scan is
one, so a lane that adds printed words runs it before it lands. A shared commit in a joint push has one owner: the
other lane drops its copy and never rebases a cherry-pick of it. A hook asks before any command holding the word
"restore" and before `git push --delete`: leave the word out of -k filters, and never push a share branch you would
have to delete. If the classifier refuses a command, do not reword it: message the lead. Pass --allow-red-trunk to
ship-land only after reading the red run's log and finding no test failed (round 30: a lost hosted runner).

Lane peers (round 20 Y80): once all five lanes are live, the lead messages every lane the five session ids. Send a
seam note straight to the lane that owns the file, and copy the lead only when it changes another lane's scope.

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r31-<lane>.md`.
