"""Command-line entry point: `pyrrhon [repo-path]`."""

from __future__ import annotations

import argparse

from pyrrhon import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pyrrhon",
        description="Talk to a codebase like a senior engineer is sitting next to you.",
    )
    parser.add_argument("repo", nargs="?", default=".", help="Path to the repo to discuss")
    parser.add_argument("--version", action="version", version=f"pyrrhon {__version__}")
    args = parser.parse_args(argv)

    # Imported lazily so `--version` works before the REPL exists (Task 9 wires it).
    from pyrrhon.repl import run_repl

    run_repl(args.repo)
