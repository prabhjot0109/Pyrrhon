# Pyrrhon v1 — Design Spec

> Status: approved 2026-07-03 (brainstorming session).
> Companion docs: `README.md` (pitch), `VISION.md` (scope + success criteria).
> Next step: implementation plan (`docs/superpowers/plans/`).

## Summary

Pyrrhon is a voice-first, terminal-based engineering agent with two acts:
**Understand** a codebase you didn't write, and **Design** a system you're
about to build. This spec defines the v1 architecture: a **headless agent
core** (the product) with two thin channels over it — a Pipecat-driven voice
pipeline and a Textual TUI. Voice drives; the screen shows the code being
discussed.

## Decisions made (and why)

| Decision | Choice | Why |
|---|---|---|
| Interface | **CLI/TUI now, GUI-ready core** | Runs inside VS Code's terminal next to the code it cites; single stack. The core/channel split makes a Tauri/Electron front-end a later milestone, not a rewrite. |
| Voice feasibility | **TUI is sufficient** | Audio I/O goes through the OS sound API, not the terminal. Full-duplex + barge-in works from any Python process (proven by headless frameworks like Pipecat/LiveKit Agents). Headphones-first sidesteps echo cancellation — the one real advantage a browser shell has. |
| Plugins | **Seams now, loader later** | v1 ships three real extension points (provider registry, MCP client, slash commands) that are needed anyway. The OpenClaw-style plugin loader is a defined interface built in M7, after the Understand loop works. VISION.md amended accordingly. |
| Voice plumbing | **Pipecat framework** | VAD, barge-in, streaming STT/TTS, and provider services (Groq/OpenAI/Gemini) are solved there. All custom engineering effort goes into the agent core instead. |
| Providers on hand | Groq, OpenAI, Gemini, OpenRouter, Cerebras, HuggingFace | All but HF speak the OpenAI-compatible chat API → one base LLM adapter covers them. Groq is the default low-latency voice path. |

## Design principles

1. **The agent core is the product; voice and TUI are channels.** Reasoning
   quality — mapping *why this is done here and how it affects that* — is the
   crown jewel. Channel code stays thin.
2. **Grounding is mechanical, not behavioral.** Every `file:line` citation is
   verified by code before it can be spoken. Unverifiable claims are corrected
   or downgraded to "I'm not certain." Prompt instructions alone are hope, not
   a guarantee.
3. **Conversational, first-principles teaching.** Pyrrhon talks like a senior
   engineer pairing with you, not like documentation. It explains from first
   principles — what problem exists, why this construct solves it, what the
   alternatives were — and asks check-questions to confirm understanding. In
   design mode it refuses to accept a choice until justified (skeptic spirit).
4. **Text before voice, in build order.** Voice is the identity but it is a
   channel; the brain is proven in a text REPL first (M0) because debugging an
   agent through a microphone is miserable.
5. **Scope discipline.** Only the two acts. Extension seams are built because
   v1 needs them; everything else stays parked per VISION.md.

## Architecture

One Python package (`pyrrhon/`), managed with `uv`, Python ≥ 3.12. Hard rule:
**`core/` imports nothing from `voice/` or `tui/`.**

```
pyrrhon/
├── core/            # THE PRODUCT — headless engine, no UI/audio imports
│   ├── agent/       #   reasoning loop, teaching policy, understand/design modes
│   ├── tools/       #   repo (read/grep/glob), git (log/blame/show),
│   │                #   ast (tree-sitter symbol index), web (search/fetch)
│   ├── grounding/   #   citation extraction + verification gate
│   ├── mcp/         #   MCP client manager (attach any MCP server)
│   ├── providers/   #   LLM/STT/TTS registries + adapters
│   └── session.py   #   conversation state → emits UI-agnostic event stream
├── voice/           # Pipecat wiring: local audio transport, VAD, STT/TTS, barge-in
├── tui/             # Textual app: transcript, code viewer, status bar, input
├── commands/        # slash command registry (/model, /voice, /mode, /repo …)
└── config/          # TOML settings: keys, provider profiles, MCP servers
```

### The event-stream contract

The core exposes one async API: send an utterance, receive a stream of typed
events. This single contract provides GUI-readiness, testability, and the
dual speech/screen channel:

- `SpeechChunk(text)` — speakable prose, streamed to TTS (and transcript).
- `ScreenArtifact(kind, content)` — code snippet, path list, or diagram;
  rendered on screen only, never spoken.
- `Citation(file, line, snippet)` — verified source location; the TUI code
  viewer jumps to it.
- `ToolCallStarted(name, args)` / `ToolCallFinished(name)` — progress display.
- `AskUser(question)` — the Socratic channel; Pyrrhon asking *you* something.

Voice and TUI are subscribers. A future GUI subscribes to the same stream
(over a local socket) without touching the core.

## Agent core

### Reasoning loop

A tool-calling loop over a provider-agnostic LLM adapter. One
**OpenAI-compatible base adapter** covers Groq, OpenRouter, Cerebras, Gemini
(compat endpoint), and OpenAI; new providers are a subclass + config entry.

Two model slots, both user-configurable:

- **Fast conversational model** (default: Groq-hosted) — keeps voice latency
  low for turn-by-turn dialogue.
- **Deep reasoning model** (default: user's pick via OpenRouter) — the agent
  escalates to it for multi-file analysis ("map how this affects that"),
  design interrogation, and spec writing.

### Tool harness (v1)

`read_file`, `grep`, `glob`, `git_log`, `git_blame`, `git_show`,
`find_symbol`, `find_references`, `web_search`, `web_fetch`, plus any tools
contributed by attached MCP servers.

### AST / code map

A tree-sitter symbol index (definitions + references) built lazily per repo
and cached in SQLite under `.pyrrhon/`. Powers "what calls this?", "where is
this defined?", "what breaks if I change this?" — a real reference graph
without a graph database. Invalidated by file mtime/git HEAD change.

### Grounding gate

Runs between the LLM and the output channels, before anything is spoken:

1. Extract every `file:line` / commit reference from the draft answer.
2. Verify mechanically: file exists, line exists, (when the claim quotes
   code) content matches; commit hashes resolve via git.
3. On failure: the draft goes back to the agent once to fix; if still
   unverifiable, the claim is downgraded to an explicit "I'm not certain"
   before reaching TTS.

Confident hallucination cannot reach the speakers. Failures are logged to
feed the grounding eval.

### Teaching policy

Encoded in the system prompt and mode logic:

- **Understand mode**: grounded walkthroughs; explains from first principles
  (the problem, the construct that solves it, the alternatives, the trade-off
  chosen); connects cause and effect across files; asks short check-questions;
  offers "want to see the code?" rather than dumping it.
- **Design mode**: interrogates before generating; challenges at least the
  weakest assumption; writes `PRD.md` / `HLD.md` / `LLD.md` / `api.md` /
  `database.md` / `risks.md` only once reasoning is explicit.

## Voice layer (Pipecat)

Pipeline: local mic/speaker transport → Silero VAD → **Groq Whisper STT**
(default) → bridge processor into the agent core → streaming TTS (default:
OpenAI TTS; Groq PlayAI as alternate) → speaker.

- **Barge-in**: Pipecat's interruption handling — VAD detects user speech
  mid-answer, output buffer flushes instantly, the interruption becomes the
  next input.
- **Headphones-first**: assumed use case (per README); sidesteps acoustic
  echo cancellation. Speaker-mode AEC is a later concern (WebRTC APM bindings
  or push-to-talk fallback).
- **Text input always works in parallel** — quiet environments, and
  developing without audio.
- The agent produces speakable prose (`SpeechChunk`) while code and paths go
  to `ScreenArtifact` — the voice never reads a file path aloud
  character-by-character.

## TUI (Textual)

- **Transcript pane** — conversation with inline citation chips.
- **Code viewer pane** — auto-jumps to any cited `file:line`, syntax
  highlighted.
- **Status bar** — mode (understand/design), active models, live latency.
- **Input box** — text entry + slash commands.
- `/code` also opens the current citation in VS Code (`code --goto
  file:line`) since Pyrrhon runs in its integrated terminal.

Slash commands (v1): `/repo <path>`, `/mode understand|design`, `/model
<slot> <provider/model>`, `/voice on|off`, `/mcp list|add`, `/help`.

## Extension seams (v1) and the plugin loader (M7)

Ship in v1:

1. **Provider registry** — a new STT/TTS/LLM backend is one adapter class +
   one config entry.
2. **MCP client** — MCP servers (stdio or HTTP) declared in config; their
   tools are exposed to the agent automatically.
3. **Slash command registry** — decorator-registered commands.

Defined now, built in M7: a plugin loader where a plugin is a folder with a
manifest contributing `{tools, commands, prompts, providers}` — the three v1
registries are exactly the surfaces plugins will hook into, so the loader is
additive, not a refactor.

## Configuration

TOML at `~/.pyrrhon/config.toml` (global) merged with `<repo>/.pyrrhon.toml`
(per-project). Holds provider keys (or env-var references), model slot
assignments, TTS voice, MCP server list, fallback chains. `pyrrhon` is run
from (or pointed at) the target repo: `pyrrhon [path]`.

## Error handling

- **Provider failure** → configured fallback chain (e.g., Groq STT →
  OpenAI STT) with a one-sentence spoken notice.
- **Audio device failure / no mic** → degrade to text mode with a clear
  message; the session continues.
- **Grounding failure** → honest "I couldn't verify that" (never a fabricated
  path).
- **MCP server crash** → its tools are removed from the roster; the agent is
  told they're unavailable.

## Testing

- **Core**: pytest, fully headless. Agent loop tested against recorded LLM
  responses; tool harness tested against a fixture repo checked into
  `tests/fixtures/`; grounding verifier gets pure unit tests (it's
  deterministic).
- **Grounding eval**: a YAML set of question → expected-citation pairs run
  against a known repo, scored automatically. This is the metric for
  VISION.md's open question "how do we measure cited-the-right-file:line."
- **TUI**: Textual's pilot/snapshot testing for panes and commands.
- **Voice**: loopback smoke test (synthesized audio in → transcript out) plus
  manual barge-in checks; voice logic stays thin enough that this suffices.

Run: `uv run pytest` (single test: `uv run pytest path::test_name`).

## Milestones

Each is independently demo-able; fine-grained tasks live in the
implementation plan.

- **M0 — Grounded text REPL**: package scaffold, config, provider registry +
  OpenAI-compat adapter, minimal agent loop with repo tools. Ask a repo
  questions in text, get cited answers.
- **M1 — Trust**: grounding gate, git tools, grounding eval v0.
- **M2 — TUI**: Textual transcript + code viewer + slash commands.
- **M3 — Voice**: Pipecat pipeline, barge-in, speech/screen dual channel.
  *(v1 success criteria 1–3 become testable here.)*
- **M4 — Deep reasoning**: tree-sitter symbol index, web search/fetch, model
  escalation.
- **M5 — Extensibility**: MCP client, provider fallback chains, latency
  polish.
- **M6 — Act 2**: design mode, Socratic interrogation, spec artifacts.
  *(Success criterion 4.)*
- **M7 — Plugin loader v1** + optional GUI (Tauri) spike over the event
  stream.

## Out of scope for v1

Unchanged from VISION.md: enterprise onboarding, student/interview
positioning, plugin *marketplace*, company-standards enforcement,
architecture knowledge graph, multi-agent orchestration, whiteboard
generation. The plugin *loader* moved from "parked" to M7 by explicit
decision (2026-07-03).
