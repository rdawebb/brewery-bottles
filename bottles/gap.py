"""Find the formulae with no Intel bottle for their current version."""

import argparse
import graphlib
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

FORMULA_URL = "https://formulae.brew.sh/api/formula.json"
PAGES_URL = "https://rdawebb.github.io/brewery-bottles"
SEQUOIA = 15

# Failures of one `name@pkg_version` before it's skipped until core bumps it
SKIP_AFTER = 2


def fetch_json(url: str, missing: Any = None) -> Any:
    """Fetch and parse `url`, returning `missing` on a 404 (the Pages files before the first publish).

    Args:
        url: the URL to fetch.
        missing: the value to return on a 404.

    Returns:
        The parsed JSON data or `missing` if a 404 is received.
    """
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return json.load(resp)

    except urllib.error.HTTPError as e:
        if e.code == 404 and missing is not None:
            return missing

        raise


def pkg_version(version: str, revision: int) -> str:
    """Return brew's `pkg_version`: `version`, plus `_<revision>` when it's non-zero.

    Args:
        version: the stable version.
        revision: the formula revision.

    Returns:
        The `pkg_version` string.
    """
    return f"{version}_{revision}" if revision else version


def _macos(version: str) -> int:
    """Return the macOS version number for a `version` string.

    Args:
        version: a macOS version string

    Returns:
        The macOS version number.
    """
    return int(version.split(".")[0])


def has_intel_bottle(formula: dict) -> bool:
    """Return whether core has an Intel macOS or `:all` bottle for `formula`.

    Args:
        formula: a `formula.json` entry.

    Returns:
        True if core has a bottle that pours on Intel Sequoia, False otherwise.
    """
    files = (formula["bottle"].get("stable") or {}).get("files", {})

    return any(not t.startswith("arm64_") and not t.endswith("_linux") for t in files)


def can_build(formula: dict) -> bool:
    """Return whether `formula` can build on Intel Sequoia.

    Args:
        formula: a `formula.json` entry.

    Returns:
        False if the formula is disabled or needs arm64, Linux, or another macOS.
    """
    if formula["disabled"]:
        return False

    for req in formula["requirements"]:
        name, version = req["name"], req.get("version")
        if (
            name == "linux"
            or (name == "arch" and version == "arm64")
            or (name == "macos" and version and _macos(version) > SEQUOIA)
            or (name == "maximum_macos" and version and _macos(version) < SEQUOIA)
        ):
            return False

    return True


def unpublished(name: str, formulae: dict[str, dict], index: dict) -> bool:
    """Return whether neither core nor `index.json` has a bottle for `name`'s current version.

    Args:
        name: the formula to check.
        formulae: homebrew/core's formulae by name.
        index: the published `index.json`.

    Returns:
        True if installing `name` would build it from source, False otherwise.
    """
    f = formulae[name]
    row = index.get("bottles", {}).get(name, {})
    indexed = (row.get("version"), row.get("revision")) == (
        f["versions"]["stable"],
        f["revision"],
    )

    return not indexed and not has_intel_bottle(f)


def find_gap(
    formulae: dict[str, dict], names: Iterable[str], index: dict, state: dict
) -> list[str]:
    """Return the sorted `names` that need a build and aren't already published or skipped.

    Args:
        formulae: homebrew/core's formulae by name.
        names: the selection to check.
        index: the published `index.json`.
        state: the published `state.json`, failure counts by `name@pkg_version`.

    Returns:
        The sorted list of names that need an Intel build.
    """
    gap = []
    for name in names:
        f = formulae[name]
        if not unpublished(name, formulae, index):
            continue

        key = f"{name}@{pkg_version(f['versions']['stable'], f['revision'])}"
        if state.get(key, {}).get("failures", 0) >= SKIP_AFTER:
            continue

        if can_build(f):
            gap.append(name)

    return sorted(gap)


def closure(formulae: dict[str, dict], names: Iterable[str], index: dict) -> set[str]:
    """Return `names` plus everything needed to install them.

    Runtime dependencies are always followed; build dependencies are followed only for
    unpublished formulae; `uses_from_macos` is left out, as the system provides it.

    Args:
        formulae: homebrew/core's formulae by name.
        names: the formulae to start from.
        index: the published `index.json`.

    Returns:
        The set of formula names in the closure.
    """
    seen: set[str] = set()
    todo = list(names)
    while todo:
        name = todo.pop()
        if name in seen:
            continue

        seen.add(name)
        f = formulae[name]
        todo += f["dependencies"]
        if unpublished(name, formulae, index):
            todo += f["build_dependencies"]

    return seen


def waves(
    formulae: dict[str, dict], names: Iterable[str], index: dict, state: dict
) -> tuple[list[str], list[str]]:
    """Split the gap in `names`' closure into this wave and the formulae it blocks.

    A gap formula is buildable when nothing it needs, including the runtime dependencies
    of poured bottles, is still unpublished.

    Args:
        formulae: homebrew/core's formulae by name.
        names: the selection.
        index: the published `index.json`.
        state: the published `state.json`.

    Returns:
        The sorted buildable names, and the sorted blocked names.
    """
    wave, blocked = [], []
    for name in find_gap(formulae, closure(formulae, names, index), index, state):
        needs = closure(formulae, [name], index) - {name}

        if any(unpublished(d, formulae, index) for d in needs):
            blocked.append(name)

        else:
            wave.append(name)

    return wave, blocked


def load(formula_json: list[dict]) -> dict[str, dict]:
    """Index `formula.json` by name, with each formula's Intel Sequoia overrides applied.

    The top-level fields describe brew's default platform; `variations["sequoia"]`
    replaces the ones that differ on Intel Sequoia, such as `dependencies`.

    Args:
        formula_json: the parsed `formula.json`.

    Returns:
        The formulae by name.
    """
    return {
        f["name"]: f | (f.get("variations") or {}).get("sequoia", {})
        for f in formula_json
    }


def dependencies(formulae: dict[str, dict], name: str, index: dict) -> list[str]:
    """Return the dependencies of `name` that pour from the bottles, in install order.

    Each comes after its own dependencies, so installing them one at a time never makes
    brew build one from source.

    Args:
        formulae: homebrew/core's formulae by name.
        name: the formula about to be built.
        index: the published `index.json`.

    Returns:
        The names with no official Intel bottle.

    Raises:
        ValueError: if any of them has no current bottle in `index.json` yet.
    """
    needs = closure(formulae, [name], index) - {name}
    if missing := sorted(n for n in needs if unpublished(n, formulae, index)):
        raise ValueError(f"{name} needs unpublished bottles: {' '.join(missing)}")

    graph = {n: formulae[n]["dependencies"] for n in needs}

    return [
        n
        for n in graphlib.TopologicalSorter(graph).static_order()
        if not has_intel_bottle(formulae[n])
    ]


def bottle_filename(name: str, row: dict) -> str:
    """Return brew's local filename for the bottle in an `index.json` row.

    Args:
        name: the formula name.
        row: its `index.json` row.

    Returns:
        `<name>--<pkg_version>.sequoia.bottle[.<rebuild>].tar.gz`, as `brew bottle` names it.
    """
    version = pkg_version(row["version"], row["revision"])
    rebuild = f".{row['rebuild']}" if row["rebuild"] else ""

    return f"{name}--{version}.sequoia.bottle{rebuild}.tar.gz"


def run(args: argparse.Namespace) -> None:
    """Print this wave as a workflow matrix, plus the blocked formulae, as one JSON object.

    Args:
        args: the parsed command-line arguments.
    """
    names = args.seed.read_text().split()
    formulae = load(fetch_json(FORMULA_URL))
    index = fetch_json(f"{PAGES_URL}/index.json", missing={})
    state = fetch_json(f"{PAGES_URL}/state.json", missing={})

    wave, blocked = waves(formulae, names, index, state)
    matrix = {"include": [{"name": name} for name in wave]}

    print(json.dumps({"matrix": matrix, "blocked": blocked}))


def run_deps(args: argparse.Namespace) -> None:
    """Print `<image> <sha256> <filename>` for each of the bottles the formula needs, in order.

    Args:
        args: the parsed command-line arguments.
    """
    try:
        formulae = load(fetch_json(FORMULA_URL))
        index = fetch_json(f"{PAGES_URL}/index.json", missing={})

        for name in dependencies(formulae, args.formula, index):
            row = index["bottles"][name]
            print(row["image"], row["sha256"], bottle_filename(name, row))

    except ValueError as e:
        sys.exit(f"error: {e}")


def add_parser(sub: Any) -> None:
    """Register the `gap` and `deps` commands.

    Args:
        sub: the `argparse` subparsers object.
    """
    p = sub.add_parser(
        "gap", help="plan the next wave of Intel builds for the seed list"
    )
    p.add_argument("--seed", type=Path, default=Path("seed.txt"))
    p.set_defaults(run=run)

    p = sub.add_parser(
        "deps", help="list our bottles a formula needs, in install order"
    )
    p.add_argument("formula")
    p.set_defaults(run=run_deps)
