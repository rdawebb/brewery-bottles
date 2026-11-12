"""Tests for finding the Intel gap."""

from bottles.gap import find_gap

ARM = ["arm64_tahoe", "arm64_sequoia", "arm64_linux", "x86_64_linux"]


def formula(
    name: str, tags: list[str], version: str = "1.0", revision: int = 0, **extra
) -> dict:
    """Build a `formula.json` entry with just the fields `gap` reads.

    Args:
        name: The formula name.
        tags: The bottle tags for the formula.
        version: The formula version.
        revision: The formula revision.
        extra: Extra fields to include in the formula.

    Returns:
        A dictionary representing the formula.
    """
    return {
        "name": name,
        "versions": {"stable": version},
        "revision": revision,
        "disabled": False,
        "requirements": [],
        "bottle": {"stable": {"files": {t: {} for t in tags}}},
    } | extra


FORMULAE = {
    f["name"]: f
    for f in [
        formula("intel", [*ARM, "sonoma"]),
        formula("all", ["all"]),
        formula("arm-only", ARM),
        formula("no-bottle", [], bottle={}),
        formula("disabled", ARM, disabled=True),
        formula("needs-arm", ARM, requirements=[{"name": "arch", "version": "arm64"}]),
        formula("needs-linux", ["x86_64_linux"], requirements=[{"name": "linux"}]),
        formula("needs-tahoe", ARM, requirements=[{"name": "macos", "version": "26"}]),
        formula(
            "max-sonoma", ARM, requirements=[{"name": "maximum_macos", "version": "14"}]
        ),
        formula(
            "needs-sequoia", ARM, requirements=[{"name": "macos", "version": "15"}]
        ),
        formula("indexed", ARM, version="2.0", revision=1),
        formula("bumped", ARM, version="2.1"),
        formula("revised", ARM, revision=1),
        formula("skipped", ARM, version="3.0", revision=2),
        formula("failed-once", ARM),
    ]
}

INDEX = {
    "bottles": {
        "indexed": {"version": "2.0", "revision": 1},
        "bumped": {"version": "2.0", "revision": 0},
        "revised": {"version": "1.0", "revision": 0},
    }
}

STATE = {"skipped@3.0_2": {"failures": 2}, "failed-once@1.0": {"failures": 1}}


def test_gap() -> None:
    """Only buildable formulae with no Intel bottle, not indexed at this version, and not skipped."""
    assert find_gap(FORMULAE, FORMULAE, INDEX, STATE) == [
        "arm-only",
        "bumped",
        "failed-once",
        "needs-sequoia",
        "no-bottle",
        "revised",
    ]


def test_empty_index_and_state() -> None:
    """Before the first publish, every buildable gap formula is in the gap."""
    assert find_gap(FORMULAE, ["indexed", "skipped", "intel"], {}, {}) == [
        "indexed",
        "skipped",
    ]
