#!/usr/bin/env python3
"""Score the converter's share / not-share call per tick against hand labels (spec C19, the layout rows of 9.2).

    uv run python scripts/meeting-eval/layout.py LABELS PREDICTIONS [--json]

LABELS is a ``gt.py`` outside the repository: ``GT = {"<excerpt>": "<codes>"}``, one code per 10 s of the excerpt
(string literals, ``+`` and ``*`` by a number only; nothing in it is run).  Codes:

- ``S`` share, ``C`` share with a camera inset: the converter should say ``share``;
- ``P`` one person or room camera, ``G`` gallery, ``A`` avatars only, ``N`` audio-only name card, ``X`` black
  transition: it should not;
- ``F`` a camera filming a screen, ``V`` a video played in the meeting, ``W`` a whiteboard: counted apart, not
  scored (S6 rule 2 leaves them to P4).

PREDICTIONS is JSON: ``{"<excerpt>": {"profile": "teams", "kinds": ["share", "camera", "other", ...]}}``, one kind
per 2 s tick, as the converter decided them.  A tick is scored only when it is stable: a tick in the last 4 s of
a 10 s label before the label changes is left out, as the v3 layout probe left it out.  Ticks are grouped by
the platform the excerpt was recorded on (``PLATFORM`` in the labels file, else the name's first word), never
by the profile the converter detected, so a misdetected Teams recording still meets the Teams mark: ``teams``
passes at 95 % (rule T3), every other platform at 90 % (rule R4).  A labelled excerpt without a prediction fails
the run, so the set cannot shrink to the easy cases.

Exit 0 when every profile is at or above its mark, 1 when one is below, 2 when the input cannot be read.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

SHARE, NOT_SHARE, APART = "SC", "PGANX", "FVW"
PLATFORMS = ("teams", "zoom", "meet", "webex", "jitsi", "loom")
PASS_MARK = {"teams": 0.95}
OTHER_MARK = 0.90


class InputError(Exception):
    """The labels or the predictions cannot be read."""


def _value(node: ast.AST) -> object:
    """A string or a number built from literals with ``+`` and ``*``; anything else is refused."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult)):
        left, right = _value(node.left), _value(node.right)
        if isinstance(node.op, ast.Add) and type(left) is type(right):
            return left + right
        if isinstance(node.op, ast.Mult) and {type(left), type(right)} <= {str, int}:
            return left * right
    raise InputError(f"labels: only string literals, + and * are read (line {getattr(node, 'lineno', '?')})")


def _string_dict(tree: ast.Module, name: str) -> dict[str, str] | None:
    """The literal ``name = {"...": "...", ...}`` of the labels file, or None when it has none."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            if not isinstance(node.value, ast.Dict):
                raise InputError(f"labels: {name} is a dict")
            out = {}
            for key, value in zip(node.value.keys, node.value.values):
                k, v = _value(key), _value(value)
                if not isinstance(k, str) or not isinstance(v, str):
                    raise InputError(f"labels: {name} maps names to strings")
                out[k] = v
            return out
    return None


def load_labels(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """The hand labels, and the platform each excerpt was recorded on: ``PLATFORM = {"<excerpt>": "teams"}``
    when the file has one, else the excerpt name's first word when it is a platform, else ``generic`` (the v3
    layout probe's rule)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise InputError(f"labels: {exc}") from exc
    labels = _string_dict(tree, "GT")
    if labels is None:
        raise InputError("labels: no GT = {...} assignment")
    for name, codes in labels.items():
        if set(codes) - set(SHARE + NOT_SHARE + APART):
            raise InputError(f"labels: {name} holds a code outside {SHARE + NOT_SHARE + APART}")
    given = _string_dict(tree, "PLATFORM") or {}
    platforms = {}
    for name in labels:
        first = name.split("-")[0]
        platforms[name] = given.get(name) or (first if first in PLATFORMS else "generic")
    return labels, platforms


def load_predictions(path: Path) -> dict[str, dict]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"predictions: {exc}") from exc
    for name, item in doc.items():
        kinds = item.get("kinds") if isinstance(item, dict) else None
        if not isinstance(item.get("profile") if isinstance(item, dict) else None, str) or not isinstance(
            kinds, list
        ):
            raise InputError(f"predictions: {name} needs a profile and a list of kinds")
        if set(kinds) - {"share", "camera", "other"}:
            raise InputError(f"predictions: {name} holds a kind other than share, camera, other")
    return doc


def score(
    labels: dict[str, str],
    predictions: dict[str, dict],
    platforms: dict[str, str] | None = None,
    *,
    step_s: int = 2,
    label_s: int = 10,
) -> dict:
    """Agreement per platform the excerpt was recorded on (not the profile the converter detected, so a Teams
    recording taken for ``generic`` is still held to the Teams mark)."""
    platforms = platforms or {}
    groups: dict[str, dict] = {}
    excerpts = {}
    missing = sorted(set(predictions) - set(labels))
    if missing:
        raise InputError(f"no hand labels for {', '.join(missing)}")
    for name, item in sorted(predictions.items()):
        codes, kinds = labels[name], item["kinds"]
        platform = platforms.get(name, "generic")
        row = {
            "platform": platform,
            "profile": item["profile"],
            "ticks": 0,
            "agree": 0,
            "apart": 0,
            "apart_share": 0,
            "unscored": 0,
        }
        for k, kind in enumerate(kinds):
            i = k * step_s // label_s
            if i >= len(codes):
                row["unscored"] += 1
                continue
            if i + 1 < len(codes) and codes[i + 1] != codes[i] and (k * step_s) % label_s >= label_s - 4:
                row["unscored"] += 1
                continue
            if codes[i] in APART:
                row["apart"] += 1
                row["apart_share"] += kind == "share"
                continue
            row["ticks"] += 1
            row["agree"] += (kind == "share") == (codes[i] in SHARE)
        excerpts[name] = row
        total = groups.setdefault(platform, {"ticks": 0, "agree": 0, "excerpts": 0, "detected_otherwise": 0})
        total["ticks"] += row["ticks"]
        total["agree"] += row["agree"]
        total["excerpts"] += 1
        total["detected_otherwise"] += item["profile"] != platform
    for platform, total in groups.items():
        total["mark"] = PASS_MARK.get(platform, OTHER_MARK)
        total["rate"] = total["agree"] / total["ticks"] if total["ticks"] else None
        total["pass"] = total["rate"] is not None and total["rate"] >= total["mark"]
    return {"platforms": groups, "excerpts": excerpts, "unpredicted": sorted(set(labels) - set(predictions))}


def render(result: dict) -> str:
    out = [
        "| Platform | Excerpts | Detected as another profile | Ticks | Agree | Rate | Pass mark | Result |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for platform, t in sorted(result["platforms"].items()):
        rate = "none scored" if t["rate"] is None else f"{100 * t['rate']:.1f} %"
        out.append(
            f"| {platform} | {t['excerpts']} | {t['detected_otherwise']} | {t['ticks']} | {t['agree']} | {rate} | {100 * t['mark']:.0f} % | "
            f"{'pass' if t['pass'] else 'FAIL'} |"
        )
    out += [
        "",
        "| Excerpt | Platform | Profile | Ticks | Agree | Apart (F, V, W) | Called share apart | Unscored |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, e in result["excerpts"].items():
        out.append(
            f"| {name} | {e['platform']} | {e['profile']} | {e['ticks']} | {e['agree']} | {e['apart']} | {e['apart_share']} | "
            f"{e['unscored']} |"
        )
    if result["unpredicted"]:
        out.append(f"\nFAIL: labelled but not predicted: {', '.join(result['unpredicted'])}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("labels", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        labels, platforms = load_labels(args.labels)
        result = score(labels, load_predictions(args.predictions), platforms)
    except InputError as exc:
        print(f"layout.py: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1) if args.json else render(result))
    groups = result["platforms"].values()
    return 0 if groups and all(t["pass"] for t in groups) and not result["unpredicted"] else 1


if __name__ == "__main__":
    sys.exit(main())
