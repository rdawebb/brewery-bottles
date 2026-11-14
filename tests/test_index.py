"""Tests for merging bottles into `index.json` and counting failures."""

import json
from pathlib import Path

from bottles.__main__ import main
from bottles.gap import find_gap
from bottles.index import add, fail, row, summary
from tests.test_gap import formula


def built(name: str, pkg_version: str, cellar: str = "any_skip_relocation") -> dict:
    """Return a `brew bottle --json` entry with just the fields `row` reads.

    Args:
        name: the formula name.
        pkg_version: its `pkg_version`.
        cellar: the cellar as brew writes it.

    Returns:
        The bottle entry.
    """
    tag = {"sha256": f"sha-{pkg_version}", "tab": {"arch": "x86_64"}}
    formula = {"name": name, "pkg_version": pkg_version}
    bottle = {"cellar": cellar, "rebuild": 0, "tags": {"sequoia": tag}}

    return {"formula": formula, "bottle": bottle}


IMAGE_INDEX = {"annotations": {"io.github.rdawebb.brewery-bottles.source-url": "u"}}


def test_row() -> None:
    """Test that the revision is split out, `:` goes back on symbol cellars, and `@` names map."""
    name, r = row(built("python@3.13", "3.13.1_2"), IMAGE_INDEX)
    assert name == "python@3.13"
    assert r == {
        "version": "3.13.1",
        "revision": 2,
        "rebuild": 0,
        "sha256": "sha-3.13.1_2",
        "cellar": ":any_skip_relocation",
        "image": "python/3.13",
        "source_url": "u",
        "tab": {"arch": "x86_64"},
    }
    assert row(built("a", "1.0", "/usr/local/Cellar"), IMAGE_INDEX)[1]["cellar"] == (
        "/usr/local/Cellar"
    )


def test_add_replaces_on_bump_and_leaves_other_rows() -> None:
    """Test that a new name is added, a bump replaces its row and clears its failures, others stay."""
    index = {"bottles": {"old": {"version": "9"}}}
    state = {"go@1.1": {"failures": 1}, "other@1.0": {"failures": 1}}

    add(index, state, *row(built("go", "1.0"), IMAGE_INDEX))
    assert index["bottles"]["go"]["version"] == "1.0"

    add(index, state, *row(built("go", "1.1"), IMAGE_INDEX))
    assert index["bottles"]["go"]["sha256"] == "sha-1.1"
    assert index["bottles"]["old"] == {"version": "9"}
    assert state == {"other@1.0": {"failures": 1}}


def test_failures_count_up_to_the_skip_threshold() -> None:
    """Test that the first failure is retried; the second skips that version in the gap."""
    formulae = {"go": formula("go")}
    state: dict = {}

    fail(state, "go@1.0", "build", 60)
    assert find_gap(formulae, ["go"], {}, state) == ["go"]

    fail(state, "go@1.0", "timeout", 19800)
    assert state["go@1.0"] == {"failures": 2, "reason": "timeout", "seconds": 19800}
    assert find_gap(formulae, ["go"], {}, state) == []


def test_merge_results(tmp_path: Path, monkeypatch, capsys) -> None:
    """Test that a built leg is added, a failed one counted, and a missing one left to retry."""
    results, pages = tmp_path / "results", tmp_path / "pages"
    (results / "go").mkdir(parents=True)
    (results / "bad").mkdir()
    pages.mkdir()
    (pages / "claims.json").write_text('{"go": 1, "bad": 1, "other": 2}')
    entry = built("go", "1.0")
    (results / "go" / "go--1.0.sequoia.bottle.json").write_text(
        json.dumps({"go": entry})
    )
    (results / "go" / "image-index.json").write_text(json.dumps(IMAGE_INDEX))
    (results / "bad" / "failed.json").write_text('{"reason": "test", "seconds": 5}')
    api = [formula("go"), formula("bad", version="2.0", revision=1), formula("gone")]
    monkeypatch.setattr("bottles.index.fetch_json", lambda url: api)

    main(["merge", str(pages), str(results), "go", "bad", "gone"])

    assert capsys.readouterr().out == "go sha-1.0\n"
    assert (
        json.loads((pages / "index.json").read_text())["bottles"]["go"]["version"]
        == "1.0"
    )
    assert json.loads((pages / "state.json").read_text()) == {
        "bad@2.0_1": {"failures": 1, "reason": "test", "seconds": 5},
    }
    assert json.loads((pages / "claims.json").read_text()) == {"other": 2}


def test_summary(tmp_path: Path) -> None:
    """Test that each leg's result, minutes and version drift are shown, then blocked and skipped."""
    (tmp_path / "go").mkdir()
    (tmp_path / "go" / "go--1.1.sequoia.bottle.json").write_text(
        json.dumps({"go": built("go", "1.1")})
    )
    (tmp_path / "go" / "seconds").write_text("1380\n")
    (tmp_path / "rust").mkdir()
    (tmp_path / "rust" / "failed.json").write_text(
        '{"reason": "timeout", "seconds": 19800}'
    )
    legs = [{"name": n, "version": "1.0"} for n in ("go", "rust", "gone")]
    plan = {
        "matrix": {"include": legs},
        "blocked": ["a", "b"],
        "skipped": [],
        "building": ["c"],
    }

    assert summary(plan, tmp_path).splitlines() == [
        "| Formula | Result | Minutes | Version |",
        "| --- | --- | --- | --- |",
        "| go | built | 23 | 1.0 → 1.1 |",
        "| rust | failed (timeout) | 330 | 1.0 |",
        "| gone | lost, retried next run |  | 1.0 |",
        "",
        "**Building elsewhere:** c",
        "",
        "**Blocked:** a, b",
    ]
