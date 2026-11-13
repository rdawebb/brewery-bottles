"""Tests for Homebrew's GHCR naming and image index."""

import json

from bottles.ghcr import annotations, image_index, image_name, image_tag


def test_names_match_homebrew() -> None:
    """Test that the image name and tag are constructed correctly."""
    assert image_name("python@3.13") == "python/3.13"
    assert image_name("libxml++") == "libxmlxx"
    assert image_tag("1.0_1", 0) == "1.0_1"
    assert image_tag("1.0_1", 2) == "1.0_1-2"


def test_image_index() -> None:
    """Test that the image index is constructed correctly."""
    tab = {"arch": "x86_64", "built_on": {"os_version": "macOS 15.7"}}
    formula = {"name": "go", "pkg_version": "1.27.4", "license": None}
    entry = {
        "formula": formula,
        "bottle": {"rebuild": 1, "tags": {"sequoia": {"sha256": "abc", "tab": tab}}},
    }
    manifest = {"mediaType": "m", "digest": "sha256:def", "size": 9}
    notes = annotations(formula, "https://go.dev/src.tar.gz")
    index = image_index(entry, manifest, notes)

    assert "org.opencontainers.image.licenses" not in notes
    assert index["annotations"] == notes
    [m] = index["manifests"]
    assert m["digest"] == "sha256:def"
    assert m["platform"]["os.version"] == "macOS 15.7"
    assert m["annotations"]["org.opencontainers.image.ref.name"] == "1.27.4.sequoia.1"
    assert m["annotations"]["sh.brew.bottle.digest"] == "abc"
    assert json.loads(m["annotations"]["sh.brew.tab"]) == tab
