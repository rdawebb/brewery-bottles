"""Push a bottle to GHCR in Homebrew's OCI layout."""

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

REGISTRY = "ghcr.io/rdawebb/brewery-bottles"
REPO_URL = "https://github.com/rdawebb/brewery-bottles"
LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"
INDEX = "application/vnd.oci.image.index.v1+json"
SOURCE_URL = "io.github.rdawebb.brewery-bottles.source-url"


def image_name(name: str) -> str:
    """Return Homebrew's GHCR image name for a formula.

    Args:
        name: the formula name.

    Returns:
        The name with `@` → `/` and `+` → `x`, which OCI names can't hold.
    """
    return name.replace("@", "/").replace("+", "x")


def bottle_entry(d: Path) -> dict:
    """Return the one formula entry in the `brew bottle --json` output in `d`.

    Args:
        d: the directory `brew bottle` wrote to.

    Returns:
        The entry, with its `formula` and `bottle` sections.
    """
    ((_, entry),) = json.loads(next(d.glob("*.bottle.json")).read_text()).items()

    return entry


def image_tag(pkg_version: str, rebuild: int) -> str:
    """Return the image tag, without a bottle tag.

    Args:
        pkg_version: the formula's `pkg_version`.
        rebuild: the bottle rebuild.

    Returns:
        `<pkg_version>`, plus `-<rebuild>` when it's non-zero.
    """
    return f"{pkg_version}-{rebuild}" if rebuild else pkg_version


def ref_name(pkg_version: str, rebuild: int) -> str:
    """Return the `ref.name` brew matches a bottle's manifest entry on.

    Args:
        pkg_version: the formula's `pkg_version`.
        rebuild: the bottle rebuild.

    Returns:
        `<pkg_version>.sequoia`, plus `.<rebuild>` when it's non-zero.
    """
    return f"{pkg_version}.sequoia" + (f".{rebuild}" if rebuild else "")


def annotations(formula: dict, source_url: str) -> dict[str, str]:
    """Return the image annotations: this repo (which links the package), version, licence and source.

    Args:
        formula: the `formula` section of the `brew bottle --json` output.
        source_url: the formula's stable source URL, for the licence source offer.

    Returns:
        The annotations, without any that are empty.
    """
    notes = {
        "org.opencontainers.image.source": REPO_URL,
        "org.opencontainers.image.version": formula["pkg_version"],
        "org.opencontainers.image.licenses": formula["license"],
        SOURCE_URL: source_url,
    }

    return {k: v for k, v in notes.items() if v}


def image_index(entry: dict, manifest: dict, notes: dict[str, str]) -> dict:
    """Return the image index brew reads: one manifest, with the `sh.brew.*` annotations.

    Args:
        entry: one formula's `brew bottle --json` output.
        manifest: the pushed image manifest's descriptor (`mediaType`, `digest`, `size`).
        notes: the image annotations.

    Returns:
        The image index.
    """
    bottle = entry["bottle"]
    tag = bottle["tags"]["sequoia"]
    tab = tag["tab"]

    return {
        "schemaVersion": 2,
        "mediaType": INDEX,
        "manifests": [
            {
                "mediaType": manifest["mediaType"],
                "digest": manifest["digest"],
                "size": manifest["size"],
                "platform": {
                    "architecture": "amd64",
                    "os": "darwin",
                    "os.version": tab["built_on"]["os_version"],
                },
                "annotations": {
                    "org.opencontainers.image.ref.name": ref_name(
                        entry["formula"]["pkg_version"], bottle["rebuild"]
                    ),
                    "sh.brew.bottle.digest": tag["sha256"],
                    "sh.brew.tab": json.dumps(tab),
                },
            }
        ],
        "annotations": notes,
    }


def _run(*cmd: str | Path, cwd: Path | None = None) -> Any:
    """Run `cmd`, letting its stderr through, and parse its stdout as JSON.

    Args:
        cmd: the command and its arguments.
        cwd: the directory to run it in.

    Returns:
        The parsed stdout.
    """
    out = subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.PIPE, text=True)

    return json.loads(out.stdout)


def run(args: argparse.Namespace) -> None:
    """Push the bottle in `args.dir`: the image manifest, then the image index on the same tag.

    Args:
        args: the parsed command-line arguments.
    """
    entry = bottle_entry(args.dir)
    formula, bottle = entry["formula"], entry["bottle"]
    name = formula["name"]
    tag = image_tag(formula["pkg_version"], bottle["rebuild"])
    ref = f"{REGISTRY}/{image_name(name)}:{tag}"

    # The local tap, not the API, so it's the version just built
    info = _run("brew", "info", "--json=v2", "--formula", name)
    notes = annotations(formula, info["formulae"][0]["urls"]["stable"]["url"])

    flags = [f"--annotation={k}={v}" for k, v in notes.items()]
    layer = f"{bottle['tags']['sequoia']['local_filename']}:{LAYER}"
    manifest = _run("oras", "push", "--format=json", *flags, ref, layer, cwd=args.dir)

    index = args.dir / "image-index.json"
    index.write_text(json.dumps(image_index(entry, manifest, notes), indent=2))
    subprocess.run(
        ["oras", "manifest", "push", f"--media-type={INDEX}", ref, index], check=True
    )


def add_parser(sub: Any) -> None:
    """Register the `push` command.

    Args:
        sub: the `argparse` subparsers object.
    """
    p = sub.add_parser("push", help="push a built bottle to GHCR")
    p.add_argument("dir", type=Path, help="the directory `brew bottle` wrote to")
    p.set_defaults(run=run)
