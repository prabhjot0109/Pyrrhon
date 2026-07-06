"""Command-line entry point: `pyrrhon [repo-path] [--text]`."""

from __future__ import annotations

import argparse
import sys

from pyrrhon import __version__


def _force_utf8_io() -> None:
    """Windows consoles default to a legacy codepage (cp1252); the banner owl,
    the `→` tool marker, and 📍/⏹ citation glyphs are outside it and raise
    UnicodeEncodeError on the first tool call. Force UTF-8 on the real streams
    once, at the entry point, so every channel (REPL, wizard, TUI) is safe.
    No-op where the stream is already UTF-8 or lacks reconfigure()."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> None:
    _force_utf8_io()
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
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Start with the voice pipeline on (equivalent to /voice on)",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run the provider/API-key setup wizard, then start",
    )
    parser.add_argument("--version", action="version", version=f"pyrrhon {__version__}")
    args = parser.parse_args(argv)

    if args.setup:
        from pyrrhon.config.wizard import run_wizard

        run_wizard()

    # Channels imported lazily so `--version` works without touching them.
    if args.text:
        from pyrrhon.repl import run_repl

        run_repl(args.repo, voice=args.voice)
    else:
        from pyrrhon.tui.app import run_tui

        run_tui(args.repo, voice=args.voice)
