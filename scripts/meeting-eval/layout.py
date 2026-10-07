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
the profile the converter detected; the ``teams`` profile passes at 95 % (rule T3), every other profile at 90 %
(rule R4).

Exit 0 when every profile is at or above its mark, 1 when one is below, 2 when the input cannot be read.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

SHARE, NOT_SHARE, APART = "SC", "PGANX", "FVW"
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


def load_labels(path: Path) -> dict[str, str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise InputError(f"labels: {exc}") from exc
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "GT" for t in node.targets
        ):
            if not isinstance(node.value, ast.Dict):
                raise InputError("labels: GT is a dict")
            labels = {}
            for key, value in zip(node.value.keys, node.value.values):
                name, codes = _value(key), _value(value)
                if not isinstance(name, str) or not isinstance(codes, str):
                    raise InputError("labels: GT maps names to strings of codes")
                if set(codes) - set(SHARE + NOT_SHARE + APART):
                    raise InputError(f"labels: {name} holds a code outside {SHARE + NOT_SHARE + APART}")
                labels[name] = codes
            return labels
    raise InputError("labels: no GT = {...} assignment")


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
    labels: dict[str, str], predictions: dict[str, dict], *, step_s: int = 2, label_s: int = 10
) -> dict:
    profiles: dict[str, dict] = {}
    excerpts = {}
    missing = sorted(set(predictions) - set(labels))
    if missing:
        raise InputError(f"no hand labels for {', '.join(missing)}")
    for name, item in sorted(predictions.items()):
        codes, kinds = labels[name], item["kinds"]
        row = {
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
        total = profiles.setdefault(item["profile"], {"ticks": 0, "agree": 0, "excerpts": 0})
        total["ticks"] += row["ticks"]
        total["agree"] += row["agree"]
        total["excerpts"] += 1
    for profile, total in profiles.items():
        total["mark"] = PASS_MARK.get(profile, OTHER_MARK)
        total["rate"] = total["agree"] / total["ticks"] if total["ticks"] else None
        total["pass"] = total["rate"] is not None and total["rate"] >= total["mark"]
    return {"profiles": profiles, "excerpts": excerpts, "unlabelled": sorted(set(labels) - set(predictions))}


def render(result: dict) -> str:
    out = [
        "| Profile | Excerpts | Ticks | Agree | Rate | Pass mark | Result |",
        "|---|---|---|---|---|---|---|",
    ]
    for profile, t in sorted(result["profiles"].items()):
        rate = "none scored" if t["rate"] is None else f"{100 * t['rate']:.1f} %"
        out.append(
            f"| {profile} | {t['excerpts']} | {t['ticks']} | {t['agree']} | {rate} | {100 * t['mark']:.0f} % | "
            f"{'pass' if t['pass'] else 'FAIL'} |"
        )
    out += ["", "| Excerpt | Profile | Ticks | Agree | Apart (F, V, W) | Called share apart | Unscored |"]
    out.append("|---|---|---|---|---|---|---|")
    for name, e in result["excerpts"].items():
        out.append(
            f"| {name} | {e['profile']} | {e['ticks']} | {e['agree']} | {e['apart']} | {e['apart_share']} | "
            f"{e['unscored']} |"
        )
    if result["unlabelled"]:
        out.append(f"\nLabelled but not predicted: {', '.join(result['unlabelled'])}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("labels", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = score(load_labels(args.labels), load_predictions(args.predictions))
    except InputError as exc:
        print(f"layout.py: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1) if args.json else render(result))
    return 0 if result["profiles"] and all(t["pass"] for t in result["profiles"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
