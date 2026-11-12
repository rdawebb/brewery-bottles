"""Find the formulae with no Intel bottle for their current version."""

import argparse
import json
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


def pkg_version(formula: dict) -> str:
    """Return brew's `pkg_version`: the stable version, plus `_<revision>` when it's non-zero.

    Args:
        formula: a `formula.json` entry.

    Returns:
        The `pkg_version` string.
    """
    version, revision = formula["versions"]["stable"], formula["revision"]

    return f"{version}_{revision}" if revision else version


def _macos(version: str) -> int:
    """Return the macOS version number for a `version` string.

    Args:
        version: a macOS version string

    Returns:
        The macOS version number.
    """
    return int(version.split(".")[0])


def needs_build(formula: dict) -> bool:
    """Return whether core has no Intel bottle for `formula` and it can build on Intel Sequoia.

    Args:
        formula: a `formula.json` entry.

    Returns:
        True if the formula needs an Intel build, False otherwise.
    """
    files = (formula["bottle"].get("stable") or {}).get("files", {})
    if "all" in files or any(
        not t.startswith("arm64_") and not t.endswith("_linux") for t in files
    ):
        return False

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


def find_gap(
    formulae: dict[str, dict], names: Iterable[str], index: dict, state: dict
) -> list[str]:
    """Return the sorted `names` that need a build and aren't already published or skipped.

    Args:
        formulae: homebrew/core's formulae by name, from `formula.json`.
        names: the selection to check.
        index: the published `index.json`.
        state: the published `state.json`, failure counts by `name@pkg_version`.

    Returns:
        The sorted list of names that need an Intel build.
    """
    gap = []
    for name in names:
        f = formulae[name]
        row = index.get("bottles", {}).get(name, {})
        if (row.get("version"), row.get("revision")) == (
            f["versions"]["stable"],
            f["revision"],
        ):
            continue

        if state.get(f"{name}@{pkg_version(f)}", {}).get("failures", 0) >= SKIP_AFTER:
            continue

        if needs_build(f):
            gap.append(name)

    return sorted(gap)


def run(args: argparse.Namespace) -> None:
    """Print the gap in the seed list, one name per line.

    Args:
        args: the parsed command-line arguments.
    """
    names = args.seed.read_text().split()
    formulae = {f["name"]: f for f in fetch_json(FORMULA_URL)}
    index = fetch_json(f"{PAGES_URL}/index.json", missing={})
    state = fetch_json(f"{PAGES_URL}/state.json", missing={})

    print(*find_gap(formulae, names, index, state), sep="\n")


def add_parser(sub: Any) -> None:
    """Register the `gap` command.

    Args:
        sub: the `argparse` subparsers object.
    """
    p = sub.add_parser("gap", help="list the seed formulae that need an Intel build")
    p.add_argument("--seed", type=Path, default=Path("seed.txt"))
    p.set_defaults(run=run)
