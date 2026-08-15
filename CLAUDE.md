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
history as the intended design.

## Toolchain and commands

Uses [uv](https://docs.astral.sh/uv/) for dependency and environment management.
Python >= 3.12 (`.python-version`, `pyproject.toml`).

- Install/sync deps: `uv sync` (add `--extra voice` for the audio stack —
  `pipecat-ai[local]`/PyAudio, which only the voice channel imports)
- Add a dependency: `uv add <package>`
- Run the app: `uv run pyrrhon [repo-path]` — launches the Textual TUI;
  add `--text` for the plain-text REPL (needs `GROQ_API_KEY` set, or
  configure another provider in `.pyrrhon.toml`); add `--voice` for the
  voice pipeline (needs `GROQ_API_KEY` + `OPENAI_API_KEY` and
  `uv sync --extra voice`). `/voice on|off` toggles voice inside the TUI;
  `/debug-history` dumps the session history.
- Run tests: `uv run pytest` (single test: `uv run pytest path::test_name`)
- Lint and types (both gated in CI, keep them clean):
  `uv run ruff check .` and `uv run mypy pyrrhon/core`

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

Current state: M8 (context engineering + deep
subagent harness) — history compaction and tool-result eviction live inside
`Agent.run_turn` (`pyrrhon/core/context.py`), per-turn `ToolGuard` budgets
with a forced final answer (`pyrrhon/core/agent/guards.py`), an import graph
and ranked repo map over the AST index (`list_dependencies`, `repo_map`),
`think_deeper` running a bounded read-only subagent loop with narration, and
an STT/TTS provider registry (`pyrrhon/voice/providers.py`) plus keyless
local LLM providers (`ollama`, `lmstudio`). See
`docs/superpowers/plans/2026-07-06-pyrrhon-m8-context-engineering-subagent-harness.md`.

M9 (provider expansion + onboarding) — DeepSeek/Hugging Face LLM providers,
Gemini STT/TTS via plain API key (`pyrrhon/voice/gemini.py`), in-process
Piper, per-provider voice defaults, a first-run setup wizard
(`pyrrhon/config/wizard.py`, `pyrrhon --setup`) with owner-only key storage
(`pyrrhon/config/credentials.py`), `/settings`, the branding banner
(`pyrrhon/branding.py`), and safety-invariant tests (`tests/test_safety.py`).
Gemini Live speech-to-speech is parked: it would bypass the grounding gate.
See `docs/superpowers/plans/2026-07-06-pyrrhon-m9-providers-onboarding-polish.md`.

M11 (trust boundary + ops guard rails) — repo-supplied config is partitioned by
privilege (`pyrrhon/config/settings.py:partition_repo_config`), gated behind
content-bound grants (`pyrrhon/config/trust.py`) recorded in
`<repo>/.pyrrhon/trusted`, and surfaced as one startup consent prompt
(`load_channel_plugins` in `pyrrhon/repl.py`). Repo soul markdown goes through
the same gate; `/init` and `remember` self-grant what they write. `--trust-repo`
is the automation escape hatch; no TTY means refuse. Global config tables now
deep-merge with repo ones instead of being replaced wholesale. Lint and types
are enforced: `uv run ruff check .` and `uv run mypy pyrrhon/core` are clean and
gated in CI (`.github/workflows/ci.yml`). See
`docs/superpowers/plans/2026-08-13-pyrrhon-m11-trust-boundary.md`.

M13 (truthful grounding) — the gate no longer treats "this line exists" as
"we looked at this line". `Agent` builds a per-turn `EvidenceLedger`
(`pyrrhon/core/grounding/evidence.py`) recording the line *ranges* each tool
result actually displayed, and `GroundingGate.check(text, evidence)` classifies
three ways: observed → cited, real-but-unopened → downgraded to the bare path
with `LINE_UNSEEN_HEDGE`, unverified → stripped as before. Behind
`[grounding] require_provenance`, **off by default** and privileged (a repo may
not relax it without a grant); flipping it on is blocked on the eval runs
recorded in the plan's Implementation record. Evals gained a real-repo case set
(`evals/grounding-self.yaml`, needs `--repo .`), fabrication classes that cite a
real file with no relevant content, `expected`-requires-all semantics plus
`expected_any`, and an Act 2 runner (`pyrrhon/evals/design.py`) measuring
VISION criterion 4. `evals/README.md` documents all of it. See
`docs/superpowers/plans/2026-08-13-pyrrhon-m13-truthful-grounding.md`.


M14 (code intelligence) — the symbol index is table-driven
(`pyrrhon/core/tools/languages.py`): python, typescript, tsx, javascript, and
go, with grammars compiled lazily and every query verified by *capture* against
the installed grammar, not merely by compiling. The cache carries a `lang`
column behind `_SCHEMA_VERSION` (a bump drops and rebuilds — the cache is
derived). `symbol_context` (`pyrrhon/core/tools/symbol_context.py`) answers
definition + source + call sites + import edges in one round and **replaced
`find_references` on the belt**; `list_dependencies` stays, because it is
path-addressed and `symbol_context` is name-addressed. Its description is
deliberately gated on knowing an exact identifier — steering harder sends
concepts to a tool that can only answer "No definition found". The repo map
takes a conversation-mention boost (`build_repo_map(mentioned=...)`, wired
through `Agent._mentions_now`), and `build_orientation`
(`pyrrhon/core/tools/orientation.py`) emits the first real `ScreenArtifact` at
session start. Answers point at `path:line` rather than pasting source, and
citations are OSC 8 hyperlinks (`pyrrhon/core/citation_link.py`).
`evals/understanding.yaml` measures the round-trip claim via `max_rounds`, split
into identifier cases (cap 2) and concept cases (a discovery round is
legitimate). See
`docs/superpowers/plans/2026-08-13-pyrrhon-m14-code-intelligence.md`.

## Design constraints (do not violate without discussion)

- **Voice-first, screen-supported — not voice-only.** Voice drives; the terminal
  shows what's being discussed. Don't remove the visual channel to be "pure voice."
- **Grounding is a hard requirement.** Every claim about the code must cite a real
  `file:line` or commit, or the agent must say it doesn't know. Confident
  hallucination spoken aloud is the worst failure mode here; never fabricate a
  path to sound complete.
- **A cloned repo is untrusted input.** Anything the repo supplies that runs a
  program, redirects where prompts or keys are sent, or writes into the system
  prompt requires a content-bound grant in `<repo>/.pyrrhon/trusted`. Adding a
  new repo-readable config key means deciding which side of that line it sits
  on — see `pyrrhon/config/settings.py:PRIVILEGED_PATHS`.
- **Scope discipline.** Only the two acts are in scope. Enterprise onboarding,
  student/interview positioning, a plugin marketplace, and company-standards
  enforcement are explicitly parked in `VISION.md` — do not build them until the
  Understand loop is undeniable.
