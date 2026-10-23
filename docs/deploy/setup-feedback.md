# Setup feedback: from one Mac's setup to a one-command setup

The goal of the [one-prompt setup](../../README.md#set-up-on-a-new-mac-one-prompt) is a single paste and a single
command. Every question, wait, retry or manual step on a real Mac is a bug against that goal, including one the agent
worked around. This page is the loop that finds those bugs and closes them: a report is produced on the Mac, reviewed
by the person who ran the setup, sent, and triaged into fixes.

## 1. How a report is produced

Three sources feed one file, `~/agent-context/setup-report.md`:

| Source | Written by | What it knows |
|---|---|---|
| `~/agent-context/setup/install.log` | [`scripts/install.sh`](../../scripts/install.sh), on every real run (never `--report-only`) | per run: the setup-prompt-compat number, install.sh commit, arguments; per step (uv, agentsync, launcher, helpers, config, status, first-sync, agent, wait, report; list-folders for a `--list-folders` run): UTC start, seconds, exit status, done / skipped / failed; the run's exit status and total. Since 2026-10-07 two lines end with counts: `list-folders` with `synced=N`, the folders the config already syncs, when it read the config, or, when a config is there and could not be read (since 2026-10-13), `synced=unreadable marks=<why>`, the why one fixed word (`no-tool`, `timeout`, `older:<name>`, `failed` or `failed:<ErrorClass>`), and the list's other fields (`gone=`, `missing=`, `apps=`, `vanished=`, `config_err=`) are in [A Mac that is already set up](#a-mac-that-is-already-set-up); `config` with `kept=N added=M`, the folders synced before the step and the ones it added |
| the machine sections | `agentsync setup-report --out ~/agent-context/setup-report.md`, which `install.sh --report-only` runs (step 3 of the prompt; `install.sh` also writes the report at every exit) | macOS and MDM enrollment, Command Line Tools, shell and terminal, uv, Python, pandoc, OneDrive and Company Portal versions, proxy mode, whether git's config sets a proxy and how many URL rewrites it has (a count, never a host), whether this terminal can list `~/Library/CloudStorage`, `~/.local/bin` on `PATH`; the installer runs; the configuration (counts only); doctor's lines that are not ok and the names of the ok checks; status; the LaunchAgents' runs and last exit codes and the launcher's `TCC_*` lines; the last 40 WARNING/ERROR log entries (repeats in a row shown once), read from the last 64 KiB of each log file |
| `~/agent-context/setup/friction.md` | `install.sh --log-start` and `--log`, which the coding agent runs as it goes, and `--report-only`, which closes the attempt (the attempt starts in step 1's command, so the log survives a session that dies; always appended to, 0600 in a 0700 folder, so a second attempt keeps the first) | per attempt, three header lines from `--log-start 'prompt v32, <agent>'` (`Attempt: <UTC time>`, `Prompt: v32`, `Agent: <tool and model id>`; the version is the one the pasted prompt gave, section 4), then one line per event from `--log '<step>' '<kind>' '<what happened>' '<fix>'`: `<time> \| step <n> \| <kind> \| what happened \| what would have avoided it`, with `<kind>` one of the six in section 4 (a failed command's line gives its exit code; a `prompt` line gives better wording). The steps themselves are not logged: install.log times them. `--log` with other than four values, or a kind not in the list, still logs the event as an `error` line and exits 2. The last line is `<time> \| end \| finished`, which `--report-only` in step 3's command appends just before the report when the attempt has none (until 2026-10-05 a separate end-line command). Every time in these lines is the UTC second of the `install.sh` call that wrote the line, not the moment of the event: lines an agent logs one after another, or that one call writes together, share one time (three lines at one second are not a fault), and the file's order is the order they were logged. Section 4's rules read those times: the session time is first to last, a step 1 `error` starts another session when it is dated more than 10 minutes after the closing line, or when it states `prompt vN, <agent>:` and the open attempt has a header (a `Prompt:`, `Agent:`, `Outcome:` or `Run:` line; the last two appear only in a v4 log), at any gap (the prefix is written only before `--log-start`), or the attempt has no header but stated words of its own and the line is in the strict `<time> \| step 1 \| error \|` form, at any gap (since round 14, Q28), end line or not, and an install.sh run resolves a step 1 `error` only when it started at or after the line's time |

`--log-start` and `--log` also keep `proxy-hosts` beside friction.md (0600, one host a line, in lower case and, where
it differs, as written: the proxy, git proxy and URL rewrite hosts, and the host of each git remote's URL (since
2026-10-18), in force at that call). It is never embedded; both reports use it only to redact.

The machine part is read-only and bounded: no network (doctor runs without its Graph probe), no sudo, no prompts,
no `tmutil`, every command with a timeout and the whole report within a 12 s budget (`TIME_BUDGET_S`); a slow doctor is given at least 20 s and the deadline
moves to make room, so on a busy Mac the report can take about 35 s (the Summary's took line names the moved limit,
e.g. `time limit 12s, moved to 26.2s for a slow doctor`), still inside install.sh's 120 s bound on it. The docs repo's
remote check (a local `git remote` read, made only when doctor printed a `governance.remote` FAIL) is not counted
against the budget. Each section records its own failure
("This section failed: ...") instead of stopping the report, so a Mac where setup broke half-way still produces one.
Since 2026-10-07 the Status section ends with six evidence parts read from the manifest (OCR, Quarantine by
reason, Purge queue, Overlapping sources, Empty cloud folders, Repeat conversions), and Background runs,
Installer and Configuration carry a few more lines; section 4 says how to read them. Apart from the three helper
lines, they are counts, states, seconds, version strings and fixed words: no file name, folder name or quarantine
reason text is in them. The helper lines quote the probe's detail as Doctor's ocr, media and speech lines do: a
failed build's reason (paths removed) is shown, so read those three lines before sharing. They share 3 of the 12 seconds, and a part that runs out prints "not measured (time limit)".
The three programs they start, the OCR, media and speech helpers' `--version`, each have 1.5 seconds of their
own beside those 3, so a helper that hangs costs the other parts nothing: its own line (`- helper:`,
`- media helper:` or `- speech:`) then says `did not answer within 1.5s`.
The manifest is opened read-only and no folder is listed.
The command exits 0 unless `--out` cannot be written; then it prints the report and exits 1. It embeds
`friction.md` with the same redaction, takes the prompt version and agent from the last attempt's header, and works
out the outcome, the run type and the times itself (section 4), so the agent states none of them and re-running it
after the agent's last friction line is always safe. The prompt makes the report its last command, so no friction
line is written after it.

Setup stopped before agentsync was installed (steps 1 and 2)? These reports matter most, and step 3 still produces
one: every failure but the four below goes to its report, `~/src/agent-context-sync/scripts/install.sh --report-only`, which needs no
agentsync (prompt v6 ran it after `agentsync it-request`, whose failure a `;` let through; v7 drafts no IT request).
Without agentsync, `--report-only` writes
`~/agent-context/setup-report.md` from what exists (install.log, `friction.md`, the macOS facts). Review it as in
section 2. If the checkout is missing too (step 1 stopped before the clone), there is no installer and no friction
log, and the agent tells you setup stopped before the code was downloaded. Four stops have no report (three before v19): a new Mac
whose agent is waiting for the folder answer of step 1; a copy of the prompt that step 1 stopped because it
is not the installer's, by either check: the `--version` line, or the `--log-start` error (the prompt says so
since the second v10 text, and names both checks since v11); and a missing checkout, where step 1 stopped before
or during the clone (an Xcode stop on a new Mac, a failed clone); and, since v19, step 1's not-a-checkout stop (git
clone says the folder "already exists and is not an empty directory", git says "not a git repository" or "invalid gitfile format", or main is
used by another worktree) and its ownership stop ("detected dubious ownership"), which run no install.sh command at
all, so they log nothing and write no report, and the Xcode and other-git-failure stops when the folder's
`install.sh --version` does not end with the prompt's own `setup-prompt-compat N`, which log nothing either. Since
v14 the prompt lists the third with the
other two, and skips the `## Not used` section and its heading on the same condition, so such a session that asks
for no change writes nothing to `~/agent-context/setup`. Since v15 step 3 says none of the three has a `## Not used`
section, and the heading rule names that section, not step 3's skip, so a session that asks for no change writes no
heading at the folder wait or the version stop either. Since v19 step 3 lists four such cases, and none of them has a
`## Not used` section. Since v20 the worktree stop (a recovery stops because main "is already used by worktree" or
"is already checked out at" a path) is a stop of its own, with no installer run and no report, and step 3 names it
beside the not-a-checkout and ownership stops. Without an agent, run
step 3's command yourself, and log what happened with `install.sh --log` first (or write `friction.md` in your
own words).

## 2. Review (the person who ran the setup)

Redaction is on by default and consistent within one report, so the same value is always the same placeholder there:

| Placeholder | Replaces |
|---|---|
| `~` | the home folder path |
| `<user>`, `<name>` | the login name and the full name (`id -F`) |
| `<org-N>` | the organization or team after the app's name in a `~/Library/CloudStorage` folder (`OneDrive-<org>`, `OneDrive-SharedLibraries-<org>`, `Dropbox-<team>`; never `Personal`, `Business`, `Work` or a name repeating the app's, such as `Box-Box`), `OneDrive - <org>`, the tenant's and SharePoint host's first label; a single word from a short reviewed list of generic words the report prints (`Link`, `Sync`, `Team`, `Docs`, ...) only in those names, never in the report's sentences; any other one-word organization the report prints (`Status`) everywhere, which makes the report Private only (below) |
| `<link-N>` | in both reports (the agentsync report since 2026-10-18, the shell fallback report since 2026-10-19, B13 round 17 U61), the whole name of a `~/Library/CloudStorage` entry that is a symlink (`OneDrive-Link`), numbered after the organizations and never one of them; in the agentsync report, the Environment line names the folder it points into by that folder's own placeholder (`a symlink into OneDrive-<org-1>`); a link whose name gives an organization already registered is that organization's |
| `<library-N>`, `<folder-N>`, `<source-N>` | SharePoint library names; each configured folder name below `CloudStorage/<provider>/`, and a configured folder elsewhere under the home folder from its project folder down (in the shell fallback report since 2026-10-19); each folder the agentsync report's CloudStorage listing showed, and each folder step 1's `--list-folders` runs printed into install.out (both reports), except a generic name such as `Documents` or a coding agent's own name, which takes none, and, in the agentsync report, a `OneDrive-SharedLibraries-` entry at depth 2, which is a `<library-N>`; every configured source id, except one of agentsync's own words such as `inbox` or `mail` whose folder is not under `~/Library/CloudStorage` (in both reports), and an id that is its cloud folder's name in slug form is that folder's `<folder-N>`, as, in the agentsync report since 2026-10-20 (B13 round 19 W6), is an id that is a project folder's name in slug form (a configured folder outside `~/Library/CloudStorage`, nested or not); since 2026-10-18, numbered after those, a source id the manifest or the purge queue still holds and sources.toml no longer has (in the shell fallback report, one that doctor's `config.vanished_sources` [FAIL] or the sync's refusal quotes), everywhere in the report, the evidence parts and the Configuration section's loads line included, as `(<source-N>, not in the config)` there (by place only where the report cannot read it, below), and, since 2026-10-19, the folder each such source's walk recorded (the agentsync report, B13 round 17 U46; the shell fallback report reads it from doctor's fix and the sync's refusal, `--source-local "<folder>"` or `add-source "<folder>"`, single-quoted for a name holding `!`, round 18 V46, round 19 W34): a cloud folder by its names, as a configured one (a `OneDrive-SharedLibraries-` library a `<library-N>`), any other from its project folder down; in the shell fallback report, a generic name such as `Documents` only as a whole word; and, in the agentsync report since 2026-10-21 (B13 round 22 Z21, Z28, CONTRACTS §16.158) and in the installer's fallback report since 2026-10-22 (B13 round 23 A41, CONTRACTS §16.165), each of agentsync's own folders (`state_dir`, `docs_repo`, `cache_dir`, `log_dir`) that sources.toml moves from its default, as whole words: below the home folder, the path from its first folder that names something, and that folder alone (after the generic home folders, dot folders, agentsync's own containers and a folder named only with report or product words); anywhere else, the whole path; one at its default takes none |
| `<path>` | an item's path, a document's name or any quoted name in one of agentsync's WARNING or ERROR log lines (Recent errors, and the installer output's tail, with a sync's `alarm:` and `error:` lines there); the part of a `--source-local` argument from the first folder the report does not know; in the shell fallback report (install.sh, when agentsync cannot run), every `$'...'` word, which install.log writes for a name with a tab or another control character, a curly apostrophe, an en dash or an accent (under the C locale, any character outside ASCII), and a relative `--source-local` argument from install.log that names no CloudStorage folder |
| `<email-N>`, `<guid-N>`, `<serial>`, `<host>`, `<proxy-N>` | email addresses, GUIDs (client, tenant and volume ids), the serial number, the Mac's host and computer names, proxy hosts (git's config files and the LaunchAgent's proxy included, and, since 2026-10-15, any host other than the installer's public hosts (github.com, astral.sh, pypi.org and their API and download hosts) that a `Could not resolve proxy:`, `Could not resolve host:` or `Failed to connect to <host> port N` message in friction.md names, which need not be a proxy, and the hosts `--log-start` and `--log` kept in `~/agent-context/setup/proxy-hosts`, so a proxy unset since an earlier line stays redacted; a host that is just a common word, such as `proxy` or `wpad`, only where a URL or `host:port` names it, in both reports), the host of each git URL rewrite (`[url "<base>"] insteadOf`, never `pushInsteadOf`; github.com is never replaced) and its path when a word in it is not only a git server's generic words such as `git`, `scm` or `repos`, a tool or kind of host such as `gh`, `homebrew` or `npm`, or the report's own words (one number for the host wherever it is printed), and, since 2026-10-18, the host of each git remote's URL (`[remote "<name>"] url`, and, since 2026-10-21, git's older dotted `[remote.<name>]` such as `[remote.origin]` or `[remote.contoso "docs"]`, in both reports (B13 round 22 Z76, CONTRACTS §16.158 and §16.159); from the same git config files and, since 2026-10-19, the docs repo's own config, read last; B13 round 16 T88, round 17 U93), and, since 2026-10-19, that URL's path when a word in it is not only one of those generic words, as a rewrite's (U15): a `://` URL other than `file://`, or git's `[user@]host:path` form, whose host has a dot and is none of the installer's public hosts, github.com or another GitHub host (`*.github.com`, `*.githubusercontent.com`); a local remote (a relative or absolute path, a bare word) names no host, and the system PAC URL after its scheme |
| `<mdmhost>` | the MDM server's host (`profiles status -type enrollment`), when it has a dot; the whole host, no vendor suffix kept |
| `<tmp>` | this account's temporary folder (`$TMPDIR`, and in the agentsync report any `/var/folders/<x>/<y>` path, with or without `/private`; the shell fallback report names only `$TMPDIR`, in both its forms) |
| `<label>`, `<remote>` | in the agentsync report, since 2026-10-18, the value a policy or `[governance]` error quotes, wherever the report shows that error (Doctor, the installer output's tail, Recent errors): a label id or name after `exclude_label_ids:` or `exclude_label_names:`, and a remote URL prefix after `remote_url_prefixes:`; since 2026-10-19 also the remote URL a governance remote finding quotes (`remote origin (<remote>) exists but [governance] allow_remote ...`, `... is not under a tenant-owned prefix`, push's `refused: remote origin (<remote>) ...`), in both reports, as the shell fallback report now matches all of these too; `agentsync sync` and `agentsync status` still print the value, so the person can find the entry |
| `<value>` | in the agentsync report and the installer's fallback report, since 2026-10-19, any other value a `[governance]` error quotes after `got ` (a `hold_owner`, `hold` or `history_days` of the wrong type, which can carry a name), wherever the report shows that error; `agentsync sync` still prints the value. Since 2026-10-22 the agentsync report also never shows a legal/records hold's reason or owner (free text: a matter, a person's name): a hold reads `hold <source-1> (state) since <time>: reason and owner not shown` (a refusal naming several, `hold all (config) and 1 more hold: reasons and owners not shown`; a reason or owner of several lines is also cut line by line), its release log line `hold <source-1> released (owner not shown)`, each counted as a `<value>`; `agentsync status` and `agentsync hold --list` still print both |
| `<hash-N>`, `<commit-N>` | a hex fingerprint of 16 or more digits (launcher cdhash, content hash); a docs-repo commit id (every abbreviation sharing its first 7 digits is the same placeholder) |
| `(source N)`, `(not in the config, N)` | in the evidence parts and the Configuration section's `source excludes beyond agentsync's own` line only: a configured source id the redaction does not know, by its place in sources.toml (the shell fallback report's excludes line names every source this way); a source id that is in the manifest or the purge queue and no longer in sources.toml, since 2026-10-19 only where the report could not read it as a placeholder (an unreadable manifest, a word the report prints): otherwise it reads `(<source-N>, not in the config)` (or `<folder-N>` in place of `<source-N>`), the placeholder Doctor and the install.out tail print for it, so one source has one name (the evidence parts, and the Configuration section's `loads: yes, but a sync refuses it` line, or `... step 2's sync refused it` line, which names such a source the same way) |

When a folder, library or organization name redacted everywhere is also a word the report prints (a folder
`Status`), its placeholder in the report's own text reads back as that word, so the Redaction section says so
(`Private only: ...`) and its `Send it:` line sends the report privately, never as a public issue; such a report
ends with no issue link. A configured folder or library with a name every OneDrive or Google Drive has (`Documents`,
`Downloads`, `Desktop`) is the exception: it is still redacted, but reading its placeholder back gives nothing away,
so it does not make the report private (in either report). So, since 2026-10-21, is one named only with
coding-agent product words (`Copilot`, `Cursor`, `GitHub`; the words are claude, codex, copilot, cursor, gemini and
github, [CONTRACTS §16.153](../design/CONTRACTS.md) Y7, Y72): it is still redacted, but it does not make either
report private. The agentsync report is Private only too when its
listing of `~/Library/CloudStorage` for redaction did not finish and install.out holds no folder list from step 1
(`Private only: the listing of ~/Library/CloudStorage for redaction did not finish ...`): a folder name may then
show in clear. A listing that finishes before the report reaches its friction section (it waits at most half a second more
there) is still used: the report is then not private
for this reason, its Environment line reads `- redaction: the CloudStorage listing finished after 3.0s and was used
for redaction`, and the Configuration line's count of folders named only with coding-agent words still says `could
not list ~/Library/CloudStorage`. The shell fallback report never lists CloudStorage, so it never prints this line.

The "Redaction" section gives the count per kind: a numbered placeholder as often as the report shows it, the
unnumbered ones (`~`, `<user>`, `<name>`, `<serial>`, `<host>`, `<mdmhost>`, `<tmp>`) as often as a value was
replaced. The agent's `friction.md` is redacted with the same mapping when the report embeds it, and since setup
prompt v11 the prompt has the agent name a folder, a file, a person, its organization or a customer in a friction
line by its role in plain words, never by its real name, a short form of it or a placeholder (lines from older
sessions stay as written: the log is append-only). A name matches in either Unicode form or without its accents, so an
accented folder name is the same placeholder however it was typed. Before sending, read the whole file once. Redaction only covers
values agentsync knows about, so a project or customer name it has never seen (inside a log line or in the agent's
words) is yours to remove. The CLI always redacts.

## 3. Send

- **Public:** a new [Setup report issue](https://github.com/renchris/agent-context-sync/issues/new?template=setup-report.yml)
  ([form](../../.github/ISSUE_TEMPLATE/setup-report.yml)). Step 3's `install.sh --report-only` prints, just before
  its `NEXT:` line (as `agentsync setup-report --out` does as its last line),
  `issue link (review the report first): <link>`, a link that opens the form with the Summary's five values
  already in their fields (the table below). Paste the whole redacted file into
  the Setup report field and tick the review box. Opened another way, copy the five values from the Summary. The
  form shows the report as a code block. The report fences command output with `~~~`, which the form's backtick
  fence does not break. Keep the agent's additions to `~~~` too.
  The shell fallback report ends its Redaction part on the same one-line `Send it:` advice as the agentsync
  report, then the link.
  Its Summary has an `- outcome:` line. That line is computed only for an attempt that ran no `install.sh`,
  whose lines are all step 1's and whose first line is a step 1 error stating the prompt version and the agent.
  Then the line also states the run type as the agentsync report computes it (`Sandbox` when HOME is under a
  temporary folder, otherwise `Real Mac`, and Sandbox with simulated launchd when the attempt's last run says
  `launchd=simulated`), and the link fills Outcome, Run type, Prompt version and Agent tool and model (Agent only
  when the line names one), with the run type in its title. When the stated version is newer than this
  installer's, the line's bracket also says so, naming no cause. A copy `--log-start` stopped is computed the same
  way, with the version and agent from the attempt's `Prompt:` and `Agent:` lines (`from the attempt's Prompt
  line`; an older copy's bracket says why it was stopped). Otherwise the line says no outcome is computed,
  and the link fills Run type from the `- run:` line below it, which computes it the same way, and Prompt version
  and Agent tool and model where the friction log's latest attempt states them (its `Prompt:` and `Agent:` header
  lines, or the words its step 1 error line states); the Send it line names the fields it fills. Choose the
  Outcome from the friction log's step lines (when the attempt has none, the line says so, and names the Installer
  section when the attempt has an install run), and take any field the link leaves empty from the friction log. In both cases the shell report has no `Loop:` line, and Loop stage stays empty.
  Not for a report with a `Private only:` line (section 2): neither `install.sh` nor `agentsync setup-report --out`
  prints the issue link for it, neither report has one, and `--report-only`'s NEXT names only
  bring-back.md. Send that one privately.
- **Privately (no public post needed):** copy `~/agent-context/bring-back.md` back to the machine you administer
  this setup from (AirDrop, a USB stick, a shared folder you already use). It is the one file the prompt's last
  step names: section 1 is the redacted setup report, section 2 the agent's fix request and section 3 the patches
  of any local work step 1 kept, one per commit. Sections 2 and 3 are not redacted and may name a real folder,
  file or person, so review them first, and never paste this file into the public form: the public route takes
  `setup-report.md` alone. A person's own kept commit keeps its `From:` line (their git name and email, whose
  domain can name the organization, or a name and address git filled in from the Mac's account) and its message.
  Section 2 is the whole file, every session's part in the order written: `fix-request.md` is kept from
  session to session and nothing in it is deleted. Since prompt v9 each session writes under a heading of its
  own, which names the prompt's version and the date and time, and rewrites only its own part. Since prompt v11
  that includes a session that asks for no change: its `## Not used` section goes under its own heading (since
  v14, unless the checkout is missing, when the session writes neither; since v15, in any of step 3's three
  cases with no report; since v19, in any of step 3's four). Before
  v11 such a session could add its `## Not used` with no heading of its own; then the lines from the first
  difference down are that later session's, not the part whose heading is above them. Two sessions
  of one day are told apart by the time. Do not start at the last session heading: the Mac cannot know what
  was copied back, and a session whose file nobody copied leaves its part above the next session's heading.
  So triage compares section 2 with section 2 of the last bring-back file received from that Mac, finds the
  first line that differs, and reads from the session heading above it down to the end. With no earlier file
  from that Mac, it reads all of section 2 (K23). Each section holds its file inside a tilde fence, every byte
  kept, so the file's own headings stay inside it.
  A placeholder in section 2 is the one the report on the Mac gave when that part was written, which need not
  be section 1's numbering. A report numbers values in the order it registers them (configured CloudStorage
  folders, then the CloudStorage listing, then the folders step 1's `--list-folders` runs printed into
  install.out, then configured folders elsewhere, then, in both reports, agentsync's own folders that
  sources.toml moves from their defaults, and last a listing that finished after its 3-second bound but in
  time for the report, so no earlier number moves; the shell fallback report: that run's `--source-local` folders,
  then sources.toml, then install.log, then install.out's step 1 list), so an added folder, a listing past its
  3-second bound (step 1's list then stands in its place) or a fallback report gives a folder another number,
  between two sessions or between step 2's and step 3's report of one. Since prompt v11 each name carries a role that tells it apart: identify the folder
  by that role, not by the placeholder's number. A part written before v11 may give a placeholder alone: trust
  its number only against section 1 of the file it first arrived in (K34).
  Both reports also say in counts that step 1 kept local work, under Run metadata in the agentsync report and at
  the end of the shell fallback's Summary: `- local work kept by setup prompt step 1: N patch files in the setup folder's local-work, newest written
  <time> (no patch text is in this report: each patch goes once to the private bring-back.md section 3; any patch
  an earlier report already carried is listed there, by its hash when it can be read); M local-work branches in the checkout` (`1 patch file`, `1 local-work branch` for one). Patches
  this account cannot read are not in bring-back.md, so after `local-work` the line then adds `, U of them unreadable
  and not in bring-back.md (chmod u+r, then run ~/src/agent-context-sync/scripts/install.sh --report-only again)`
  (B13 round 14 Q29); a later report sends such a patch once it is readable. A patch an earlier attempt's report
  carried is not counted there: section 3 lists it as not repeated, with no hash while it cannot be read, and a
  rerun would send nothing (B13 round 16 T24). It counts
  the patches at the top of `local-work` (the files section 3 reads), whatever the checkout is, and the local-work
  branches that hold a commit of their own (one at main's or origin/main's commit, or at any commit origin/main's
  reflog names, is not counted unless a patch is named for it), read from the checkout's common dir, so a checkout
  whose `.git` is a file (a clone whose git dir is kept apart, a linked worktree) is counted too; with branches
  and no patch it reads `- local-work branches in the checkout: M with commits of their own, no patch file in the
  setup folder's local-work (moved, deleted or never written; those commits are not in bring-back.md, whose section 3 says how to send them)` (`1 with commits of its own` for one; B13 round 17 U38)
  (the pointer since B13 round 16 T82: the issue path sends this report alone), and
  section 3 reads `none: no patch file is in ~/agent-context/setup/local-work, but M local-work branches in
  ~/src/agent-context-sync hold commits this file does not carry` (`1 local-work branch ... holds` for one; B13
  round 13 O23, round 16 T27). Those commits exist only on
  the branch. Since B13 round 14 Q30 that line goes on with the remedy, naming no branch (`; to send them, for each
  branch ... run git -C ~/src/agent-context-sync -c format.filenameMaxLength=128 format-patch -q
  --suffix=".<branch>.patch" origin/main..<branch> -o ~/agent-context/setup/local-work (a branch that writes
  nothing holds no new commit), then run ~/src/agent-context-sync/scripts/install.sh --report-only again`), and the file's header says section 3 sends no patch and that its line says how to add them; ask the
  person for that only if the file came back without it. A public report shows that the work exists, never the
  patches.
  Section 3 holds the local work kept since an earlier attempt reached its report. A patch from before that is
  not sent again: one `not repeated:` line names the earlier report that held it, `not repeated: a patch file an
  earlier attempt's report held (<time>)` (read from `local-work/.sent`, which every report that embeds a patch
  adds it to, so also after an attempt that stopped at step 1 and wrote no end line), or, for a patch that record
  lacks, the time it was last written and that report's time, `not repeated: a patch file last written <time>, at
  or before an earlier attempt's report (<time>)` (B13 round 17 U39). While this account can read the patch, the
  line then gives its line count and the first 12 hex digits of its SHA-256, and says it is still in
  `~/agent-context/setup/local-work`. Compare that hash with
  the patch you hold (`shasum -a 256`). If you hold none, that work never arrived: the Mac cannot know what was
  copied back. The line names no file (a patch's file name carries its commit's subject), so ask the person to run
  `shasum -a 256 ~/agent-context/setup/local-work/*.patch` on that Mac (the folder the line's `still in` names, if
  it is another) and to send privately the patch whose hash starts with the line's 12 digits. A line ending `this
  account cannot read it now, so no line count or hash; still in ...` has no hash to match (B13 round 17 U82). Ask
  the person to make that patch readable (`chmod u+r ~/agent-context/setup/local-work/*.patch`; the report gives
  no remedy for a patch an earlier report carried, and the command works only where their account owns the file)
  and to run
  `~/src/agent-context-sync/scripts/install.sh --report-only` again: that rerun sends nothing new, since an earlier
  report carried the patch, but its line then gives the line count and the hash, and you go on as above. A patch
  no report sent yet that this account cannot read has a third line, `not sent: 1 patch file in
  ~/agent-context/setup/local-work this account cannot read; make it readable (chmod u+r) and run
  ~/src/agent-context-sync/scripts/install.sh --report-only again` (`N patch files ... make them readable` for more):
  the patch is not in the file, and the line names no file, for the same reason. Ask the person for that chmod and
  that rerun, which works only where their account owns the file; the readable report sends the patch once
  (B13 round 18 V25). The header's sentence on each patch's author is there only
  when section 3 sends a patch; when it sends none because each was in an earlier report, the header says so (`its one patch` when there is one).
  Whoever looks after agentsync for your team triages it there with this page, exactly like an issue; that needs
  no contact with the agentsync maintainer.
  To hand a report to the maintainer without a public post, the optional route is the contact on the maintainer's
  GitHub profile ([github.com/renchris](https://github.com/renchris)); no email address is published in this
  repository. Keep the report redacted. A privately
  sent report is filed as a public issue, with the organization removed, only if the person agrees. The issue form's
  "Send a setup report privately" link points here.

The form's field ids are stable, because the pre-filled link names them
(`https://github.com/renchris/agent-context-sync/issues/new?template=setup-report.yml&title=...&outcome=...&run_type=...&prompt_version=...&agent=...&loop_stage=...`,
each value URL-encoded; `title` is the issue title: "Setup report: ", then the outcome, run type and prompt
version joined by ` · `. A failed step is named in the title by the number and name the attempt's own prompt gives
it (a v7 or later attempt's step 2 is `install and start`, its step 3 `sync loop and report`; a v5 attempt uses its
own six steps, so its step 3 reads `Failed at step 3 (install and start)`), while the `outcome` field takes the
form's option below, the same words where the form has them and otherwise the option for the same work, such as
`Failed at step 2 (install and start)`, so the link always fills a valid option. A stopped attempt's title reads `Stopped at step 1 (preflight)` and its field `Failed at step 1
(preflight)`):

| Field id | Type | Value |
|---|---|---|
| `outcome` | dropdown | the computed outcome, exactly one option: `Fully one command`, `Worked with help`, or `Failed at step <n> (<step title>)` with the README step's title (`preflight`, `install and start`, `IT request and report`, `sync loop and report`, `finish`); setup prompt v7 and later name step 3 `sync loop and report`, v6 `IT request and report`, so a count of step 3 failures adds both |
| `run_type` | dropdown | the computed run type: `Real Mac`, `Sandbox` or `Sandbox with simulated launchd` |
| `prompt_version` | input | the prompt version, `v32` (an attempt an older copy started reports that copy's: `v31`, `v30`, `v29`, `v28`, `v27`, `v26`, `v25`, `v24`, `v23`, `v22`, `v21`, `v20`, `v19`, `v18`, `v17`, `v16`, `v15`, `v14`, `v13`, `v12`, `v11`, `v10`, `v9`, `v8`, `v7`, `v6`, `v5`) |
| `agent` | input | the attempt header's `Agent:` value (for an attempt with no header, the agent its step 1 error line names, below) |
| `loop_stage` | input | the stage on the Summary's `Loop:` line (below); empty for a report from before that line |
| `report` | textarea | not in the link (too long for a URL): pasted |
| `review` | checkboxes | ticked by the person |

GitHub documents the field id as the key for pre-filling a form field from the URL. It does not document pre-filling
a dropdown, so if Outcome or Run type opens empty, choose the option the Summary names.

Nothing is sent automatically. Neither the prompt nor agentsync uploads, emails or posts the report.

## 4. Triage (maintainer)

Read in the report's own order: the Summary (computed outcome and run type, prompt version, agent, times), the
friction lines, then the machine sections that explain them (Installer timings for slow or failed steps, Doctor
`[FAIL]`/`[warn]`, Background runs' last exit code and `TCC_*` lines, Recent errors). Times the report or agentsync
writes are UTC (`...Z`). agentsync writes its log lines' times in UTC too since 2026-10-14; in an older log, the
time was local in the log file, and Recent errors and the install.out tail show it converted to UTC with the Mac's
zone (also the time install.sh quotes in `warning: agentsync setup-report failed (...)`), so search such a log by
its text, not its time. A friction line is referred to
as `F<n>`, where n is its line number in the embedded friction.md (the Agent friction log section gives each
attempt's line range).

### Friction kinds

The agent gives every line one kind from a closed list, so the report counts turns from the kind column and never
guesses from the wording. Prompt v6 logs only what the prompt did not foresee: not the steps (install.log times
them), not the folder question and not the Allow clicks it announces. The `Attempt:` header and the
`end \| finished` line come from `install.sh --log-start` and `--report-only` and are not kinds. A v5 log's `start` and
`end` step lines are still read, for its step times:

| Kind | Meaning | Counts against "fully one command" | Usual fix class |
|---|---|---|---|
| `question` | in the chat, the agent asked the person something other than which folders to sync, or the person gave a go-ahead or an answer other than the folder answer, word of a click or word of the sign-in step 1 waits for (since v11, the sign-in since v21; an older log may record a go-ahead as a `deviation`, which is not counted) | yes (v6 never logs the folder question; in a v5 log, the first question in its step 2 is that question) | prompt wording; for a go-ahead, the step it allowed (installer automation or agentsync code) |
| `click` | the person clicked something other than an Allow or (since v21) the sign-in the prompt announced | yes (v6 never logs the announced Allow clicks, section 5, and v21 not the sign-in step 1 waits for on a Mac that already syncs folders; in a v5 log, the first click in its steps 2 and 3 are those) | installer automation, or unavoidable OS step |
| `approval` | the agent's tool asked the person to approve a command (only when the agent can see it) | yes: the README's pre-allow rules avoid it | prompt wording (a pre-allow list) |
| `deviation` | the agent did something the prompt did not say, worked around a problem, or stopped at step 1 to wait for the folder answer | no: agent friction, counted on its own Summary line | prompt wording or installer automation |
| `error` | a command failed; the line gives its exit code | only when it stopped the run (below); otherwise agent friction | installer automation or agentsync code |
| `prompt` | the prompt was wrong or unclear; the line gives better wording | no: agent friction | prompt wording |

A tool approval the agent cannot see is not in the log. When none is logged, the report prints approvals as "not
observable", not as zero, in two cases: the agent's tool does not tell it when it asks the person to approve a
command (Claude Code, GitHub Copilot CLI), or the attempt names no agent (no `Agent:` value), so nothing can vouch
for zero.

### The computed outcome

setup-report works the outcome out from what the person saw and did, never from the agent's words, and the
form's Outcome copies it (the form has no stopped option, so it records a stopped attempt as step 1's failure). It
reads the last attempt in friction.md and that attempt's last install run in
install.log (a `--list-folders` run is not an install run):

- **Fully one command**: the last install run exited 0, and the attempt has no `question` line and no `click`
  line (prompt v6 logs neither the folder question in step 1 nor the Allow clicks it announces in steps 1 and 2,
  and v7 neither that question nor the one Allow click it announces in step 1, so every logged one is beyond
  them), no `approval` line, no error that stopped the run, and doctor shows no
  `[FAIL]`; and step 1's list did not log `synced=unreadable` before an install run that kept folders (section
  "A Mac that is already set up"). An attempt with no event line at all is the expected case.

  Since prompt v7 the install command must also have worked the first time. An install run that failed before
  the attempt's first install run that exited 0 is a retry, so the outcome is "worked with help" and the
  Summary says `1 earlier install run of this attempt exited 1`. That run is then the first line under "Items
  that were not one command", with the step it failed at. Three kinds of run do not count:
  - a run after the first one that exited 0 (the operator's own `--confirm-install-agent` run, which exits 3
    while macOS waits for a click, comes after the setup worked);
  - a run the agent's tool stopped, which has no end line or a signal's exit (129, 130, 143): the prompt calls
    running the command again safe;
  - a `--list-folders` run, which is step 1.

  install.log does not say why a run failed. So a run that failed only because macOS held a listing for a
  click counts as well: the click came after the install command had failed, and it cost a second run. A v5
  or v6 attempt is judged as before, since those prompts announce the launcher's Allow click in the install
  step.
- **Worked with help**: the last install run exited 0, but one of those conditions fails; the Summary says which
  (for the folder list, `the folder question: step 1's list could not read the 2 folders this Mac already
  syncs`).
  Without a friction log, or without the attempt's `Attempt:` line (from `install.sh --log-start`), the human
  turns are unknown, so the outcome is at best this one.
- **Failed at step N**: the last install run did not exit 0 or has no end line (killed, or still running); then N
  is 2, the step that runs it. When it exited 0 but an `error` line stopped the run, N is that line's step: an
  error logged in step 1 or 2 that no later install.sh run ending 0 resolved (for step 1 any run, a
  `--list-folders` re-run included, but not for install.sh's own `--log-start` stop of a copy of the prompt; for step
  2 an install run), with no later step logged before the report. That stop is resolved by no run (CONTRACTS §16.147,
  X19; §16.152, Y17: the stop writes its header, error and end lines with one time, so a run stamped that same second
  is a stand-alone one too), so a later install.sh or `--list-folders` run leaves the attempt failed at step 1; every other step 1 error is still judged
  with the runs after the end line (round 5, F37). Step 2
  is the one install.sh command (in v6 too: its exit 0 covers the background sync v6 could ask for), so any install
  run of the attempt that ended 0 resolves a step 2 error, whenever the line was logged: a step 2 `error` line
  written after install.sh exited 0 is agent friction. If no install run happened, N is the step of the last
  `error` line, failing that the last step logged (unless the attempt only stopped at step 1, below), and failing
  that 1 when only `--list-folders` ran (for an earlier attempt whose last list worked, see the U35 exception
  below).
- **Stopped at step 1**: no install run, no `error` line and no `end | finished` line; at least one step 1
  `deviation`; and every other line the attempt logged is a step 1 `question`, `click`, `approval` or `prompt`
  line (since round 7: step 1 may log an approval or a go-ahead before it stops). The session stopped where step 1
  says to wait for the folder answer and may still be waiting, so it did not fail. The outcome reads `stopped at
  step 1 (waiting for the folder answer)` when a deviation's words mention the folder answer or waiting (the
  deviations' words only), and `stopped at step 1 (a deviation, no install run and no error)` otherwise; the
  Summary's reason names the other lines (`step 1 logged a deviation (F5), 1 approval and nothing else`). It shows
  so in the Summary's outcome line, in its `earlier:` clause and in the friction section's attempt lines. The
  form's Outcome records it as `Failed at step 1 (preflight)`. An end line, an error, a `start` line, a line of a
  later step or an untyped line keeps the attempt failed (at step 1 or the step logged); an install run gives the
  outcome of the bullets above. One exception (since round 8, J9): an attempt whose step 1 error is install.sh's
  `--log-start` stop of a copy newer than the installer (`this checkout is older than the prompt`) is stopped, not failed, though it holds that error and its end line, when the next attempt
  names the stopping installer's own version (not the report's build), names the same agent, and starts within
  120 s of the stop. That is the agent changing the value back and running step 1's command again, as the stop
  message tells it to. It reads `stopped at step 1 (rerun: --log-start said v61; the next attempt, 18s later, said
  v18)` (versions and seconds only), in the Summary's `earlier:` clause and the friction section's attempt lines.
  Such an attempt is never the latest one, so the Summary's outcome line, the title and the form's Outcome never
  show it. A copy fetched again from the README and pasted into a new session stays `failed at step 1`. Since
  2026-10-14 (round 9, K11) the stop of an older copy (`the pasted copy is not the current one`) is never read as a
  rerun, since a person can paste a fresh copy inside the 120 s (a rehearsal did in 23 s): it stays failed and, inside
  the window, reads `failed at step 1 (an older copy, or a changed --log-start value; the next attempt, 23s later,
  said v18)`.
  Since round 17 (U35), an earlier attempt also reads `stopped at step 1 (after its folder list, with no report)`,
  with no deviation, when all of these hold: it has an `Attempt:` line, no event line, no `end | finished` line and
  no v4 line; its only install.sh runs are `--list-folders`; and the last of them listed the folders (rc 0,
  `result=done`). That is step 1's wait for the folder answer, or a session closed there. It shows in the Summary's
  `earlier:` clause and the friction section's attempt lines. The latest attempt is never read this way: its
  Summary outcome, title, form Outcome and issue label stay `failed at step 1`, since a stuck config's list also
  exits 0 and tells the agent to go on to step 2. An attempt with an end line (`--report-only` ran) or a list that
  failed keeps `failed at step 1` (B13 round 18 V78).

A line logged after an attempt's `end | finished` line cannot have stopped it, so it never changes that
attempt's outcome. One kind of late line is another session: a step 1 `error` dated more than 10 minutes after
the closing line, or, since round 5 (F23), one that starts with `prompt vN, <agent>: ` after a headed attempt, at
any gap (2 minutes after the end line is enough). Its session's step 1 stopped before `install.sh --log-start` wrote a header (since prompt v11
the Xcode and git stops log that error before the report; since v19 only when the folder's installer is the
prompt's version, and otherwise they log nothing and write no report), so the report
lists it, and the lines after it, as an attempt of its own with "no Attempt: line, since it was logged after the
previous attempt finished". The unbroken run of step 1 lines just before it, at or after the closing line, moves with it
at any gap: the finished session runs nothing after its report. When it is the last attempt it is the one the Summary judges: "failed at step 1". Since
prompt v12 that line's text starts with `prompt vN, <agent>: ` (`prompt v32, <agent>: ` now), the words step 1 gives `--log-start`, so the
Summary's `prompt:` and `agent:`, the attempt's line in the friction log section and the issue link's
`prompt_version` and `agent` name them (the version marked `(from the step 1 error line)`; in the attempt's line
`prompt v32, from the step 1 error line`), and the item shows
the rest of the line. Without those words, as in a v11 log, they say `not stated`. Those words also split an
attempt that has no end line. Since round 5 (F23) a step 1 `error` that states them always starts its own attempt
when the open attempt has a header (a `Prompt:`, `Agent:`, `Outcome:` or `Run:` line; the last two appear only in
a v4 log), whatever the gap and even in the same words, because a session that wrote a header never writes the
prefix. Since round 14 (Q28) it also does so after a header-less attempt that already has stated words, at any gap
and even in the same words, when the line is in install.sh's strict `<time> | step 1 | error |` form: the prefix is
written once per session, so a second stated line is another session's. Such an attempt is listed as "no Attempt: line, since another session logged it after the previous
attempt, which has no end line". When such an error is the log's first line its words are read too, and the attempt
is "no Attempt: line, since its session stopped before `install.sh --log-start`". Since round 10 (L26) an attempt
with no header and no words yet reads them from its first step 1 error that states them, wherever it sits: a
stopping session may log a deviation or a prompt line first. When a step 1 error opens such an attempt, the unbroken
run of step 1 lines just before it, dated at or after the earlier attempt's end line, is that session's and moves
with it, whatever the gap (since round 12, N23: the finished session runs nothing after its report); since round 13
(O24) the same run moves to the next `Attempt:` line too, and that attempt still begins at its `Attempt:` time (two
`Attempt:` lines with no end line between them leave it where it was logged; install.sh's friction log check reads
the same boundary); a late line that neither such an error nor an `Attempt:` line follows stays where it is. A step
1 error without those words and a line of another step or kind are never read that way. Any other late line (one of
another step, or a step 1 line that such a line separates from the next session's error or `Attempt:` line) stays
in its attempt (run `install.sh --report-only` again to put it in the report). A report
written more than 10 minutes after the last attempt's end line, with no run in install.log since (`--report-only`
is not logged there), says so in a note: a session whose step 1 stopped before `--log-start` and logged nothing is
not in it, and the person who ran it says which stop it was.

`deviation` and `prompt` lines, and `error` lines that did not stop the run, are agent friction: the Summary counts
them on their own "agent friction" line with their F-ids, and they do not change the outcome, except that an
attempt that only stopped at step 1 (above) reads 'stopped' rather than 'failed'. They are still
triaged like every other line (below).
- **Failed at step 4 (finish)** is never computed. Only the person sees whether the closing lines came, so
  they pick it by hand. With no friction log and no install.sh run, the outcome is "unknown", and the person picks
  the option too.

An attempt whose header says `Prompt: v5` is judged with v5's six steps: its folder question is the first
question in its step 2, its Allow clicks the first click in its steps 2 and 3, a later `end` line of its step
resolves an error, and N is 3 for the installer. The form has v6's four step options plus v7's step 3 by its own
name (`Failed at step 3 (sync loop and report)`). A v5 attempt's step maps to the v6 option for the same work: 1
and 2 to 1, 3 to 2, 4 and 5 to 3, and 6 to 4. A `Prompt: v7` attempt has three steps, numbered as v6's first three
(step 3 runs the sync loop, then the report; there is no IT request step). Its step 1 and step 3 each take the
form's option with the same number and name, and its step 2 (`install and start`, the prompt's own name since v7)
takes `Failed at step 2 (install and start)`.
A `Prompt: v8`, `Prompt: v9`, `Prompt: v10`, `Prompt: v11`, `Prompt: v12`, `Prompt: v13`, `Prompt: v14`, `Prompt: v15`, `Prompt: v16`, `Prompt: v17`, `Prompt: v18`, `Prompt: v19`, `Prompt: v20`, `Prompt: v21`, `Prompt: v22`, `Prompt: v23`, `Prompt: v24`, `Prompt: v25`, `Prompt: v26`, `Prompt: v27`, `Prompt: v28`, `Prompt: v29`, `Prompt: v30`, `Prompt: v31` or `Prompt: v32` attempt is judged by the same rules: none moved a step. So is every
later version until one moves a step, since the version changes with every edit of the prompt's text (below) and
the rules only with its steps.

The outcome judges only the install. How far the loop got after it is the Summary's `Loop:` line (KISS K16b):
`Loop: <stage>; NEXT: <the loop's current NEXT line>`. The stage is `installed` (no sync ran), `synced`,
`baseline drafted`, `baseline confirmed`, `before run`, `topics N` (N curated pages) or `after run`, computed from
status's loop line; the NEXT line is the one `agentsync status` would print, with every path cut to its last part
except agentsync's own fixed `~/.local/bin/agentsync`, which holds no name, matches doctor's fixes and the status
lines in the same report, and stays runnable when ~/.local/bin is not on PATH; the draft baseline's folder reads `the docs repo's _eval folder`,
the docs repo's AGENTS.md (named by rules 4, 6 and 8) reads `the docs repo's AGENTS.md`, its _sync/STATE.md (named
by the waits for empty and unlisted folders) reads `the docs repo's _sync/STATE.md`, and the config file reads `the config file <its file name>` (`the config file sources.toml` for the default config), never its path. So a setup that synced and stopped before drafting the baseline
questions reads "fully one command" with `Loop: synced; NEXT: draft the baseline questions: ...`. When
something waits on the person, the line ends with every `WAITING ON YOU:` line the loop printed: first the one
the loop stopped on, then the others in the loop's order; when the command printed that one after others, a
fixed clause before them says so ("also waiting, which the command prints before it:", or "also waiting
(the command prints the first K of these before it):" when it printed only some of them first). The one it
stopped on is the draft baseline's when the NEXT line says "(see the WAITING ON YOU line)" or, over two or
more waits, "(see the WAITING ON YOU lines)", else a listing macOS holds for an Allow click, else the
first. The loop prints its queued purges first, so shown by place they hid the wait the NEXT line points at.
When files are still to be read again, the loop's note about them
follows (two notes when the last sync did not get to one of the sources): `note: sync again: N files ...`
means the report was written before the one-time re-read finished (section "The evidence parts" says what that
takes). The line holds no path (in an exclude line's list, agentsync's own globs show as written and every other
entry as `"<path>"`, or `"/<path>/"` for a folder anchored at the source root, in the agentsync report and, since
round 4, in the installer's shell fallback report; a list that is cut or cannot be parsed shows the whole list as
`exclude = [<path>]`, in both), so redaction is unchanged; its stage prefills the form's Loop stage.

The run type is computed too:

- **Sandbox with simulated launchd**: the last install.sh run says `launchd=simulated`.
- **Sandbox**: HOME, or what it resolves to, is under `/tmp`, `/private/tmp`, `/var/folders` or
  `/private/var/folders`.
- **Real Mac**: neither of the above.

The agent's own opinion is not asked for.

The same test guards the skill. A run whose HOME is under one of those folders writes its `agentsync-docs`
skill under that HOME only, and not under a `CLAUDE_CONFIG_DIR` that is outside them: a sandbox run that
kept a real session's config folder would otherwise put a skill there that names a docs repo in a temporary
folder ([CONTRACTS §16.30](../design/CONTRACTS.md), "A temporary home writes no skill into a real
`CLAUDE_CONFIG_DIR`"). A `CLAUDE_CONFIG_DIR` that is itself under a temporary folder is still written. So a
sandbox report whose status says the skill is current counts the home copy alone.

### The prompt version

The prompt's first line carries its version, and the version changes with every change of its text, a reworded
sentence included. A saved copy is therefore always known by its number. Twice a copy saved before an edit was
pasted again, ran its old wording against the new installer, and nobody could tell: the edit had kept the
version, and the installer wrote its own number into the log.

- Step 1 hands the prompt's version to the installer: `install.sh --log-start 'prompt v32, <agent>'`. The
  `Prompt:` line of the attempt is that version.
- When it is not the installer's own number, the pasted copy is not the README's: `--log-start` logs the attempt
  with the version the copy gave (`v7 or older` for a copy from before v8, which gives none), a `step 1 |
  error` line and the attempt's end line, tells the agent to stop and what to tell the person, and exits 2. A
  copy older than the installer, or one that gives no version, is told to copy the prompt again from
  `README.md` on the main branch; a newer copy is told what the installer finds about its own checkout (the
  last item below). The exit 2 means step 1's command stops before it lists the folders. A v8
  or later copy checks the same from its side: `install.sh --version` must end with exactly its own number.
- The version leads the value, and is read in the shape the agent gave it (`Prompt v32: <agent>` and `prompt v32 -
  <agent>` are v32). The stop message shows no value that passes; its last sentence tells a current copy whose
  agent changed the value to run step 1's command again as the prompt writes it. When the changed value named a
  version newer than the installer's (the stop reads `this checkout is older than the prompt`), an attempt the
  session ran again so reads stopped, not failed (the rerun exception under "Stopped at step 1"). After the stop of
  an older value, or of one that gives no version (`the pasted copy is not the current one`), it stays failed and
  its outcome names both readings, since a fresh copy pasted inside the window looks the same (round 9, K11).
- A copy whose step 3 does not name this stop as one with no report (v9 and older, and a v10 copy taken before its
  step 3 named it) still goes on to its own report; a later copy stops without one, as the installer's stop
  message says. That report's Summary says
  `prompt: v7 or older (older than the installer's v32: the pasted copy was not the current README, or the --log-start value was changed)`
  (the second reading, as the stop line names it, since 2026-10-14), its outcome
  is "failed at step 1", and `install.sh --report-only` ends on a `NEXT:` line that says not to bring that
  report back and to copy the prompt again. That run writes the report to `~/agent-context/setup/stopped-copy-report.md`,
  whose first line says not to send it, so `setup-report.md` and `bring-back.md` keep the bytes of the last attempt
  that was not stopped (the `NEXT:` names `bring-back.md` as untouched only where one exists); it prints no `bring
  back:` or issue link line, and the file itself ends on `Not for sending: see the note at the top of this report.`
  in place of the `Send it:` line and the issue link. The next report of an attempt that was not stopped deletes
  it. Since 2026-10-20 (CONTRACTS §16.147, X19), any other run that writes a report after such an attempt, with no
  `--log-start` since (a plain `install.sh` the stopped copy's Doctor asked for, say), routes it the same way: its
  own file, no bring-back file, no `bring back:` or issue link line. It keeps its own `NEXT:` and adds (after a `; `
  when there is one) `the last setup attempt was a copy of the prompt this installer stopped at step 1; its report is not to be sent, so paste the current prompt into a new session to report a setup`.
  Triage such a report as `known K18`, not as a failed setup.
- That report counts no folder question. The copy was stopped before the folder list, so nobody was asked
  and no Allow click was announced: the Summary says `human turns: 0 (0 questions; ...)` and `expected turns:
  none (the installer stopped this copy of the prompt in step 1, before the folder list: no folder question
  and no Allow click)`. Before 2026-10-07 it said `1 question` and `the folder question (step 1; not logged)`
  there (K28). A question the agent logged is still counted, and so is a folder list the agent ran all the same,
  whenever it started: that list puts the attempt back under the usual rules. A plain `install.sh` run from the
  attempt's end line on (the stand-alone run the stopped copy's Doctor asks for) does not. The Summary's
  install.sh, time, folders, `installer output:` and sync lines name it as a run after the end line, the
  Installer section heads its install.out tail `(the install.sh run after this attempt's end line, a
  stand-alone run: this attempt ran no install.sh)` (`earlier attempts' install.sh output, then the install.sh
  run after this attempt's end line, ...` when older output is shown too; B13 round 23 A18, A19, A20), and the
  attempt still counts no folder question ([CONTRACTS §16.152](../design/CONTRACTS.md) Y18).
- A copy newer than the installer (`newer than the installer's v32`) has one of two causes, and the installer
  tells them apart by its own checkout's upstream ([CONTRACTS §16.59](../design/CONTRACTS.md) F17); the stop
  line's last column (the fix) in the friction log says which it found. The checkout follows main of the
  repository: step 1's pull worked, and the copy came from another branch or a fork, so the fix is to copy
  the prompt again from the README. The checkout follows another remote or branch (a fork, a mirror): the
  checkout is the cause, so the fix is to point it at main, or move it aside so step 1 clones it, and paste
  the same prompt; a fresh copy would stop at the same place. When git cannot say which, the fix names both.
  Since 2026-10-13 the agentsync report's Summary says which cause the fix column names, in its own words
  ([CONTRACTS §16.64](../design/CONTRACTS.md) G20), and never shows the column: `prompt: vM (newer than the
  installer's vN: this Mac's checkout follows main, so the pasted copy came from another branch or a fork, or the --log-start value was changed)` for a
  checkout on main of the repository; `prompt: vM (newer than the installer's vN: this Mac's checkout follows
  another remote or branch, or the --log-start value was changed)` when the checkout is the cause; and the bare `(newer than the installer's vN, or the --log-start value was changed)` when
  the fix names both (a checkout of this repository that follows no upstream branch, or one whose remote git
  cannot read), the installer is too old to name a cause, or the column is empty. The shell report (`install.sh --report-only` without agentsync) has no such clause, so read its friction
  log's fix column.
  Since v15 the prompt has the agent tell the person what the installer's error says, in its words, and
  copy the prompt again only when there is no such error (the `--version` check failed alone).
- Both lines come from the installer's own stop line in the attempt, so they name the installer that stopped
  the copy. A bare `prompt: v7` says only which version the attempt was logged under. An installer before v8
  wrote its own number there, so an old v7 attempt is not a stale copy, and it is not K18.
- `prompt: vM (newer than this report's agentsync, which is for prompt vN)` is a different case: no installer stop. The
  agentsync that wrote the report is older than the pasted prompt: an earlier install left it, and this attempt
  did not replace it, so the report's step names and checks are that older build's. The clause names no cause:
  step 1's pull may have worked with a later step failing before the reinstall, or the attempt may have stopped
  before any pull. The run metadata's `agentsync:` line names the build's own prompt version. When the
  installer's stop line is in the attempt, the Summary shows the `newer than the installer's` form and not this
  one ([CONTRACTS §16.69](../design/CONTRACTS.md) H23). Do not triage it as K18.

### A Mac that is already set up

The folder question is asked once per Mac. A v8 run on a Mac that already synced two folders asked it again,
got no answer from an unattended tool, and stopped as the prompt said: a round lost (K20). Since v9 a re-run
needs no folder answer ([CONTRACTS §16.29](../design/CONTRACTS.md)):

- `install.sh --list-folders` reads the config. When it already syncs folders, the list starts with
  `already synced on this Mac: N folders (marked [synced] below)` (`1 folder` for one, since B13 round 14 Q44) and each of them has `[synced]` before its
  path. A synced folder the list does not reach (deeper than it goes, outside `~/Library/CloudStorage`) is
  printed first with the same mark, and the first line then ends `(marked [synced] below: first the 1 outside
  the list, then the list)` when the list shows a folder. A synced folder under `~/Library/CloudStorage` that is
  not on disk is printed first too, counted apart (`first the 1 not on disk, then the list`, or `first the 1
  outside the list and the 1 not on disk`; B13 round 15 S10); one missing outside `~/Library/CloudStorage` is
  counted outside the list, as no NEXT clause names it (B13 round 16 T46). So every synced folder is named once.
- A sync reads the whole tree under a synced folder. So a listed folder inside one has
  `[inside a synced folder]` before its path, and one that holds one has `[contains a synced folder]`. Only
  an unmarked folder is one to add: a marked one, added, would be read twice.
- A list that ends on a click (exit 4: a provider this terminal app was denied, or macOS still asking) has
  the line and the marks too, for what it could list. Step 1's exception applies there as well, and the click
  is needed only to add a folder from that provider. Its log line reads `result=failed note=denied synced=2`.
- A set-up Mac with nothing to list (OneDrive signed out, and a synced folder outside
  `~/Library/CloudStorage`) gets the line, its synced folders and exit 0, logged `note=listed-0 synced=1`. The
  `NEXT:` names `install.sh` alone, so step 2 runs. A Mac that syncs no folder still gets exit 3 there.
- Step 1 then tells the person which folders those are and asks only whether to add an unmarked one. An agent that
  cannot ask adds none and goes on to step 2. That is not a deviation and is not logged; the agent says it in
  its final message. On a Mac with no synced folder nothing changed: an agent that cannot ask logs a deviation,
  stops and waits.
- A set-up Mac whose synced folder under `~/Library/CloudStorage` is missing because its app is signed out, or
  still setting up (its provider folder gone with no other folder of that app beside it, or there and empty), gets
  the line, the `[synced]` lines and exit 3, logged `note=not-signed-in apps=<app> missing=M synced=N` (B13 round 11
  M4, narrowed by round 12 N41, N61; the app and the missing count since round 13 O5). Its `NEXT:` asks for that app's sign-in and a re-run, and says the folders marked `[synced]`
  are kept. A click the list ends on (exit 4) comes first. Since v20, step 1 waits for that sign-in as for a click,
  then lists the folders again, before the folder question above. Since v21 (B13 round 12 N32, N6), an agent that
  cannot ask does not wait and logs nothing for that sign-in: it goes to step 3's report, whose expected-turns line
  is the set-up Mac sign-in row below (K45). A folder missing while its app is signed in
  (renamed, moved or deleted there, or not synced on this Mac) keeps exit 0, logged `gone=N`; the `NEXT:` names it and step 2's doctor names
  its fix. So does a folder in a SharePoint library not synced on this Mac (`OneDrive-SharedLibraries-Contoso` gone,
  or there and empty, beside `OneDrive-Contoso`); its `NEXT:` says to sync the library on its SharePoint page.
- Since v25 (B13 round 16 T6, T30), a folder still mirrored after its `[[source]]` block was deleted (the manifest
  still holds its id; sources.toml no longer has it) drops `(none is needed)` from the `NEXT:` and adds `; 1 folder
  in the list above is still mirrored but has no [[source]] block in <config>: <folder>. Step 2's status fails on
  it until it is added again (one --source-local "<folder>") or its [[source]] block is put back, as it was or
  retired` (for more than one, `on each` and `one --source-local for each`). A source that no listed folder adds back
  is given by count only, never by its id (`; 1 source that no folder in the list above adds back is still mirrored
  but has no [[source]] block in <config>. ...`). The tail is on every list whose `NEXT:` points to step 2, the
  folder question included; the list's exit is as without it, and its log line adds `vanished=N` (those folders and
  sources together). Step 1 tells the person and adds the folder again only if they name it; step 2's doctor
  reports it as `config.vanished_sources` [FAIL] ([CONTRACTS §16.124](../design/CONTRACTS.md), B13 round 17 U91).
- Since v26 (B13 round 17 U12), when the list has read the config and no provider was denied or still asking, it
  also asks the installed agentsync whether policy.toml, the `[governance]` values in <config> or agentsync's state
  file would stop step 2's config step. When one would, the `NEXT:` drops `(none is needed)` and adds `; step 2
  stops at its config step on <what>: fixing that is the person's, and ~/.local/bin/agentsync status prints the
  error`, where <what> is `an invalid [policy] in policy.toml`, `an invalid [governance] value in <config>`,
  `agentsync's state file, which cannot be read` or, since v27, `a git remote on the docs repo that [governance] does
  not allow` (read only when that table loads; never the remote's host), joined by `and`. Since v27 the list also
  reads each table's and each index's root page (B13 round 18 V9, V21, V36); one it cannot read logs `state-table`.
  Since v29 that word gives the same `agentsync's state file, which cannot be read`: this checkout's agentsync reads
  each root page too (round 19 W41, round 20 X50), so step 2's config step stops on it, and only an older installed
  build reads `state-table`, which step 2 reinstalls first. Its v27 tail, `(at its status or its first sync)`, was
  false for an index: a status and a sync with nothing new read none, and step 2 passed (round 20 X72). Since v29 a
  state folder, `manifest.sqlite` or its `manifest.sqlite-wal` or `manifest.sqlite-shm` this account cannot open, which no probe can read, adds a tail of its
  own, with no reinstall form (round 20 X9, X29). Since v30 (round 21) the folder and the manifest, which stop any
  build's config step, are named apart from the -wal and the -shm, which init opens past (Y57), the noun covers
  the folder (Y14), and the owner is the one `doctor.state_path_fix` gives (Y24): logged `state-owner`, `; step 2
  stops at its config step on agentsync's state folder or a file in it, which another account owns, so this one
  cannot open it (not damage): fixing that is the person's (a sudo chown; the agent runs no sudo), and
  ~/.local/bin/agentsync status prints it on its state_dir lines`; for this account's own whose fix is a chmod,
  `state-access`, `; step 2 stops at its config step on agentsync's state folder or a file in it, which this
  account cannot open (a mode, not damage): the chmods that fix it are the agent's to run, once each, as step 2's
  NEXT names them, and ~/.local/bin/agentsync status prints it on its state_dir lines` (plural since round 23 A37: a
  shut folder and the file in it take a chmod each); for one no chmod fixes,
  `state-refused`, `; step 2 stops at its config step on agentsync's state folder or a file in it, which this
  account is refused, and a chmod of it does not fix that (an ACL, a folder above it this account cannot search, or
  a volume mounted read-only, whatever its mode; not damage): fixing that is the person's, and
  ~/.local/bin/agentsync status prints it on its state_dir lines` (round 23 A75: it said "though its mode allows
  it", false for a read-only volume, which refuses whatever the mode); and for
  a -wal or -shm, `state-side-owner`, `; step 2 stops at its status step on the -wal or -shm of agentsync's state
  file while it holds unwritten changes or is in use (an empty one may not stop it), which another account owns (not
  damage): fixing that is the person's (a sudo chown; the agent runs no sudo), and ~/.local/bin/agentsync status
  prints it on its state_dir.files line`, or `state-side-access`, `; step 2 stops at its status step on the -wal or
  -shm of agentsync's state file while it holds unwritten changes or is in use (an empty one may not stop it), which
  this account cannot write (a mode, not damage): the chmods that fix it are the agent's to run, once each, when
  step 2 stops on them, and ~/.local/bin/agentsync status prints it on its state_dir.files line` (the condition: an empty one
  leaves no stop once the manifest is opened; until round 22 Z36 it read "step 2's status may fail on", not the
  "step 2 stops on" the prompt relays), or, when an ACL refuses one its mode allows, `state-side-refused`, `; step 2
  stops at its status step on the -wal or -shm of agentsync's state file while it holds unwritten changes or is in
  use (an empty one may not stop it), which this account is refused, and a chmod of it does not fix that (an ACL, or
  a volume mounted read-only, whatever its mode; not damage): fixing that is the person's, and
  ~/.local/bin/agentsync status prints it on its state_dir.files line`. Those -wal and -shm words are for one this
  account can read but not write; one it cannot read stops init as the manifest does, empty or not, so it takes
  the config step's words above, and step 2's config step names its chmod 600 in its own `NEXT:` (round 23 A78:
  that `NEXT:` was `fix the error above`). A folder or file whose own mode already
  allows this account is refused by an ACL or a sandbox, which no chmod changes: `state-refused` (or
  `state-side-refused`), the person's; so is one on a volume mounted read-only, whatever its mode, since a chmod
  fails there (round 22 Z79: the words named only an ACL or a folder above it). An installed agentsync without
  `state_path_fix` judges by the mode, as doctor does. Step 2's config step then names that chmod in its own
  `NEXT:`: for a file, `agentsync's state folder or a file in it has a mode this account cannot open ...: the fix
  is the agent's: run chmod 600 <file> once, then re-run: ...` (with the -wal and -shm its mode refuses too, as
  status's `state_dir.files` names them); for the folder, `... run chmod 700 <folder> once, then, in the same
  pass, since that folder's mode hid its files' modes, run ~/.local/bin/agentsync status and each chmod 600 or
  chmod 700 its [FAIL] lines name for a path in that folder, then re-run: ...` (round 22 Z81: a shut folder hides
  its files' modes, so one pass takes the folder's chmod and then its files'; round 23 A70: this page quoted the
  folder's NEXT as the file's with `chmod 700 <folder>` put in); or the person's for
  `state-refused`. A -wal or -shm, the lock, the heartbeat or a governance file stops step 2 past its config step,
  at its status step: when every [FAIL] that stops it there is such a state check whose fix is this account's
  chmod 600 or chmod 700 of paths in the state folder, its `NEXT:` names them the same way (`agentsync's state folder
  or a file in it (the manifest, its -wal or -shm, the lock, the heartbeat, a governance file or another file there) has
  a mode that is wrong for it, ...: the fix is the agent's: run <the chmods> once, then re-run: ...; if the re-run
  names the same chmod again, ...`; round 22 Z8, Z36, Z59);
  a mix with any other [FAIL] keeps `fix the [FAIL] lines above`. A folder (or a socket or a fifo) where
  `manifest.sqlite` belongs passes every probe above, and every build's init stops on it: `state-notfile`, `; step 2
  stops at its config step on agentsync's state file, manifest.sqlite, which is a folder or something else that is
  not a file (not damage of its contents): fixing that is the person's (keeping a copy, they put the file back, or
  move the folder aside and heartbeat.json with it, so the next sync starts a new manifest), and
  ~/.local/bin/agentsync status prints it on its state_dir.manifest line`, with no
  reinstall hedge, and step 2's config step its own `NEXT:` (`agentsync's state file, manifest.sqlite, is a folder
  or something else that is not a file (...): fixing it is the person's (...), not a step for the agent, so show them
  the error and go on to the report; ...`), where it said `fix the error above` (round 22 Z11); since round 23 A46 a
  symlink there counts by what it points to, so a link to a folder or a link loop is no file either. The folder goes
  aside with heartbeat.json, as doctor's fix says: moved alone, the next run stopped on the lost manifest below
  (round 24 ruling 8). No manifest at all while heartbeat.json beside it records an earlier pass is a manifest lost
  from a Mac that has synced, which every init step 2 reinstalls stops on (round 23 A29): `state-lost`, `; step 2
  stops at its config step on agentsync's state file, manifest.sqlite, which is missing though heartbeat.json beside
  it records earlier passes (not damage, not a mode): fixing that is the person's (they restore the file from a copy
  they trust, or move heartbeat.json aside, keeping a copy, so the next sync starts a new manifest), and
  ~/.local/bin/agentsync status prints it on its state_dir.manifest line`, and step 2's config step its own
  `NEXT:` (`agentsync's state file, manifest.sqlite, is missing, though heartbeat.json beside it records earlier
  passes (...): fixing it is the person's (...), not a step for the agent, so show them the error and go on to the
  report; ...`), where it said `fix the error above` (round 24 ruling 7). Something that
  is not a folder (a file, a symlink to one, a fifo) where the state folder or a folder above it belongs is the
  person's too, judged before every probe above (round 23 ruling 4, A2, A6, A21, A32, A80: the access probe gave
  the agent a chmod of that file, then the re-run blamed an ACL): `state-notdir`, `; step 2 stops at its config step
  on agentsync's state folder, which is not a folder (a file or something else is where it or a folder above it
  belongs; not damage, not a mode): fixing that is the person's (they move it aside, keeping a copy), and
  ~/.local/bin/agentsync status prints it on its state_dir line`, and step 2's `NEXT:` is the person's, with no
  chmod (`agentsync's state folder is not a folder (...): fixing it is the person's (they move it aside, keeping a
  copy), not a step for the agent, ...`). When none of those is said, the files status checks by name, the lock,
  the heartbeat, governance/ and its holds.json and purge queue (never audit.jsonl, which no check reads), are read
  the same way, since step 2 stops on them at its status step (round 23 A17, A83: the list said `(none is needed)`
  over them): `state-file-owner`, `; step 2 stops at its status step on a file or folder agentsync's status checks
  in its state folder (the lock, the heartbeat, or the governance folder or a file in it), which another account
  owns, so this one cannot open it (not damage): fixing that is the person's (a sudo chown; the agent runs no
  sudo), and ~/.local/bin/agentsync status prints it on its lock, heartbeat or governance lines`;
  `state-file-access`, `; step 2 stops at its status step on a file or folder agentsync's status checks in its
  state folder (the lock, the heartbeat, or the governance folder or a file in it), which this account cannot open
  (a mode, not damage): the chmods that fix it are the agent's to run, once each, as step 2's NEXT names them, and
  ~/.local/bin/agentsync status prints it on its lock, heartbeat or governance lines`; or `state-file-refused`, `;
  step 2 stops at its status step on a file or folder agentsync's status checks in its state folder (the lock, the
  heartbeat, or the governance folder or a file in it), which this account is refused, and a chmod of it does not
  fix that (an ACL, a folder above it this account cannot search, or a volume mounted read-only, whatever its mode;
  not damage): fixing that is the person's, and ~/.local/bin/agentsync status prints it on its lock, heartbeat or
  governance lines`. When step 2 would
  reinstall agentsync first
  (the installed one is not this checkout's build, the checkout has uncommitted changes, or its bundled pandoc is
  not whole), the tail opens `; step 2 reinstalls agentsync first, and if this checkout's agentsync rejects it
  too, step 2 stops at its config step on <what>: ...`. The tail is on every list whose `NEXT:` points to step 2,
  the folder question included, after any vanished tail; the list's exit is as without it, and its log line adds
  `config_err=<words>`, the comma-joined fixed words `policy`, `governance`, `state`, `remote`, `state-table`,
  `state-access`, `state-owner`, `state-refused`, `state-side-access`, `state-side-owner`,
  `state-side-refused`, `state-notfile`, `state-lost`, `state-notdir`, `state-file-access`, `state-file-owner` and
  `state-file-refused`, never the error's text.
  Step 1 tells the person ([CONTRACTS §16.130](../design/CONTRACTS.md), B13 round 18 V77).
- A config the list could not read (logged `synced=unreadable marks=<why>`) gets no mark and no `already synced`
  line; the folder question's `NEXT:` adds `; this Mac has a config (<config>) whose folders could not be read:
  if it was set up before, <install.sh> with no --source-local keeps whatever that config syncs, and each
  --source-local only adds`, and after a reader timeout `; or re-run <install.sh> --list-folders to mark them`.
  When the installed agentsync failed while reading it (`marks=failed`, a config that does not load or a broken
  install), it adds instead `; this Mac has a config (<config>) whose folders could not be read: the installed
  agentsync failed while reading it (it raised <Class>), and ~/.local/bin/agentsync status prints why. Step 2 reinstalls agentsync first, and if that
  version cannot load it either, step 2 stops at its config step and nothing is kept or added until the config is
  fixed`: step 2's reinstall reads a config an older agentsync rejected for a newer key (B13 round 10 L11). When the
  installed agentsync is already this checkout's build and its bundled pandoc is whole (install.sh's
  `install_is_current`; B13 round 16 T54), step 2 does not reinstall it, and the sentence says so
  instead: `The installed agentsync is already this checkout's build (commit <sha>), so step 2 (run from this
  checkout) does not reinstall it: ...until the person fixes the config` (B13 round 11 M37). In that case the list
  does not end on the folder question: its `NEXT:` opens `no folder can be added until the config is fixed: run
  <install.sh> with no --source-local, which shows the error`, followed by the same clause, and the list logs done
  (B13 round 12 N29). A build whose bundled pandoc is missing or not the file pypandoc_binary installed is
  reinstalled by step 2, so step 1's list prints the sentence above that says step 2 reinstalls agentsync first
  (B13 round 17 U90).
  Since v18 step 1 treats that list like the one above: it runs the offered re-run once, says the list could not
  read the folders, and asks only whether to add one (since v21, nothing when step 2 does not reinstall agentsync);
  an agent that cannot ask adds none and runs step 2 with no
  `--source-local`, which is not a deviation. If step 2 then says no folder is synced yet (the config held none),
  the Mac syncs no folder after all, and step 1's folder question applies as on a new Mac. Up to v17 an agent that
  could not ask logged a deviation and waited there (B13 round 9 K9, K20, K32, K73, K78). Since v19 (B13 round 10
  L62, L11, L10): when the offered re-run's `NEXT:` no longer says the config could not be read, step 1 follows that
  list as on any Mac. When it says the installed agentsync failed while reading the config (`marks=failed`), step 1
  shows that line in its own words and does not say step 2 keeps the folders. Since v21, when that line says step 2
  does not reinstall agentsync, step 1 asks nothing about folders and runs step 2 with no `--source-local`. After step 2's "no folder is synced
  yet", an agent that cannot ask logs a step 1 deviation and waits, runs no `--report-only` and adds no "## Not used"
  section. Its finish says `none: waiting for your folder answer` and `none` for bring-back.md, and the answer goes
  to step 2 again with one `--source-local` per folder, not to the add-source its `NEXT:` names. The sign-in `NEXT:`s
  carry the same clause but list no folder, so step 1 shows them and goes to the report, as before.
- Step 2 with nothing to add is `install.sh` with no `--source-local`. It keeps every source, updates
  agentsync, and runs status and a sync.

The Summary shows which case a report is:

| Summary line | What it says |
|---|---|
| `expected turns: no folder question (2 folders already synced: step 1 asks at most whether to add one; not logged) · ...` | The Mac already synced folders when the attempt began, so the folder question is not a turn the report expects, and `human turns` does not count it. On a new Mac the line starts `the folder question (step 1; not logged as a question)`. |
| `expected turns: no folder question (2 folders already synced; step 1's list could not read them, so step 1 asks at most whether to add one; not logged) · ...` | Prompt v18 or later: the Mac already synced folders, step 1's list could not read them, and step 1 asked at most whether to add one, as when the list marks them. No Item and no outcome reason. |
| `expected turns: no folder question (step 1's list could not read the config, so step 1 asks at most whether to add one; not logged) · ...` | Prompt v18 to v20, or v21 or later unless step 1's list logged `marks=failed` and the first install run's agentsync step logged `skipped note=current` (a list that timed out, found no tool or an older build, or logged no `marks=` word, or an install run that reinstalled agentsync), when the install could not say either: its config step failed before it logged how many folders it kept. Nothing shows a folder question was due, so `human turns` does not count it, unless the agent logged a step 1 deviation (it waited for the answer). |
| `expected turns: no folder question (step 1's list could not read the config, and ..., so step 2 does not reinstall it: step 1 asks nothing, since no folder can be added until the config is fixed; not logged) · ...` | Prompt v21 or later (B13 round 13 O8; B13 round 14 Q69): step 1's list logged `marks=failed` and the attempt's first install run logged its agentsync step `skipped note=current`, and, as in the row above, the install could not say either (its config step logged no `kept=`); the left-out clause says the installed agentsync is already this checkout's build. Step 2 does not reinstall the build that failed to read the config, so step 1 asks nothing about folders. As in the row above, `human turns` does not count a folder question unless the agent logged a step 1 deviation. |
| `expected turns: no folder question (2 folders already synced; step 1's list could not read them, so it was asked: see Items) · ...` | The Mac already synced folders, but step 1's list could not read the config (it logged `synced=unreadable`), so it marked none and the folder question was asked: a turn agentsync caused, not an expected one. Items names it, and an install run that otherwise passed is `worked with help`. Only when the attempt's first install run then logged `kept=N`, N at least 1. |
| `expected turns: none (step 1's list stopped: OneDrive is not signed in, so no folder was listed: no folder question and no Allow click)` | On a Mac that syncs no folder (no list logged `synced=N`, N at least 1), the attempt's only runs are step 1 lists, every one failed, and the last exited 3 (`not-signed-in`, or `no folders are synced yet` for `no-folders`): nothing was listed, so `human turns` counts no folder question. When the list stopped on an Allow click instead (exit 4, `denied` or `tcc-pending`), the line starts `no folder question (step 1's list ended on an Allow click, before the folder question)` and the click stays the turn. A list that finished, or an install run, keeps the usual rules, and so does an exit 4 that logged `synced=N`. install.sh's sign-in exit on a set-up Mac (§16.93 M4, `not-signed-in apps=<app> missing=M synced=N`; `not-signed-in synced=N` from an install.sh before B13 round 13 O5) has the next row's line. |
| `expected turns: none (step 1's list stopped: Dropbox is not signed in, so 1 of the 2 folders this Mac already syncs is missing; they are kept: no folder question and no Allow click)` | The same stop on a Mac that already syncs folders (since round 12 N31; it read as the already-synced line, which named an Allow click no one announced): a folder of the app named was missing, and the folders already synced are kept. The app and the count are the ones install.log's line names (`apps=`, `missing=`; since round 13 O1, O42, O66 the line said OneDrive and counted every synced folder as missing whichever app's folder it was): `the 2 folders ... are missing` when all are, and, from an older install.sh that logged neither, `a sync app is not signed in, so some of the 2 folders ...`. The outcome says `it stopped: Dropbox is not signed in` and the Installer row `Dropbox is not signed in on this Mac: 2 already synced, 1 missing`. Neither turns line names an Allow click. When the person signed in and a later list or install run followed, the usual rules hold, and from prompt v20 the expected turns add `the Dropbox sign-in step 1 asked for (F<n>)`, or `(not logged)`: that one step 1 click line, logged once the list ran, is no click beyond the Allow clicks (N5; round 13 O43), and that list's Installer row reads `stopped` and what came next (O19). From v21 that holds only after a list that logged `synced=N`, N at least 1 (round 12 N7): on a Mac that syncs no folder step 1 asks for no sign-in, so one logged there is a click beyond the Allow clicks (round 13 O40, O59). |
| `note: step 1's folder list could not read which folders sources.toml already syncs (the reader took over 10 s), so it marked none` | Any list run that logged `synced=unreadable`, whatever followed: the parenthesis is its `marks=` word in the report's words (`no-tool`, `timeout`, `older:<name>`, `failed[:<ErrorClass>]`; general words for a log without `marks=`). When step 1 ran the list again before the install and the re-run read the config, the end reads `; its re-run read it: 2 already synced` instead, and the expected turns are the already-synced line. |
| `folders: kept the 2 already synced (none added) · 0 named with --source-local (install.log)` | What the attempt's install runs did with the folders, from the counts they logged: kept (what was synced before the first run), or `1 added to the 2 already synced`, or `2 added (none was synced before)`; an earlier run that added one is named, `(by run <label>, which exited 1)`. The second part is how many folders the `--source-local` options named, a folder named again in a retry counted once. A run that named a folder the config already had reads `kept the 1 already synced (none added) · 1 named with --source-local`: it was left as it was. |
| `sync: done in 9s (install.log) · converted 6, deferred 0 online-only (install.log) · ...` | The install run that ran the sync found sources.toml already there (its config step logged `add-source` or `exists`), so the line says `sync:`, as the installer's own lines did. On a Mac whose config that run created it starts `first sync:`. install.log's step is `first-sync` either way. |

These lines are counts and fixed words. Triage: a `deviation` line that says the agent stopped at the folder
question, in a report whose `expected turns` line says `no folder question`, is a prompt-wording defect and
not the new Mac's expected stop. That reading is safe because the line goes by what step 1's list printed:
`no folder question` needs a list that logged `synced=N`, N at least 1, which is a list that printed
`already synced on this Mac:`. A list that finished with no `synced=` word at all (no config, or an installer
from before 2026-10-13 that could not read it) printed no such line, so the report says `the folder question`
even when step 2 then logged `kept=2`, and a stop there is the expected one (an older list's `warning:` line says
why the config was not read). A list that logged `synced=unreadable` is different: a config was there and the
list could not read it (since 2026-10-13), and the Summary's `note:` line above gives why. When the attempt's
first install run then logged `kept=N`, N at least 1, the Mac already synced folders and the folder question was
a turn agentsync caused, not the expected stop: for a v6 to v17 prompt (and a v18 or later one whose agent logged a
step 1 deviation, as it waited all the same) the `expected turns` line reads `no folder
question (N folders already synced; step 1's list could not read them, so it was asked: see Items)`, an Items line
`install.sh · step 1 · the folder list could not read the N folders this Mac already syncs ...; the folder
question was a turn agentsync caused` names it whatever the outcome, and an install run that otherwise passed is
`worked with help`. Triage a stop there as an agentsync defect (why the reader failed), not a prompt-wording one.
With `kept=0`, no install run, or a stop at step 1, the question stays expected and only the note shows. Only an
attempt with no finished list goes by the install run's `kept`. A report with no `folders:` line comes from an installer older than the
counts, or from a config the installed agentsync could not read; `install.sh --list-folders` then printed a
`warning:` line, which the installer output's tail shows.

A re-run also no longer stops on what an earlier session left readable by others (K21). A coding agent's
file tool writes under the agent's own umask, so the docs repo's `_eval` folder was readable by group and
other. Step 2's `install.sh` ran status before its sync, stopped on `[FAIL] docs_repo.permissions`, and its
`NEXT:` led to a `chmod` the prompt does not let the agent run
([CONTRACTS §16.29](../design/CONTRACTS.md), "A re-run does not stop on a permission agentsync clears itself"):

- The installer's config step (`agentsync add-source`, or `agentsync init` with no folder) now makes
  agentsync's own paths owner-only before status looks, as every sync does: the docs repo's top level, its
  `_eval/` and `topics/` folders, the cache and the logs. It prints
  `tightened N paths inside the docs repo, the cache or the logs: they hold tenant data` (`1 path ...: it
  holds` for one), a count and no path.
- The check's fix names a command the agent may run when one clears it:
  `(fix: run ~/.local/bin/agentsync sync, which makes these owner-only)`. Since 2026-10-12 (Q49, 9ac4273f), every
  runnable fix doctor prints names agentsync by its full path, `~/.local/bin/agentsync`, because install.sh leaves
  ~/.local/bin off PATH.
- The `chmod` is still the fix for a path agentsync does not change: one inside `mirror/` or `.git`, one of
  another user's, or one its owner may not read (a file at mode 0044). A run still stops there, and that
  stop is the person's to clear.

Triage: a `[FAIL] docs_repo.permissions` whose fix names the sync was written between an agent's write and
the next sync, and is gone after it. One whose fix is the `chmod` is a path agentsync does not change, named
in its detail: the person's step.

The setup folder is closed the same way, by the installer (K25). The agent writes `fix-request.md` with its
file tool and `local-work/` with step 1's git command, both under its own umask, so they were readable by
group and other inside `~/agent-context/setup`, and no check looks in that folder
([CONTRACTS §16.30](../design/CONTRACTS.md), "The setup folder is owner-only after every run"):

- Every `install.sh` run now ends by clearing group and other access on every file and folder in
  `~/agent-context/setup`: an install run, `--list-folders`, `--report-only`, and `--log-start` and `--log`
  too. `--report-only` is the one that counts: it is the prompt's last command and follows the agent's last
  write.
- It changes only regular files and folders the person owns, follows no symlink, and prints nothing. A dry
  run changes nothing.
- `status` still does not check that folder. A `[FAIL]` there would stop step 2 on something the prompt's
  own steps cause, which is K21 again.

Triage: a report whose fix request says files in `setup/` are readable by others, from a build with this
fix, means a file there belongs to another user or the folder is a symlink: the walk leaves both alone.

On a re-run, step 2 waits for a running sync before it reinstalls agentsync, at most 2 minutes
([CONTRACTS §16.41](../design/CONTRACTS.md), "The reinstall holds the background sync's lock"). The lines are
`agentsync: another sync is running (<who>); ...` and
`agentsync: reinstalling after the other sync ends, still running, <N>s`. `<who>` is what the lock's holder sidecar
says for the lock body's own pid, in the words of agentsync's own wait line: `the background job, pass poll` or `a sync
not started by the background job, pass poll` (a build since B13 round 10 L73 records it; B13 round 20 X32). For a holder that records nothing,
usually the older installed build that is being replaced, it reads `the background job, or a sync started another
way`: the environment cannot tell a terminal from cron or an agent's shell (B13 round 7 H33, round 11 M43, M60). A lock held with an empty body is held by no sync (install.sh, a status or doctor check, or a cycle starting), and its lines say so: `agentsync: another sync is running (or install.sh, a status or doctor check, or a cycle starting holds the lock); the reinstall waits for it to be free (at most <N>s)`, `agentsync: reinstalling after the lock is free, still running, <N>s` and `agentsync: the lock is still held after <N>s; reinstalling without waiting longer` (B13 round 19 W31). A poll that exits 75 at that
time was skipped on purpose: the reinstall held its lock, and launchd starts the next at its interval. The
report's Recent errors shows that skip as `WARNING agentsync.cli: skipped: lock held by an unrecorded holder
(install.sh while it reinstalls agentsync, a status or doctor check, or a cycle starting)`, and doctor's lock line during the wait
reads `held by an unrecorded holder (...)` with no cycle named: the reinstall writes no lock body.
The install log's `step=agentsync` line notes what step 2 saw: `waited-for-a-background-sync` (the setup report
shows it as "waited for another sync to end": the note does not say who started that sync, and doctor's lock line
names it when the sidecar says),
`wait-ran-out-after-<N>s` (the reinstall then ran without the lock; the report says "waited <N>s for another sync,
then reinstalled while it was still running") or `lock-not-taken-rc<N>` ("could not take the sync lock, lockf exit
<N>; reinstalled without waiting"). A lock held with an empty body, which no sync need hold, notes
`waited-for-an-unrecorded-holder` ("waited for the sync lock to be free") or `lock-wait-ran-out-after-<N>s` ("waited
<N>s for the sync lock, then reinstalled while it was still held") instead (B13 round 20 X43). With no note, the
step's seconds are uv's own (or a sync that started after the probe). A launchd run that ends in the launcher's
`SPAWN_FAILED` (exit 71) or an import error at that time started while uv recreated the tool environment; the
next interval runs normally.

### A check that ran out of time

The rehearsal of prompt v9 (three sandbox homes, the real installer, 2026-10-07) found one thing that would
have cost a round (K27). Step 2's `install.sh` printed `[FAIL] pandoc — check crashed: TimeoutExpired ...`,
skipped the sync and exited 1, and its `NEXT:` said each `[FAIL]` names its fix, which that line did not.
The same command run again passed. The bundled pandoc is an Intel program, and on Apple silicon macOS
prepares each new copy at its first start: 10 to 67 s measured, against the 60 s the check gives it, and
every update installs a new copy ([CONTRACTS §16.32](../design/CONTRACTS.md)):

- `install.sh` now starts that pandoc once itself, right before status. A quick start prints nothing. A slow
  one prints `pandoc: still running, <N>s (...)` at each progress interval and then
  `pandoc: started after <N>s`; the time is part of the status step in install.log, as it was.
- The status step prints `status: still running, <N>s` lines while status works, as the first sync does.
  The pandoc start and status keep one clock: a start too quick to print a line is counted in status's
  first interval, so the first `status: still running` line can show a small `<N>`. Before that, a 13 s
  start followed by a status that waits was about 27 s with no line.
- A check whose program does not answer in time says so:
  `<command> did not answer within <N>s: the check ran out of time, so it could not say whether anything is
  wrong`. Under `install.sh` its fix starts `run ~/src/agent-context-sync/scripts/install.sh again without
  --report-only, which builds nothing; a plain run keeps the folders already synced`, a step the prompt lets
  the agent take.
  Run by hand, it starts `run ~/.local/bin/agentsync status again as it is`.
- That fix goes on `; if this line comes back: ...`, because a program that never answers gives the same
  line at every run, and running again does not clear that one. For `pandoc` it names the check's own fix,
  which is the fix of the pandoc the line names (CONTRACTS §16.120, S29; §16.126, T54): for the bundled one
  `run ~/src/agent-context-sync/scripts/install.sh again (it reinstalls agentsync when the bundled pandoc is
  missing or not the file pypandoc_binary installed); if this line comes back after that, show the person this
  line: setting [convert] pandoc_path to an absolute pandoc is theirs`, and for a `[convert] pandoc_path`
  `check or replace the pandoc at [convert] pandoc_path ('<path>'), or remove that line to use the bundled
  pandoc or its native arm64 copy`. Under `install.sh`, whose step 2 has already found the bundled pandoc's
  file whole or reinstalled it, a second re-run would repeat the first, so the bundled one's names `show the
  person this line: setting [convert] pandoc_path to an absolute pandoc is theirs (the bundled pandoc does not
  answer on this Mac, and install.sh found its file whole)` (CONTRACTS §16.132, U78). For any other check it
  says `report it: the program does not answer on this Mac, and no setup step clears that`.
  The bundled pandoc's re-run is a step the prompt lets the agent take: install.sh's step 2 reinstalls when that
  pandoc is not the file pypandoc_binary's RECORD lists, under the background sync's lock, with
  `UV_SYSTEM_CERTS=1` and at uv.lock's versions. Before, the fix was a bare `uv tool upgrade --reinstall-package
  pypandoc_binary agentsync`, which had none of those and moved every dependency to its newest allowed version.
  A pandoc whose file is whole and still gives the line is not something a reinstall changes, so an agent that
  meets the line again after that re-run, or meets another check's line twice, logs it and goes to the report.
  A fix's first words say whose it is: the bundled pandoc's starts with the agent's re-run and names the
  `[convert]` edit only as the person's, while a `[convert] pandoc_path` that fails or is not absolute has a
  `[convert]` edit for its whole fix, which status's and the installer's `NEXT:` name as the person's
  (CONTRACTS §16.132, U79, U80). A `pandoc` line whose path is followed by `(native arm64 copy)` is the operator's own copy under
  `~/.local/share/agentsync/pandoc/<version>-arm64`, which the converters run instead of the bundled pandoc. Its
  fix is the person's: `the native pandoc copy at ~/'.local/share/agentsync/pandoc/<version>-arm64' is yours to
  replace or remove (the converters run it instead of the bundled pandoc), or set [convert] pandoc_path to an
  absolute pandoc`, because reinstalling the bundled pandoc does not touch that copy, and the operator installed
  it, so status's and the installer's `NEXT:` name it as theirs (CONTRACTS §16.120, S47). After a time-out the
  agent's re-run comes first, then `show the person this line: ...`, and its time-out line gives no
  slow-first-start reason: the 10 to 67 s were macOS translating the Intel program (CONTRACTS §16.114, Q60).
  It, and a configured pandoc's, gives `(any start can take a minute on a busy Mac)` instead, the half of the
  bundled one's reason that holds for any copy (CONTRACTS §16.132, U104).
- Every `[FAIL]` line names a fix. A check that crashed keeps its `check crashed: ...` text and names
  `~/.local/bin/agentsync status -v`, which prints the traceback: that is a fault in agentsync, and the line says to
  report it.
- An operating-system error is not a crash. One that gets past its check reads
  `could not read <path>: <reason>` (or `stopped on a system error: <type>: <reason>` when it names no
  path), and its fix is `check that <path> is there and can be opened, then run again; if the line stays,
  report it: ~/.local/bin/agentsync status -v prints where the check stopped`. A folder's sentinel that cannot be read
  for a reason other than "missing" or "not permitted" is its own `source.<id>.sentinel` line, with the
  fix of the listable line (`check that <folder> opens in Finder`, and for a cloud folder `and that its
  sync app is running and signed in`). Before, a source whose path is a file, or a cloud folder whose sync
  app stopped answering, was one `source.<id> — check crashed` line that said no setup step clears it, and
  the listable line with the real fix was not printed.

Triage:

- `pandoc: started after <N>s` in the installer output's tail is a new copy's first start (after an install
  or an update, or on the run after one that stopped during the wait) or a start on a busy Mac, and no
  friction. `pandoc: no answer after 300s; status checks it next` is a pandoc that does not
  start at all: read the `pandoc` line under Doctor.
- A `[FAIL]` that says `ran out of time`, with an install run that exited 1 and a second one that ended 0,
  is "worked with help" by the outcome's rule (K22). For `pandoc` it means the installer's own start did not
  run (no interpreter or no config to read the path from) or ran out of its 300 s. For another check, the
  line names the program that did not answer; a Mac that slow at `git --version` or `codesign` is the thing
  to ask about.
- The same `ran out of time` line in two install runs in a row, both exited 1, is a program that does not
  answer at all, not a slow start. For `pandoc`, a `pandoc: no answer after 300s` line in the installer
  output's tail says the same, and the way out is the second half of the fix: the install.sh re-run, which
  reinstalls a bundled pandoc that is not the file pypandoc_binary installed, or set `[convert] pandoc_path`;
  for a configured pandoc, check or replace that pandoc, or remove the line. For a `(native arm64 copy)` line, the
  person replaces or removes that copy instead of reinstalling the bundled pandoc, as its fix says.
- `pandoc ... could not be started: Bad CPU type in executable (it is an Intel program, which an Apple silicon
  Mac runs only with Rosetta)`, the bundled or configured pandoc's line, is an Apple silicon Mac without Rosetta. Its
  fix line names both ways out. Installing Rosetta is the person's step, IT's on a managed Mac, and never
  the setup agent's: the line says `Rosetta is yours to install, not a setup step` and gives the command
  whole, `softwareupdate --install-rosetta --agree-to-license` (without the last option it stops at a
  license question). An agent that ran it went outside the prompt, which lets it follow a failed step 2's
  `NEXT:` only into an `install.sh` or `agentsync` command or a click for the person. Since CONTRACTS
  §16.120 (S53) status's and the installer's `NEXT:` name that fix as the person's too. The same error on a
  `(native arm64 copy)` line is not about Rosetta: that copy is not a program this Mac can run, and its fix
  line says to replace or remove it, with no Rosetta words.
- A `check crashed` line is an agentsync code fix, with the traceback the fix request should carry.
- A `could not read <path>` line is the Mac's state first: a path that is not what the config says it is, a
  disk or a sync app that stopped answering. The same line in a second run is a check with a rule missing:
  an agentsync code fix too, and the traceback says which read.

### The evidence parts

One bring-back file should be enough. The parts below are what a maintainer would otherwise have to ask the
person for after reading the report, so the tool reports them every time
([CONTRACTS §16.28](../design/CONTRACTS.md)). Each is a `### ` heading at the end of Status unless the table
says otherwise.

| Part | What it settles | How to read it |
|---|---|---|
| OCR | Did the OCR build work on this Mac, and what did it leave unread? | `helper:` is the state doctor reports. `media helper:` and `speech:` are the states of the helpers that read and hear recordings, from the same probes doctor's media and speech lines call; `speech: off` with the models not placed is the expected state until they are placed. The image line counts files by outcome. A `no-converter stub on this Mac` is an image OCR has not read yet or failed on; an image that is online-only, or a Graph source's, is a `not-on-this-Mac stub` and is never downloaded for OCR. The document lines say how many pages were made with an engine. The re-read table says, per source, whether the one-time re-read is finished and how many files it gave up. When every source is finished and every count is 0, one line, `re-read, per source: finished in all N ...`, names the sources and stands for the table. A file that failed once is tried once more only while its source's scan is not finished; under a finished scan the count stays in the record, a deleted file's (or one with no row) only until the next sync (B13 round 19 W75), and a line under the table says what the manifest holds of those files now (`on this Mac`, `online-only`, `with a stub`, and, until that sync or in a record an older build left, `deleted` or `with no row`). The `waiting for OCR` line gives the files the newest run left for a later cycle; the sums beside it are over all the runs read, so a file that waited in ten runs is ten waits. The last table is OCR's time per run against the cycle's 180 s; its last column is the files that run left to read again (`-` for a run that did not look; `0 + 1 waiting for a converter` when the re-read left files whose converter could not start, which that count leaves out), so the column read down the runs says how many syncs the re-read takes on this mirror. It shows the last five runs that had an engine, and its title says so when there were more (`The last 5 of the 6 runs ...`): the sums above it are over every run read, so the rows need not add up to them. `read again (conversion failed)` is the files a run read again and, in brackets, those it could not convert, which kept their page; after a comma, `N of them a first read` counts the stubs among them read for the first time, which the bullet above calls first reads, not reads again, and a run whose every one was a first read shows only `N first read`. It is no count of new pages: a file read again whose text comes out the same changes no page and makes no commit. `pages added + changed` is what the run did change, from every cause. A run starts no more re-reads once it has spent 120 s on them, which usually ends its reading before the 180 s of OCR time. `mode` is the kind of pass, not who started it: a sync typed in a terminal and the background job's are both `poll`. |
| Quarantine by reason | Why are files quarantined or refused, per source? | A reason is shown as one of a fixed list of classes, never as its text. The last column is when those stubs were built: a stub older than a fix has not been read since. Under the table, one line per source says which file types no converter reads: `no converter by type: .zip 9 (1 online-only) · .dmg 2 · other 1 (1 distinct: 1 like a type)`. A type is printed only when it is one of a fixed list of some 260 type suffixes. `other` is any suffix outside that list, which can be a piece of a file's name, so it is counted and never printed; the number of distinct ones and their shape tell a missing type (`other 5 (1 distinct: 1 like a type)`) from odd names (`other 5 (5 distinct: 2 digits only, 3 with a space)`); the shape words are fixed: like a type, digits only, with a space, with a symbol, too long for a type, nothing after the dot. `like a type` is the shape every listed type has, and a dotted name can have it too; the others are most often a piece of a name, a version or a copy mark, though a few real types have one (a split archive's `.001` is digits only). A source with more than 12 listed types ends `(+N files of M more types)`. Each line adds up to that source's `no converter` rows in the table. While a `[policy]` label rule is on, a binary `.msg`, or any `.msg` not on this Mac (online-only, on a Graph source, or one the OS will not hydrate, which is not downloaded to tell), is `withheld by label rule` instead (a binary one's label cannot be read; one not on this Mac is refused by its name unless `~/.local/bin/agentsync materialise PATH` names it; a `.msg` on this Mac that holds MIME text is screened and read as an `.eml`), and is not in those lines. An image is `withheld by label rule` too when its stub page says so: its `reason:`, or its summary less any leading `refused: `, starts `withheld by label rule`, or its body under the heading says its content is withheld (content policy). That is the test the cycle's re-render uses, and the page keeps what the run that wrote the stub decided, so an OCR engine added or removed since then does not change the class. A page written before its `reason:` said so (round 6, G29) says withheld only in its summary or body, under `reason: no converter for <suffix>`: the report classes it withheld, and so does `_sync/QUARANTINE.tsv`, which writes the withheld reason whenever the page says so (round 10, L54). Only a stub whose page could not be read, or that was read but says neither withheld nor `no converter for` (the legend below counts both as 'could not be read'), can disagree with the TSV, which then keeps the row's reason while the report goes by the probe. An image whose page says `no converter for` and nowhere says withheld keeps the `no converter` class and is counted in the per-type line: a run with no OCR engine has no image converter whatever the policy says, so the stubs it writes say so. Every other image stub goes by whether the OCR part's `helper:` reads `ready` now: one with no page, a page that is a symlink, not a file or outside the docs folder, one that does not parse, one past the report's page-read time (a quarter of the time left, at most 0.75 s), and one that says neither. The `withheld by label rule` line under the table counts those in either state (`, and N image stubs whose page could not be read are counted as withheld because OCR is ready now`, or `... under no converter because OCR is not ready now`), and when the manifest query for the pages fails it says instead that every image stub is counted that way (`the manifest query for their pages failed`); the same line names the routes: a message saved as `.eml`, an image exported as a PDF. |
| Purge queue | Are the queued purges real deletions? | `same bytes live` and `same path live` are files that were renamed, re-exported or saved again, not deleted. `no live twin` is a deletion. `still listed` is a file that is back, under the queued id or under a later id of the same file: a file that is saved again is re-keyed, and its earlier id becomes an alias that a purge follows. A run of the queue keeps an `upstream-deleted` entry queued while its file is live or online-only, and also a `label-escalation` entry whose file the label policy publishes again (live or online-only). Its preview says so (`note: stays queued: ...`). A file that is back quarantined or refused, or an entry with any other reason (DLP, erasure, operator), is still erased, page and history. The `still listed` line under the table says which of the two a run does, with both counts when the queue holds both. `no row` is an id with no row and no alias, so the manifest holds no trace of the file: a purge that already ran (on a build before this one, or one that could not stand in for the entry: reason `upstream-deleted` for an entry with another reason, or narrowed to another source), or an erasure, took the row and left the entry queued. A run of the queue then erases only what history still names and takes the entry off the queue. The lines under the table give these counts in words, with how many ids were judged through an alias (`renamed or re-keyed`). That reading of `no row` holds only when the line under the table starts `no trace`. If it starts `no row` and says the manifest has no alias table, no id was looked up as an alias (a manifest from before that table, read before any command updated it): an earlier id of a file listed now then reads the same as a purged one, and only the preview tells them apart. Whatever the columns say, the operator previews a queue before running it: `agentsync purge --queue --dry-run` writes nothing and prints `note: stays queued: ...` for an `upstream-deleted` or `label-escalation` entry whose file is live or online-only (a preview leaves the queue as it is: its last line, `N queued purges previewed (dry run, nothing written); M queued`, counts as previewed only the entries a run would run, and M is every queued entry; the entries a run would keep are exactly those with a `note: stays queued:` line: an `upstream-deleted` entry whose file is listed, a `label-escalation` entry whose file is published again (`note: stays queued: the label policy no longer refuses the file and its page is current again ...`; the run takes it up again if the policy refuses the file once more), a held one (`note: stays queued: purge suspended by legal/records hold: ...`, which the log also records as `queued purge kept`), and one whose purge failed (`note: stays queued: the purge failed; the log has the error`); a real run's last line counts what stayed: `N queued purges run; M still queued`, or `N queued purges run; M still queued (K of them while their file is listed)` when some of the M are upstream deletions or label escalations whose file is listed, or `N queued purges run; K stay queued while their file is listed` when those are all that is left (that run exits 0; a held or failed entry left makes it exit non-zero)). A `path:` line followed by a `taken:` line for the same path is another file's live page, which stays with its rows. Any other `path:` line of a file still there is erased with its history by the run: bring that preview back before running the queue. |
| Overlapping sources | Is one source's folder inside another's, and which of the two holds the files? | One line per pair with each source's counts, and whether the outer source's `exclude` prunes the inner folder. |
| Empty cloud folders | Can an empty cloud folder be told from one that was never listed? | `N unknown: D dataless, M materialised-and-empty`. A dataless folder's child list is not on this Mac. A materialised one with a link count of 2 is still empty on this Mac: its own metadata says it holds no entry (`N of them still empty on this Mac, by their link count of 2`; `its` when N is 1, and for one such folder `still empty on this Mac, by its link count of 2` or `no longer empty on this Mac, by its link count`). That head and `C of N checked` always show; a kind with no folder (gone, not readable, not a folder, not checked) is left out, and a zero is said in words (`none of them excluded in sources.toml now`, `the mirror holds no file below them`; for one checked folder `it is not excluded ...` and `... below it`). The last clause counts the mirrored files the source's last walk did not list, or says it listed them all. A complete listing checks each one, and those still absent are removed with the usual deletion check (two complete passes, and the deletion breaker). After a walk that ended on an error, the clause says so instead of giving a count. |
| Repeat conversions | Are the same files converted on every run? | The column `the same file from the same bytes and converter options as an earlier run` counts files converted again from the same bytes, under the same converter options, as their own page was made from. The last column (`of those, as the run just before`) counts those whose earlier conversion was in the run just before. When a shown run re-screened files for a rule change, a `re-screened for a rule change:` note under the table gives its count per run. A label rule is one of the converter's options, so a file re-screened after a label-rule change is a new conversion and is not in that column; a file re-screened after a secret-rule change is counted. A copy of a file is a new file and is not counted. The cache line answers for runs of an older build, by the bytes alone: a copy counts there. A failed conversion is counted in each run it failed in and is tried again from the same bytes in up to 2 later runs, or, when its converter could not start (`converter unavailable`), once it starts, so a later run with no failure converted it, gave it up, or, while its converter still cannot start, left it waiting: that run counts it as waiting for its converter, never in a column, and a `waiting for a converter that could not start:` line under the table gives the count per run. When an upgrade gave a run the failed conversions an older build had given up on, a `tried again after an upgrade:` note under the table gives their count per run: that happens once, possibly in a background cycle nobody watched, and each such file is tried again from the same bytes in up to 2 runs of its source from that one on, so an old conversion error can show in those runs; a source that run does not read (a paused one, one left out by `--source`, or one past the time limit) makes its first try in a later run of its own. The note names, per run, the files that run converted (`which that run converted`) and those it settled with no try left (an unreadable stub, a refusal or a removal: `that run settled with no try left`); neither can show an old error, and the retry words follow only when some shown run still has files left to retry (CONTRACTS §16.158, Z35; §16.162, Z55). The files it left waiting for a converter (a `no converter` stub, such as an image that run read with no OCR engine: `that run left waiting for a converter`) are read again by the first run with a converter for their type, so an old conversion error can show in that run (CONTRACTS §16.168, A63). Quarantine by reason counts the files still stuck, class `conversion failed` (CONTRACTS §16.132, U3; §16.140, W52; §16.144, W18; §16.150, X53). |
| Background runs: installed plist | Which arguments of an installed LaunchAgent differ from what this build would write? | Positions and their class (launcher, interpreter, config path, canary path, ...), never a value. `the same file: yes` means both paths reach one file. It does not mean the job runs: the launcher starts only the exact path it was built for, and two tool environments can share one base interpreter. Since 2026-10-07 this build writes the launcher's own path when that is its interpreter under another name in the same folder (`python` beside `python3`), so such a job reads `interpreter same`, and `canary paths: 1 installed, 3 in this build` is a config that gained folders: the operator's `agentsync install-agent` refreshes it. A job whose `last exit code` is 64 is one the launcher refuses at every run; doctor's `launchd.*` line names the two paths. |
| Summary: `installer output:` | Did a run print two `NEXT:` lines, or another instruction beside its own? | `N NEXT: lines in N runs` (`1 NEXT: line in 1 run` for one): one per run is expected, so the two numbers are the same. The counts are over whole runs. The report reads the end of install.out, and when that starts inside a run the lines before the first run header are left out: the line then ends `(whole runs only: the N lines read before the first run header are not counted)`. In the Summary the counts start at this attempt's first run, and a bracket says so and names the earlier runs the Installer section also counts (`(this attempt's runs only; the Installer section also counts the N earlier runs in the lines read)`); the Installer section's line counts every whole run read, older attempts' too, and the tail under Installer shows the last run. When none of this attempt's runs is in what was read, it says `no run of this attempt in the last N lines of install.out` instead. For a copy of the prompt the installer stopped, a run from its end line on is a stand-alone run, not this attempt's: the line reads `no run of this attempt; the run after its end line: <counts>` (`the N runs` for more), and the bracket names only the earlier runs (B13 round 23 A18). A report that install.sh wrote at its own exit (the one a session that stopped before step 3 brings back) adds `the run writing this report prints its NEXT: after the report, so N expected here`: that run's `NEXT:` line comes after the report. |
| Summary: `first sync:` or `sync:` | Did the installer's sync read files again, or did other runs? | The first clauses are the installer's own sync (`sync:` when sources.toml already existed before that install run: its config step logged `add-source` or `exists`, as the installer's own lines say): install.log's step, then what it converted and read again, then status's count of sources with a complete baseline, read when the report was written. After the converted, deferred and read-again counts the clause can add up to five parts, copied from the sync's own line in install.out when that line describes the same sync (the same converted count; the first part also needs the same read-again count): `: F of them a first read of a stub whose bytes had never been read, also in the converted count` (a first read is in both numbers, so do not add them; when every file read again was a first read, the read-again count reads `first read R: stubs whose bytes had never been read, also in the converted count` instead); `; of the converted: U unreadable, F failed` (with `, R refused` when the line gave a refused count, and `, K of them withheld by the label rule` when it gave that count; K + W are the pages that say withheld); `; W waiting for its converter` (`their` for more than one: files whose converter could not start, read once it does, in no other count); `; W withheld unread by the label rule`; and `; Q refused without a read`, files refused with no conversion (no converter for the type, or a rule that needs no bytes). When the step's counts come from install.log, the source tag then names the parts install.out gave, for example `(install.log; the unreadable/failed split and the refused-without-a-read count: install.out)`. A last clause `since this install run began, N reads again by K other runs` (`1 read`, `1 other run` for one) sums what the other runs read again since that install run began: a background poll while the helpers built, or the syncs the prompt runs before the report. A run record does not say who started it. The clause is left out when no other run read a file again, and says `at least` when the 200 runs the report reads do not reach back to the install. When the attempt ran no install.sh, the step is the newest earlier run's and says so: `(install.log: the install run of <time>, before this attempt; this attempt ran none)`. For a copy of the prompt the installer stopped, a run from its end line on is a stand-alone run and is named apart (B13 round 23 A19): one that reached its sync reads `no install run during this attempt; the install run after its end line: <result> · converted ...` (`the last install run after its end line` for more than one), and one that did not adds `; the install run after its end line stopped at its <step> step` or `; the --list-folders run after its end line ran no first sync` to the first clause, before its `install.log` tag. When its install runs stopped before the sync, the head says `no install run of this attempt reached its first sync (install.log: the last stopped at its <step> step)` (`this attempt ran only install.sh --list-folders, no first sync` for a list-only attempt), and install.out's counts are the attempt's only under one of its own run headers: an earlier run's read `(install.out: the install run of <time>, before this attempt; ...)`, unless a newer earlier run, one that began after the run that printed those counts, logged a first-sync step (a sync skipped on a busy lock, say): then the line adds `· the newest earlier run's sync: <result> (install.log: the install run of <time>, before this attempt)`, the step's result in the same words as the Installer table, and leaves install.out's older counts out, since they are not the Mac's latest sync (B13 round 13 O32; B13 round 14 Q70); and those of an install.out with no header `(install.out: which run printed it is not recorded; ...)`. When an install run of the attempt synced but its last install run (never a `--list-folders` one) logged no first-sync step, the clause reads `no first sync in the last install run (install.log: it stopped at its <step> step)`, or `it logged no first-sync step` for a killed run, then `· the newest earlier run's sync: <result> (install.log: the install run of <time>)`, with that run's counts tagged by its name, and install.out's sync line is not read as the last run's; the `sync`/`first sync` label follows the config note of the run whose sync the line shows, this earlier run's, not the last run's (B13 round 17 U42, round 18 V48). The step's install.log note is in words (`lock busy: another sync held the lock past the wait`, `no folder to sync in sources.toml`, ...), here and in the Installer table. The OCR part's sums are over the same runs; its table shows only the last five runs that had an engine, so the run named here can be missing from it. |
| Installer: every run | Was a run cut short? | One line per run, and each run with no end line with its start time, whether its process is gone (killed) or still exists, and the last step it logged (the step under way has no line). |
| Configuration: agent-named folders | Is a folder named like the coding agent only listed, or also a source? | Two counts. A listed one keeps its name in the report. A configured one is a placeholder, except as a word of the agent's name, which keeps it as written (the Summary, the attempt lines, the friction log's `Agent:` line, a step 1 line that states it (`prompt vN, <agent>:`), and the issue link; the line then says so; B13 round 19 W17, round 20 X38). |

A part that prints "not measured (time limit)" ran out of its share of the report's time. The Summary's
`evidence:` line says so first, so the person who ran the setup can write the report again on an idle Mac
(`install.sh --report-only`) before sending it. A run of a build older than these parts did not record what it
converted or OCR's time: its row says "not recorded" or "-".

The report shows the Mac at the moment it is written. After an upgrade to a build with OCR, the files from
before it are read again over several syncs (two minutes of re-reads and three minutes of OCR per sync), so a
report written right after the first sync says `scan finished: no` for a source with many such files. That is
not a fault: the re-read table says how many files are left, and the per-run table how many each sync read.
A report written once the table says `yes` for every source, or the one line that stands for it, shows the
finished state.

Since prompt v8 the report is not written that early. While files are left, `sync` prints
`note: sync again: N files in <sources> are still to be read again, ...` (`1 file ... is` for one). Once the loop's `NEXT:` line has said
"session done" (the loop runs at least one plain sync, even when the installer's NEXT already said it: the
installer's sync downloads nothing), step 3 runs `agentsync sync --materialise-budget 0`, a sync that downloads nothing, and runs it
again while a note starts with `sync again:`, up to 12 times in all, before it writes the report (12 syncs at
both limits are an hour of re-reads and OCR; listing comes on top). They download nothing because a plain sync
brings up to 1 GiB a folder each time, and those files have the OCR time first. The note says "sync again" only
while a sync reads more: the last sync read a file again, or other work had its OCR time first and the count did not rise.
In every other state it says why not, and the agent goes on to the report:

- The last sync read none of the files left with OCR time to spare (pandoc cannot run, the OCR helper stopped
  working, a folder is not listed), or it did not get to the source at all (macOS held its listing for a privacy
  prompt, or the source failed). The note says another sync does not clear it. A source the last sync did not
  get to has a note of its own, with no count.
- The last sync read none because new and changed files took its OCR time, more files joined the count, and
  online-only files still wait for a download. The note says so, and that they are read once a sync has OCR
  time left.

So `scan finished: no` in a v8 report means one of three things: the 12 syncs were not enough (the per-run
table's last column falls run by run and has not reached 0), something stops the re-read (the last runs read
none and the column stands still), or downloads take the OCR time (the last runs read none and the column
rises; step 3's own syncs download nothing, so this is a report whose loop did not reach them, or one written
by hand between plain syncs). An image left for a later sync's OCR needs no note: it is a file not converted
yet, and the loop's `NEXT:` line itself says to sync again until none is left.

`scan finished` is `yes` or `no` only for a record the newest sync could have written: the sentence above the
table, or the one line that stands for it, names that run and says whether it had an engine. `not started` is a source whose record an earlier
build or another engine left, which is how a source reads that no sync of this build has reached yet (paused,
or waiting at a macOS permission prompt). When the `helper:` line is not `ready`, no sync has an engine: the
count of files from before OCR is then the work that waits for the helper, and the sentence says so.

### Fix classes

Map **every friction line** whose kind counts (and every `approval` line), by its `F<n>`, to one fix class:

| Fix class | Signal in the report | Where the fix lands | Proof it stays fixed |
|---|---|---|---|
| **prompt wording** | the agent asked, guessed or did something unasked; "unclear, wrong or missing" lines | the README one-prompt block, with its version bumped in the same commit (a test holds the text to its version) | `tests/test_deploy_pack.py` (the block's version and compat line, its text against its version, step 1's one command and its folder listing on fixtures, step 3's one command, the `--log` template under bash and zsh, every command and flag it names, the pre-allow rules) |
| **installer automation** | a step the agent or person did by hand that a script could do; slow or failed install.log steps | `scripts/install.sh` | `tests/test_launcher.py` (stubbed runs, the setup log) |
| **agentsync code** | a doctor `[FAIL]` without a working fix, a wrong status, an error in Recent errors | `src/agentsync/` | a unit or integration test that reproduces the report's lines |
| **IT pack** | MDM, Conditional Access, proxy/TLS inspection, Command Line Tools blocked by policy | [it-request.md](it-request.md), [mdm/README.md](mdm/README.md), this folder | the page names the exact setting and who sets it |
| **unavoidable OS step** | macOS requires a person: an Allow click, a sign-in, an admin password | the prompt says it up front and waits; the list below | the step is in the list, with its evidence |

The rule: **every line whose kind counts becomes either a fix or a documented unavoidable step.** No line is
closed as "won't fix" or "user error". When the person had to help, the setup had a gap. Close the issue with one
comment, one line per `F<n>`, in this form:

```text
Prompt v<N>, run <Real Mac | Sandbox | Sandbox with simulated launchd>, outcome <outcome>, loop <stage>.
F1: prompt wording -> <commit>, test <tests/test_file.py::test_name>
F2: installer automation -> <commit>, test <tests/test_file.py::test_name>
F3: unavoidable: <the list item below, and the evidence: a macOS dialog, an MDM setting, an IT consent>
F4: known K<n> (<commit>)
```

A line that repeats a known item (below) closes as `known K<n>` without being triaged again.

**A sandbox or simulated run never counts as a success metric.** Its friction lines still become wording or
installer fixes, but only runs whose computed run type is "Real Mac" count in the measures: the share of "fully one
command" outcomes, the questions, clicks, approvals and deviations per report, and the install.log totals per step.
"Fully one command" means no human turn beyond the paste and the current unavoidable steps (section 5), as computed
above.

Maintainer setup, once: commit the form, this page and the README block together, so the report's links resolve on
GitHub, and create the form's label with `gh label create setup-report` (a form's label that does not exist is not
applied).

### Known friction

Items already fixed or documented. The evidence is the sandbox validation runs of the earlier prompts, the
maintainer's review of the two v4 runs (its J-numbered findings), the review of the two v5 runs (its own K-numbered
findings, named as "v5 review, its K<n>") and the review of the two later v5 runs (its L- and V-numbered findings,
named as "v5b review, its L<n>").

| Id | Friction | Fix class | Answered by |
|---|---|---|---|
| K1 | step 3's `ls -d` folder glob exited 1 when no depth-3 folder existed, so the agent said OneDrive was not signed in; in v4, a terminal denied access to OneDrive read as "not signed in" too (J9) | prompt wording | v5 step 2 (v6 step 1) runs `install.sh --list-folders`, which exits 3 for no OneDrive folder and 4 for a denied terminal; `test_list_folders_*` |
| K2 | the friction log lived only in the agent's context, and both agents invented an unredacted file in the home folder | prompt wording | v4 names `~/agent-context/setup/friction.md`; since v6, step 1's `install.sh --log-start` starts it; `test_readme_step1_command_clones_then_pulls` |
| K3 | step 9 said to append a friction heading the report already had, so the heading was duplicated | prompt wording | since v4 the report embeds `friction.md`; no heading to write; `test_readme_setup_report_steps_match_the_code` |
| K4 | the installer guard passed while `setup-report` was not published | installer automation | `install.sh --version` prints `setup-prompt-compat <N>`; `test_readme_prompt_compat_matches_the_installer` |
| K5 | turns were counted by keyword (negations too), and the two agents used the clean / needed help status in opposite ways (J1, J2) | prompt wording | v5 lines carry one kind from a closed list (section 4); `test_readme_friction_line_format_and_kinds` |
| K6 | the outcome and run type were the agent's own statement, and the form could not say "sandbox with simulated launchd" (J3, J14) | agentsync code | v5 computes both (section 4); the form's run types; `test_setup_report_form_has_a_run_type` |
| K7 | friction lines written after the report never reached it (J4) | prompt wording | v5 appends `end \| finished`, then runs the report as its last command; `test_readme_report_is_the_last_command` |
| K8 | the IT request was drafted by hand in about six actions, with a guessed start, dead relative links and a false "byte-identical" claim (J10) | agentsync code | `agentsync it-request --out ~/agent-context/it-request-draft.md`; since v7 the prompt drafts no request, and the loop's `NEXT:` names that command only when the network refuses Microsoft Graph; `test_it_request_placeholders_are_in_its_top_table` |
| K9 | a second attempt overwrote the first attempt's friction log or repeated its header (J5) | prompt wording | v5 appends an `Attempt:` header block per attempt; `test_readme_friction_log_two_attempts` |
| K10 | the per-line friction command double-quoted the agent's words, so a backtick or `$(...)` in them ran as a command (v5 review, its K7) | prompt wording | the four values are single-quoted, with ’ for an apostrophe; `test_readme_friction_line_never_runs_the_agents_words` |
| K11 | every distinct command could cost a tool approval, and no pre-allow list was shipped (v5 review, its K11 and K12) | prompt wording | step 1 is one command; the README's "Fewer approval prompts" rules; `test_readme_step1_is_one_command`, `test_readme_pre_allow_rules_cover_every_command` |
| K12 | the pack told the person to reset the privacy grant with a Terminal command after a denied Allow, while the installer's `NEXT:` line names the System Settings toggle (v5 review, its K13) | IT pack | the Files and Folders toggle everywhere; `test_denied_access_remedy_is_the_files_and_folders_toggle` |
| K13 | every friction line was a `printf ... >> ~/agent-context/setup/friction.md`, and Claude Code asks for every redirect target that starts with `~`, whatever the allow rules say (v5b review, its L4) | prompt wording | v6 logs with `install.sh --log`, which appends at 0600; the block has no redirect; `test_readme_block_redirects_to_no_file`, `test_readme_friction_line_never_runs_the_agents_words` |
| K14 | the block took six tool calls, each a possible approval: the friction header, preflight, the folder list, the IT draft, the end line and the report (v5b review, its L5) | prompt wording | v6 has three: step 1 (preflight, code, `--log-start`, `--list-folders`), step 2 (install) and step 3 (IT draft, the end line, report; since 2026-10-05 `--report-only` writes the end line itself); v7's step 3 is the sync loop, whose `sync`, `curate` and `status` have exact pre-allow rules (and v8's `sync --materialise-budget 0`), then the report alone; `test_readme_step1_is_one_command`, `test_readme_report_is_the_last_command` |
| K15 | the `start` and `end` kinds could not be followed literally (step 5's `start` never had an `end`), and the Summary's event count did not add up (v5b review, its L9 and V2) | prompt wording | v6 has no step lines: install.log times the steps and the kinds are the six in section 4; `test_readme_friction_line_format_and_kinds` |
| K16 | the setup ended at the install, and the loop (baseline questions, curation) lived in a second prompt that was never pasted on the field Mac (KISS K04) | prompt wording | v7 replaces both prompts: step 3 runs `agentsync sync` and follows its `NEXT:` line until the session is done or waits on the person, then the report; `test_readme_step3_runs_the_loop_then_the_report` |
| K17 | no prompt said where the inbox was, what to drop in it or that emptying it erases pages, and on the field Mac the agent's own exporters read other apps' private stores and drove the browser, which raised clicks (field report N3, N9, N14, N16) | prompt wording | v7 step 2 names the inbox by its `sources.toml` entries, the formats and the label caveat, and says it is never emptied; the rules say agentsync never needs `~/Library/Containers`, Group Containers or the browser; `test_readme_prompt_carries_the_field_lines` |
| K18 | a copy of the prompt saved before an edit was pasted again, twice, and nobody knew it was old: the edit had kept the version (still v7), the copy's check read "7 or higher", and the installer wrote its own number as the attempt's `Prompt:` line (field, 2026-10-06) | prompt wording and installer automation | since v8 the version moves with every change of the text, step 1 hands it to `install.sh --log-start`, which stops a copy that is not its own, and the report names the copy's version; `test_the_prompt_text_changes_only_with_its_version`, `test_a_saved_copy_of_an_older_prompt_is_stopped_at_step_1`, and `tests/test_install_oneshot.py` for each shape of the value |
| K19 | the bring-back file was written right after the first sync, while the files from before the upgrade were still being read again, so the next question took another round (field, 2026-10-06) | prompt wording and agentsync code | `sync` prints a `sync again:` note while another sync reads more of them, and v8's step 3 runs a sync that downloads nothing (`sync --materialise-budget 0`) once the loop is done and again while the note says so, up to 12 times in all, before the report; `test_readme_step3_syncs_again_while_the_tool_says_so_before_the_report`, and `tests/test_loop.py` for the note |
| K20 | a Mac that already synced two folders was asked which folders to sync again; the tool ran unattended, the question came back unanswered, and the agent stopped and waited as v8's step 1 says, with nothing done (field, 2026-10-07) | prompt wording and installer automation | `install.sh --list-folders` marks the folders the config already syncs; v9's step 1 then asks only whether to add one and goes on when it cannot ask, and step 2 names `install.sh` with no `--source-local`; the report expects no folder question there and says what was kept; `test_readme_prompt_asks_a_mac_already_set_up_for_no_folder`, `test_readme_step1_command_marks_the_folders_a_set_up_mac_syncs`, and `tests/test_install_oneshot.py` for the list and the run with no folder |
| K21 | on a Mac that already ran agentsync, step 2's `install.sh` stopped at status on `[FAIL] docs_repo.permissions` (an `_eval` folder an earlier session's agent had written under its own umask), skipped the sync and named a `chmod` the agent may not run, so the round ended before the loop (field, 2026-10-07) | agentsync code | `add-source` and `init`, which `install.sh` runs before status, make agentsync's own paths owner-only as every sync does (the docs repo's top level, `_eval/`, `topics/`, the cache, the logs); the check's fix names `agentsync sync` when a sync clears what it found and keeps the `chmod` for the rest; `tests/test_install_oneshot.py` for the field layout under the real installer run, `tests/test_cli.py` for the two commands, and `tests/test_cycle.py` for the paths, the bound and a path of another user's |
| K22 | the report read "fully one command" for an attempt whose first install run had exited 1 on a doctor `[FAIL]` and whose second run came after a fix made by hand: only the last install run was judged, and the issue link said the same (field, 2026-10-07) | agentsync code | since prompt v7 an install run that failed before the attempt's first run that exited 0 makes the outcome "worked with help"; the Summary names it and lists it first under the items that were not one command; a run the agent's tool stopped and a run after the first success do not count; `tests/test_setup_report.py` for the field's log, each order of exits, the earlier attempts and the link |
| K23 | `fix-request.md` outlives a session and the prompt forbids deleting, so a v8 session appended its request to the v7 one, and after a second report it rewrote its own `## Not used`; neither was in the prompt, and the bring-back carried the earlier round's request again (field, 2026-10-07) | prompt wording | v9's rules say the file is kept from session to session: an earlier session's text stays, each session writes under its own heading (the prompt's version and the date and time, so two sessions of one day differ) and rewrites only its own part; step 3's `## Not used` is one per session. The bring-back still carries the whole file. Triage compares it with the last file received from that Mac and reads from the heading above the first line that differs, not from the last heading: a part nobody copied back sits above that one; v11 (v10 rehearsal, 2026-10-08): a session that asks for no change still writes its heading, and its `## Not used` goes under it; `test_prompt_routes_changes_to_the_source_not_the_checkout` |
| K24 | step 2 with two `--source-local` printed the `docs repo ...` and `sources: ...` lines once per folder, 16 source ids each time: noise, and an agent could not tell what the second block meant (fix requests, 2026-10-06 and 2026-10-07) | installer automation | `install.sh` prints the first call's `docs repo` line and the last call's `sources:` line; every other line, the error stream and the call's exit status are as they were, and `agentsync add-source` by hand still prints both; `tests/test_install_oneshot.py` for three folders against the real agentsync and for a call that fails |
| K25 | `fix-request.md` and `local-work/` in `~/agent-context/setup` were readable by group and other: the agent writes them under its own umask, the installer closed only its own three files, `--report-only` changed no mode, and doctor's printed `chmod -R` was the only thing that ever closed them (fix request, 2026-10-07) | installer automation | every `install.sh` run that reaches the setup folder ends by clearing group and other access on all of it, `--report-only` and the two `--log` options included; only the default folder, only the person's own regular files and folders, no symlink followed; `tests/test_install_oneshot.py` for each command, the symlinks, a setup log somewhere else and the dry run |
| K26 | both installed background jobs read "plist differs" although they ran, and the fix doctor named, `agentsync install-agent`, would have written an interpreter path the launcher refuses: the launcher is built for the tool's `bin/python`, and a tool uv updated in place runs as `bin/python3`, the same file (field, 2026-10-07) | agentsync code | a job names its interpreter by the launcher's own path when that is the running interpreter under another name in the same folder, so doctor and the report compare against it and the refresh is safe; a job the launcher refuses is a doctor warn that says exit 64, and the report and the installer give 64 its meaning; `tests/test_ops_launchd.py`, `tests/test_launcher.py` with the real launcher, `tests/test_ops_doctor.py` and `tests/test_setup_report.py` |
| K27 | on a fresh install, and again after an update, step 2's `install.sh` exited 1 on `[FAIL] pandoc — check crashed: TimeoutExpired ...`, a line with no fix under a `NEXT:` that said each `[FAIL]` names one; the same command run again passed. The bundled pandoc is an Intel program, and its first start after each install took longer than the check's 60 s (v9 rehearsal, 2026-10-07) | installer automation and agentsync code | `install.sh` starts that pandoc once before status, with progress lines and a 300 s limit of its own, and the status step has progress lines too; a check that runs out of time says so, and its fix is the same command again, then what to do if the line comes back (for pandoc the check's own fix, since a pandoc that never answers gives the same line at every run); the pandoc start and status keep one progress clock, so the two are not silent for an interval each; every `[FAIL]` names a fix, which the whole suite is held to: a crash names `agentsync status -v`, an operating-system error reads `could not read <path>` and names the path, a sentinel that cannot be read is its own line, and a Mac without Rosetta is told that installing it is the person's step, with the whole command; `tests/test_install_oneshot.py` for the slow first start against the real agentsync and for the limit, `tests/test_ops_doctor.py` for each check that starts a program, and `tests/conftest.py` for the rule |
| K28 | the report of an attempt that an out-of-date copy of the prompt started said `human turns: 1 (1 question; ...)` and `expected turns: the folder question (step 1; not logged)`, though the installer had stopped that copy before the folder list and nobody was asked (v9 rehearsal, 2026-10-07) | agentsync code | an attempt the installer stopped, with no folder list in it (a stand-alone install.sh run after its end line does not count, §16.152 Y18), counts no folder question and expects no turn; a logged question still counts; `tests/test_setup_report.py` for the rehearsal's log, a copy that named no version and an attempt that reached a list after all |
| K29 | a first install printed one raw log line, `WARNING agentsync.curate: .../DEPENDS.tsv: missing or empty`, from the status that runs before the first sync; harmless, but a line the prompt does not explain, which an agent may log as friction (v9 rehearsal, 2026-10-07) | agentsync code | while no page is curated a missing or empty `DEPENDS.tsv` is the normal state before the first sync and is logged at debug level; with a curated page it is still a warning; `tests/test_curate.py`, and `tests/test_install_oneshot.py` for a first install's output with the real agentsync |
| K30 | the last line of a new Mac's setup, `WAITING ON YOU: the baseline questions are a draft: keep about 10 in _eval/questions.md, ...`, named its two files without their folder, and the person who confirms them is not the one who wrote them (v9 rehearsal, 2026-10-07) | agentsync code | the wait names the folder once, `in ~/agent-context/docs/_eval, keep about 10 in questions.md, correct the answers in answers.md, ...` (the docs repo's path from the config, with `~` under the home folder); it still carries no document's name, and the report's Loop line shows `in _eval`, whatever the docs repo's path (one with a space in it left `in Client Alpha/kb docs/_eval` there until the folder was replaced as the loop wrote it); `tests/test_loop.py` and `tests/test_setup_report.py`. Amended (B13, N18): the report's Loop line shows `in the docs repo's _eval folder`, and the Configuration part's `docs repo:` line gives the folder; `tests/test_setup_report.py` |
| K31 | on a Mac with fourteen inbox sources the person had added, step 2's "the folder of each kind = "inbox" source" named fifteen folders as the inbox when read literally; the agent announced only the folder beside the docs repo and logged the wording as a prompt friction line; and its Not used line for `hold` read it as a pause, "No source needed pausing" (field, v9, 2026-10-08). One more is from reading the text, not from that run, which met no folder stop: step 1's "stop and wait for my answer" and step 3's "Then the report, always" disagree at the folder stop of a new Mac (held in the plan since the v9 rehearsal, 2026-10-07) | prompt wording | v10 step 2 names the inbox as the folder named inbox beside the docs repo, one of the `kind = "inbox"` sources, any other being a folder the person added; step 3 names the two cases with no report, the wait for the folder answer in step 1 and a copy that step 1 stopped because it is not the installer's (the second since the v10 rehearsal, 2026-10-08: naming one case left that stop with two orders); the Not used list says `hold (a legal or records hold, not a pause)`; doctor's "drop files you save by hand here" is said of that one folder only; `test_the_inbox_the_prompt_names_is_the_one_agentsync_keeps`, `test_readme_report_step_after_any_failure` |
| K32 | wording that v10 rehearsals and the fourth bring-back read two ways or found wrong (held in B11 and B12): step 1's Xcode and git stops said "and stop" and logged nothing, so a report written after them could not tell which stop it was; step 3's second no-report case named only one of step 1's two version checks; the finish's "the last NEXT: or WAITING ON YOU: line of the loop" had several candidates, and two agents gave different lines; a re-run whose installer `NEXT:` already said "session done" read as a reason to skip the loop, whose syncs are the only ones that download; "removing a file removes its page" (the page becomes a deletion notice); an indented `alarm:` line had no rule, so every one was logged and its advice read as a step; step 2's 10-minute timeout was shorter than the speech helper's own build limit (field: 687 s) | prompt wording | v11: both step 1 stops go to the report and log the failed command as a step 1 `error` first, when the checkout exists; step 3's second case names both checks ("the --version line" or the command); the finish names the last `NEXT:` line printed before the report, or "none", with every `WAITING ON YOU:` line of the same command under it; step 2's `NEXT:` line does not count toward the loop; the inbox sentence says a deletion notice; an `alarm:` line other than the click-Allow one is shown, not acted on and not logged; step 2 suggests 30 minutes; `test_readme_step1_is_one_command`, `test_readme_report_step_after_any_failure`, `test_readme_step3_runs_the_loop_then_the_report`, `test_readme_prompt_carries_the_field_lines`, `test_readme_step3_says_an_alarm_line_is_not_a_step`, `test_readme_step2_is_the_one_install_command` |
| K33 | friction lines are the only agent prose in the report's public part, and four short forms of registered names reached them, which redaction does not catch; a typed placeholder made two false residue hits (2026-10-07 C12) | prompt wording | v11 has the agent name a folder, a file, a person, the organization or a customer in a friction line by its role in plain words, never by its real name, a short form of it or a placeholder; a name that is itself the problem goes to `fix-request.md`, and its line is still logged by role; `test_readme_friction_line_format_and_kinds` |
| K34 | an earlier part's `<folder-N>` in `fix-request.md` can name another folder in section 1: placeholders are numbered per report in registration order, so an added folder, a listing past its 3 s bound or the shell fallback report renumbers them, between sessions and between step 2's and step 3's report of one (b11 C7 skeptic; measured again for B13) | prompt wording | v11 asks for a role that tells the folder apart, with the report's placeholder beside it, and says the numbers can change from one report to the next; triage identifies a folder by the role; `test_prompt_routes_changes_to_the_source_not_the_checkout` |
| K35 | a go-ahead the person gave in the chat for a printed fix was logged only as a `deviation`, which no count reads, so human turns left it out (2026-10-07 U4) | prompt wording | v11's `question` kind covers a go-ahead or an answer given in the chat, other than the folder answer or word of a click, as a line of its own beside any `deviation` it allowed; `test_readme_question_kind_counts_a_go_ahead_given_in_the_chat` |
| K36 | wording the v11 rehearsals read two ways or found missing (B13 round 2): the git stop gave the person nothing to do once git works (R22), and its proxy hint named only `HTTPS_PROXY`, which git's own `http.proxy` setting comes before (R29); "either of these two stops" followed three cases (R32); a header-less step 1 error lost the session's prompt and agent (R25); step 2's rule for `WAITING ON YOU:` lines was a re-run's only, though a new Mac's first sync prints one (R9), and its reinstall wait for a running sync was in no rule (R40); "step 3's report" was never defined, so whether it included the `## Not used` section was inferred (R31); the finish's "the folders synced" read two ways and had no words for a session that synced nothing (R11, R30); "I was not asked" was false when the question went unanswered (R41) | prompt wording | v12: the git stop tells the person to start a new session from a terminal where git reaches GitHub and paste the prompt again, and names git's own proxy setting; the stops it means are named; the step 1 error starts with `prompt v12, <agent>: `, which the report reads for that attempt alone; step 2 makes every `[warn]` or `WAITING ON YOU:` line the person's and names the reinstall wait as no deviation; step 3 defines its report as the `## Not used` section, then `--report-only`; the finish names the folders the installer counts, which ones this session added, or "none: this Mac syncs no folder"; the final message says no answer was given; `test_readme_step1_is_one_command`, `test_readme_step2_is_the_one_install_command`, `test_readme_prompt_carries_the_field_lines`, `test_readme_report_step_after_any_failure`, `test_readme_step3_runs_the_loop_then_the_report`, `test_readme_prompt_asks_a_mac_already_set_up_for_no_folder`, `tests/test_setup_report.py::test_a_header_less_step_1_error_states_its_prompt_and_agent` |
| K37 | wording the v12 rehearsals read two ways (B13 round 4): step 1 says to log a deviation and wait when the folder question goes unanswered, while the kind list defined a deviation as doing what the prompt did not say (Q8); the finish named `~/agent-context/bring-back.md` when no report ran, after a failed clone, or where only an earlier session's file existed (Q24); the git stop's proxy hint made git's own `http.proxy` winning over `HTTPS_PROXY` conditional on the error naming a proxy, which an unreachable proxy's error never does, and "the one" read two ways (Q25) | prompt wording | v13: the deviation kind names the step 1 stop; the finish says "none" for `bring-back.md` when no report ran this session and points to no older `bring-back.md`; the git stop's hint is one unconditional sentence that says to check git's setting when its error names a host other than github.com; `test_readme_friction_line_format_and_kinds`, `test_prompt_routes_changes_to_the_source_not_the_checkout`, `test_readme_step1_is_one_command` |
| K38 | wording the v13 rehearsals read two ways (B13 round 5): the git stop's proxy hint sent a person whose terminal holds a stale `HTTPS_PROXY` to git's own setting, which prints nothing, though `https_proxy` and `ALL_PROXY` fail the same way (F24); step 3 named two cases with no report and then required one at every other stop, while its own parenthesis ruled one out when the checkout is missing (F25); the rules asked for a session heading even when step 3 skips its `## Not used` section, so a literal agent created `fix-request.md` just to hold one (F26); that section was skipped when `~/agent-context/setup` was missing, which happens only when the checkout is missing too (F27); nothing said what to do when step 1's local-changes command fails at its last pull (F28) | prompt wording | v14: the hint says a host other than github.com in git's error is a proxy git was told to use, by its own setting (which wins) or by the terminal's `HTTPS_PROXY`, `https_proxy` or `ALL_PROXY`, and names the command that shows each; step 3 lists three cases with no report, the missing checkout the third, and skips the `## Not used` section on that one condition; the heading rule has the same exception; step 1 runs its command again after a local-changes command whose pull alone failed; `test_readme_step1_is_one_command`, `test_prompt_routes_changes_to_the_source_not_the_checkout`, `test_readme_report_step_after_any_failure`, `test_readme_keeps_local_work_then_runs_step_1_again` |
| K39 | wording the v14 rehearsals and review read two ways or found wrong (B13 round 6): the heading rule's exception named step 3's skip, which the folder wait and the version stop never reach (G5), and its last clause read as if the skipped section still went under the heading (G26); step 1's version stop always said to copy the prompt again, while the installer's error for a newer copy whose checkout follows a fork or another branch says to point the checkout at main, and called every such copy older (G15, G67); the git stop's proxy hint set its two commands in no code marks (G24) and called every host other than github.com a proxy, though a URL rewrite (`insteadOf`) names one too (G73); step 3's report came "always", just before three cases with none (G25); after step 2's reinstall wait ran out the first sync waited again, up to 10 minutes, in WARNING lines the prompt never named (G37) | prompt wording | v15: a session that asks for no change and adds no `## Not used` section writes no heading, and step 3's three cases have neither; the version stop passes on what the installer's error says, in its words, and only without one says to copy the prompt again, then to point the checkout at main if that copy stops too; the hint names a URL rewrite with `git config --get-regexp 'url\..*insteadof'` and a proxy with git's setting (which wins) or the terminal's variables, `all_proxy` included, each command in code marks; the report comes "even after a failure (except in the three cases below)"; the installer says `another sync is still running; waiting for it to end, at most 10 minutes`, and the prompt calls both waits and a skipped first sync expected; the report's last lines are an issue link, "none when the report says it is for a private copy only" (lane RB's words, G47/G65); `test_readme_step1_is_one_command`, `test_prompt_routes_changes_to_the_source_not_the_checkout`, `test_readme_report_step_after_any_failure`, `test_readme_prompt_names_its_version_and_the_installer_compat_line`, `test_readme_step2_is_the_one_install_command` |
| K40 | wording the v15 rehearsals and review read two ways or found wrong (B13 round 7): the finish's folder list was step 1's `[synced]` marks plus step 2's additions, so a list that could mark nothing (an installed agentsync too old for its reader, the reader's timeout, a config it cannot load) with step 2 keeping the folders gave a false "none" (H9, H30); at the folder wait the finish's "last NEXT: line printed before the report" had no report, and the folder list's NEXT: line read as the answer (H6); a checkout with a local commit fails the pull with "Not possible to fast-forward", which the prompt did not call local changes, and the keep command then failed at its commit and left the checkout on a branch with no upstream, so every later paste got the network message (H19); the git stop's quote was one 106-word sentence (H21) and its rewrite query also printed push rewrites (H25); after a stop in step 1 the `## Not used` section asked for twelve lines with one reason (H26); a v8 to v14 installer's error always says what to tell, so the checkout sentence never came (H65); the version stop said to run no other step while the installer's error says to run step 1 again for a changed value (H70); step 2 tied the first sync's lock wait to the reinstall's (H66) | prompt wording | v16: the folder list is `sources.toml`'s live `kind = "local"` sources every time, and "added" is each folder for which a run of step 2 printed add-source's `added source` line; at the folder wait the NEXT item is "none: waiting for your folder answer"; the prompt names git's words for both kinds of local change, the keep command first checks that main has no commit of its own the checked-out branch lacks, commits only what is staged, writes a patch per commit the update lacks and moves main to the update (`switch -C main origin/main`), with its last two steps to run again and `git switch main` for a checkout a v15 keep left on its branch; the git stop's quote is sentences ending on the action, with `git config --get-regexp '^url\..*\.insteadof$'`; one line suffices for `## Not used` after a stop before step 2; the checkout sentence follows any error whose words do not mention the checkout; a copy whose installer is for this prompt's own number runs step 1 again once; the two waits are separate sentences; `test_readme_step1_is_one_command`, `test_readme_keeps_a_local_commit_and_moves_main_to_the_update`, `test_a_checkout_a_v15_keep_left_on_its_branch_goes_back_to_main`, `test_the_keep_command_leaves_a_main_with_commits_of_its_own_as_it_is`, `test_readme_keeps_local_work_then_runs_step_1_again`, `test_readme_prompt_names_its_version_and_the_installer_compat_line`, `test_readme_step2_is_the_one_install_command`, `test_readme_step3_runs_the_loop_then_the_report`, `test_prompt_routes_changes_to_the_source_not_the_checkout`, `test_readme_report_step_after_any_failure` |
| K41 | wording the v16 rehearsals and review read two ways or found wrong (B13 round 8): at the folder wait, and at any stop before step 2, the finish said to read `sources.toml`, which step 1 does not write (J6); on a branch with no upstream, or when the keep command stopped at its first step, the prompt's `git switch main` refused an uncommitted edit to a file main changes, so step 1 looped, and that path copied none of the branch's commits to `local-work` nor told the person where they were (J7, J8); a folder at `~/src/agent-context-sync` that is not a git checkout made the clone fail with words the prompt did not name, so the person got the proxy message, and the log and report steps, keyed on the folder, ran an installer that is not there (J19); v16's re-wrap left a line holding only "If" (J68) | prompt wording | v17: the "none" form covers a `sources.toml` that does not exist yet; the no-tracking path keeps the branch's work on a new `local-work-<date>` branch and as patches (`format-patch origin/main..`), leaves the person's branch as it was, goes back to main and gives main `origin/main` as its upstream when it has none, and the person is told both branch names; git's "already exists and is not an empty directory" is its own stop, and the step 1 log, step 3's skip and the third no-report case key on `scripts/install.sh`, not the folder; the block is re-wrapped with no lone word; `test_readme_step1_is_one_command`, `test_readme_keeps_a_local_commit_and_moves_main_to_the_update`, `test_a_branch_with_no_upstream_and_an_edit_main_changes_is_kept_and_main_updated`, `test_a_main_with_no_upstream_gets_the_sources_main_and_does_not_loop`, `test_a_folder_at_the_checkouts_path_that_is_not_a_checkout_is_named`, `test_readme_report_step_after_any_failure`, `test_readme_step3_runs_the_loop_then_the_report`, `test_no_line_of_the_prompt_block_is_a_lone_word_or_wider_than_the_block` |
| K42 | wording the v17 rehearsals and review read two ways or found wrong (B13 round 9): where step 1's list could not read an existing config, an agent that could not ask logged a deviation and waited, though the list's own `NEXT:` said a run with no folder keeps that config (K9, K20, K32, K73, K78); on a main with nothing to keep the no-tracking recovery still had the agent say my commits stay on my branch and were copied as patches (K12), and it printed a bare `refs/heads/main` (K13); the finish's "the folder list's NEXT: line is not a step for me" read as covering the sign-in stops too (K19); an empty or broken `.git` got the proxy advice, and under a home that is itself a repository git climbed to it (K25); a second keep overwrote a first keep's patch of the same name (K28); the git stop's three commands did not say whose they are (K31) | prompt wording | v18: a second exception in step 1 for a list that could not read the config (the offered re-run once, then at most whether to add a folder; unattended, step 2 with no `--source-local`, no deviation), and the report counts no turn for it from v18 on; the no-tracking recovery prints the count of commits it kept and the words key on it, and tests main's upstream inside `$( )`; the finish's clause covers only the folder wait; step 1 pulls only when `.git/HEAD` exists, and git's "not a git repository" and "detected dubious ownership" are named stops; each kept patch's name carries its keep's branch; the git stop's commands are the person's; `test_a_folder_list_that_could_not_read_the_config_goes_on_unattended`, `test_readme_prompt_asks_a_mac_already_set_up_for_no_folder`, `test_the_branch_recovery_prints_what_it_kept_and_no_bare_ref`, `test_a_second_keep_leaves_the_first_keeps_patch`, `test_a_folder_at_the_checkouts_path_that_is_not_a_checkout_is_named`, `test_readme_step3_runs_the_loop_then_the_report`, `test_readme_step1_is_one_command` |
| K43 | wording and commands the v18 rehearsals and review read two ways or found wrong (B13 round 10): in a home that is itself a git repository, a checkout whose `.git` held only HEAD let git search up to the home, and the no-tracking recovery committed the home's files there (L6, L61); a checkout whose `.git` is a file was told it is not one (L27); on a main with a commit of its own and no upstream, the recovery left main behind, so the next keep kept the same commit twice (L9); the branch suffix cut each kept patch's subject to about 25 characters (L17, L30); the not-a-checkout, ownership, Xcode and git stops ran whatever installer the folder held (L31); after an offered re-run read the config, the second exception still said it could not (L62); after step 2's "no folder is synced yet" the wait did not say which report to name or how to add the answer (L10); the git stop did not point at the report's proxy lines (L74); a config the installed agentsync failed on was said to be kept (L11, lane RB) | prompt wording | v19: step 1's command and each git recovery export `GIT_CEILING_DIRECTORIES="$HOME/src"`, and step 1 also pulls when `.git` is a file, with git's "invalid gitfile format" a not-a-checkout stop; the no-tracking recovery moves main to the update when the new branch keeps all of main; both keep commands set `format.filenameMaxLength=128`; the not-a-checkout and ownership stops run no installer and write no report, and the Xcode and git stops report only through this prompt's installer; the second exception holds only while the last list still says the config could not be read, and claims no keep where agentsync failed on it; the wait after step 2 is step 1's, named "none" in the finish, and the answer goes to step 2 again; the git stop names the two proxy entries both reports print; `test_step1_and_its_recoveries_never_reach_a_repository_above_the_checkout`, `test_a_checkout_whose_git_is_a_file_pulls`, `test_the_branch_recovery_prints_what_it_kept_and_no_bare_ref`, `test_a_kept_patch_name_keeps_its_whole_subject`, `test_readme_step1_is_one_command`, `test_a_folder_list_that_could_not_read_the_config_goes_on_unattended` |
| K44 | wording and commands the v19 rehearsals and review read two ways or found wrong (B13 round 11): in a second worktree of another checkout, the recoveries' `switch -C main origin/main` moved the main the other checkout has out under its files on Apple git 2.39.5, or refused with words the prompt did not name (M69); the worktree stop named only the local-work branch (M17); a keep with nothing to keep left an empty branch both reports counted (M83); the git stop's report entries were unmarked and said "only set or not set" (M21, M33); "no pull ran" was false at the git stop, and an older installer's stop gave no next step (M32, M16); the finish had no words for a sources.toml that does not load (M28) and said bring-back.md holds patches an earlier report carried (M35); step 3 pointed at "step 1's last sentence" for a rule that is not there (M74); a signed-out OneDrive sent an unattended agent on to a step 2 that fails (M4, lane RB) | prompt wording | v20: each recovery goes back with `switch main` and `reset -q --keep origin/main`, which refuse a main checked out elsewhere on every git; the worktree stop is its own, also covers the keep command and its retry, names both of git's phrases, the person's branch and the local-work branch, and gives `git worktree move` or `repair` for a second worktree, or taking the other worktree off main for a main checkout; the Rules line names removing an empty local-work branch; a count of 0 deletes the empty branch in the same command; the entries are in code marks and "say whether each is set"; the stops say step 1 did not update the copy and that the next paste tries the update first; the finish says "unknown: ~/agent-context/sources.toml does not load" and that a patch an earlier report carried is listed by its hash; step 3 names the rule; step 1's first exception waits for an OneDrive sign-in its list asks for; `test_readme_prompt_asks_a_mac_already_set_up_for_no_folder`, `test_a_recovery_in_a_second_worktree_leaves_the_other_checkouts_main`, `test_the_worktree_stops_commands_keep_a_moved_worktree`, `test_a_recovery_in_the_main_checkout_whose_worktree_has_main_stops`, `test_an_empty_keep_on_the_way_back_to_a_main_of_its_own_is_deleted`, `test_the_branch_recovery_prints_what_it_kept_and_no_bare_ref`, `test_readme_step1_is_one_command`, `test_readme_step3_runs_the_loop_then_the_report`, `test_prompt_routes_changes_to_the_source_not_the_checkout`, `test_readme_report_step_after_any_failure` |
| K45 | wording the v20 rehearsals and review read two ways or found wrong (B13 round 12): the sign-in step 1's first exception waits for was logged as a `click`, so a clean run read "worked with help" (N5, N32); that wait had no rule for nobody to ask (N6, N32); whether the same sign-in on a Mac that syncs no folder yet is "a click for me" read two ways (N7); "did not finish" read as cut off at the git stop, where the pull ran and failed (N27); the keep command and its retry left a main with no upstream, so the next pull met "no tracking information" and ran a second, empty keep (N76); and lane RB's installer changes needed words: the sign-in names the app (N61, N41), a config the installed agentsync cannot read adds no folder (N29), the shell report's "loads: not checked" (N25) and a bring-back.md that could not be written (N71) | prompt wording | v21: the `click` kind leaves out the sign-in this prompt announced and the `question` kind word of it; with nobody to ask, the sign-in wait logs nothing and goes to the report; on a Mac that syncs no folder yet a sign-in is not a click, so its NEXT: goes to the report as before; the stops say the pull "did not run, or failed"; the keep command and its retry end by giving main the Source's main as its upstream when it has none; the first exception names the app the list names; the second exception asks nothing about folders when step 2 does not reinstall agentsync; the finish has words for "loads: not checked" and for a bring-back.md that could not be written; `test_readme_prompt_asks_a_mac_already_set_up_for_no_folder`, `test_readme_question_kind_counts_a_go_ahead_given_in_the_chat`, `test_readme_step1_is_one_command`, `test_a_keep_from_a_branch_leaves_main_with_an_upstream`, `test_readme_keeps_local_work_then_runs_step_1_again`, `test_readme_step3_runs_the_loop_then_the_report` |
| K46 | wording the v21 rehearsals and review read two ways or found wrong (B13 round 13): step 2's NEXT after a [FAIL] whose fix is the person's (a synced folder renamed in a signed-in OneDrive) read as a re-run or as the agent's edit of sources.toml (O11, O30); the list's "no longer on disk" tail had no words, so the renamed folder was offered as a new one (O30); the finish's "name the files the NEXT: line names" took in the bring-back.md that NEXT says not to send (O15); with no installer at the Xcode or git stop, "Otherwise" told the person the code on the Mac was not this prompt's version (O27); the finish's "loads: not checked" case gave no order and did not forbid parsing the file, and its "loads: no" case also matched it (O28, O44) | prompt wording and installer automation | v22: step 2 says a [FAIL] line whose fix edits sources.toml or asks for a sign-in, rename or sync is the person's, and re-running is the agent's, and install.sh's NEXT for such a run says to show the lines and go on to the report; the first exception says which folder is no longer on disk and adds none in its place; the finish names the files the NEXT: line says to copy back; step 1's version check has a branch for no installer; the not-checked case comes first and forbids parsing, and the does-not-load case keys on "loads: no:"; `test_readme_prompt_asks_a_mac_already_set_up_for_no_folder`, `test_readme_step1_is_one_command`, `test_readme_step3_runs_the_loop_then_the_report`, `test_prompt_routes_changes_to_the_source_not_the_checkout` |
| K47 | wording the v22 rehearsals and review read two ways or found wrong (B13 round 14): step 3 said status prints the same NEXT: line as a sync, but with a check that FAILs it names that check and exits 1 (Q2); whether to log step 2's person-owned stop ("not a step for the agent", then the re-run once fixed) read both ways, and unlogged the report had no item for it (Q7); the inbox sentence came after step 2's jump to the report, so a failed step 2 never explained it (Q8); the first exception keyed on "no longer on disk" and missed the list's "is not on disk because" tails, whose fix is a sign-in or a library sync, not putting the folder back (Q15, Q73); "log the failed command" logged step 1's whole one-liner, whose error the Items line cut off (Q31); the Xcode and git stops gave both stops' causes, GitHub at the Xcode stop (Q32) | prompt wording and report parsing | v23: step 3 says status's NEXT names a FAILing check and that it exits 1, and a fix that is the person's stays theirs; step 2 logs a person's stop as a step 2 error, shows the lines, goes to the report and runs no re-run; the inbox sentence comes first, when sources.toml has an inbox; the first exception keys on "on disk" and relays each tail's fix; step 1 logs only the command that failed, and the report keeps a logged `||` and the error; each stop names only its own cause; `test_readme_step3_runs_the_loop_then_the_report`, `test_status_next_differs_from_a_syncs_only_on_a_failing_check`, `test_readme_prompt_asks_a_mac_already_set_up_for_no_folder`, `test_readme_prompt_carries_the_field_lines`, `test_readme_step1_is_one_command` |
| K48 | wording the v23 rehearsals and review read two ways or found wrong (B13 round 15): the first sync's refusal of a config that loads ("configuration error: ...", exit 78) sent the agent down the does-not-load case and past the inbox, while the report said `- loads: yes` with no reason (S8, S55); step 1 said to log git's error while the friction rule names the organization only by role, and git's error can name a host carrying it (S6); whether to log a failed step 2 run whose NEXT re-run the agent follows read both ways (S18); status's NEXT names a [FAIL] line only after rule 1's skill and sign-in steps (S60); lane RB's status that exits non-zero with no [FAIL] line had no words in step 2's bracket (S33, S58, S62) | prompt wording and report parsing | v24: the report says `- loads: yes, but a sync refuses it: ...` (or `step 2's sync refused it`), the inbox skip keys on "does not load", the does-not-load case on "loads: no:" alone, and the finish gives the refusal after the folders or the "none"; step 1 logs the error as printed, host names included, and says which hosts the report replaces; a failed run whose NEXT the agent follows is not logged; status's NEXT names a [FAIL] line unless a missing or out-of-date skill copy or a sign-in comes first; the bracket names a status error with no [FAIL] line; `test_a_first_sync_refusal_is_no_config_that_does_not_load`, `test_readme_step2_does_not_log_a_failed_run_whose_next_it_follows`, `test_status_next_names_rule_1s_own_steps_before_a_fail`, `test_readme_step1_is_one_command`, `test_readme_step3_runs_the_loop_then_the_report` |
| K49 | wording the v24 rehearsals and review read two ways or found wrong (B13 round 16): step 2's NEXT said a config that loads but init or add-source refuses (a bad policy.toml or [governance] value) "does not load", so the inbox was skipped against the report's `- loads: yes, but` (T1, T10, T29, T45, T67, T81); step 1 said "(none is needed)" for a folder still mirrored whose [[source]] block is gone, and step 2 failed on it (T6, T30); step 2's person's-fix sentence left out Rosetta, a native pandoc copy, a block to put back and a [policy] or [governance] value, and its re-run clause read as licensing Rosetta's command and had no stop for a line that comes back after it (T70, lane B's T5, T2, T9, lane E's pandoc fix); step 1 and the list gave a folder unticked in a signed-in app no sync fix (T72) | prompt wording and installer automation | v25: config_failed asks the installed agentsync whether the config loads and says "loads, but agentsync refuses it" when it does; step 1 reads the manifest read-only and names the folder one --source-local adds back, and the prompt tells the person and adds it only if they name it; step 2's sentence names every kind of person's fix and otherwise leaves only an install.sh re-run to the agent, once; the list and step 1 name "not synced on this Mac" and the sync fix; `test_a_first_sync_refusal_is_no_config_that_does_not_load`, `test_readme_prompt_asks_a_mac_already_set_up_for_no_folder`, `test_the_deploy_guides_say_a_set_up_mac_with_a_missing_folder_exits_3` |
| K50 | wording the v25 rehearsals and review read two ways or found wrong (B13 round 17): step 1 said "(none is needed)" over a policy.toml, [governance] value or state file step 2's config step then stopped on (U12); step 1's git quote listed three causes of another host and none was a company mirror set as the checkout's origin, and "Once git can reach GitHub" fixed nothing there (U32); its log rule left a git remote's host out of what the report replaces (U97); step 2's person's fixes left out a docs-repo remote [governance] does not allow, which the agent was sent to delete (U2, U16), and the bundled pandoc's fix, which names a re-run and then a [convert] edit (lane E's U13, U47, U79, U80); the finish relayed nothing of a report whose bring-back.md lacks a kept patch it could not read (U29) | prompt wording and installer automation | v26: step 1's list says what step 2's config step will stop on and the prompt tells the person; the quote names the checkout's own remote first; the log rule names a git remote; step 2's sentence keys on a fix's first words and names the remote; the finish tells the person which patch to make readable; `test_the_prompt_text_changes_only_with_its_version`, `test_readme_step1_is_one_command`, `test_readme_prompt_asks_a_mac_already_set_up_for_no_folder` |
| K51 | wording the v26 rehearsals and review read two ways or found wrong (B13 round 18): step 1's list said "(none is needed)" over a docs-repo remote [governance] does not allow and over a damaged table page, which step 2 then stopped on, the second at its status or first sync, not its config step (V9, V21, V36); step 1's git quote named `remote get-url origin`, which a URL rewrite changes, as the checkout's own remote, and named it after a clone that failed, when there is no folder (V33, V70); step 3 said a sync prints nothing while it works, but it prints a lock wait and an inbox settle wait, which an agent logged as deviations, and a sync that waited out the lock ends "skipped: lock held" with no NEXT: line (V24, V39, with lane B's V40 words); the finish asked which kept patch file to make readable, and no line names one, by design (V12, V17, V32, V79) | prompt wording and installer automation | v27: step 1's list names the remote and the damaged state file, and the prompt tells the person of a stop at step 2's config step, status or first sync; the quote names `config --get remote.origin.url`, if the checkout exists; step 3 calls the waits expected and runs a lock-skipped sync once more; the finish gives the count, the chmod and the report command, and names no file; `test_the_prompt_text_changes_only_with_its_version`, `test_readme_step1_is_one_command`, `test_step_3_calls_a_syncs_waits_expected_and_retries_a_lock_skip` |
| K52 | wording the v27 rehearsals and review read two ways or found wrong (B13 round 19): step 1's git quote checked the checkout's remote "if ~/src/agent-context-sync exists", but a clone that failed into a folder that already existed leaves that folder, and the query then printed a repository above it, such as a dotfiles repo in ~ (W26); step 1's no-answer sentence for a Mac that already syncs folders came right after the not-on-disk sub-case, so it could be read as bound to it, and a Mac whose kept folders are all on disk would then fall back to the general rule, a logged deviation and a stop (W28) | prompt wording | v28: the quote checks the remote if ~/src/agent-context-sync/.git exists, since a clone that failed leaves no checkout; the no-answer sentence holds "whether or not that line names such a folder" and keeps any not-on-disk folder and its fix for the final message; `test_the_prompt_text_changes_only_with_its_version`, `test_readme_step1_is_one_command`, `test_readme_prompt_asks_a_mac_already_set_up_for_no_folder` |
| K53 | wording the v28 rehearsals and review read two ways or found wrong (B13 round 20): step 1's log rule said the report replaces the host of a "Could not resolve" or "Failed to connect to" message and of a git remote, but it keeps a public host such as github.com (X24); step 1 told the person every stop step 1's list names is theirs, but a state file of this account's own that it cannot open is a chmod the agent runs (X9, X26, X29); step 2's person's fixes said "a state file that cannot be read", which read as that same file (X26); the finish did not relay the report's NEXT when part of the report was cut off or not measured, and the report is the last command the agent runs (X3) | prompt wording | v29: the log rule adds "but keeps a public host such as github.com"; step 1 tells the person what the list names and whose fix it says that is; step 2 names "a state file that is damaged or another account's"; the finish relays the cut-off NEXT in its own words, with the --report-only re-run left to the person; `test_the_prompt_text_changes_only_with_its_version`, `test_readme_step1_is_one_command`, `test_readme_prompt_asks_a_mac_already_set_up_for_no_folder`, `test_prompt_routes_changes_to_the_source_not_the_checkout` |
| K54 | wording the v29 rehearsals and review read two ways or found wrong (B13 round 21): step 1 said the chmod of this account's own state file is the agent's once step 2 stops on it, but step 2 let the agent follow a failed run's NEXT only when it was an install.sh or agentsync command or a click, its [FAIL] rule only re-ran install.sh, and step 2's NEXT was "fix the error above", which names no command (Y4, Y10, Y24, Y59) | prompt wording | v30: step 2 grants a chmod 600 or chmod 700 of agentsync's own state folder or a file in it, once, when the NEXT: line or a [FAIL] state_dir line names it as the fix, and keeps a sudo or a chown the person's; install.sh's NEXT names that chmod; `test_the_prompt_text_changes_only_with_its_version`, `test_readme_step2_gives_the_agent_the_chmod_of_its_own_state` |
| K55 | wording the v30 rehearsals and review read two ways or found wrong (B13 round 22): step 2 granted the chmod only when the NEXT: line or a [FAIL] state_dir line named it, so a lock, heartbeat or governance file at 0000 sent a literal agent to the report (Z59, Z8); "once" read as one chmod, while a shut folder holding a shut file needs two (Z81); step 3 had no words for the same chmod (Z38); the person's kinds named only "a state file that is damaged or another account's" (Z65); and the finish did not relay the report's NEXT about a local-work branch's commits (Z30, Z71) | prompt wording | v31: step 2 grants each chmod 600 or chmod 700 of agentsync's own state folder or a file in it (the manifest, its -wal or -shm, the lock, the heartbeat or a governance file) that the NEXT: line or any [FAIL] line (state_dir, lock, heartbeat or governance) names, once each, in one pass, a folder's first, with the status a shut folder's NEXT names; step 3 grants it too, once each in that step; the kinds name a state file or folder an ACL, a privacy setting, a folder above it or a read-only volume refuses, or one that is not a file; the finish relays the local-work NEXT, names no branch and runs nothing; `test_the_prompt_text_changes_only_with_its_version`, `test_readme_step2_gives_the_agent_the_chmod_of_its_own_state`, `test_readme_step3_gives_the_agent_the_same_chmod`, `test_readme_finish_relays_the_local_work_next` |
| K56 | wording the v31 rehearsals and review read two ways or found wrong (B13 round 23): a step 3 sync that stopped on a chmod the agent then ran left no record, and the report said "fully one command, 0 error" over it (A36); the person's kinds did not name a state folder that is not a folder (ruling 6, lane B's words) | prompt wording | v32: step 3 logs the sync that stopped as a step 3 error with its exit code and the chmod it ran, and the report reads a step 3 error as worked with help; step 2's kinds name a state folder that is not a folder; `test_readme_step3_gives_the_agent_the_same_chmod` (the report's half: test_setup_report.py) |

## 5. Unavoidable steps so far

Each one has its evidence. A report showing that one of them can be avoided moves it into a fix class above.

Current unavoidable human steps (these do not break "fully one command"):

- **Choosing the folders.** The one question the prompt asks on purpose: what to sync is the user's decision.
  It is asked once per Mac. On a Mac that already syncs folders the choice is in `sources.toml`: the prompt
  asks at most whether to add one, and an agent that cannot ask adds none and goes on (K20).
- **The TCC Allow click** ("wants to access files managed by OneDrive"), for the terminal (step 1). Setup no
  longer asks a second one: the click for `agentsync-launcher` comes only with the operator's optional background
  sync (`install.sh --confirm-install-agent`, KISS K11b), never in a setup run. The launcher's click goes away when MDM grants Full Disk Access to the
  Developer-ID-signed launcher ([the one-time Allow click](README.md#the-one-time-allow-click),
  [mdm/README.md](mdm/README.md)). After a denied prompt the person turns `agentsync-launcher` (or the terminal)
  on in System Settings > Privacy & Security > Files and Folders (a click the prompt forbids the agent), and the
  agent re-runs the command the `NEXT:` line names. That re-run is free in step 1, where the command is
  `install.sh --list-folders`. A click that comes only after step 2's install command failed (a listing
  macOS held, or a terminal denied at the first sync) cost a second install run, so that report reads "worked
  with help" (section 4, "The computed outcome").
- **The OneDrive sign-in step 1 waits for** (or the sign-in to the app the list names), on a Mac that already
  syncs folders whose app is signed out or still setting up. Step 1's list exits 3 with `note=not-signed-in
  apps=<app> missing=M synced=N` and keeps the `[synced]` folders. Since prompt v20 the agent waits for the sign-in as for a click, then
  lists the folders again (K44; B13 round 11 M4). Since v21 the prompt logs it as neither a click nor a question;
  the report counts it among the expected turns for any v20 or later attempt (`the OneDrive sign-in step 1 asked
  for`; K45, B13 round 12 N5), so a clean run still reads "fully one command". An agent that
  cannot ask does not wait: it goes to the report.
- **IT consent** for Outlook, Teams and unsynced SharePoint: an Entra admin must register the app and consent
  ([it-request.md](it-request.md)). `agentsync it-request` only drafts the request; the person sends it.

Moved out of this list: **Agent-tool approvals**, the commands the coding agent's tool asks the person to approve.
The operator pre-allows them in the tool's settings with the tested rules for Claude Code and GitHub Copilot CLI
under "Fewer approval prompts" in the README's [one-prompt setup](../../README.md#set-up-on-a-new-mac-one-prompt),
so a logged `approval` line makes the outcome "worked with help". Since v6 the rules cover every command in the
block: it logs through `install.sh --log` and has no `>>` redirect, which Claude Code's documentation says always
needs approval when its target starts with `~` (K13). The rules cover commands only: since v7 the loop also has
the agent write files (the baseline questions in `~/agent-context/docs/_eval/`, and in later sessions subject
pages), and each of those writes still asks in a tool's default mode, so a v7 run that reaches the draft may log
an `approval` line for them. What the rules cannot change: neither tool tells the agent about an approval
(section 4: "not observable").

Preconditions, outside the setup's count because the prompt stops at step 1 and says why:

- **Installing the Xcode Command Line Tools** when they are missing. `xcode-select --install` opens a macOS dialog,
  and a managed Mac may need an admin or Self Service.
- **Signing in to OneDrive** before setup, on a Mac that syncs no folder yet.
  The OneDrive app owns that sign-in. Step 1's list exits 3 and the prompt shows its `NEXT:` line and goes to the
  report.
