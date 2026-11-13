"""Entry point: `python -m bottles <command>`."""

import argparse

from bottles import gap, ghcr


def main(argv: list[str] | None = None) -> None:
    """Parse `argv` and run the chosen command.

    Args:
        argv: The command line arguments to parse. If `None`, defaults to `sys.argv[1:]`.
    """
    parser = argparse.ArgumentParser(prog="bottles")
    sub = parser.add_subparsers(dest="command", required=True)
    gap.add_parser(sub)
    ghcr.add_parser(sub)
    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
