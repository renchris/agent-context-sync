"""CONTRACTS §16.300.18 (U-B11): install.sh's folder list. ``--all`` and ``--limit`` size it with no new
stdout line (the "(N more)" line stays exact and the NEXT names the --all command), a name holding a control,
bidi or zero-width character prints escaped on one row of its own, and the shell and report readers of
install.out register the same names. Every run is /bin/bash 3.2, through test_install_oneshot's harness."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from agentsync import arm_local, setup_report
from agentsync.config import local_source_table
from agentsync.ops import doctor
from test_install_oneshot import (  # noqa: F401  (fixtures)
    BASH32,
    HELD_LIST_NEXT,
    INSTALL_SH,
    LIST_NEXT,
    LOOK_ALIKE_WORDS,
    SHELL_HELPER_FNS,
    TWIN_MARK,
    _cloud,
    _inbox,
    _tool_python,
    _write_config,
    env,
    install_log,
    install_out,
    install_sh,
    one_next,
    report_path,
    steps,
)

ESCAPED_MARK = "[name holds a hidden or control character: rename it to sync it]"
"""install.sh's ``ESCAPED_MARK``, as test_install_oneshot pins it."""

PAST = re.compile(r"; (\d+) folders? past the first (\d+) (?:are|is) not listed: \S+ --list-folders --all$")
"""The clause a capped list's NEXT ends with."""


def _many(env: dict[str, str], n: int = 205) -> Path:  # noqa: F811
    lib = _cloud(env) / "OneDrive-Contoso"
    for i in range(n):
        (lib / f"P{i:03d}").mkdir(parents=True)
    return lib


@pytest.mark.parametrize("opt", ["--all", "-a"])
def test_list_all_prints_every_folder_with_no_more_line(env: dict[str, str], opt: str) -> None:  # noqa: F811
    lib = _many(env)
    cp = install_sh(env, "--list-folders", opt)
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    assert lines[:-1] == [f"{lib}/P{i:03d}" for i in range(205)], lines[-3:]
    assert one_next(cp) and lines[-1].startswith("NEXT: choose the folders") and not PAST.search(lines[-1])
    log = install_log(env)
    assert log[0].endswith(" args=--list-folders"), log[0]
    assert steps(log) == [("list-folders", "done", "0", "listed-205")]


@pytest.mark.parametrize("opt", [["--limit", "3"], ["-n", "3"], ["--limit=3"], ["--limit", "003"]])
def test_list_limit_3_keeps_the_more_line_and_names_the_rest_on_next(
    env: dict[str, str],  # noqa: F811
    opt: list[str],
) -> None:
    lib = _many(env)
    cp = install_sh(env, "--list-folders", *opt)
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    assert lines[:-1] == [f"{lib}/P000", f"{lib}/P001", f"{lib}/P002", "(202 more)"], lines
    assert one_next(cp) and lines[-1].startswith("NEXT: choose the folders"), lines[-1]
    found = PAST.search(lines[-1])
    assert found is not None and found.groups() == ("202", "3"), lines[-1]
    log = install_log(env)
    assert log[0].endswith(" args=--list-folders"), log[0]
    assert steps(log) == [("list-folders", "done", "0", "listed-205")]


@pytest.mark.parametrize(
    ("opt", "past"),
    [
        ([], None),  # B13 round 47 C3336: 205 top folders, every one listed under the default cap
        (["--limit", "200"], ("5", "200")),
        (["--limit", "204"], ("1", "204")),
        (["--limit", "205"], None),
        (["-n", "9999"], None),
    ],
)
def test_list_cap_clause_only_when_folders_are_left_out(
    env: dict[str, str],  # noqa: F811
    opt: list[str],
    past: tuple[str, str] | None,
) -> None:
    _many(env)
    cp = install_sh(env, "--list-folders", *opt)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    found = PAST.search(cp.stdout.splitlines()[-1])
    assert (found.groups() if found else None) == past, cp.stdout.splitlines()[-1]
    if past == ("1", "204"):
        assert "; 1 folder past the first 204 is not listed: " in cp.stdout
    assert ("(" in cp.stdout.splitlines()[-2]) == (past is not None)


@pytest.mark.parametrize("opt", [[], ["--limit", "200"]])
def test_the_default_cap_lists_every_top_folder_past_200_c3336(
    env: dict[str, str],  # noqa: F811
    opt: list[str],
) -> None:
    """B13 round 47 C3336 with ruling 216 (lane RB keeps every depth-1 row when it cuts): 205 top
    folders, each with a subfolder. With no --limit every top folder is listed, past 200 too, and "(205
    more)" and its NEXT count only the subfolders left out; an explicit --limit 200 stays at most 200, top
    folders first."""
    lib = _cloud(env) / "OneDrive-Contoso"
    for i in range(205):
        (lib / f"P{i:03d}" / "Sub").mkdir(parents=True)
    cp = install_sh(env, "--list-folders", *opt)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    shown = 205 if not opt else 200
    assert lines[:-1] == [*(f"{lib}/P{i:03d}" for i in range(shown)), f"({410 - shown} more)"], lines[-3:]
    found = PAST.search(lines[-1])
    assert found is not None and found.groups() == (str(410 - shown), str(shown)), lines[-1]


def _held(source_id: str, path: Path, state: str) -> str:
    """A [[source]] table for the folder ``path`` in ``state`` (paused or retired)."""
    extra = f'state = "{state}"\n' + ('retired_reason = "made-up"\n' if state == "retired" else "")
    return local_source_table(source_id, path).replace('kind = "local"\n', f'kind = "local"\n{extra}')


@pytest.mark.parametrize("live", [False, True])
def test_a_paused_or_retired_folder_is_marked_and_never_counted_c3332(
    env: dict[str, str],  # noqa: F811
    live: bool,
) -> None:
    """B13 round 47 ruling 214, C3332 (turn): only live sources were marked, so a folder sources.toml holds
    paused or retired was listed unmarked, offered, and --source-local left it held, so step 2 asked the
    folder question again. It is marked [paused] or [retired] by its exact folder in the [synced] slot (an
    escaped row's mark first), one the list does not reach is printed right after the unlisted [synced] rows,
    none counts as synced, a live source at or above the same folder wins, and the NEXT says that setting one
    live is the person's edit; with no live folder too. --json gives each row its state."""
    home = Path(env["HOME"])
    lib = _cloud(env) / "OneDrive-Contoso"
    for d in ("Alpha", "Beta", "Kestrel\tPlans", "Gamma/In"):
        (lib / d).mkdir(parents=True)
    old = home / "Documents" / "Old Notes"  # never listed: outside ~/Library/CloudStorage
    notes = home / "Documents" / "notes"
    tables = [_inbox(env), _held("beta", lib / "Beta", "paused"), _held("old", old, "retired")]
    tables.append(_held("kestrel", lib / "Kestrel\tPlans", "paused"))
    if live:
        tables += [local_source_table("gamma", lib / "Gamma"), local_source_table("notes", notes)]
        tables += [_held("gamma-held", lib / "Gamma", "paused"), _held("in", lib / "Gamma" / "In", "retired")]
    _write_config(env, *tables)
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    if live:
        head = [
            "already synced on this Mac: 2 folders (marked [synced] below: first the 1 outside the list, "
            "then the list)",
            f"[synced] {notes}",
        ]
        nxt = (
            f"NEXT: this Mac already syncs 2 folders, and a re-run keeps them: run {INSTALL_SH}, with one "
            '--source-local "<folder>" for each unmarked folder to add from the list above (none is needed)'
        )
        gamma = [f"[synced] {lib}/Gamma", f"[inside a synced folder] {lib}/Gamma/In"]
    else:
        head, nxt, gamma = [], LIST_NEXT, [f"{lib}/Gamma", f"{lib}/Gamma/In"]
    assert cp.stdout.splitlines() == [
        *head,
        f"[retired] {old}",
        f"{lib}/Alpha",
        f"[paused] {lib}/Beta",
        *gamma,
        f"{ESCAPED_MARK} [paused] {lib}/Kestrel\\tPlans",
        f"{nxt}{HELD_LIST_NEXT}",
    ], cp.stdout
    assert install_log(env)[-2].endswith(f" result=done note=listed-5 synced={2 if live else 0}"), (
        install_log(env)
    )
    doc = json.loads(install_sh(env, "--list-folders", "--json").stdout)
    rows = [(f["path"], f["state"], f.get("mark")) for f in doc["folders"]]
    assert rows == [
        *([(str(notes), "synced", None)] if live else []),
        (str(old), "retired", None),
        (f"{lib}/Alpha", "listed", None),
        (f"{lib}/Beta", "paused", None),
        (f"{lib}/Gamma", "synced" if live else "listed", None),
        (f"{lib}/Gamma/In", "inside_synced" if live else "listed", None),
        (f"{lib}/Kestrel\tPlans", "paused", ESCAPED_MARK),
    ], rows
    assert doc["total"] == 5


@pytest.mark.parametrize("json_run", [False, True])
def test_a_cut_list_keeps_every_top_folder_before_subfolders_c3336(
    env: dict[str, str],  # noqa: F811
    json_run: bool,
) -> None:
    """B13 round 47 C3336 (installer half): 120 personal OneDrive folders, each with a subfolder, sorted ahead
    of a SharePoint library, filled the 200 rows with OneDrive rows and hid the library whole behind "(41
    more)". A cut keeps every folder 1 level inside a provider first, then subfolders in sort order up to the
    cap; the rows stay in sort order, and the "(N more)" line and its --all NEXT stay."""
    cs = _cloud(env)
    lib = cs / "OneDrive-Contoso"
    for i in range(120):
        (lib / f"P{i:03d}" / "Sub").mkdir(parents=True)
    alpha = cs / "OneDrive-SharedLibraries-Contoso" / "Project Alpha"
    alpha.mkdir(parents=True)
    want = [str(lib / f"P{i:03d}" / s) for i in range(120) for s in (["", "Sub"] if i < 79 else [""])]
    want = [w.removesuffix("/") for w in want] + [str(alpha)]
    assert len(want) == 200 and want == sorted(want)
    cp = install_sh(
        env, "--list-folders", *(["--json", "--limit", "200"] if json_run else [])
    )  # JSON: no cap unasked
    assert cp.returncode == 0, cp.stdout + cp.stderr
    if json_run:
        doc = json.loads(cp.stdout)
        assert [f["path"] for f in doc["folders"]] == want and doc["total"] == 241, doc["total"]
        assert doc["pagination"]["next"] is not None
        return
    lines = cp.stdout.splitlines()
    assert lines[:-1] == [*want, "(41 more)"], lines[-4:]
    found = PAST.search(lines[-1])
    assert found is not None and found.groups() == ("41", "200"), lines[-1]


@pytest.mark.parametrize(
    "opt",
    [
        ["--limit", "0"],
        ["--limit=0"],
        ["-n", "00"],
        ["--limit", "x"],
        ["--limit", "3x"],
        ["--limit", "-3"],
        ["--limit="],
        ["--limit"],
    ],
)
def test_list_limit_below_1_or_not_a_number_is_a_usage_error(
    env: dict[str, str],  # noqa: F811
    opt: list[str],
) -> None:
    lib = _many(env, 3)
    cp = install_sh(env, "--list-folders", *opt)
    assert cp.returncode == 2, cp.stdout + cp.stderr
    assert cp.stderr.startswith("usage error: "), cp.stderr
    assert str(lib) not in cp.stdout, cp.stdout


@pytest.mark.parametrize(
    "args",
    [["--all"], ["--limit", "3"], ["--list-folders", "--all", "--source-local", "x"]],
)
def test_list_options_outside_a_bare_list_are_usage_errors(
    env: dict[str, str],  # noqa: F811
    args: list[str],
) -> None:
    cp = install_sh(env, *args)
    assert cp.returncode == 2, cp.stdout + cp.stderr
    assert "usage error: " in cp.stderr, cp.stderr


ODD = [
    "Spoof\n/Forged",  # a newline, then a path: the raw list printed "<lib>/Spoof" and "/Forged"
    "Esc\x1b[31mred",
    "Rtl\u202eexe.pdf",
    "Zero\u200bwidth",
    "Bom\ufeffmark",
    "Réunion plans",
    "Plain",
]


def _odd(env: dict[str, str]) -> Path:  # noqa: F811
    lib = _cloud(env) / "OneDrive-Contoso"
    for n in ODD:
        (lib / n).mkdir(parents=True)
    return lib


def test_odd_names_print_escaped_one_row_per_real_folder(env: dict[str, str]) -> None:  # noqa: F811
    lib = _odd(env)
    cp = install_sh(env, "--list-folders", "--all")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    esc = f"{ESCAPED_MARK} {lib}"
    rows = cp.stdout.splitlines()[:-1]
    assert sorted(rows) == sorted(
        [
            f"{esc}/Spoof\\n",
            f"{esc}/Spoof\\n/Forged",
            f"{esc}/Esc\\033[31mred",
            f"{esc}/Rtl\\342\\200\\256exe.pdf",
            f"{esc}/Zero\\342\\200\\213width",
            f"{esc}/Bom\\357\\273\\277mark",
            f"{lib}/Réunion plans",
            f"{lib}/Plain",
        ]
    ), rows
    assert one_next(cp)
    assert not [r for r in rows if r in {"/Forged", "", f"{lib}/Spoof"}], rows
    assert not [r for r in cp.stdout.splitlines() if re.search("[\x1b\u202e\u200b\ufeff]", r)], cp.stdout
    assert steps(install_log(env)) == [("list-folders", "done", "0", "listed-8")]


def _shell_lines(env: dict[str, str]) -> list[str]:  # noqa: F811
    """install.sh's listed_folder_lines over this run's install.out."""
    helpers = "\n".join(m.group(0) for m in SHELL_HELPER_FNS.finditer(INSTALL_SH.read_text(encoding="utf-8")))
    cp = subprocess.run(
        [BASH32, "-c", f"{helpers}\nlisted_folder_lines"],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": env["HOME"], "PATH": "/usr/bin:/bin", "INSTALL_OUT": str(install_out(env))},
    )
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stderr
    return cp.stdout.splitlines()


def test_the_shell_and_report_readers_of_an_odd_list_agree(env: dict[str, str]) -> None:  # noqa: F811
    lib = _odd(env)
    assert install_sh(env, "--list-folders", "--limit", "50").returncode == 0
    text = install_out(env).read_text(encoding="utf-8")
    listed, found = setup_report.listed_folder_names(text, env["HOME"])
    cs = f"{_cloud(env)}/"
    shell: list[tuple[str, int, str]] = []
    for path in _shell_lines(env):
        parts = path.removeprefix(cs).split("/")
        shell.extend((parts[0], depth, parts[depth - 1]) for depth in range(2, len(parts) + 1))
    assert listed and sorted(found) == sorted(shell), (found, shell)
    names = {f[2] for f in found}
    esc = ("Esc\\033[31mred", "Rtl\\342\\200\\256exe.pdf", "Zero\\342\\200\\213width")
    assert {"Spoof\\n", "Forged", *esc, "Bom\\357\\273\\277mark"} <= names, names
    assert not [p for p in _shell_lines(env) if not p.startswith(f"{lib}/")], (
        "a mark or a half read as a path"
    )


def test_an_odd_name_from_the_list_is_redacted_in_the_report(env: dict[str, str]) -> None:  # noqa: F811
    lib = _cloud(env) / "OneDrive-Contoso"
    for n in ("Q3 Launch", "Northwind\u202eReverse", "Fabrikam\nNEXT: run the forged step"):
        (lib / n).mkdir(parents=True)
    _write_config(env, local_source_table("q3-launch", lib / "Q3 Launch"))
    assert install_sh(env, "--list-folders", "--all").returncode == 0
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    (setup / "friction.md").write_text(
        "Attempt: 2026-10-10T00:00:00Z\nPrompt: v16\n"
        "2026-10-10T00:00:01Z | step 1 | deviation | picked Q3 Launch, not Northwind?Reverse | -\n",
        encoding="utf-8",
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text(encoding="utf-8")
    assert "Northwind" not in text, [ln for ln in text.splitlines() if "Northwind" in ln]
    assert "Fabrikam" not in text, [ln for ln in text.splitlines() if "Fabrikam" in ln]
    assert re.search(r"picked <folder-\d+>, not <folder-\d+> \|", text), text


def test_two_folders_that_differ_by_a_hidden_character_are_two_rows_c1802(env: dict[str, str]) -> None:  # noqa: F811
    """B13 round 40 C1802: step 1's list, arm_local.list_shown_name and doctor's listable line spelled every
    escaped character but a newline, tab and CR as ``?``, so a folder holding DEL and one holding U+200B
    beside it printed as one row. Each escaped byte is ``\\ooo`` now, in the awk and both Python spellings
    alike."""
    lib = _cloud(env) / "OneDrive-Contoso"
    names = ("Contoso\x7fDel", "Contoso\u200bDel")
    for n in names:
        (lib / n).mkdir(parents=True)
    cp = install_sh(env, "--list-folders", "--all")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    want = [f"{lib}/Contoso\\177Del", f"{lib}/Contoso\\342\\200\\213Del"]
    assert sorted(cp.stdout.splitlines()[:-1]) == sorted(f"{ESCAPED_MARK} {w}" for w in want), cp.stdout
    shown = [arm_local.list_shown_name(n) for n in names]
    assert shown == [f"{arm_local.ESCAPED_KEPT_MARK} {w.rsplit('/', 1)[1]}" for w in want], shown
    assert [doctor.list_spelling(lib / n) for n in names] == want


def test_a_folder_named_as_another_ones_octal_spelling_is_still_its_twin_c1802(env: dict[str, str]) -> None:  # noqa: F811
    """B13 round 40 C1802: with DEL spelled ``\\177``, a folder literally named ``Contoso\\177Del`` reads as
    the DEL folder's row: both rows are marked, and --source-local refuses the plain one as a look-alike."""
    lib = _cloud(env) / "OneDrive-Contoso"
    for n in ("Contoso\x7fDel", "Contoso\\177Del"):
        (lib / n).mkdir(parents=True)
    cp = install_sh(env, "--list-folders", "--all")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert sorted(cp.stdout.splitlines()[:-1]) == sorted(
        [f"{ESCAPED_MARK} {lib}/Contoso\\177Del", f"{TWIN_MARK} {lib}/Contoso\\177Del"]
    ), cp.stdout
    cp = install_sh(env, "--source-local", str(lib / "Contoso\\177Del"))
    assert cp.returncode == 2, cp.stdout + cp.stderr
    assert cp.stderr.splitlines()[0] == (
        f"usage error: --source-local {lib}/Contoso\\177Del: {LOOK_ALIKE_WORDS} (see --help)"
    ), cp.stderr


def _octal(ch: str) -> str:
    """``ch`` as LIST_ESC_AWK's oct() spells it: a backslash and three octal digits per UTF-8 byte."""
    return "".join(f"\\{b:03o}" for b in ch.encode("utf-8"))


_NON_ASCII_BLANKS = tuple(chr(c) for c in range(0x80, 0x110000) if chr(c).isspace())
"""Every character outside ASCII that ``str.isspace`` is true for, which Python's ``str.strip`` trims."""
_BLANK_NEAR_MISSES = (
    *("\u00a1", "\u167f", "\u1681", "\u180e", "\u1fff"),
    *("\u2030", "\u205e", "\u2061", "\u3001"),
)
"""Beside each blank's range, no blank and never escaped (U+180E stopped being one in Unicode 6.3)."""


def test_a_folder_ending_in_a_non_ascii_blank_lists_escaped_c3333(env: dict[str, str]) -> None:  # noqa: F811
    """B13 round 47 C3333 (ruling 209): ``Project`` and ``Project<U+3000>`` side by side printed as two rows
    of the same text, so the person could paste ``Project`` for the folder they chose. A name ending in a
    blank outside ASCII (U+3000, U+00A0) is an escaped row now, the plain sibling stays plain, each listed
    once, and --json keeps the real path with the escaped mark."""
    lib = _cloud(env) / "OneDrive-Contoso"
    names = ("Project", "Project\u3000", "Kestrel", "Kestrel\u00a0")
    for n in names:
        (lib / n).mkdir(parents=True)
    cp = install_sh(env, "--list-folders", "--all")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    rows = cp.stdout.splitlines()[:-1]
    assert sorted(rows) == sorted(
        [
            f"{lib}/Project",
            f"{ESCAPED_MARK} {lib}/Project\\343\\200\\200",
            f"{lib}/Kestrel",
            f"{ESCAPED_MARK} {lib}/Kestrel\\302\\240",
        ]
    ), rows
    assert not [r for r in rows if re.search("[\u3000\u00a0]", r)], rows
    doc = json.loads(install_sh(env, "--list-folders", "--json").stdout)
    got = sorted((f["path"], f.get("mark")) for f in doc["folders"])
    assert got == sorted(
        [
            (f"{lib}/Project", None),
            (f"{lib}/Project\u3000", ESCAPED_MARK),
            (f"{lib}/Kestrel", None),
            (f"{lib}/Kestrel\u00a0", ESCAPED_MARK),
        ]
    ), got


def test_a_folder_with_a_space_at_its_edge_lists_escaped_and_its_literal_twin_is_refused_c3527(
    env: dict[str, str],  # noqa: F811
) -> None:
    """B13 round 48 ruling 220 (C3527, option a): `Project` and `Project ` (a trailing space) listed as two
    rows that read alike. A space at a part's edge is escaped as `\\040` with ESCAPED_MARK, a leading one too,
    a mid-name space and a mid-name U+3000 stay plain; --json gives the escaped row its mark; and a folder
    literally named `Project\\040` beside `Project ` is the look-alike twin step 2 refuses, while the lone
    `Project ` is added by its path."""
    lib = _cloud(env) / "OneDrive-Contoso"
    for n in ("Project", "Project ", " Notes", "Project Alpha", "Kestrel\u3000Ledger"):
        (lib / n).mkdir(parents=True)
    cp = install_sh(env, "--list-folders", "--all")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert sorted(cp.stdout.splitlines()[:-1]) == sorted(
        [
            f"{lib}/Project",
            f"{ESCAPED_MARK} {lib}/Project\\040",
            f"{ESCAPED_MARK} {lib}/\\040Notes",
            f"{lib}/Project Alpha",
            f"{lib}/Kestrel\u3000Ledger",
        ]
    ), cp.stdout
    doc = json.loads(install_sh(env, "--list-folders", "--json").stdout)
    marks = {f["path"]: f.get("mark") for f in doc["folders"]}
    assert marks[f"{lib}/Project "] == ESCAPED_MARK and marks[f"{lib}/Project Alpha"] is None, marks
    assert marks[f"{lib}/Kestrel\u3000Ledger"] is None, marks
    (lib / "Project\\040").mkdir()
    refused = install_sh(env, "--source-local", str(lib / "Project "))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "step 1 shows this folder's name with a hidden or control character" in refused.stderr, (
        refused.stderr
    )


_ANYWHERE_BLANKS = ("\u0085", "\u2028", "\u2029")
"""The blanks escaped wherever they stand: line breaks to the report's splitlines (U+0085, U+2028, U+2029)."""


def _awk_rows(names: list[str]) -> list[str]:
    """Each of ``names`` as LIST_ESC_AWK prints it, ``E `` or ``P `` first by its escd, under /usr/bin/awk."""
    [awk] = re.findall(r"^LIST_ESC_AWK='(.*?)^'$", INSTALL_SH.read_text(encoding="utf-8"), flags=re.M | re.S)
    shown = subprocess.run(
        ["/usr/bin/awk", "-v", "mark=", awk + '{ print (escd($0) ? "E " : "P ") shown($0) }'],
        input="".join(n + "\n" for n in names).encode(),
        capture_output=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    ).stdout.decode()
    return shown.split("\n")[:-1]


def _py_rows(names: list[str]) -> list[str]:
    """Each of ``names`` as arm_local's twin of the awk spells it, ``E `` or ``P `` first."""
    return [
        ("E " if arm_local._LIST_ESCAPED_RE.search(n) else "P ")
        + arm_local._LIST_ESCAPED_RE.sub(arm_local.list_escaped_char, n)
        for n in names
    ]


def test_the_list_escapes_every_non_ascii_blank_as_its_python_twin_does_c3333() -> None:
    """B13 round 47 C3333: LIST_ESC_AWK's escd and esc mark every blank outside ASCII str.isspace names, each
    as its octal bytes, and arm_local's ``_LIST_ESCAPED_RE`` (list_shown_name, doctor.list_spelling) holds the
    same set; the characters next to each range, and U+200B (escaped since U-B11), read as before. Since B13
    round 48 ruling 220 (C3419, C3527) one rule in both: a blank, U+0020 too (as ``\\040``), only at a path
    part's start or end, every blank of that run, a line break and a control, bidi or zero-width character
    anywhere: a blank inside a name lists plain, the folder step 2 adds by its path."""
    names = [f"Project{ch}" for ch in (*_NON_ASCII_BLANKS, *_BLANK_NEAR_MISSES, "\u200b")]
    want = [
        *(f"E Project{_octal(ch)}" for ch in _NON_ASCII_BLANKS),
        *(f"P Project{ch}" for ch in _BLANK_NEAR_MISSES),
        "E Project\\342\\200\\213",
    ]
    assert len(_NON_ASCII_BLANKS) == 19 and "\u0085" in _NON_ASCII_BLANKS and "\u2029" in _NON_ASCII_BLANKS
    assert _awk_rows(names) == want
    assert _py_rows(names) == want
    # mid-name, leading, a run, a part's end inside a path, and the ASCII space
    rows = [
        *(f"Pro{ch}ject" for ch in _NON_ASCII_BLANKS),
        *(f"{ch}Project" for ch in _NON_ASCII_BLANKS),
        "Project\u3000\u00a0",
        "~/Kestrel/Plans\u3000/Sub",
        "~/Kestrel/\u202fPlans/Sub",
        "Project ",
        " Notes",
        "Project Alpha",
        "~/Kestrel Plans /Sub Folder",
        "Mid\u3000\u3000name",
    ]
    want = [
        *(
            (f"E Pro{_octal(ch)}ject" if ch in _ANYWHERE_BLANKS else f"P Pro{ch}ject")
            for ch in _NON_ASCII_BLANKS
        ),
        *(f"E {_octal(ch)}Project" for ch in _NON_ASCII_BLANKS),
        "E Project\\343\\200\\200\\302\\240",
        "E ~/Kestrel/Plans\\343\\200\\200/Sub",
        "E ~/Kestrel/\\342\\200\\257Plans/Sub",
        "E Project\\040",
        "E \\040Notes",
        "P Project Alpha",
        "E ~/Kestrel Plans\\040/Sub Folder",
        "P Mid\u3000\u3000name",
    ]
    assert _awk_rows(rows) == want
    assert _py_rows(rows) == want
