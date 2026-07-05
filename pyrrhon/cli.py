"""Command-line entry point: `pyrrhon [repo-path] [--text]`."""

from __future__ import annotations

import argparse

from pyrrhon import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pyrrhon",
        description="Talk to a codebase like a senior engineer is sitting next to you.",
    )
    parser.add_argument("repo", nargs="?", default=".", help="Path to the repo to discuss")
    parser.add_argument(
        "--text",
        action="store_true",
        help="Use the plain-text REPL instead of the TUI",
    )
    parser.add_argument("--version", action="version", version=f"pyrrhon {__version__}")
    args = parser.parse_args(argv)

    # Channels imported lazily so `--version` works without touching them.
    if args.text:
        from pyrrhon.repl import run_repl

        run_repl(args.repo)
    else:
        from pyrrhon.tui.app import run_tui

        run_tui(args.repo)
