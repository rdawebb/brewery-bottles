"""Merge built bottles into `index.json`, and track failures in `state.json`."""

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bottles.gap import (
    FORMULA_URL,
    fetch_json,
    load,
    pkg_version,
    state_key,
    write,
)
from bottles.ghcr import REGISTRY, SOURCE_URL, bottle_entry, image_name

ROOT_URL = REGISTRY.replace("ghcr.io/", "https://ghcr.io/v2/", 1)


def split_pkg_version(pkg: str) -> tuple[str, int]:
    """Split brew's `pkg_version` into version and revision.

    Args:
        pkg: the `pkg_version`, such as `1.0_2`.

    Returns:
        The version and the revision, 0 when there's no `_<n>` suffix.
    """
    if m := re.fullmatch(r"(.+)_(\d+)", pkg):
        return m[1], int(m[2])

    return pkg, 0


def row(entry: dict, image_index: dict) -> tuple[str, dict]:
    """Return a bottle's name and `index.json` row.

    Args:
        entry: the formula's entry in the `brew bottle --json` output.
        image_index: the image index, for the source URL.

    Returns:
        The formula name and its row.
    """
    formula, bottle = entry["formula"], entry["bottle"]
    tag = bottle["tags"]["sequoia"]
    version, revision = split_pkg_version(formula["pkg_version"])
    cellar = bottle["cellar"]

    return formula["name"], {
        "version": version,
        "revision": revision,
        "rebuild": bottle["rebuild"],
        "sha256": tag["sha256"],
        # brew writes symbol cellars without their `:`; the formula API keeps it
        "cellar": cellar if cellar.startswith("/") else f":{cellar}",
        "image": image_name(formula["name"]),
        "source_url": image_index["annotations"][SOURCE_URL],
        "tab": tag["tab"],
    }


def add(index: dict, state: dict, name: str, new: dict) -> None:
    """Add or replace `name`'s row, and clear its failures.

    Args:
        index: the `index.json` data, updated in place.
        state: the `state.json` data, updated in place.
        name: the formula name.
        new: its row.
    """
    index.setdefault("bottles", {})[name] = new
    state.pop(f"{name}@{pkg_version(new['version'], new['revision'])}", None)


def fail(state: dict, key: str, reason: str, seconds: int | None = None) -> None:
    """Count a failed build of `key` (`name@pkg_version`).

    Args:
        state: the `state.json` data, updated in place.
        key: the `name@pkg_version` that failed.
        reason: `timeout`, `build` or `test`.
        seconds: how long it ran.
    """
    failures = state.get(key, {}).get("failures", 0) + 1
    state[key] = {"failures": failures, "reason": reason, "seconds": seconds}


def read(path: Path) -> dict:
    """Return the JSON in `path`, or `{}` before the first publish.

    Args:
        path: the file to read.

    Returns:
        The parsed data.
    """
    return json.loads(path.read_text()) if path.exists() else {}


def run(args: argparse.Namespace) -> None:
    """Merge each named build's result into `index.json` and `state.json` in `args.pages`.

    Each formula's result is in `<results>/<name>`: the bottle JSON and image index on
    success, or `failed.json` (`reason`, `seconds`) on failure; prints `<image> <sha256>` for
    each added row; each name's claim in `claims.json` is released, results or not.

    Args:
        args: the parsed command-line arguments.
    """
    if not args.names:  # Leave `generated_at` alone, so nothing is committed
        return

    index = read(args.pages / "index.json")
    state = read(args.pages / "state.json")
    formulae = None

    for name in args.names:
        d = args.results / name
        if any(d.glob("*.bottle.json")):
            new_name, new = row(bottle_entry(d), read(d / "image-index.json"))
            add(index, state, new_name, new)
            print(new["image"], new["sha256"])
            continue

        # Timeouts recorded too, so no output means a lost runner: retry it
        if not (failed := read(d / "failed.json")):
            continue

        # Keyed on the API's version, as `find_gap` looks it up
        formulae = formulae or load(fetch_json(FORMULA_URL))
        if f := formulae.get(name):
            fail(state, state_key(name, f), failed["reason"], failed["seconds"])

    index |= {
        "schema": 1,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tag": "sequoia",
        "root_url": ROOT_URL,
    }
    write(args.pages / "index.json", index)
    write(args.pages / "state.json", state)
    claims = read(args.pages / "claims.json")
    write(
        args.pages / "claims.json",
        {n: r for n, r in claims.items() if n not in args.names},
    )


def summary(plan: dict, results: Path) -> str:
    """Return the run's Markdown summary: each leg's result, wall time and version drift.

    Args:
        plan: the `gap` output this run built from.
        results: the downloaded build artifacts.

    Returns:
        The summary.
    """
    lines = ["| Formula | Result | Minutes | Version |", "| --- | --- | --- | --- |"]
    for leg in plan["matrix"]["include"]:
        name, version = leg["name"], leg["version"]
        d = results / name
        if any(d.glob("*.bottle.json")):
            result, seconds = "built", int((d / "seconds").read_text())

            # The build's fresh core clone can be ahead of the API the plan read
            if (built := bottle_entry(d)["formula"]["pkg_version"]) != version:
                version = f"{version} → {built}"

        elif failed := read(d / "failed.json"):
            result, seconds = f"failed ({failed['reason']})", failed["seconds"]

        else:
            result, seconds = "lost, retried next run", None

        minutes = "" if seconds is None else round(seconds / 60)
        lines.append(f"| {name} | {result} | {minutes} | {version} |")

    if not plan["matrix"]["include"]:
        lines = ["Nothing to build."]

    titles = {
        "building": "Building elsewhere",
        "blocked": "Blocked",
        "skipped": "Skipped",
    }
    for key, title in titles.items():
        if names := plan[key]:
            lines.append(f"\n**{title}:** {', '.join(names)}")

    return "\n".join(lines) + "\n"


def run_summary(args: argparse.Namespace) -> None:
    """Print the run's Markdown summary.

    Args:
        args: the parsed command-line arguments.
    """
    print(summary(read(args.plan), args.results), end="")


def add_parser(sub: Any) -> None:
    """Register the `merge` and `summary` commands.

    Args:
        sub: the `argparse` subparsers object.
    """
    p = sub.add_parser("merge", help="merge built bottles into index.json")
    p.add_argument("pages", type=Path, help="the checked-out Pages branch")
    p.add_argument("results", type=Path, help="the downloaded build artifacts")
    p.add_argument("names", nargs="*", help="the formulae this run built")
    p.set_defaults(run=run)

    p = sub.add_parser("summary", help="summarise a drain run in Markdown")
    p.add_argument("plan", type=Path, help="the `gap` output for this run")
    p.add_argument("results", type=Path, help="the downloaded build artifacts")
    p.set_defaults(run=run_summary)
