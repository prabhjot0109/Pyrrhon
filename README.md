# Pyrrhon

A voice-first engineering agent that lives in your terminal. You talk to it, it
talks back, and every claim it makes about your code cites a real `file:line`.
You can interrupt it mid-sentence.

Named after [Pyrrho of Elis](https://en.wikipedia.org/wiki/Pyrrho), who founded
philosophical skepticism: suspend judgment and question the assumption before
accepting it.

Pyrrhon does two things.

**Understand a codebase you didn't write.** Point it at a repo and ask how a
feature works, where to add something, or what changed last week. Every answer
cites a real location or says it doesn't know. It will not invent a path to
sound complete. The terminal shows the code being discussed while you talk.

**Design a system you're about to build.** Pyrrhon interrogates the idea the way
a senior architect would ("your data looks relational, so what do you expect
Mongo to buy you over Postgres?"), and writes the spec only once the reasoning
holds up. The conversation is the product. The markdown (`PRD.md`, `HLD.md`,
`LLD.md`, `api.md`, `database.md`, `risks.md`) is the artifact.

NotebookLM showed that people learn by listening and interrupting, but it can't
read a repo. Coding agents edit code rather than teach it to you out loud.
Pyrrhon sits in that gap. [VISION.md](VISION.md) has the scope and the v1
success criteria.

## Quickstart

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/prabhjot0109/Pyrrhon && cd Pyrrhon
uv sync                              # text + TUI
uv sync --extra voice                # plus the audio stack, for --voice

uv run pyrrhon --setup               # pick providers, paste keys (stored owner-only)
uv run pyrrhon /path/to/some/repo    # Textual TUI (default)
uv run pyrrhon --text .              # plain-text REPL
uv run pyrrhon --voice .             # TUI with the voice pipeline on
```

The audio stack is an optional extra. Text and TUI never import it, so a plain
`uv sync` stays small. If you run `--voice` without it, Pyrrhon tells you what
to install and continues in text mode.

A first launch with no configuration offers the same wizard. To skip it, export
a key yourself. Text and TUI need one LLM key, and Groq is the default:

```bash
export GROQ_API_KEY=gsk_...          # PowerShell: $env:GROQ_API_KEY = "gsk_..."
```

Wizard keys are stored owner-only in `~/.pyrrhon/credentials.toml`, never in
project config. Environment variables always win. `/settings` shows what is
configured and where each key came from.

Voice also needs `OPENAI_API_KEY` for TTS; STT runs on the same Groq key. Inside
the TUI, `/voice on|off` toggles the pipeline. Start talking whenever, and
interrupt whenever: barge-in rewrites history to exactly what you heard.

Worth knowing once you're in: `/help`, `/mode design`, `/model fast
groq/llama-3.3-70b-versatile`, `/mcp`, `/plugins`, `/code` (opens the last
citation in your editor), `/debug-history`, `/quit`.

### Configuration

Settings merge from `~/.pyrrhon/config.toml`, then `<repo>/.pyrrhon.toml`, where
the repo wins. All of it is optional.

```toml
fast = { provider = "groq", model = "llama-3.3-70b-versatile" }  # every turn
deep = { provider = "openai", model = "o4-mini" }                # think_deeper escalation

[fallbacks]
fast = ["cerebras", "openrouter/meta-llama/llama-3.3-70b-instruct"]

[providers.myllm]                       # any OpenAI-compatible endpoint
base_url = "http://localhost:8000/v1"
api_key_env = "MYLLM_KEY"

[mcp_servers.docs]                      # MCP tools join the agent's toolset
command = "docs-mcp"                    # stdio, or: url = "http://..." for HTTP
```

Built-in providers: `groq`, `openai`, `gemini`, `deepseek`, `cerebras`,
`openrouter`, `huggingface`. Each needs only its `*_API_KEY` environment
variable. For Cerebras, set `CEREBRAS_API_KEY` and point a slot at `provider =
"cerebras"`. Hugging Face uses `HF_TOKEN` against the Inference Providers
router. Two keyless local providers ship as well: `ollama`
(`http://localhost:11434/v1`) and `lmstudio` (`http://localhost:1234/v1`).

Drop markdown "soul files" into `~/.pyrrhon/` or `<repo>/.pyrrhon/` to give
Pyrrhon standing context about you or the repo. `/init` scaffolds one.

### Voice providers

```toml
[voice]
stt_provider = "groq"          # groq | openai | gemini | huggingface | deepgram | whisper-local (no key)
tts_provider = "cartesia"      # openai (default) | groq | gemini | huggingface | cartesia | elevenlabs | deepgram | piper (local)
tts_voice = "<voice-id>"       # OpenAI/Groq voice name, Gemini voice (Kore/Puck/...), Cartesia/ElevenLabs id, or Deepgram Aura voice
tts_model = "sonic-2"          # optional provider-specific model (for huggingface TTS, the HF model id)
# tts_url = "http://localhost:5000"   # piper HTTP-server mode only (default is in-process)
```

OpenAI TTS is the zero-setup default. For real-time conversation, Cartesia and
ElevenLabs are noticeably snappier, roughly 100 to 300ms to first audio against
400ms and up.

Fully local and keyless is possible: `stt_provider = "whisper-local"` (any
faster-whisper size via `stt_model`), `tts_provider = "piper"` (in-process,
downloads voices on demand, no server), and a local LLM through `[fast] provider
= "ollama"` or `lmstudio`. Gemini STT and TTS ride a plain `GEMINI_API_KEY`.

| Task | Cloud (API key) | Local (keyless) |
|---|---|---|
| LLM | Groq, OpenAI, Gemini, DeepSeek, Cerebras, OpenRouter, Hugging Face | Ollama, LM Studio |
| STT | Groq Whisper, OpenAI, Gemini, Hugging Face, Deepgram | whisper-local (faster-whisper: tiny through large-v3, distil, any HF id) |
| TTS | OpenAI, Groq (Orpheus), Gemini, Hugging Face, Cartesia, ElevenLabs, Deepgram Aura | Piper (in-process, downloads voices on demand) |

Hugging Face STT/TTS and the Groq LLM/STT/TTS trio each ride a single key
(`HF_TOKEN` and `GROQ_API_KEY`), so one credential covers reasoning and voice
together. Any OpenAI-compatible endpoint works as a custom provider through
`[providers.<name>]` with `base_url` and `api_key_env`.

Gemini Live speech-to-speech is deliberately absent. It generates speech
directly from audio, which skips Pyrrhon's agent loop and the grounding gate
that checks every `file:line` before it is spoken. Confident hallucination read
aloud is the worst thing this program could do, so Gemini participates as LLM,
STT, and TTS instead, each behind the gate.

### Context budget

```toml
[context]
budget_tokens = 32000       # estimated tokens before old turns are summarized
keep_last_messages = 8      # recent messages always kept verbatim
```

## Security model

Pyrrhon is built so that a voice agent cannot damage your repo even when the
model is wrong.

It is read-only by construction. The only write tool is `write_spec`, which
accepts exactly six filenames (`PRD.md`, `HLD.md`, `LLD.md`, `api.md`,
`database.md`, `risks.md`) and writes only under `docs/design/`.

There is no shell. Git access is three read-only subcommands (`log`, `blame`,
`show`) run as argv lists, so there is no command string to inject into. Paths
are resolved and rejected if they escape the repo.

Speech is grounded. Every `file:line` claim is checked against the real repo
before it is spoken. Unverifiable claims get stripped on voice or corrected once
on screen, and Pyrrhon says "I'm not certain" rather than guessing.

Keys stay out of the repo. They live in `~/.pyrrhon/credentials.toml` with
owner-only permissions, never in project config, and environment variables take
precedence.

A cloned repo is untrusted input. Anything in it that would run a program,
redirect where your prompts and API key go, or write into Pyrrhon's own
instructions requires one explicit consent, described below.

`tests/test_safety.py` and `tests/test_repo_trust_boundary.py` pin these.

### What a repo may and may not do

You clone a repo you have never read and point Pyrrhon at it. Its
`.pyrrhon.toml` and `.pyrrhon/` directory are that stranger's input rather than
your configuration, so they are split by what each key can actually do.

| Repo supplies | Effect |
|---|---|
| `[voice]` (except `tts_url`), `[model]`, `[context]` | Applied. Cosmetic knobs. |
| `[fast]`, `[deep]`, `[fallbacks]` naming a builtin or *your* provider | Applied. A repo may suggest `groq/llama-3.3`. |
| `[mcp_servers.*]` | Needs consent. It launches a program. |
| `[providers.*]` | Needs consent. It decides where your prompts and API key go. |
| `voice.tts_url` | Needs consent. Piper HTTP mode POSTs everything Pyrrhon says to that URL. |
| `[fast]`/`[deep]`/`[fallbacks]` naming a provider *the repo itself defined* | Needs consent. Same key redirection, one level of indirection away. |
| `.pyrrhon/*.md` (soul files) | Needs consent. They are appended to the system prompt, so an ungated one can tell Pyrrhon to stop citing sources. |
| `.pyrrhon/plugins/*` with tools or commands | Needs consent. It is Python that Pyrrhon would import and run. |

Everything needing consent is collected into one prompt at startup, listing each
item and what approving it allows. Saying no is not fatal. The session opens
normally with whatever permissions it already has.

Consent is recorded in `<repo>/.pyrrhon/trusted` and bound to the value's
content rather than its name. Approving `mcp_servers.indexer = "node ./build.js"`
approves that command; if the repo later changes it, you are asked again.
Editing a soul file you approved re-prompts for the same reason.

Two conveniences that are not exceptions: your own global
`~/.pyrrhon/config.toml` is never gated because it is yours, and the files
Pyrrhon writes itself (`/init`'s `soul.md`, the `remember` tool's `memory.md`)
self-grant, so you are never asked to approve words you just dictated.

For automation, `pyrrhon --trust-repo` grants everything without prompting. It
runs programs the repo chose, so use it only where you already trust the repo. A
run with no interactive terminal (piped stdin, CI) refuses silently rather than
hanging on a prompt or auto-approving.

## Architecture

Headless core, thin channels. The core never imports a channel. Channels
subscribe to a typed event stream and render it however they like.

```mermaid
flowchart TB
    subgraph channels["Channels (thin, swappable)"]
        REPL["Text REPL<br/>(rich)"]
        TUI["TUI<br/>(Textual: transcript · code viewer · status bar)"]
        VOICE["Voice<br/>(Pipecat: mic → Silero VAD → Groq Whisper STT<br/>→ bridge → OpenAI TTS, barge-in)"]
    end

    EVENTS(["Event stream<br/>SpeechChunk · Citation · ScreenArtifact ·<br/>ToolCall* · AskUser · TruncateSpeech"])

    subgraph core["pyrrhon/core: headless, imports no channel"]
        SESSION["Session<br/>(history, modes, cancellable turns, latency)"]
        AGENT["Agent loop<br/>(LLM ⇄ tools, citation extraction)"]
        GATE["Grounding gate<br/>(verifies every file:line; retry or strip)"]
        LLM["LLM adapter<br/>(OpenAI-compatible · fallback chains ·<br/>fast/deep slots)"]
        TOOLS["Tools<br/>read/grep/glob · git log/blame/show ·<br/>symbol index (tree-sitter) · web ·<br/>remember · write_spec · think_deeper"]
        MCP["MCP client<br/>(stdio + HTTP servers bridged as tools)"]
    end

    subgraph compose["pyrrhon/bootstrap.py: composition root"]
        PLUGINS["Plugin loader<br/>~/.pyrrhon/plugins + repo/.pyrrhon/plugins<br/>prompts · tools · commands · providers · MCP<br/>(repo code trust-gated, once per repo)"]
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

Three rules keep it honest.

Grounding is not optional. The gate checks every cited `file:line` against the
repo before it is spoken. An unverifiable citation triggers one self-correction
round trip on text channels, or is stripped immediately on voice, where a retry
would blow the latency budget.

Real-time discipline holds everywhere in `core/`. No synchronous filesystem or
CPU work runs inline in an `async def`; anything blocking goes through
`asyncio.to_thread`, so audio never glitches while a tool reads disk.

There is one composition point. `pyrrhon/bootstrap.py` assembles the LLM, tools,
prompt, MCP servers, and plugins. Nothing else constructs an `Agent`.

## Plugins

A plugin is a folder with a `plugin.toml`, dropped into `~/.pyrrhon/plugins/`
for global scope or `<repo>/.pyrrhon/plugins/` for one repo.

```toml
name = "hello-reviewer"
version = "0.1.0"

[contributes]
prompts = ["prompts/*.md"]      # appended to the system prompt
tools = "tools.py:get_tools"    # Python entry point returning list[Tool]
```

Plugins contribute prompts, tools, slash commands, LLM providers, and MCP
servers. All of it is additive, so a plugin never overrides your config or the
built-ins. Prompts and config load from anywhere, but repo-level plugin code
runs only after a one-time consent prompt recorded in `<repo>/.pyrrhon/trusted`.
Cloning a repo never means executing its code. A broken plugin is skipped with
one warning instead of crashing the session, and `/plugins` shows what loaded.
There is a worked example in
[`tests/fixtures/plugins/hello-reviewer/`](tests/fixtures/plugins/hello-reviewer/).

## Development

```bash
uv run pytest                    # full suite, no API keys needed
uv run pytest path/to/test.py::test_name
uv run ruff check . && uv run mypy pyrrhon/core
uv run python -m pyrrhon.evals.grounding evals/grounding.yaml  # real-LLM eval
```

Layout:

| Path | What lives there |
|---|---|
| `pyrrhon/core/` | The headless agent. Imports nothing from outer layers. |
| `pyrrhon/bootstrap.py` | Composition root: builds the Agent, runs the trust gate. |
| `pyrrhon/channels.py` | The event-to-renderer dispatch table shared by channels. |
| `pyrrhon/repl.py`, `pyrrhon/tui/`, `pyrrhon/voice/` | The three channels. |
| `pyrrhon/commands/` | Slash commands, registered by decorator. |
| `pyrrhon/config/`, `pyrrhon/plugins/` | Settings, credentials, trust, plugin loader. |

## Not in scope yet

Enterprise onboarding as a product, student and interview-prep positioning, a
plugin marketplace, and company-standards enforcement. All parked in
[VISION.md](VISION.md) until the Understand loop is undeniable.
