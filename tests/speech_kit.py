"""A fake speech helper for the speech engine's and the recording converter's tests, in the style of the fake
media helper (``tests/media_kit.py``).

``fake_speech(folder, script)`` writes a Python script that speaks the speech helper's protocol and logs each
call.  ``script`` says what the recording's sound "holds": ``{"words": [[text, start_ms, end_ms], ...],
"segments": [[speaker, start_ms, end_ms], ...], "hidden": [[text, start_ms, end_ms], ...]}``.  ``hidden``
words are missed by a whole-file run and found by a clip run, as a hole of the real engine is (spec S8 rule
5).

What the helper answers (one JSON document on stdout, keys sorted; exit 3 with a message on stderr on
failure):

- ``--version``: ``{"engine": ..., "fluidaudio": <40 hex>, "helper": ...}``.
- ``words PCM --models DIR [--from MS --to MS]``: ``{"words": [...]}``, the script's words; with a clip only
  the words that lie wholly inside it, ``hidden`` ones included, their times still absolute.  ``--from``
  and ``--to`` go together.
- ``voices PCM --models DIR --threshold T``: ``{"segments": [...]}``, the script's segments.

Each needs a PCM file that exists and a ``--models`` folder holding ``parakeet-tdt-0.6b-v3/`` (words) or
``speaker-diarization/`` (voices), as the real helper does; it writes no file but its call log.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

FAKE_PIN = "04e363c29d9a754022d602d6fe1468ab80a0f705"
FAKE_SPEECH_VERSION = json.dumps(
    {"engine": "paper-speech", "fluidaudio": FAKE_PIN, "helper": "0.1.0"}, sort_keys=True
)

FAKE_SPEECH = r"""#!{python}
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
with open(sys.argv[0] + ".calls", "a") as log:
    log.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\n")
if "--version" in args:
    print({version!r})
    sys.exit({version_exit})
command, rest = (args[0], args[1:]) if args else ("", [])
failures, replies, script = {fail!r}, {reply!r}, {script!r}

def die(message):
    sys.stderr.write("error: " + message + "\n")
    sys.exit(3)

if command in failures:
    die(failures[command])
if command in replies:
    print(replies[command])
    sys.exit(0)
options, plain, it = {}, [], iter(rest)
for a in it:
    if a.startswith("--"):
        options[a] = next(it)
    else:
        plain.append(a)
if len(plain) != 1 or not Path(plain[0]).is_file():
    die("cannot read the sound file")
folder = {"words": "parakeet-tdt-0.6b-v3", "voices": "speaker-diarization"}.get(command)
if folder is None:
    die("unknown command " + command)
if not (Path(options.get("--models", "")) / folder).is_dir():
    die("the model folder " + folder + " is not there")
if command == "words":
    if ("--from" in options) != ("--to" in options):
        die("--from and --to go together")
    words = script.get("words", [])
    if "--from" in options:
        lo, hi = int(options["--from"]), int(options["--to"])
        words = [w for w in words + script.get("hidden", []) if lo <= w[1] and w[2] <= hi]
        words.sort(key=lambda w: (w[1], w[2], w[0]))
    print(json.dumps({"words": words}, sort_keys=True))
else:
    if "--threshold" not in options:
        die("--threshold is required, above 0 and at most 2")
    print(json.dumps({"segments": script.get("segments", [])}, sort_keys=True))
"""


def fake_speech(
    folder: Path,
    script: dict[str, Any] | None = None,
    *,
    fail: dict[str, str] | None = None,
    reply: dict[str, str] | None = None,
    version: str = FAKE_SPEECH_VERSION,
    version_exit: int = 0,
) -> Path:
    """The fake speech helper at ``folder/fake-speech``, owner-only.  ``script``: what the sound holds (see
    the module).  ``fail``: commands that exit 3 with that message.  ``reply``: commands that print this text
    instead.  ``version`` and ``version_exit``: what ``--version`` prints and returns.  Each call is logged
    in ``<helper>.calls`` (``calls``)."""
    values = {
        "python": sys.executable,
        "script": script or {},
        "fail": fail or {},
        "reply": reply or {},
        "version": version,
    }
    text = FAKE_SPEECH.replace("{version_exit}", str(version_exit))
    for key, value in values.items():
        text = text.replace("{" + key + "!r}", repr(value)).replace("{" + key + "}", str(value))
    path = folder / "fake-speech"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o700)
    return path


def calls(helper: Path) -> list[dict[str, Any]]:
    """Every call the fake helper logged, oldest first."""
    log = Path(str(helper) + ".calls")
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def place_models(speech_dir: Path) -> Path:
    """Two model folders under ``speech_dir`` with one made-up file each: placed, and matching no pinned
    digest.  Returns ``speech_dir``."""
    for name, file in (
        ("parakeet-tdt-0.6b-v3", "parakeet_vocab.json"),
        ("speaker-diarization", "plda-parameters.json"),
    ):
        (speech_dir / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        (speech_dir / name / file).write_text("{}\n")
    return speech_dir


def pcm(path: Path, seconds: float = 1.0) -> Path:
    """A silent 16 kHz mono s16le file of ``seconds``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(2 * int(16000 * seconds)))
    return path
