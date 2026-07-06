# Pyrrhon

**A voice-first senior-engineer agent that lives in your terminal. You talk to
it; it talks back — grounded in real `file:line` citations, interruptible
mid-sentence.**

Named after [Pyrrho of Elis](https://en.wikipedia.org/wiki/Pyrrho), founder of
philosophical skepticism: suspend judgment and question every assumption before
accepting it.

It does two things (the "two acts"):

1. **Understand** a codebase you didn't write. Point it at a repo and ask how a
   feature works, where to add something, what changed recently. Every claim
   cites a real `file:line` or commit — or it says it doesn't know rather than
   inventing one. The terminal shows the code it's talking about.

2. **Design** a system you're about to build. Pyrrhon interrogates the idea
   like a senior architect — *"your data looks relational, what do you expect
   Mongo to buy you over Postgres?"* — and only once the reasoning is clear
   does it write the spec (`PRD.md`, `HLD.md`, `LLD.md`, `api.md`,
   `database.md`, `risks.md`). The conversation is the product; the Markdown
   is the artifact.

Why it exists: NotebookLM proved people learn by *listening and interrupting*,
but it can't take a repo. Coding agents (Cursor, Copilot, Claude Code) edit
code; they aren't built to *teach* it to you out loud. Pyrrhon fills that gap.
See [VISION.md](VISION.md) for scope and the verifiable v1 success criteria.

## Quickstart

Requires Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/prabhjot0109/Pyrrhon && cd Pyrrhon
uv sync

# Text/TUI needs one LLM key (Groq is the default provider):
export GROQ_API_KEY=gsk_...          # PowerShell: $env:GROQ_API_KEY = "gsk_..."

uv run pyrrhon /path/to/some/repo    # Textual TUI (default channel)
uv run pyrrhon --text .              # plain-text REPL
uv run pyrrhon --voice .             # TUI with the voice pipeline on
```

Voice additionally needs `OPENAI_API_KEY` (TTS) — STT runs on the same Groq
key. Inside the TUI, `/voice on|off` toggles the pipeline; just start talking,
and interrupt whenever you like — barge-in rewrites history to exactly what
you actually heard.

Useful commands once you're in: `/help`, `/mode design`, `/model fast
groq/llama-3.3-70b-versatile`, `/mcp`, `/plugins`, `/code` (open the last
citation in your editor), `/debug-history`, `/quit`.

### Configuration

Settings merge from `~/.pyrrhon/config.toml` (global) then `<repo>/.pyrrhon.toml`
(repo wins). Everything is optional:

```toml
fast = { provider = "groq", model = "llama-3.3-70b-versatile" }  # every turn
deep = { provider = "openai", model = "o4-mini" }                # think_deeper escalation

[fallbacks]
fast = ["cerebras", "openrouter/meta-llama/llama-3.3-70b-instruct"]

[providers.myllm]                       # any OpenAI-compatible endpoint
base_url = "http://localhost:8000/v1"
api_key_env = "MYLLM_KEY"

[mcp_servers.docs]                      # MCP tools join the agent's toolset
command = "docs-mcp"                    # stdio — or: url = "http://..." (HTTP)
```

Built-in providers: `groq`, `openai`, `openrouter`, `cerebras`, `gemini` —
each just needs its `*_API_KEY` env var (Cerebras, for example: set
`CEREBRAS_API_KEY` and point a slot at `provider = "cerebras"`). Two keyless
local providers ship too: `ollama` (`http://localhost:11434/v1`) and
`lmstudio` (`http://localhost:1234/v1`). Drop markdown "soul files" in
`~/.pyrrhon/` or `<repo>/.pyrrhon/` (scaffold with `/init`) to give Pyrrhon
standing context about you or the repo.

### Voice providers

```toml
[voice]
stt_provider = "groq"          # groq | openai | deepgram | whisper-local (no key, on-device)
tts_provider = "cartesia"      # openai (default) | cartesia | elevenlabs | deepgram | piper (local)
tts_voice = "<voice-id>"       # OpenAI voice name, Cartesia/ElevenLabs voice id, or Deepgram Aura voice
tts_model = "sonic-2"          # optional provider-specific model
# tts_url = "http://localhost:5000"   # piper only
```

OpenAI TTS is the zero-setup default; for real-time conversation Cartesia or
ElevenLabs are noticeably snappier (~100-300ms to first audio vs 400ms+).
Local, keyless operation: `stt_provider = "whisper-local"`, `tts_provider =
"piper"`, and a local LLM via `[fast] provider = "ollama"` (or `lmstudio`).

### Context budget

```toml
[context]
budget_tokens = 32000       # estimated tokens before old turns are summarized
keep_last_messages = 8      # recent messages always kept verbatim
```

## Security model

Pyrrhon is built so a voice agent *cannot* damage your repo, even when the
model is wrong:

- **Read-only by construction.** The agent's only write tool is `write_spec`,
  which accepts exactly six filenames (`PRD.md`, `HLD.md`, `LLD.md`, `api.md`,
  `database.md`, `risks.md`) and only ever writes under `docs/design/`.
- **No shell.** Git access is three read-only subcommands (`log`, `blame`,
  `show`) executed as argv lists — never a shell, so there is nothing to
  inject. Paths are resolved and rejected if they escape the repo.
- **Grounded speech.** Every `file:line` claim is verified against the real
  repo before it is spoken; unverifiable claims are stripped (voice) or
  corrected once (screen). Pyrrhon says "I'm not certain" instead of guessing.
- **Keys stay out of the repo.** API keys live in `~/.pyrrhon/credentials.toml`
  (owner-only permissions), never in project config; environment variables
  always take precedence.
- **Consent for third-party code.** Repo-level plugin code runs only after an
  explicit one-time consent per repo; MCP servers run only if you configured
  them.

These invariants are pinned by `tests/test_safety.py`.

## Architecture

Headless core, thin channels. The core never imports a channel; channels
subscribe to a typed event stream and render it however they like.

```mermaid
flowchart TB
    subgraph channels["Channels (thin, swappable)"]
        REPL["Text REPL<br/>(rich)"]
        TUI["TUI<br/>(Textual: transcript · code viewer · status bar)"]
        VOICE["Voice<br/>(Pipecat: mic → Silero VAD → Groq Whisper STT<br/>→ bridge → OpenAI TTS, barge-in)"]
    end

    EVENTS(["Event stream<br/>SpeechChunk · Citation · ScreenArtifact ·<br/>ToolCall* · AskUser · TruncateSpeech"])

    subgraph core["pyrrhon/core — headless, imports no channel"]
        SESSION["Session<br/>(history, modes, cancellable turns, latency)"]
        AGENT["Agent loop<br/>(LLM ⇄ tools, citation extraction)"]
        GATE["Grounding gate<br/>(verifies every file:line; retry or strip)"]
        LLM["LLM adapter<br/>(OpenAI-compatible · fallback chains ·<br/>fast/deep slots)"]
        TOOLS["Tools<br/>read/grep/glob · git log/blame/show ·<br/>symbol index (tree-sitter) · web ·<br/>remember · write_spec · think_deeper"]
        MCP["MCP client<br/>(stdio + HTTP servers bridged as tools)"]
    end

    subgraph compose["Composition at startup (repl.build_agent)"]
        PLUGINS["Plugin loader (M7)<br/>~/.pyrrhon/plugins + repo/.pyrrhon/plugins<br/>prompts · tools · commands · providers · MCP<br/>(repo code trust-gated, once per repo)"]
        CONFIG["Settings<br/>global config.toml ← repo .pyrrhon.toml"]
        SOUL["Soul files<br/>(user markdown → system prompt)"]
    end

    REPL <--> EVENTS
    TUI <--> EVENTS
    VOICE <--> EVENTS
    EVENTS <--> SESSION
    SESSION --> AGENT
    AGENT --> GATE
    AGENT --> LLM
    AGENT --> TOOLS
    MCP --> TOOLS
    compose -->|"tools · prompt · providers"| AGENT
```

The rules that keep it honest:

- **Grounding is a hard requirement.** The gate verifies every cited
  `file:line` against the repo before it's spoken; unverifiable citations
  trigger one self-correction round-trip (text channels) or are stripped
  immediately (voice — a retry would blow the latency budget).
- **Real-time discipline.** No synchronous filesystem/CPU work inline in
  `async def` anywhere in `core/` — everything blocking goes through
  `asyncio.to_thread`, so audio never glitches while a tool reads disk.
- **One composition point.** `build_agent` assembles LLM + tools + prompt +
  MCP + plugins; nothing else constructs an `Agent`.

## Plugins

A plugin is a folder with a `plugin.toml`, dropped into `~/.pyrrhon/plugins/`
(global) or `<repo>/.pyrrhon/plugins/` (repo-level):

```toml
name = "hello-reviewer"
version = "0.1.0"

[contributes]
prompts = ["prompts/*.md"]      # appended to the system prompt
tools = "tools.py:get_tools"    # Python entry point returning list[Tool]
```

Plugins can contribute prompts, tools, slash commands, LLM providers, and MCP
servers — all additive; they never override your config or built-ins. Prompts
and config load from anywhere; **repo-level plugin code runs only after a
one-time consent prompt** (recorded in `<repo>/.pyrrhon/trusted`) — cloning a
repo never means executing its code. A broken plugin is skipped with one
warning, never a crashed session. `/plugins` shows what loaded. Worked
example: [`tests/fixtures/plugins/hello-reviewer/`](tests/fixtures/plugins/hello-reviewer/).

## Development

```bash
uv run pytest                    # full suite, no API keys needed
uv run pytest path/to/test.py::test_name
uv run python -m pyrrhon.evals.grounding evals/grounding.yaml  # real-LLM grounding eval
```

Layout: `pyrrhon/core/` (headless agent), `pyrrhon/repl.py` + `pyrrhon/tui/` +
`pyrrhon/voice/` (channels), `pyrrhon/commands/` (slash commands),
`pyrrhon/plugins/` (loader), `pyrrhon/config/` (settings). Design docs live in
`docs/superpowers/` — the spec plus one implementation plan per milestone
(M0–M7, all landed).

## Not in scope (yet)

Enterprise onboarding-as-a-product, student/interview-prep positioning, plugin
marketplace, company-standards enforcement. Parked in
[VISION.md](VISION.md) until the core Understand loop is undeniable.
