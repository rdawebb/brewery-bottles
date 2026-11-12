"""Entry point: `python -m bottles <command>`."""

import argparse


def main(argv: list[str] | None = None) -> None:
    """Parse `argv` and run the chosen command.

    Args:
        argv: The command line arguments to parse. If `None`, defaults to `sys.argv[1:]`.
    """
    parser = argparse.ArgumentParser(prog="bottles")
    parser.add_subparsers(dest="command", required=True)
    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
