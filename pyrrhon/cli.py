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
        "-c",
        "--continue",
        dest="continue_last",
        action="store_true",
        help="Resume this repo's most recent saved session",
    )
    parser.add_argument(
        "--resume",
        metavar="ID",
        help="Resume a saved session by id (a leading prefix is enough; see /sessions)",
    )
    parser.add_argument(
        "--no-save",
        dest="save",
        action="store_false",
        help="Do not write this session to ~/.pyrrhon/sessions",
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="print_prompt",
        nargs="?",
        const="",
        metavar="QUESTION",
        help=(
            "Answer one question and exit, printing to stdout. Reads the "
            "question from stdin when none is given, so it pipes."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="With --print, emit the answer, its citations and the turn's trace as JSON",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run the provider/API-key setup wizard, then start",
    )
    parser.add_argument(
        "--trust-repo",
        action="store_true",
        help=(
            "Grant this repo's .pyrrhon.toml servers/providers and soul files "
            "without prompting. For automation only — it runs programs the repo "
            "chose."
        ),
    )
    parser.add_argument("--version", action="version", version=f"pyrrhon {__version__}")
    args = parser.parse_args(argv)

    if args.setup:
        from pyrrhon.config.wizard import run_wizard

        run_wizard()

    # "" means "the most recent one", which is what --continue asks for, and
    # None means "start fresh". One value rather than two flags threaded
    # separately, because every channel would otherwise re-derive the same
    # three-way choice.
    resume = args.resume if args.resume else ("" if args.continue_last else None)

    # Channels imported lazily so `--version` works without touching them.
    if args.print_prompt is not None:
        # Checked before --text and --voice rather than beside them: --print
        # is a different KIND of run, not a third screen. Voice on a channel
        # with no microphone and no listener would be a silent no-op.
        from pyrrhon.headless import main_headless

        main_headless(
            args.repo,
            args.print_prompt or None,
            trust_repo=args.trust_repo,
            as_json=args.json,
        )
    elif args.text:
        from pyrrhon.repl import run_repl

        run_repl(
            args.repo,
            voice=args.voice,
            trust_repo=args.trust_repo,
            resume=resume,
            save=args.save,
        )
    else:
        from pyrrhon.tui.app import run_tui

        run_tui(
            args.repo,
            voice=args.voice,
            trust_repo=args.trust_repo,
            resume=resume,
            save=args.save,
        )
