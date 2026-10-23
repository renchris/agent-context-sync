"""The suite's stubs run by path as a Mach-O, not as a ``#!`` script, and install.sh hashes with a Mach-O
(B13 round 23 lane K, A97). macOS 15.8.1 keeps about 1.1 KiB of kernel memory for every ``#!`` exec until
reboot; ``exec_stub.write`` and ``exec_trampoline.c`` are how a stub avoids one."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import exec_stub
from media_kit import calls as media_calls
from media_kit import fake_media
from speech_kit import calls as speech_calls
from speech_kit import fake_speech
from test_convert_formats import _pandoc_that
from test_deploy_pack import _fake_step1_tools
from test_install_next_line import _exe
from test_install_oneshot import _write_exe, env, real_env  # noqa: F401  (fixtures)
from test_launcher import stubs  # noqa: F401  (fixture)
from test_ocr import calls as ocr_calls
from test_ocr import script, write_fake
from test_ship_land import _with_gh

ROOT = Path(__file__).resolve().parents[1]
MACH_O = (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")  # 64-bit Mach-O, universal


@pytest.fixture
def trampoline(_exec_trampoline: bytes | None) -> bytes:
    if _exec_trampoline is None:
        pytest.skip("no trampoline: not macOS, no /usr/bin/cc, or its self-test failed (stubs are #! files)")
    return _exec_trampoline


def _plain(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


def _run(stub: Path, *args: str, **kw: str) -> tuple[int, str, str]:
    cp = subprocess.run(
        [str(stub), *args],
        input="from stdin\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", **kw},
    )
    return cp.returncode, cp.stdout, cp.stderr


BODIES = {
    "bash": '#!/bin/bash\nprintf "<%s>" "$@"; echo; echo "v=$V"; exit 3\n',
    "sh_stdin": "#!/bin/sh\ncat\nprintf '%s\\n' \"$#\"\n",
    "env_python": "#!/usr/bin/env python3\nimport os, sys\nprint(sys.argv[1:], os.environ['V'])\nexit(4)\n",
    "interpreter_words": "#!/bin/bash -e\nfalse\necho not reached\n",
    "comment_ends_the_line": "#!/bin/bash # a comment the kernel drops\necho ran\n",
    "tab_and_spaces": "#! \t/bin/sh  \necho spaced\n",
}


@pytest.mark.parametrize("name", sorted(BODIES))
def test_a_stub_run_by_path_is_a_mach_o_that_runs_as_the_kernel_runs_its_body(
    trampoline: bytes, tmp_path: Path, name: str
) -> None:
    """The same body, once a plain ``#!`` file the kernel runs and once a trampoline stub: the same exit code,
    stdout and stderr, for arguments with spaces and an empty one, stdin and the environment."""
    (tmp_path / "kernel").mkdir()
    (tmp_path / "stub").mkdir()
    kernel = _plain(tmp_path / "kernel" / "tool", BODIES[name])
    stub = exec_stub.write(tmp_path / "stub" / "tool", BODIES[name])
    assert stub.read_bytes().startswith(trampoline) and stub.read_bytes()[:4] in MACH_O
    assert stub.stat().st_mode & 0o777 == 0o755
    args = ("a b", "", "c")
    assert _run(stub, *args, V="x") == _run(kernel, *args, V="x")


def test_a_stub_keeps_its_pid_and_the_body_names_its_interpreter(trampoline: bytes, tmp_path: Path) -> None:
    """The trampoline execs and never forks: the pid the caller holds is the interpreter's. ``$0`` is the
    body, a file of the stub's name under ``<dir>/../.<dir>-real/``."""
    (tmp_path / "bin").mkdir()
    stub = exec_stub.write(tmp_path / "bin" / "who", '#!/bin/bash\necho "$$ $BASH"\nps -o comm= -p $$\n')
    proc = subprocess.Popen([str(stub)], stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate(timeout=60)
    pid, interp, comm = out.split()
    assert int(pid) == proc.pid and interp == "/bin/bash" and comm.endswith("bash")
    body = tmp_path / ".bin-real" / "who"
    assert exec_stub.body_path(stub) == body and body.read_text().startswith("#!/bin/bash")
    assert sorted(p.name for p in (tmp_path / "bin").iterdir()) == ["who"]


def test_a_copied_rewritten_or_chmodded_stub_changes_only_itself(trampoline: bytes, tmp_path: Path) -> None:
    """Each stub is its own copy, so ``cp``, ``shutil.copy``, a rewrite at the same path and a chmod reach
    only that stub (a symlink to one shared binary would carry each to every stub, and a copy of the link
    would lose its body)."""
    (tmp_path / "bin").mkdir()
    one = exec_stub.write(tmp_path / "bin" / "one", "#!/bin/sh\necho one\n")
    two = exec_stub.write(tmp_path / "bin" / "two", "#!/bin/sh\necho two\n")
    (tmp_path / "elsewhere").mkdir()
    copied = Path(shutil.copy(one, tmp_path / "elsewhere" / "renamed"))
    subprocess.run(["/bin/cp", str(two), str(tmp_path / "elsewhere" / "cp")], check=True)
    assert _run(copied)[1] == "one\n" and _run(tmp_path / "elsewhere" / "cp")[1] == "two\n"
    exec_stub.write(one, "#!/bin/sh\necho one again\n")
    assert _run(one)[1] == "one again\n" and _run(two)[1] == "two\n"
    one.chmod(0o644)
    with pytest.raises(PermissionError):
        _run(one)
    assert _run(two)[1] == "two\n"


def test_a_body_that_reads_its_own_path_stays_a_shebang_file(trampoline: bytes, tmp_path: Path) -> None:
    """``$0`` would name the body, not the stub: a body that reads its own path (``"$0.calls"``,
    ``sys.argv[0]``) keeps the plain ``#!`` file, as does text with no ``#!`` line."""
    for text in (
        '#!/bin/sh\necho "$@" >> "$0.calls"\n',
        "#!/bin/bash\necho ${0##*/}\n",
        "#!/usr/bin/env python3\nimport sys\nopen(sys.argv[0] + '.calls', 'a')\n",
        "echo no shebang\n",
    ):
        stub = exec_stub.write(tmp_path / "tool", text)
        assert stub.read_text() == text


def test_without_a_trampoline_every_stub_is_the_plain_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No compiler, or not macOS: the stub is the ``#!`` file it always was."""
    monkeypatch.setattr(exec_stub, "TRAMPOLINE", None)
    stub = exec_stub.write(tmp_path / "tool", "#!/bin/sh\necho plain\n")
    assert stub.read_text() == "#!/bin/sh\necho plain\n" and _run(stub)[1] == "plain\n"
    assert not (tmp_path.parent / f".{tmp_path.name}-real").exists()


def test_the_trampoline_names_a_stub_with_no_body(trampoline: bytes, tmp_path: Path) -> None:
    """The bare binary, or a stub whose body was deleted, stops with exit 127 and says why."""
    bare = tmp_path / "bare"
    bare.write_bytes(trampoline)
    bare.chmod(0o755)
    rc, out, err = _run(bare)
    assert (rc, out) == (127, "") and "no stub body recorded" in err
    (tmp_path / "bin").mkdir()
    stub = exec_stub.write(tmp_path / "bin" / "gone", "#!/bin/sh\necho hi\n")
    exec_stub.body_path(stub).unlink()
    rc, _, err = _run(stub)
    assert rc == 127 and "No such file or directory" in err


def test_the_harness_helpers_write_mach_o_stubs(
    trampoline: bytes,
    tmp_path: Path,
    stubs: dict[str, str],  # noqa: F811
) -> None:
    """test_install_oneshot's ``_write_exe``, test_install_next_line's ``_exe``, test_launcher's ``stubs``,
    test_ship_land's ``_with_gh`` and test_deploy_pack's ``_fake_step1_tools`` write their stubs through
    ``exec_stub.write``: the launchctl stub (install.sh runs its getenv 18 times and print twice) was 77% of
    the ``#!`` execs of a run of the three install files (B13 round 23 A97's count). agentsync's stand-in,
    which the stub uv copies at each install (round 24 B93), and test_convert_formats' ``_pandoc_that``
    (B91) are Mach-O stubs too."""
    (tmp_path / "a").mkdir()
    _with_gh(tmp_path, {"PATH": "/usr/bin:/bin"}, "exit 0\n")
    _fake_step1_tools(tmp_path / "step1", tmp_path / "calls.log")
    for stub in (
        _pandoc_that(tmp_path, "old-pandoc", ":"),
        _write_exe(tmp_path / "a" / "launchctl-stub", "#!/bin/bash\necho print\n"),
        Path(_exe(tmp_path / "a" / "scutil", "#!/bin/bash\nexit 0\n")),
        tmp_path / "bin" / "gh",
        tmp_path / "step1" / "git",
        Path(stubs["AGENTSYNC_LAUNCHCTL"]),
        Path(stubs["PATH"].split(":")[0]) / "uv",
        Path(stubs["STUB_AGENTSYNC"]),
    ):
        assert stub.read_bytes()[:4] in MACH_O, stub


def test_the_real_agentsyncs_tool_stubs_are_mach_o(
    trampoline: bytes,
    real_env: dict[str, str],  # noqa: F811
) -> None:
    """``real_env``'s stub uv copies the tool's Python and ``agentsync`` from two Mach-O stubs, not a ``#!``
    file printf writes: install.sh runs the tool's Python about two dozen times a run, 999 ``#!`` execs of the
    suite's census with agentsync's 350 (B13 round 24 B93)."""
    home = Path(real_env["HOME"]) / ".local"
    uv = Path(real_env["PATH"].split(":")[0]) / "uv"
    py, cli = home / "share" / "uv" / "tools" / "agentsync" / "bin" / "python", home / "bin" / "agentsync"
    inodes: list[tuple[int, int]] = []
    for i in range(2):  # the second install copies over the copies that ran, each as a new file
        subprocess.run([str(uv), "tool", "install", "agentsync"], env=real_env, check=True, timeout=60)
        assert py.read_bytes()[:4] in MACH_O and cli.read_bytes()[:4] in MACH_O, i
        inodes.append((py.stat().st_ino, cli.stat().st_ino))
        assert _run(py, "-c", "import agentsync; print(7)")[:2] == (0, "7\n"), i
        assert _run(cli, "--help", HOME=real_env["HOME"])[0] == 0, i
    assert inodes[0][0] != inodes[1][0] and inodes[0][1] != inodes[1][1], "rm -f before cp"


def _installs_a_new_agentsync(harness: dict[str, str]) -> None:
    """The stub uv's install, twice: its agentsync is a new file each time (``rm -f`` before ``cp``, so not
    a Mach-O overwritten in place after it ran), and runs."""
    uv = Path(harness["PATH"].split(":")[0]) / "uv"
    cli = Path(harness["HOME"]) / ".local" / "bin" / "agentsync"
    inodes = []
    for i in range(2):
        subprocess.run([str(uv), "tool", "install", "agentsync"], env=harness, check=True, timeout=60)
        assert cli.read_bytes()[:4] in MACH_O, i
        assert _run(cli, "status", STUB_LOG=harness["STUB_LOG"], HOME=harness["HOME"])[0] == 0, i
        inodes.append(cli.stat().st_ino)
    assert inodes[0] != inodes[1], "rm -f before cp (B13 round 24 B93)"


def test_test_launchers_stub_uv_installs_a_new_agentsync(
    trampoline: bytes,
    stubs: dict[str, str],  # noqa: F811
) -> None:
    _installs_a_new_agentsync(stubs)


def test_test_install_oneshots_stub_uv_installs_a_new_agentsync(
    trampoline: bytes,
    env: dict[str, str],  # noqa: F811
) -> None:
    _installs_a_new_agentsync(env)


def test_agentsyncs_stand_in_copied_onto_one_that_ran_still_runs(trampoline: bytes, tmp_path: Path) -> None:
    """The harness copies agentsync's stand-in onto ``~/.local/bin/agentsync`` again and again (the stub uv's
    ``cp`` at each install, ``exec_stub.copy`` in test_install_oneshot). macOS kills a Mach-O copied in place
    over one that ran (round 23: 294 of 300 in a loop; round 24: 93 of 100 in one run, 0 in the next three)
    and never one written as a new file, so each copier unlinks first: ``rm -f`` before ``cp``, or
    ``exec_stub.copy`` (B13 round 24 B93)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "bin").mkdir()
    a = _write_exe(tmp_path / "src" / "agentsync-a", "#!/bin/bash\necho a\n")
    b = _write_exe(tmp_path / "src" / "agentsync-stand-in-b", "#!/bin/bash\necho b\n")
    assert a.read_bytes()[:4] in MACH_O and b.read_bytes()[:4] in MACH_O
    installed = tmp_path / "bin" / "agentsync"
    rm_cp = 'rm -f "$2"\ncp "$1" "$2"\n'
    for i in range(200):
        src = (a, b)[i % 2]
        if i % 4 < 2:
            subprocess.run(["/bin/sh", "-c", rm_cp, "_", str(src), str(installed)], check=True)
        else:
            assert exec_stub.copy(src, installed) == installed
        assert _run(installed) == (0, "ab"[i % 2] + "\n", ""), i


def test_the_convert_kits_fakes_are_mach_o_and_log_their_calls(trampoline: bytes, tmp_path: Path) -> None:
    """The OCR, media and speech fakes and test_ocr's ``script`` tools ran 2,161 ``#!`` execs of the suite's
    census, read their own path only to name their call log, and now name it when written: each is a Mach-O
    stub whose ``calls`` (``<stub>.calls``) still logs, its body as owner-only as the stub (a helper's body
    under a cache_dir is inside what doctor's ``docs_repo.permissions`` walks). A fake written at a helper's
    name logs under that name; a ``script`` body that reads ``$0`` stays a ``#!`` file (B13 round 24 B92)."""
    helpers = tmp_path / "cache" / "speech"
    fakes = (
        (write_fake(tmp_path / "bin" / "fake-ocr"), ocr_calls),
        (fake_media(tmp_path / "helpers"), media_calls),
        (fake_speech(tmp_path / "helpers"), speech_calls),
        (fake_speech(helpers, name="agentsync-speech-0123456789abcdef"), speech_calls),
    )
    for fake, calls in fakes:
        assert fake.read_bytes()[:4] in MACH_O, fake
        assert exec_stub.body_path(fake).stat().st_mode & 0o777 == fake.stat().st_mode & 0o777 == 0o700, fake
        cp = subprocess.run([str(fake), "--version"], capture_output=True, text=True, check=False, timeout=60)
        assert cp.returncode == 0 and cp.stdout.startswith('{"engine": "paper-'), fake
        assert [c["args"] for c in calls(fake)] == [["--version"]], fake
    assert sorted(p.name for p in helpers.iterdir()) == [
        "agentsync-speech-0123456789abcdef",
        "agentsync-speech-0123456789abcdef.calls",
    ]
    (tmp_path / "tools").mkdir()
    tool = script(tmp_path / "tools" / "xcrun", 'echo "found $1"')
    assert tool.read_bytes()[:4] in MACH_O
    assert _run(tool, "--find", "swiftc", V="x") == (0, "found --find\n", "")
    assert Path(f"{tool}.calls").read_text() == "--find swiftc\n" and "V=x" in Path(f"{tool}.env").read_text()
    reads_0 = script(tmp_path / "tools" / "git", 'chmod -x "$0"')
    assert reads_0.read_bytes().startswith(b"#!/bin/sh\n"), "its $0 is the stub's own path"


def _sha256_fn(**swap: str) -> str:
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    [fn] = re.findall(r"^sha256\(\) \{\n.*?\n\}\n", script, re.MULTILINE | re.DOTALL)
    for old, new in swap.items():
        assert old in fn
        fn = fn.replace(old, new)
    return fn


def test_install_sh_hashes_with_sha256sum_and_falls_back_to_shasum(tmp_path: Path) -> None:
    """install.sh's patch hash and checkout fingerprint use ``sha256``: ``/sbin/sha256sum``, a Mach-O, where
    it exists, and ``/usr/bin/shasum -a 256`` (a perl ``#!`` script) on an older macOS that lacks it. Each way
    gives the same 12 digits, from stdin and from a file. Each run below breaks the other tool's path, so it
    passes only by the tool it names."""
    if not os.access("/sbin/sha256sum", os.X_OK) or not os.access("/usr/bin/shasum", os.X_OK):
        pytest.skip("this Mac lacks /sbin/sha256sum or /usr/bin/shasum")
    patch = tmp_path / "0001-local.patch"
    patch.write_bytes(b"diff --git a/x b/x\n+contoso\n")
    want = hashlib.sha256(patch.read_bytes()).hexdigest()[:12]
    gone = str(tmp_path / "absent")
    for fn in (_sha256_fn(**{"/usr/bin/shasum": gone}), _sha256_fn(**{"/sbin/sha256sum": gone})):
        cp = subprocess.run(
            [
                "/bin/bash",
                "-c",
                f'{fn}sha256 <"$1" | cut -c1-12; sha256 "$1" 2>/dev/null | cut -c 1-12',
                "_",
                patch,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        assert cp.stdout.split() == [want, want], fn
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    code = [ln for ln in script.splitlines() if "shasum" in ln.split("#", 1)[0]]
    assert len(code) == 1 and 'else /usr/bin/shasum -a 256 "$@"; fi' in code[0], "one shasum: sha256()s"
    assert "diff --no-ext-diff --no-color HEAD -- 2>/dev/null | sha256 | cut -c1-12" in script
    assert sys.platform != "darwin" or Path("/sbin/sha256sum").read_bytes()[:4] in MACH_O
