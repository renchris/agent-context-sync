# Probes

Small macOS programs that measure the facts the design rests on: whether a file is an online-only placeholder,
whether reading it downloads it, what the kernel's per-file change counter says, and which file events fire. They
produced the File Provider receipts in
[`../docs/design/receipts/verify/C14-file-provider.md`](../docs/design/receipts/verify/C14-file-provider.md) and the
Office re-save receipt in [`C12-office-resave.md`](../docs/design/receipts/verify/C12-office-resave.md).

macOS only (tested on 15.7.9, Apple silicon). Build with the system toolchain:

```sh
make -C probes          # builds probes/bin/{walkfp,readfp,gen1,fswatch,evict} with /usr/bin/clang and /usr/bin/swiftc
```

The Makefile calls the compilers by absolute path because some shells alias or shadow `cc`.

For the File Provider probes you need a folder inside a OneDrive (or other File Provider) mount, for example
`~/Library/CloudStorage/OneDrive-Contoso/agentsync-probe/`, with a few test files in it. Nothing here writes to a
file's contents; `evict` only makes a file online-only again.

## `evict` — make a file online-only

```console
$ probes/bin/evict blob.bin
evicted blob.bin
$ stat -f '%Sf  %z bytes  %b blocks' blob.bin
compressed,dataless  2000000 bytes  0 blocks
```

Calls `FileManager.evictUbiquitousItem(at:)`. A placeholder keeps its full logical size and has zero allocated blocks.

## `readfp` — read a file under a chosen materialization policy

`readfp <on|off|default> <path>` sets the process's `IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES` policy (or leaves the
inherited one with `default`), reads the whole file, and reports what happened:

```console
$ probes/bin/readfp off blob.bin
policy=off(1)  before: flags=0x40000060 (dataless) blocks=0
open=ok  read=Resource deadlock avoided (errno 11)  bytes=0  0.000s
after:  flags=0x40000060 (dataless) blocks=0

$ probes/bin/readfp default blob.bin          # from a login shell, the inherited policy is on
policy=on(2)  before: flags=0x40000060 (dataless) blocks=0
open=ok  read=ok  bytes=2000000  0.693s
after:  flags=0x40 blocks=3912
```

`open()` succeeds on a placeholder; the refusal comes at `read()`. Right after OneDrive signs in, a read can also fail
`ETIMEDOUT` (errno 60) while the provider warms up; that is a retry, not a verdict.

## `launchd-run.sh` — run a probe as a launchd job

`launchd-run.sh <command> [args...]` submits one command with `launchctl submit`, waits for it, prints its output and
removes the job. It is how the probes see the context a scheduled sync runs in:

```console
$ probes/launchd-run.sh probes/bin/readfp default /dev/null
policy=off(1)  before: flags=0x0 blocks=0
open=ok  read=ok  bytes=0  0.000s
after:  flags=0x0 blocks=0
```

A launchd job starts with the policy **off**. In C14 §2 the same job reading a placeholder failed with `EDEADLK`
(errno 11) after 1.66 s, and succeeded once the job called `setiopolicy_np(…_ON)` first or its plist set
`MaterializeDatalessFiles`.

> **Observed 2026-09-24, not yet explained.** A freshly built, unapproved `readfp` submitted as a launchd job against a
> File Provider path blocked inside `open()` for more than 20 s instead of returning `EDEADLK`. The same job read a
> local file at once. A per-app access check on File Provider paths is the likeliest cause; if a consent dialog
> appears, it comes from this. Test the launchd path against a local file first.

## `walkfp` — walk a tree with `getattrlistbulk`

`walkfp <dir> [-v]` walks a directory tree with one `getattrlistbulk` call per directory and zero file opens, and
counts how many entries return `ATTR_CMN_GEN_COUNT` and how many are placeholders. `-v` prints every entry.

```console
$ probes/bin/walkfp walk                      # 20 directories x 100 files inside the OneDrive folder
files=2000 dirs=21 gen_count_returned=2000 gen_count_nonzero=2000 dataless=0 wall=0.004s

$ probes/bin/walkfp /tmp/acs-walk             # the same tree on local APFS
files=2000 dirs=21 gen_count_returned=2000 gen_count_nonzero=2000 dataless=0 wall=0.002s

$ probes/bin/walkfp fresh -v                  # a file created on the web, never downloaded
  fresh/Document.docx fileid=1129073066 gen_returned=1 gen=1 flags=0x40000060 DATALESS
files=1 dirs=1 gen_count_returned=1 gen_count_nonzero=1 dataless=1 wall=0.001s
```

The first walk of a directory the provider has not listed yet is a round trip to the provider (0.044 s on this tree);
after that the cost matches local disk. `GEN_COUNT` also moves when a file is evicted and downloaded again, so a moved
counter means "hash it to confirm", never "edited".

## `gen1` — one file's id and change counter

```console
$ probes/bin/gen1 note2.txt
fileid=1128163271 gen=6 flags=0x40 gen_returned=1
```

Uses `getattrlist`, which returns `GEN_COUNT` only with `FSOPT_ATTR_CMN_EXTENDED`; without it the call fails `EINVAL`.

## `fswatch` — log file-level FSEvents

`fswatch <out.tsv> <path>...` appends one line per event (`epoch  flags-hex  flag-names  path`) and runs until stopped.
Watch the canonical `~/Library/CloudStorage/…` path, not a symlink to it: FSEvents reports resolved paths. In C14 §4 a
web-side edit to a downloaded file arrived as `Modified,ChangeOwner,XattrMod` within about 20 s; a new folder raised
only its directory event.

## Office no-op re-save

`agentsync-office-resave-auto.sh` opens a copy of `a.xlsx`, `a.docx` and `a.pptx` from `~/Documents/agentsync-probe/`
in the real Office apps, has each app save twice with no net edit, and compares the two saves part by part. It needs an
**activated** Office (an unactivated copy opens files read-only and cannot save) and any Python 3 (`PY=` to choose one).
`agentsync-office-resave.sh` is the earlier, operator-driven version. The recorded result (C12 §6 table):

| Format | Parts that differed between the two saves | Rollup excluding `docProps/*` | Converter output (H2) |
|---|---|---|---|
| `.pptx` | `docProps/core.xml` | equal | equal |
| `.xlsx` | `docProps/core.xml`, `xl/workbook.xml` (a fresh `documentId` GUID) | differs | equal |
| `.docx` | `docProps/core.xml`, `word/document.xml`, `word/settings.xml` (`w:rsid*` ids) | differs | equal |
