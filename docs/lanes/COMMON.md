## Common brief for every B13 round 30 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 30.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 29 landed" and "B13 round 30" (the v33 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.203 to §16.207.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round30/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round30/*.md`): read the part
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

ROUND 30 RULINGS (lead; every lane builds to them; all code-only, so the prompt stays v33):
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

Clusters: the report's other words (a sync stop in the end line's own second, C301, with C317's same-second pairing;
the lost-unread evidence line's commas, C307 with C323; the OCR part's folded stub verb, C325 with C340, C398, C405;
`a link is never short`, C374 with C399, C407; dead code and self-copying tests, C343 with C400; the redaction count
beside `<path>`, C331; the Environment line's lost-manifest words, C332; a link target's capture past `; `, C342;
the lost hold's record-clause pin, C359; lane RA); status, doctor and the commands (status's own lost-hold wait,
C305 with C314, C355; a governance file that is not a file in the listing note, C321; both side files in the note,
C322; a heartbeat link to nothing beside a lost manifest, C310; the label-unreadable count, C326; the credential
clause's plural, C353; held_purges' docstring and the dead HELD_HEAD, C356, C357; lane B); a killed child's job line
in install.out (C320; lane RB); CI's times and limit (C372 with C387), the IT request's FluidAudio order (C363 with
C373), the guide's quote, step 1 note and ruling citation (C306, C315, C376), install.sh's single-quoted program
lint (C392) and the rehearsal kit's folder check and /tmp note (C329 with C380; lane D). Lead: C391's text in
docs/plans (lane D records its reason) and C380's kit change for round 31 (rehearsal roots under ~/.cache, or a
fresh `cp -Rp` each act). Earlier rounds are in COMMON5.md to COMMON28.md.

Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`scripts/install.sh` and `tests/test_install_oneshot.py`, lane RB owns everything this round, the other lanes'
install.sh halves included: C301's claim pass (lane RA's rule), C313's `record` origin in hold_cut and the put-back
at :975 (C316, C333; lane B's words), the `hold-record` stop case (C334, C347, C367), C381's purge record, C386's
hold_scopes reader (lane RA's pairs), C351's manifest-fix words and C343's hasattr guard; C311's person's kind lands
in every copy in lane RB's one commit, doctor.PERSONS_KINDS and setup_report._PERSONS_KINDS_COPY included (ruling
6). In `setup_report.py`, `tests/test_setup_report.py` and `tests/test_report_redaction.py`, lane RA owns
everything, the other lanes' report halves included (C313's origin, C321's heartbeat branch, C326's class, C328's
_STUB_NOW_RE test, C349's wait, C351's words, C357's docstring and pin, C362's retry sentence and count with C319's
clause, C367's stop noun, C381's buckets, C394's callers); `governance.py` is lane B's whole, C394's _ro functions
included. `tests/test_cli.py` is lane B's, but lane RA moves C304's pins (:9634, :9645) and puts its new report
tests in `tests/test_setup_report.py`. In `cli.py`, lane E owns `_new_start_kept_lines` and the path re-read notes
after the legacy notes (C362), lane B the rest; in `model.py`, lane E adds the path re-read fields beside
legacy_retried and lane B C326's count. In `cycle.py` and `publish.py`, lane E owns the new start's parts, the
conversion retry's re-reads and count (C362), the stored reason fold (C361) and the label reader's re-screen beside
_check_policy_change (C404, C360), lane B the rest (C326, C328, C348, C401). `policy.py` is lane E's. In
`ops/doctor.py`, lane E owns the pandoc, convert and speech parts, lane B the rest (C310, C321, C322, C351, C381). A
CONTRACTS line that quotes words a lane changes, and a test or guide line that pins them, move in that lane's own
commit, and lane D cites the sha: setup-feedback.md:1173 and test_deploy_pack.py:6497-6499 for C304 (lane RA);
setup-feedback.md's Purge queue row, CONTRACTS.md:13088 and test_deploy_pack.py:4800-4806 for C346, the row and
data-governance.md:86-88 for C371, data-governance.md's queue and suppression-list stops for C370 and C344, and
setup-feedback.md:876 for C316 (lane B); tests/test_convert_recording.py's docstring for C387 (lane D). Lane D
writes the rest of the guides' words once, after the lanes land: setup-feedback.md's sync-stops.log row (C367), its
:126 plural (C339), its retry sentence (C362), step 1's notes (C315, after C311) and the records page's two ids
(C378, after C371). In CLAUDE.md, `docs/lanes/COMMON.md`, `.github/workflows/ci.yml` and `tests/test_contracts.py`,
lane D (C372, C387, C329, C380); COMMON.md's ruling 28 text stays as written, and ruling 42 amends it.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.203 to §16.207); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: no item needs prompt words this round, and v33 stays: rulings 10 to 45 are code-only so the
operator still pastes v33. No lane edits the README setup prompt block. If a fix seems to need new prompt words,
message the lead before you build it.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.203 to §16.207) begins "Amended
<date>, §16.NN C<n>". A line that records another round's change reads "Amended <date>, §16.NN C<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 29's ids are C101 to C225; round
30's are C301 to C407. The dated-line scans read a line of any date since round 19's W71.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
Every install.sh probe of doctor (state_path_fix, PERSONS_KINDS, the parent fix) runs under the installed
agentsync: it must fall back when that build lacks the name.

Dated-line test: since round 9 K80 the gate reads every "(C<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 30's rows the same way.

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round30/
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

Landing (round 30): land with `scripts/ship-land.sh --lock /tmp/b13-r30-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, reads trunk's CI once without waiting (a run
already ended red stops it there, exit 6, before the gate), rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r30-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6); since round 27 P140 it
also reads trunk's runs once before the gate, so a trunk already red stops the land before a 35-minute gate. If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. CI takes 18 to 52 minutes a run (the slowest, 51:10, in round 29), and a run can wait up to about 48 minutes for a macOS runner first; its limit is 70 (W80, round 21 Y68, round 22 Z67, round 23 A74, round 24 B98, round 25 D83, round 28 I99, round 29 C207, round 30 C372).

Makes things worse: 18 round 30 items carry "a fix makes things worse? True" (C307, C317, C323, C324, C326, C329,
C333, C342, C344, C350, C351, C355, C370, C371, C380, C386, C391, C396). For each, the verifier's corrected fix is
one that does not, and the rulings adopt it (C396 over C377: the shell follows Python, never the reverse; C342: the
narrower form only where a link target read from disk registers whole; C344 and C370: the refusal stays and its way
out is audited, and no id is ever stored in the audit trail): read its "Verifier" part before you build. Not built
by the lead's ruling: C371's optional (4), `--forget` running its own probe, since `--forget` is the audited exit
that erases nothing and `purge --queue` already looks each entry up.
Dated lines: every round 30 dated line reads 2026-10-26, its heading's date (lane D's round 25 test).

Regressions: round 30 found 0 turns and 27 wrongs, 16 of them data integrity. Most sit on round 29's own new paths:
a suppression list or purge queue that lost what a purge recorded, which either published purged content again or
could not be redone (rulings 33 and 35), the records governance/ keeps that do not survive the folder's loss (ruling
34), a setup report that migrated and rewrote the manifest it only reads (ruling 36), a key in a file's name printed
by the secret scan (ruling 37), a labelled mail an older reader let through that stayed published (ruling 38), and a
matter name printed as a hold scope (ruling 39). Before you land a new stop, exit code, wait or person-owned fix,
follow its words on a Mac in the state it is meant for (does doing what it says lead to a clean run, or to the next
stop?) AND test it on one where it must not fire, and grep the other readers of its words (install.sh, both reports,
status, doctor, the guides). A test marked timing is skipped above a load of 4 per core: run any one you change with
AGENTSYNC_TIMING=1 before you land. Round 29 took fourteen and a half hours from the fire to the last land, most of
it the land queue, so pre-gate with --affected and land once. A shared commit in a joint push has one owner: the
other lane drops its copy and never rebases a cherry-pick of it (round 29: a rebased cherry-pick put one CONTRACTS
paragraph on trunk twice). A hook asks before any command holding the word "restore" and before `git push --delete`:
leave the word out of -k filters, and never push a share branch you would have to delete. If the classifier refuses
a command, do not reword it: message the lead.

Lane peers (round 20 Y80): once all five lanes are live, the lead messages every lane the five session ids. Send a
seam note straight to the lane that owns the file, and copy the lead only when it changes another lane's scope.

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r30-<lane>.md`.
