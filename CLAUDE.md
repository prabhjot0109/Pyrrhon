# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this is

Pyrrhon is a voice-first, terminal-based engineering agent. You talk to it and
it talks back, podcast-style and interruptible. It does two things, called the
two acts:

1. **Understand** a codebase you didn't write: grounded question-answering over
   a repo.
2. **Design** a system you're about to build: Socratic interrogation of your
   choices, then it writes the spec (`PRD.md`, `HLD.md`, and the rest).

`README.md` is the pitch. `VISION.md` is the source of truth for scope, the two
acts in detail, and the verifiable v1 success criteria. Read `VISION.md` before
making any product or scope decision.

## Map

Start here rather than by grepping. The layering rule below is the one thing
that must stay true.

| Path | Responsibility |
|---|---|
| `pyrrhon/cli.py` | Argument parsing, picks a channel. |
| `pyrrhon/bootstrap.py` | Composition root. `build_agent` wires the tool belt, both LLM slots, the grounding gate, and the system prompt. `start_channel` runs the shared startup sequence. `load_channel_plugins` is the repo trust gate. |
| `pyrrhon/channels.py` | `EVENT_HOOKS` plus the `EventRenderer` base. One dispatch table for every channel. |
| `pyrrhon/repl.py` | Text channel (rich). |
| `pyrrhon/tui/` | Textual channel: transcript, code viewer, status bar. |
| `pyrrhon/voice/` | Pipecat pipeline: mic, Silero VAD, STT, bridge, TTS, barge-in. |
| `pyrrhon/core/agent/loop.py` | The turn state machine. LLM and tools, streaming, error recovery. |
| `pyrrhon/core/session.py` | History, modes, cancellable turns, latency. |
| `pyrrhon/core/grounding/` | `gate.py` verifies citations; `evidence.py` records what tool output actually showed. |
| `pyrrhon/core/tools/` | The belt. One module per family. |
| `pyrrhon/core/events.py` | The event contract between core and channels. |
| `pyrrhon/config/` | Settings, credentials, trust grants, setup wizard. |
| `pyrrhon/plugins/` | Plugin discovery and loading. |

**The layering rule: `pyrrhon/core/` and `pyrrhon/config/` import nothing from
`tui`, `voice`, `repl`, `commands`, or `cli`.** Channels depend on the core, and
`bootstrap.py` sits above every channel. Verify with:

```bash
grep -rn "from pyrrhon\.\(tui\|voice\|repl\|commands\|cli\)" pyrrhon/core/ pyrrhon/config/
```

That should print nothing. If it prints something, that is the bug, not a style
question.

### How to add a tool

1. Subclass `Tool` in `pyrrhon/core/tools/`. Set `name`, `description`, and
   `parameters` (the JSON schema the model sees), then implement
   `async def run(...)`. Anything blocking goes through `asyncio.to_thread`.
2. Add it to `builtin_tools` in `pyrrhon/bootstrap.py:build_agent`.
3. The deep subagent's belt is *derived* from that list, so a read-only tool is
   inherited automatically. If `think_deeper` must not have it, add the name to
   `DEEP_EXCLUDED` in the same file.
4. Add its name to `EXPECTED_BELT` in `tests/test_safety.py`. That belt is a
   reviewed set, so this step is a deliberate checkpoint rather than bookkeeping.
5. Add a spoken filler to `TOOL_FILLERS` in `pyrrhon/voice/bridge.py`. A test
   requires one per belt tool, and no filler may contain a `path:line`.

The belt's total schema size is capped by a test: it rides on every tool-bearing
turn, so it is a latency property, not a style one.

### How to add an event

Add the dataclass to `pyrrhon/core/events.py` and to the `Event` union, add a
hook name to `EVENT_HOOKS` and a no-op default to `EventRenderer` in
`pyrrhon/channels.py`, then override it in whichever renderers should show it.
`tests/test_event_dispatch.py` fails if the union and the table disagree, which
is what stops an event from silently vanishing on one channel.

### How to add a slash command

Write a handler decorated with `@command(...)` in `pyrrhon/commands/`, and make
sure the module is listed in the `from pyrrhon.commands import (...)` block that
each channel imports for its registration side effect. Handlers return a string,
prefix errors with `ERROR:`, and never raise or print.

### How to add a provider

Add an entry to `BUILTIN_PROVIDERS` in `pyrrhon/config/settings.py` with its
`base_url` and `api_key_env`, then mirror it in `LLM_CHOICES` in
`pyrrhon/config/catalog.py`, which is what the wizard and `/settings` render.
`tests/test_catalog.py` pins the two together, so skipping the second step
fails rather than producing a provider nobody can select.

Voice providers live in the STT and TTS registries in
`pyrrhon/voice/providers.py` and mirror into the same catalog. Any
OpenAI-compatible endpoint needs no code at all: users declare it under
`[providers.<name>]`.

Adding a repo-readable config key means deciding which side of the trust
boundary it sits on. See `PRIVILEGED_PATHS` in `pyrrhon/config/settings.py`.

## Toolchain and commands

Uses [uv](https://docs.astral.sh/uv/) for dependencies and environments. Python
3.12 or newer, pinned in `.python-version` and `pyproject.toml`.

```bash
uv sync                        # install; add --extra voice for the audio stack
uv add <package>               # add a dependency

uv run pyrrhon [repo-path]     # Textual TUI (default channel)
uv run pyrrhon --text .        # plain-text REPL
uv run pyrrhon --voice .       # voice pipeline on

uv run pytest                  # full suite, ~40s, no API keys needed
uv run pytest path::test_name  # one test
uv run ruff check .            # gated in CI
uv run mypy pyrrhon/core       # gated in CI

uv run python -m pyrrhon.evals.grounding evals/grounding.yaml   # needs an API key
```

The text and TUI channels need one LLM key (`GROQ_API_KEY` by default, or
configure another provider). Voice additionally needs `OPENAI_API_KEY` and
`uv sync --extra voice`. Inside the TUI, `/voice on|off` toggles the pipeline
and `/debug-history` dumps the session history.

Keep ruff and mypy clean. Both gate CI, and the ruff rule set is deliberately
narrow (`F`, `I`, `B`, `ASYNC`) so that real findings are not buried under style
opinions.

## Design constraints

Do not violate these without a discussion.

**Voice-first, screen-supported, not voice-only.** Voice drives; the terminal
shows what is being discussed. Do not remove the visual channel in the name of
purity.

**Grounding is a hard requirement.** Every claim about the code cites a real
`file:line` or commit, or the agent says it does not know. Confident
hallucination spoken aloud is the worst failure mode this program has. Never
fabricate a path to sound complete.

**A cloned repo is untrusted input.** Anything the repo supplies that runs a
program, redirects where prompts or keys are sent, or writes into the system
prompt needs a content-bound grant in `<repo>/.pyrrhon/trusted`.

**Scope discipline.** Only the two acts are in scope. Enterprise onboarding,
student and interview positioning, a plugin marketplace, and company-standards
enforcement are parked in `VISION.md`. Do not build them until the Understand
loop is undeniable.

## Current state

Everything through M14 is implemented and tested. The parts worth knowing about
before you change them:

**Grounding (M13).** The gate does not treat "this line exists" as "we looked at
this line". `Agent` builds a per-turn `EvidenceLedger` recording the line ranges
each tool result actually displayed, and `GroundingGate.check(text, evidence)`
sorts claims three ways: observed becomes a citation, real-but-unopened is
downgraded to the bare path with a hedge, and unverified is stripped. The
stricter `[grounding] require_provenance` mode is off by default and privileged,
so a repo cannot relax it.

**Code intelligence (M14).** The symbol index is table-driven
(`pyrrhon/core/tools/languages.py`) across Python, TypeScript, TSX, JavaScript,
and Go, with grammars compiled lazily and every query verified by capture
against the installed grammar. The cache carries a `lang` column behind
`_SCHEMA_VERSION`; bumping it drops and rebuilds, which is safe because the
cache is derived. `symbol_context` answers definition, source, call sites, and
import edges in one round and replaced `find_references` on the belt.
`list_dependencies` stays because it is path-addressed while `symbol_context` is
name-addressed. Answers point at `path:line` instead of pasting source, and
citations are OSC 8 hyperlinks.

**Context and escalation (M8).** History compaction and tool-result eviction run
inside `Agent.run_turn`. Per-turn `ToolGuard` budgets end in a forced final
answer rather than a canned apology. `think_deeper` runs a bounded read-only
subagent loop with narration.

**Trust boundary (M11).** Repo-supplied config is partitioned by privilege in
`pyrrhon/config/settings.py:partition_repo_config`, gated behind content-bound
grants, and surfaced as one startup consent prompt. `/init` and `remember`
self-grant what they write. `--trust-repo` is the automation escape hatch, and
no TTY means refuse.

**Plugins (M7).** A plugin is a folder with a `plugin.toml` under
`~/.pyrrhon/plugins/` or `<repo>/.pyrrhon/plugins/`. Prompts and config load
from anywhere; repo-level plugin *code* runs only after one consent prompt per
repo. Worked example in `tests/fixtures/plugins/hello-reviewer/`.

Gemini Live speech-to-speech is parked on purpose: it would bypass the grounding
gate.

Earlier entry points (`jarvis.py`, `main.py`) were removed. Do not treat their
git history as the intended design.
