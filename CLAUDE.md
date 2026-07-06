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

M0–M3 are implemented — headless `pyrrhon/core/` module (events, LLM adapter,
repo tools, citation extraction, agent loop, grounding gate, `Session` with
cancellable turns), a rich-based REPL channel (`pyrrhon/repl.py`), the Textual
TUI (`pyrrhon/tui/`), and the Pipecat voice channel (`pyrrhon/voice/`:
mic → Silero VAD → Groq Whisper STT → bridge → OpenAI TTS, with barge-in and
`TruncateSpeech` history rewriting). The previous entry points (`jarvis.py`,
`main.py`) have been removed from the working tree — do not treat their git
history as the intended design. There is no lint config yet.

## Toolchain and commands

Uses [uv](https://docs.astral.sh/uv/) for dependency and environment management.
Python >= 3.12 (`.python-version`, `pyproject.toml`).

- Install/sync deps: `uv sync`
- Add a dependency: `uv add <package>`
- Run the app: `uv run pyrrhon [repo-path]` — launches the Textual TUI;
  add `--text` for the plain-text REPL (needs `GROQ_API_KEY` set, or
  configure another provider in `.pyrrhon.toml`); add `--voice` for the
  voice pipeline (needs `GROQ_API_KEY` + `OPENAI_API_KEY` and the pipecat
  `local` extra). `/voice on|off` toggles voice inside the TUI;
  `/debug-history` dumps the session history.
- Run tests: `uv run pytest` (single test: `uv run pytest path::test_name`)

- Run the grounding eval (real LLM, needs an API key):
  `uv run python -m pyrrhon.evals.grounding evals/grounding.yaml`

## Plugins (M7)

A plugin is a folder with a `plugin.toml` under `~/.pyrrhon/plugins/` (global)
or `<repo>/.pyrrhon/plugins/` (repo-level), contributing prompts (markdown
appended to the system prompt), tools/commands (Python entry points), MCP
servers, and providers. Prompts and config load from anywhere; repo-level
plugin *code* runs only after one consent prompt per repo, recorded in
`<repo>/.pyrrhon/trusted`. `/plugins` lists what loaded. Worked example:
`tests/fixtures/plugins/hello-reviewer/`; plan:
`docs/superpowers/plans/2026-07-03-pyrrhon-m7-plugin-loader.md`.

There is no lint config yet. Current state: M8 (context engineering + deep
subagent harness) — history compaction and tool-result eviction live inside
`Agent.run_turn` (`pyrrhon/core/context.py`), per-turn `ToolGuard` budgets
with a forced final answer (`pyrrhon/core/agent/guards.py`), an import graph
and ranked repo map over the AST index (`list_dependencies`, `repo_map`),
`think_deeper` running a bounded read-only subagent loop with narration, and
an STT/TTS provider registry (`pyrrhon/voice/providers.py`) plus keyless
local LLM providers (`ollama`, `lmstudio`). See
`docs/superpowers/plans/2026-07-06-pyrrhon-m8-context-engineering-subagent-harness.md`.


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
