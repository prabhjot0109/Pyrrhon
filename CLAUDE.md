# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Pyrrhon is a voice-first, terminal-based engineering agent. You talk to it; it
talks back, podcast-style and interruptible. It does two things (the "two acts"):

1. **Understand** a codebase you didn't write — grounded Q&A over a repo.
2. **Design** a system you're about to build — Socratic interrogation of your
   choices, then it writes the spec (`PRD.md`, `HLD.md`, ...).

`README.md` is the pitch; `VISION.md` is the source of truth for scope, the two
acts in detail, and the verifiable v1 success criteria. Read `VISION.md` before
making product/scope decisions.

## Current state

Pre-implementation restart. The previous entry points (`jarvis.py`, `main.py`)
have been removed from the working tree — do not treat their git history as the
intended design. There is currently no Python source, no tests, and no lint
config. You are building Act 1 (Understand) first.

## Toolchain and commands

Uses [uv](https://docs.astral.sh/uv/) for dependency and environment management.
Python >= 3.12 (`.python-version`, `pyproject.toml`).

- Install/sync deps: `uv sync`
- Add a dependency: `uv add <package>`
- Run a script/module: `uv run <path-or-module>` (no entry point exists yet;
  create one, e.g. `uv run python -m pyrrhon` or `uv run pyrrhon.py`)

- Run the app: `uv run pyrrhon [repo-path]` (needs `GROQ_API_KEY` set, or
  configure another provider in `.pyrrhon.toml`)
- Run tests: `uv run pytest` (single test: `uv run pytest path::test_name`)

There is no lint config yet. Current state: M0 (grounded text REPL) — see
`docs/superpowers/plans/2026-07-03-pyrrhon-m0-grounded-text-repl.md`.


## Design constraints (do not violate without discussion)

- **Voice-first, screen-supported — not voice-only.** Voice drives; the terminal
  shows what's being discussed. Don't remove the visual channel to be "pure voice."
- **Grounding is a hard requirement.** Every claim about the code must cite a real
  `file:line` or commit, or the agent must say it doesn't know. Confident
  hallucination spoken aloud is the worst failure mode here; never fabricate a
  path to sound complete.
- **Scope discipline.** Only the two acts are in scope. Enterprise onboarding,
  student/interview positioning, a plugin marketplace, and company-standards
  enforcement are explicitly parked in `VISION.md` — do not build them until the
  Understand loop is undeniable.
