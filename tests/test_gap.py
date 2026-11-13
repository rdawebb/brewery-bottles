"""Tests for finding the Intel gap."""

from collections.abc import Sequence

import pytest

from bottles.gap import bottle_filename, dependencies, find_gap, load, waves

ARM = ("arm64_tahoe", "arm64_sequoia", "arm64_linux", "x86_64_linux")


def formula(
    name: str,
    tags: Sequence[str] = ARM,
    version: str = "1.0",
    revision: int = 0,
    **extra,
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
        "dependencies": [],
        "build_dependencies": [],
        "bottle": {"stable": {"files": {t: {} for t in tags}}},
    } | extra


FORMULAE = load(
    [
        formula("intel", [*ARM, "sonoma"]),
        formula("all", ["all"]),
        formula("arm-only"),
        formula("no-bottle", [], bottle={}),
        formula("disabled", disabled=True),
        formula("needs-arm", requirements=[{"name": "arch", "version": "arm64"}]),
        formula("needs-linux", ["x86_64_linux"], requirements=[{"name": "linux"}]),
        formula("needs-tahoe", requirements=[{"name": "macos", "version": "26"}]),
        formula(
            "max-sonoma", requirements=[{"name": "maximum_macos", "version": "14"}]
        ),
        formula("needs-sequoia", requirements=[{"name": "macos", "version": "15"}]),
        formula("indexed", version="2.0", revision=1),
        formula("bumped", version="2.1"),
        formula("revised", revision=1),
        formula("skipped", version="3.0", revision=2),
        formula("failed-once"),
    ]
)

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


def test_chain_builds_the_leaf_first() -> None:
    """`a→b→c`, all in the gap, gives wave `[c]` with `a` and `b` blocked."""
    formulae = {
        "a": formula("a", dependencies=["b"]),
        "b": formula("b", build_dependencies=["c"]),
        "c": formula("c"),
    }
    assert waves(formulae, ["a"], {}, {}) == (["c"], ["a", "b"])


def test_published_dependencies_dont_block() -> None:
    """A dependency with an official bottle, or with our bottle at its current version, is poured."""
    formulae = {
        "a": formula("a", dependencies=["intel"], build_dependencies=["ours"]),
        "intel": formula("intel", ["sonoma"]),
        "ours": formula("ours"),
    }
    index = {"bottles": {"ours": {"version": "1.0", "revision": 0}}}
    assert waves(formulae, ["a"], index, {}) == (["a"], [])


def test_poured_bottle_needs_its_runtime_dependencies_only() -> None:
    """A poured bottle's unpublished runtime dependency blocks; its build dependency isn't needed."""
    formulae = {
        "a": formula("a", dependencies=["intel"]),
        "intel": formula(
            "intel", ["sonoma"], dependencies=["runtime"], build_dependencies=["build"]
        ),
        "runtime": formula("runtime"),
        "build": formula("build"),
    }
    assert waves(formulae, ["a"], {}, {}) == (["runtime"], ["a"])


def test_unbuildable_dependency_blocks() -> None:
    """A dependency that can't be built leaves its dependents blocked."""
    formulae = {
        "a": formula("a", dependencies=["skipped"]),
        "skipped": formula("skipped"),
    }
    state = {"skipped@1.0": {"failures": 2}}
    assert waves(formulae, ["a"], {}, state) == ([], ["a"])


def test_load_applies_sequoia_variation() -> None:
    """Intel Sequoia's `variations` replace the top-level fields."""
    f = formula("a", dependencies=["x"])
    f["variations"] = {
        "sequoia": {"dependencies": ["y"]},
        "x86_64_linux": {"dependencies": ["z"]},
    }
    assert load([f])["a"]["dependencies"] == ["y"]


def test_our_dependencies_install_in_order() -> None:
    """Our bottles come after their own dependencies; official bottles are left to brew."""
    formulae = {
        "a": formula("a", ARM, dependencies=["intel", "x"], build_dependencies=["y"]),
        "intel": formula("intel", ["sonoma"]),
        "x": formula("x", ARM, dependencies=["y"], build_dependencies=["unneeded"]),
        "y": formula("y", ARM),
        "unneeded": formula("unneeded", ARM),
    }
    row = {"version": "1.0", "revision": 0}
    index = {"bottles": {"x": row, "y": row, "unneeded": row}}
    assert dependencies(formulae, "a", index) == ["y", "x"]


def test_our_dependencies_refuses_unpublished() -> None:
    """A missing or stale row fails loudly instead of building or pouring the wrong version."""
    formulae = {
        "a": formula("a", ARM, dependencies=["x", "y"]),
        "x": formula("x", ARM),
        "y": formula("y", ARM, version="2.0"),
    }
    index = {"bottles": {"y": {"version": "1.0", "revision": 0}}}
    with pytest.raises(ValueError, match="a needs unpublished bottles: x y"):
        dependencies(formulae, "a", index)


def test_bottle_filename() -> None:
    """Revision and rebuild appear as `brew bottle` writes them."""
    row = {"version": "1.0", "revision": 0, "rebuild": 0}
    assert bottle_filename("go", row) == "go--1.0.sequoia.bottle.tar.gz"
    row = {"version": "3.13.1", "revision": 2, "rebuild": 1}
    assert (
        bottle_filename("python@3.13", row)
        == "python@3.13--3.13.1_2.sequoia.bottle.1.tar.gz"
    )
