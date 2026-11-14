"""Merge built bottles into `index.json`, and track failures in `state.json`."""

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bottles.gap import FORMULA_URL, fetch_json, load, pkg_version
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


def write(path: Path, data: dict) -> None:
    """Write `data` with sorted keys and a stable indent, so diffs show only what changed.

    Args:
        path: the file to write.
        data: the data to write.
    """
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", newline="\n")


def run(args: argparse.Namespace) -> None:
    """Merge each named build's result into `index.json` and `state.json` in `args.pages`.

    Each formula's result is in `<results>/result-<name>`: the bottle JSON and image index on
    success, or `failed.json` (`reason`, `seconds`) on failure; prints `<image> <sha256>` for
    each added row.

    Args:
        args: the parsed command-line arguments.
    """
    index = read(args.pages / "index.json")
    state = read(args.pages / "state.json")
    formulae = None

    for name in args.names:
        d = args.results / f"result-{name}"
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
            key = f"{name}@{pkg_version(f['versions']['stable'], f['revision'])}"
            fail(state, key, failed["reason"], failed["seconds"])

    index |= {
        "schema": 1,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tag": "sequoia",
        "root_url": ROOT_URL,
    }
    write(args.pages / "index.json", index)
    write(args.pages / "state.json", state)


def add_parser(sub: Any) -> None:
    """Register the `merge` command.

    Args:
        sub: the `argparse` subparsers object.
    """
    p = sub.add_parser("merge", help="merge built bottles into index.json")
    p.add_argument("pages", type=Path, help="the checked-out Pages branch")
    p.add_argument("results", type=Path, help="the downloaded build artifacts")
    p.add_argument("names", nargs="*", help="the formulae this run built")
    p.set_defaults(run=run)
