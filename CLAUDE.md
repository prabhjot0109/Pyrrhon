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

Dont commit any docs to any brach and docs is commneted out of gitignore deliabertaly so you can read them so dont commits gitignore and docs to any branch.

## Map

Start here rather than by grepping. The layering rule below is the one thing
that must stay true.

| Path | Responsibility |
|---|---|
| `pyrrhon/cli.py` | Argument parsing, picks a channel. |
| `pyrrhon/bootstrap.py` | Composition root. `build_agent` wires the tool belt, both LLM slots, the grounding gate, and the system prompt. `start_channel` runs the shared startup sequence. `load_channel_plugins` is the repo trust gate. |
| `pyrrhon/channels.py` | `EVENT_HOOKS` plus the `EventRenderer` base. One dispatch table for every channel. |
| `pyrrhon/repl.py` | Text channel (rich). |
| `pyrrhon/tui/app.py` | Textual channel: App shell, bindings, actions, the turn worker. |
| `pyrrhon/tui/renderer.py` | The one mapping from core events onto mounted rows. |
| `pyrrhon/tui/turn.py` | `TurnView` — everything on screen that belongs to one turn, and nothing that outlives it. |
| `pyrrhon/tui/messages.py` | The transcript's mountable rows and the evidence rail. |
| `pyrrhon/tui/status.py` | Reactive status bar plus the instruments it renders. |
| `pyrrhon/tui/theme.py` | The six colours. The only file under `tui/` with a hex value. |
| `pyrrhon/tui/pyrrhon.tcss` | All layout and styling, by `$token`. |
| `pyrrhon/tui/palette.py`, `completion.py`, `prompt.py`, `editor.py`, `splash.py` | Command palette, inline `/` menu, multiline prompt, `$EDITOR` launch, startup splash. |
| `pyrrhon/core/providers/registry.py` | The LLM provider table. Data only; `BUILTIN_PROVIDERS` and the wizard's menu derive from it. |
| `pyrrhon/core/providers/adapters.py` | The one place `core/` may import pipecat, and only `pipecat.adapters`. Seam only so far. |
| `pyrrhon/voice/registry.py` | The STT/TTS provider table. Data only; imports no Pipecat. |
| `pyrrhon/voice/factory.py` | Generic construction from that table: key checks, lazy import, clean degradation. |
| `pyrrhon/voice/` | Pipecat pipeline: mic, RNNoise, Silero VAD, smart turn, STT, bridge, TTS, barge-in. |
| `pyrrhon/core/agent/loop.py` | The turn: LLM and tools, streaming, error recovery, pre-flight compaction. |
| `pyrrhon/core/agent/policy.py` | The turn state machine proper: the policy table, `TurnState`, and `decide()`. |
| `pyrrhon/core/session.py` | History, modes, cancellable turns, latency. |
| `pyrrhon/core/grounding/` | `gate.py` verifies citations; `evidence.py` records what tool output actually showed. |
| `pyrrhon/core/tools/` | The belt. One module per family. |
| `pyrrhon/core/events.py` | The event contract between core and channels. |
| `pyrrhon/config/` | Settings, credentials, trust grants, setup wizard. |
| `pyrrhon/plugins/` | Plugin discovery and loading. |

**The layering rule: `pyrrhon/core/` and `pyrrhon/config/` take no import-time
dependency on `tui`, `voice`, `repl`, `commands`, or `cli`.** Channels depend on
the core, and `bootstrap.py` sits above every channel. Verify with:

```bash
grep -rn "^from pyrrhon\.\(tui\|voice\|repl\|commands\|cli\)" pyrrhon/core/ pyrrhon/config/
```

That should print nothing. If it prints something, that is the bug, not a style
question.

Note the `^`, which is the rule stated precisely rather than approximately.
Drop it and the grep matches `catalog._providers`, whose import of
`voice/registry.py` is deliberately **function-local**: nothing runs until a
menu is rendered, so `config/` still imports without the audio stack, which is
what the rule is protecting. An unanchored grep reads like a break to whoever
runs it next. The same care applies to the pipecat exception in
`core/providers/adapters.py` — see the M15b notes below.

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

LLM providers are a row in `LLM_PROVIDERS` in
`pyrrhon/core/providers/registry.py` — no code, and no catalog edit either,
since `BUILTIN_PROVIDERS` and `llm_choices()` are both derived from that table.
Record no default model: model ids rot faster than anything else here, so the
user names the model (the wizard insists on one) and the provider supplies
nothing. Two fields carry decisions rather than facts. `base_url = None` means
"the openai SDK's default", i.e. api.openai.com, and is correct for exactly one
row — a test pins that set to `{openai}`, because any other provider left there
would post *its* key to OpenAI. `vision` gates only the automatic fallback in
`Settings.vision_slot()`, never an explicit `[vision]`, which is why the local
servers are marked `False`: they relay images fine, but whether the loaded
model can see is unknowable from here.

Voice providers are a row in `VOICE_PROVIDERS` in `pyrrhon/voice/registry.py`
— no code, and no catalog edit either, since `stt_choices()`/`tts_choices()` are
derived from that table. `tests/test_voice_registry.py` verifies the class
exists in the installed pipecat without importing it (tier 1), that the class
declares a `Settings` (tier 1 again, see below), and that the extra is either
bundled or surfaced as an install command (tier 2). Add no default model: where
pipecat or the provider supplies one, pass nothing and inherit it.

The row carries **no kwarg-name columns, and must not grow them again.** Model
and voice reach a service as `settings=Cls.Settings(model=…, voice=…)`, whose
field names pipecat made uniform in 1.7.0; the old `model_kwarg`/`voice_kwarg`
pair existed only to record that pipecat once disagreed with itself, and that
disagreement is gone. `factory._settings` builds one sparse delta, which a
service merges over its own store — so "only send what was configured, and
inherit the rest" survived the migration unchanged. The three in-repo shims
(`voice/gemini.py`, `voice/huggingface.py`) take `settings=` too, which is what
leaves exactly one construction path.

Set `verified=` only from a tier 3 run. Tier 3
(`tests/test_voice_live.py -m live`) is the only tier that proves the thing
actually works: it pushes one real utterance through each provider inside
Pipecat's own `run_test` harness, and the STT half transcribes speech that Piper
synthesized in the same session. It reads keys from `~/.pyrrhon/credentials.toml`
as well as the environment, and borrows an account-specific voice or model id
from your `[voice]` config when it names the same provider. Run it before a
release and record the results in the M15a plan. `catalog.availability()`
renders a row without that flag as `ready, unverified` rather than `ready`,
which is what makes curating more providers than we hold keys for honest.

`availability()` decides *runnable* by asking the module what it imports —
`find_spec` over the full dotted paths in its source, never an import. Two
things about that are load-bearing. It asks the **module**, not pipecat's
extra metadata, because an extra is coarser than a row: `pipecat-ai[deepgram]`
covers an STT service that needs the vendor SDK and a TTS service that is plain
HTTP, and the metadata question told users to install something Deepgram TTS
does not need. And it checks the **full dotted path**, because `google` is a
namespace package — a root-only check reports Gemini TTS ready when it cannot
import, which is the original lie wearing a new hat.

Any OpenAI-compatible LLM endpoint still needs no code at all: users declare it
under `[providers.<name>]`.

Adding a repo-readable config key means deciding which side of the trust
boundary it sits on. See `PRIVILEGED_PATHS` in `pyrrhon/config/settings.py`.

## Toolchain and commands

Uses [uv](https://docs.astral.sh/uv/) for dependencies and environments. Python
3.12 or newer, pinned in `.python-version` and `pyproject.toml`.

```bash
uv sync                        # install everything, audio stack included
uv add <package>               # add a dependency

uv run pyrrhon [repo-path]     # Textual TUI (default channel)
uv run pyrrhon --text .        # plain-text REPL
uv run pyrrhon --voice .       # voice pipeline on

uv run pytest                  # full suite, ~40s, no API keys needed
uv run pytest path::test_name  # one test
uv run pytest -m live          # tier 3 voice smoke: real providers, real keys
uv run ruff check .            # gated in CI
uv run mypy pyrrhon/core       # gated in CI

uv run python -m pyrrhon.evals.grounding evals/grounding.yaml   # needs an API key
```

The text and TUI channels need one LLM key (`GROQ_API_KEY` by default, or
configure another provider). Voice needs a key for whichever STT/TTS rows you
pick and nothing else — there is no `--extra voice` any more, because a
provider the menu offers and one command cannot start is a menu that lies. Every
extra in `pyrrhon/voice/registry.py` is in the base `pipecat-ai[...]` line, and
`tests/test_voice_registry.py::test_tier2_every_table_row_ships_installed`
fails if a new row outruns it. Inside the TUI, `/voice on|off` toggles the
pipeline and `/debug-history` dumps the session history.

A provider-scoped id is only wrong relative to a provider, so nothing in the
client can validate `[voice] stt_model` or `tts_voice` — a Piper voice sent to
Deepgram's speak socket comes back as a bare HTTP 400 at the websocket
handshake. Two things keep that from recurring. `config/wizard.py:_write_config`
goes through `patch_config` and writes every key it owns on every run, `None`
included, so a rerun converges instead of carrying the previous provider's ids
through a switch. And `voice/bridge.py:_handshake_hint` turns a rejected
handshake into the config key that caused it, reading `key_env` back off the
registry row rather than deriving it from the class name (`HF_TOKEN`, not the
`HUGGINGFACE_API_KEY` string surgery would invent).

Keep ruff and mypy clean. Both gate CI, and the ruff rule set is deliberately
narrow (`F`, `I`, `B`, `ASYNC`) so that real findings are not buried under style
opinions.

## Design constraints

Do not violate these without a discussion.

**Voice-first, screen-supported, not voice-only.** Voice drives; the terminal
shows what is being discussed. Do not remove the visual channel in the name of
purity.

**Grounding is a hard requirement — but verify is not verbalize.** Every claim
about the code must be *verified* against a real `file:line` or commit, or the
agent says it does not know. Confident hallucination spoken aloud is the worst
failure mode this program has. Never fabricate a path to sound complete.

That is a claim about *verification*, not about *delivery*. The two are separate
and `GroundedText` already returns them separately (`speech_text` vs.
`citations`): **the screen shows `path:line`; the voice never speaks it.** Spoken
coordinates are unusable — a listener cannot act on "loop dot py colon one nine
three" — so voice refers to code by name and role ("in the turn state machine,
where it recovers from a tool error"). Weakening the gate to make speech shorter
is the wrong trade; stripping citations from speech while the gate still runs is
the right one.

Concretely, since M15a: a verified reference leaves the prose entirely and is
delivered as a `Citation` event. The screen keeps it — the TUI writes a
clickable `📍 path:line` line and drives the code viewer from it, and the REPL
prints `citation_markup` — so "the screen shows `path:line`" is satisfied by the
citation, not by the sentence. `history` therefore records the prose without
coordinates, which is intended: the model should re-derive a location from a
tool result, not from its own recollection.

**Pipecat owns audio; Pyrrhon owns the harness.** Three layers, and the rule
that separates them:

- **Layer A — Pyrrhon, never delegated.** `core/agent/loop.py`,
  `core/grounding/`, `core/session.py`, `core/telemetry.py`, and
  `voice/bridge.py` (barge-in, played-text truncation, tool fillers).
- **Layer B — the seam.** `voice/registry.py` + `voice/factory.py` and
  `core/providers/`. Declarative tables plus generic construction; the only
  layer that names a Pipecat class.
- **Layer C — Pipecat, adopted never rebuilt.** Transport, VAD, turn analysis,
  STT/TTS services, the frame bus, observers, tracing, audio filters.

**If Layer C already does it, do not hand-roll it.** The voice pipeline is a
solved problem that belongs to Pipecat; Pyrrhon's moat is a low-latency agent
loop that survives long sessions on large codebases. One deliberate exception,
so nobody "fixes" it: the sentence splitter at `loop.py:101-114` duplicates
Pipecat's `SentenceAggregator` because it feeds the grounding gate from inside
`core/`, which may not import Pipecat.

**A cloned repo is untrusted input.** Anything the repo supplies that runs a
program, redirects where prompts or keys are sent, or writes into the system
prompt needs a content-bound grant in `<repo>/.pyrrhon/trusted`.

**Scope discipline.** Only the two acts are in scope. Enterprise onboarding,
student and interview positioning, a plugin marketplace, and company-standards
enforcement are parked in `VISION.md`. Do not build them until the Understand
loop is undeniable.

## Current state

Everything through M15b is implemented and tested, bar the one piece named
under "Planned next". The parts worth knowing about before you change them:

**The TUI redesign (2026-08-23).** The Textual channel had not been designed
since M2 and had drifted into a channel that measured far more than it showed.
It is now one column. The `CodeViewer` is deleted, not hidden (D1): a citation
is a clickable `📍 path:line` row plus `ctrl+o`, which suspends the app and
runs `$VISUAL`/`$EDITOR` at the line. The transcript is a `VerticalScroll` of
mounted rows rather than a `RichLog`, and that one change is what makes every
progress affordance possible — a `RichLog` line can never be updated, which
was the structural reason a spinner, a resolving tool row and a streaming
answer all had no cheap fix.

Eight things about it are load-bearing and easy to break, each one a bug that
looked like a style choice until it bit.

The theme is registered in `PyrrhonApp.__init__`, not `on_mount`, because
`CSS_PATH` is parsed at startup against the *current* theme's variables and a
later registration leaves every `$token` undefined.

`PyrrhonApp.get_theme_variable_defaults()` supplies `$evidence`, `$voice`,
`$hedge`, `$fault` and `$muted` under **every** theme. `get_css_variables()`
builds from the active theme alone, so a token that lives only in
`PYRRHON_THEME.variables` vanishes the moment the user picks another theme
from the command palette and the app dies parsing its own stylesheet. For the
same reason the background role is spelled `$background`/`$surface`, which
every theme defines, rather than a token of ours: switching theme should
restyle Pyrrhon, not crash it.

`TurnView.start()` is awaited, because `Widget.mount()` is asynchronous and an
un-awaited working row is not yet `is_mounted` when the turn's first event
arrives — which silently put the first tool row *below* the spinner and left
the transcript out of order.

`TurnView` keeps its own `_said` buffer and reconciles the document against it
after stopping the stream, because **`MarkdownStream` drops whatever is still
pending when it is stopped**. Its `_run()` catches the `CancelledError` and
*then* awaits the final append, but a task that has already absorbed a
cancellation re-raises on its next await, so that append never happens. An
answer that arrived in one late chunk rendered as an empty row — which reads
as a large blank gap under the question, not as an error. The stream is a
rendering optimisation; the buffer is the document.

`TurnView._end_speech_stream` catches `CancelledError` for the same underlying
reason, re-raising only when the current task has a pending cancellation of
its own so `esc` still aborts. Prose reaches the stream through a buffer
rather than a scheduled `stream.write`, because a turn ending between the
schedule and the run wrote into a stopped stream and raised outright.

One CSS rule is in the same category: the rail is `height: auto`, never `1fr`.
A fr unit inside an `auto`-height row is circular, and Textual resolved it
against the viewport instead of the sibling — every row rendered eighteen
lines tall, which was one part of the blank-gap symptom.

`PyrrhonApp.mount_row` follows the transcript explicitly and
`VerticalScroll.anchor()` is not called, which is the *last* part of that same
symptom and outlived both fixes above. `anchor()` runs its `scroll_end` with
`immediate=True`, i.e. before the layout that would bound it, and never
revises the offset it settles on: the transcript sat at
`scroll_y = -(viewport height)` for the whole session, so every row rendered
bottom-aligned under a screen-high gap. `max_scroll_y` reads 0 the whole time,
which is why the offset never looks wrong from inside the widget. Following on
mount keeps what the anchor was for, and a user who has scrolled up is left
alone — the check runs *before* the mount, because mounting is what moves
`max_scroll_y` underneath it.

`TurnView.stream_speech` joins chunks with the separator the core split on:
`" "` for voice, `"\n\n"` for text, read off `agent.voice_active`. The core
hands over one *unit* per chunk — `_pop_blocks` strips each markdown block
and rejoins history with a blank line — so concatenating with nothing fused
a paragraph into the list that followed it and swallowed every heading, table
and list after the first block. It cannot be recovered downstream; the joiner
has to be reapplied by the code that concatenates.

**No horizontal margin on any direct child of the screen's column.** Textual's
vertical layout narrows *every sibling* to accommodate one child's horizontal
margin, so a 1-column inset on the prompt silently shrank the transcript to
118 of 120. `test_layout_is_one_full_width_column` is the check.

**The turn boundary (2026-08-28).** `begin_turn`/`end_turn` on the App are
the one bracket both channels use. `TurnView` is created once and used to be
bracketed only inside `_agent_turn`, which is reached only from the typed
prompt — so a spoken turn, which enters through
`VoiceController -> _on_voice_event -> TuiRenderer`, crossed no boundary at
all. Every spoken answer after the first was appended to the first one's row.

A `Transcription` rotates it, because a spoken turn is started by the bridge
and reaches the screen only as events, so the user speaking is the one signal
both paths agree marks a new turn. `TurnFinished` closes it: `voice/bridge.py`
emits one from `_run_turn`'s `finally`, which is every exit path including the
cancellation a barge-in causes — without it a spoken turn's spinner never
stopped and the status bar said `speaking` for the rest of the session.

Three things about it are load-bearing.

The boundary is **awaited**, not deferred, and that is why `EventRenderer`
grew `render_awaited` beside `render` — same table, but an `async def` hook is
awaited rather than left as a coroutine nobody ran, and `render` says so out
loud if an async hook is reached through the sync door. The voice path already
defers every event with `call_later`; a sync hook deferring its own async work
schedules a *second* callback, which lands behind the events that arrived
after it, so the rotation for turn two would have run after turn two's own
answer. Textual dispatches queued messages one at a time and `invoke` awaits
an async callback to completion, so one deferral per event is ordered where
two are not. `call_next` does not rescue it: `_flush_next_callbacks` runs only
once the queue is already empty.

The end-of-turn signal races the next utterance, and the two halves of that
race are fixed in the two places that have the knowledge. `_start_turn` and
`_on_interruption` both cancel a turn task **without awaiting it**, so a
superseded turn's `finally` runs after its replacement has begun: only the
bridge knows which task is live, so it reports only when
`self._turn_task is asyncio.current_task()`. And a spoken turn can end after
something else has taken the screen — a question typed while voice is running
is the plain case — so `TuiRenderer` remembers which generation the utterance
opened and closes *that* one. Reading `turn.generation` when the report
arrives is no guard at all: it always matches.

`orient_in_background` is handed `self._renderer.render`, not
`_render_event`. The brief is a `ScreenArtifact` whose hook is a plain row
mount, and that task calls its callback synchronously from outside the message
pump; `_render_event` is async now and would have become a coroutine nobody
awaited.

One thing found while doing this and deliberately not fixed, because it is a
session concern and not a screen one: `_start_turn`'s defensive path cannot
actually start a replacement turn. It cancels its predecessor and calls
`abort_current_turn()`, but `Session.run_turn` refuses while `_current` is
merely cancelled and not yet `done()`, so the replacement raises
`RuntimeError: A turn is already running`. Reachable only when a transcription
races ahead of its own interruption. M16.

The evidence rail is the signature: one gutter column carrying the epistemic
status of each row, and it is a widget rather than a character prepended to
the body, which is what keeps it out of copied text and out of `history`.

The rail carries an **epistemic ladder**, and that is what picks the hues:
green verified, amber hedged, red faulted. Green-amber-red is the one sequence
every reader can already rank, so the rail reads before anything is explained
— which the old blue/teal pair could never do, because blue and teal have no
agreed order between them. That frees the accent (`branding.FACE`, terracotta)
to mean one thing: *you*. The wordmark, the rail on a turn you took, the
microphone and the focus ring are all it. The ground is true black rather than
the blue-shifted near-black that existed only to sit in the same family as a
blue accent. Colours are six named values in `theme.py` and nowhere else; a
six-digit hex anywhere else under `tui/` is a bug a grep catches.

There is no `Header`: it spent a row restating the title bar, and the repo
name is state, so it sits in the status line with everything else that is.
`esc` aborts a turn, the status bar shows repo, models, context fill and voice
state, and typing a bare `/` or pressing `ctrl+p` searches the command
registry live, so a plugin's command is findable without anything being told
about it. **Do not bind `ctrl+p`.** The Footer advertises the palette from
`App.COMMAND_PALETTE_BINDING`, in a slot of its own that a binding does not
override, so declaring it printed `^p commands` twice on one line — and
`active_bindings` cannot see the collision, because it is keyed by key and the
two entries share one.

A command's answer is a `CommandRow`, not a `NoticeRow`. `NoticeRow` is the
⚠ rail in hedge amber, which means "Pyrrhon could not verify this"; `/help`
wore it for listing the command table. Failures still take it.

`/exit` and `/quit` are rows in the command table, so `/help`, the inline menu
and the palette all know them. The REPL used to match both names against its
own input string *before* dispatch, which is why they appeared in no list and
the TUI had none at all. A handler returns a string and never raises, so
leaving is a request — `ctx.ui.request_exit()`, duck-typed the way `/code`
already treats `last_citation`.

The inline `/` menu overrides the redesign spec, which ruled it out; the
rejected *dependency* stays rejected, since it is Textual's own `OptionList`.
`esc` has one precedence chain — close the menu, else clear the prompt, else
stop the turn — because esc means "undo the innermost thing I just started".

The splash is mounted *inside* the transcript. As a sibling it owned a fixed
slice of the column, so clearing it resized the scroll view under an offset
computed against the old geometry and the conversation jumped; inside, it is
an ordinary row.

`pyrrhon/tui/app.py` carries **no** per-file `F401` ignore. It had one, for
the command-registration block that already carries its own `noqa`, and the
blanket ignore silenced the whole file: seventeen dead imports accumulated
behind it, including the entire core event vocabulary the module stopped
touching when `renderer.py` was split out. Only `repl.py` is listed in
`per-file-ignores` now.

A long `ScreenArtifact` arrives folded. M14's orientation brief is a hundred
lines of symbol counts on a real repo, and it used to land on top of the
splash as the first thing a new user ever saw. The REPL keeps its own
rendering on purpose.

**The provider boundary (M16a, 2026-08-30).** A live session died after four
tool rounds with `PROVIDER_ERROR_MESSAGE`. The obvious reading — "the context
window overflowed" — was wrong, and the difference is the whole milestone: the
overflow recovery existed and did not fire, because `_raise_if_typed` was
reachable only from `except BadRequestError` and Groq reports an oversized
request as **413** and a spent token allowance as **429**. Neither is a
`BadRequestError`, so the string tests never ran.

`core/providers/errors.py` keys on HTTP status instead. Prose matching survives
only as a tiebreaker inside 400, which is the one status genuinely ambiguous
between "your tools are wrong" and "your prompt is too long". **`classify`
returning `None` is load-bearing**: it means "the caller re-raises verbatim",
and an unrecognised 4xx must never be laundered into a kind the loop believes
it can recover from. `credentials` and `outage` deliberately get no type — a
bad key is user error the SDK message already names, and an outage is
`FallbackLLM`'s to answer by inspecting the SDK exception.

`LLMReply` carries `finish_reason`, and that closes the quietest fault of the
four. Every other one produces a visible error; a reply cut off at `max_tokens`
is HTTP 200, a well-formed body, and a confident half-sentence that went
through the gate and got **spoken**. The loop resumes such a round exactly
once. `RESUME_INSTRUCTION`'s wording is load-bearing — a model told only
"continue" restates its previous paragraph and spends the whole new budget on
the recap. A second `length` is a configuration fact and names `[model]
max_tokens` rather than handing over a longer fragment; silently re-running at
a larger `max_tokens`, which the reference does, is **not** adopted. On the
streaming path the fragment has already been spoken by the time the reason is
known, so the resume accepts one audible seam rather than taxing every turn to
withhold a final sentence.

`core/providers/limits.py` is the missing other half of `token_scale`. That
learns how many characters make a token; `LearnedLimit` learns how many tokens
the endpoint will take, from `x-ratelimit-limit-tokens` (which rode every
response and was read by nobody) and from a recorded refusal. `limit` is the
**min** of the two, so a failure always wins and a later header cannot undo a
ratchet — that is the mitigation for a provider advertising an allowance it
will not honour. Both calls go through `.with_raw_response.create()` plus
`.parse()`; headers land with the HTTP response, so the streaming path gets the
ceiling before chunk one.

`context_budget_tokens = 90000` is gone. The budget is a question the agent
asks its driver, and there are **two** properties because the distinction is
real: `known_context_budget` is `None` when nothing established a ceiling, and
the status meter depends on that (a percentage against a denominator nobody
measured is a claim), while `context_budget_tokens` always returns a number
because compaction has to decide something. The fallback is 32000, not 90000:
an under-budget turn compacts slightly early, an over-budget turn dies — and a
smaller model now *teaches* its ceiling on the first refusal, so the guess
self-corrects within one turn.

A 429 gets one bounded wait derived from `retry-after`, taken with
`asyncio.sleep` so a barge-in kills it. A `retry-after` above the ceiling is
**declined outright rather than clamped**: waiting twenty seconds and then
reporting failure is strictly worse than reporting now with the real number, so
the user hears "clears in about 45 seconds". `FallbackLLM`'s "never fall over
on a 4xx" rule narrows by exactly one case — a spent allowance is availability,
not user error — and it arrives typed because the link already took whatever
wait was worth taking. `ProviderRetrying` rides the existing `on_switch`
attachment shape, so the payload is a core event and the dispatch table decides
how each channel says it.

`preconnect()` warms the pool during the splash, using `models.list()`: public
SDK surface, no tokens, and a 404 from a provider that lacks it warms the pool
just as well. It is called **after** `build_agent`, because the pool worth
warming is the one behind the configured base URL and warming a default is a
silent no-op that looks like a win. Its failure mode is that the first request
pays the handshake as it does today, so it logs at debug and never warns.

**The turn state machine (M16b, 2026-08-30).** `_run_turn` drove itself from
five loose locals — a `range(max_tool_rounds)` counter, a `ToolGuard`, two
booleans, and M16a's per-round resume count. Together they *were* the turn's
state; separately, the reason the loop stopped had to be reconstructed from
whichever branch happened to break out, by both the forced-answer path and the
trace. `core/agent/policy.py` gives that a type: `TurnState` is what the turn
has spent, `TurnPolicy` is what it may spend, and `decide()` returns
`Continue | Stop` with the reason attached. **Do not reintroduce a round
counter.** A second place that decides when a turn ends is how the five locals
happened in the first place, and `TurnTrace.stop_reason` immediately starts
lying — it is recorded now, not inferred, and `/debug-history` and the latency
harness read it.

The loop is `while True`, bounded by `decide()`, which is not a cosmetic
change: the nudge, the context recovery and the truncation resume all `continue`
without consuming a tool round now, where `range()` silently charged them one.
Each is bounded on its own — one nudge per key, `MAX_CONTEXT_RECOVERIES`, one
resume per round — so every path still terminates.

`decide()` is consulted **after** a tool round, never before the first LLM call.
That is what lets `max_rounds=0` mean "no tool rounds" rather than "no reply":
a social turn still gets its one round, and the row states the same fact twice
(no belt, no rounds) because the two fail differently.

The diminishing-returns signal is **evidence, not tokens**. The reference counts
forced continuations; three or four tool rounds is a normal investigation here,
so that threshold would cut off an agent that is working. `EvidenceLedger.
fingerprint()` is taken either side of each round, and three consecutive rounds
that opened no new line range and named no new path end the turn with
`Stop("diminishing")`. Ranges collapse into a set inside the fingerprint, so a
round that re-read lines already seen counts as barren — the duplicate-call
guard only catches the case where the arguments matched too. A token count
cannot make that distinction: a round can be expensive and productive, or cheap
and decisive.

**The nudge points the opposite way from Claude Code's.** Theirs says "keep
working, do not summarize", because its failure mode is a model that stops
early. Pyrrhon's is the reverse — a spoken turn that spends four more rounds has
already lost — so `LAND_NUDGE` fires at `nudge_at` of the budget and says answer
now. Do not copy the reference's wording back in; a test asserts the direction.

The policy is a **table** keyed by `(turn_type, voice_active)`, and
`turn_type.needs_tools` is derived from it through a function-local import (the
same shape as `catalog._providers`, and for the same reason). Which turns get
tools was one fact living in two places. `TurnPolicy.withheld` is a *withhold*
list, not an allow list: `None` means no belt at all, an empty frozenset means
the whole belt, and `belt_for` is the only place that polarity is read. An allow
list of builtin names would have silently stripped every plugin and MCP tool
from every narrowed turn.

Two rows carry decisions rather than numbers. `RESUME` keeps the belt — the plan
said otherwise, but "yes, go on" is a repo question the user just re-anchored,
and `turn_type.classify` documents why withholding tools there produces exactly
the ungrounded answer the gate cannot catch. And the spoken row keeps the
**whole** belt: withholding `think_deeper` and the two web tools was built,
measured at ~348 schema tokens saved (a quarter of the plan's estimate) against
a third prefix-cache family in any mixed session, and dropped — `voice/bridge.py`
already ships a spoken filler for each of those three, so an earlier milestone
had deliberately made them voice-usable. What the spoken row does carry is half
the round cap and half the tool-char budget, which bounds a turn rather than
removing a capability. The open question is recorded at `_SPOKEN`: the round cap
does **not** bound `think_deeper`, because it is one call inside one round.

`_tool_schemas` takes the policy and keys its cache on the offered **names**, so
the key moves with both things that can change it — a plugin joining
`self.tools`, and a row narrowing it. Getting that wrong is silent in both
directions. The whole table may produce at most **three** distinct belt shapes,
and that is a test rather than a comment: filtering the schema list looks like a
pure narrowing at the call site and is in fact a decision about how many
prefix-cache families the session gets (M10 section 2.2). Two shapes are in use
today; the third is the room a future voice row needs.

**Compaction runs in front of the request, not behind its failure.**
`context.fit_to_budget` is the ladder, cheapest rung first: under budget, then
elide earlier turns' tool results, then elide harder keeping only the most
recent, then summarize. `hard_compact_tool_results` is gone — it differed from
`compact_tool_results` by exactly one thing, which results are off limits, so it
is a `keep_recent` parameter now rather than a second function with a duplicated
body.

The turn's own pre-flight passes **`summarize=False`**, and that is load-bearing
rather than an oversight. M16b's plan puts the summarize rung in the pre-flight
*and* describes the loop as compacting "locally and cheaply"; those cannot both
hold, and M10 moved that round trip off the critical path on purpose — it used
to sit in front of the first token of every over-budget turn.
`test_the_turn_itself_makes_no_summarize_call` pins it. So the ladder owns all
four rungs, the critical path runs the two pure ones, and the round trip stays
where it belongs: `Session`'s background pass in dead time, or the safety net
after the provider has already said no.

`Agent.request_budget` nets the belt's schemas off the top, because they ride
the same request as the history and a budget that ignored them would aim the
compactor at a number the request was always going to exceed. `Session.
_schedule_compaction` reads the **same** function: it used to budget against
`context_budget_tokens`, a looser number, which would schedule a round trip to
fix a history the turn had just fitted. `_cancel_compaction` is deliberately
untouched — its contract depends on `maybe_summarize`'s single await landing
before any mutation of history, and routing the background pass through the
whole ladder would put pure mutations in front of that await to buy microseconds
off a rung that is already free.

`MAX_CONTEXT_RECOVERIES` is **1**. The `ContextLengthExceededError` handler is
what its name says now: reaching it means the *estimate* was wrong, not that
nothing was tried, so it runs the same ladder once with `force=True` — every
rung, regardless of what the estimate says — and then degrades honestly.

Two things this milestone deliberately did **not** do. The eval and the runtime
checks in Task 7 need a working provider key, and `~/.pyrrhon/credentials.toml`
carries a placeholder for Groq, so the policy numbers are first guesses and the
signal for tuning them is `Stop(reason="rounds")` in real traces rather than
intuition. And M16a's handoff — trimming a sealed partial back to its last
complete sentence so the resume seam cannot duplicate a clause — is **declined**,
not deferred: on the streaming path that clause was already spoken, and
`_seal_partial`'s "history records what was heard" invariant is what barge-in
truncation and the transcript's honesty both rest on. One duplicated clause is
not worth trading it for. The seam belongs to M16e's move of verification
upstream, or to a wording change in `RESUME_INSTRUCTION`, whichever a live
listen argues for.

**LLM lane and vision (M15b).** LLM providers are rows in
`core/providers/registry.py`; `BUILTIN_PROVIDERS` and the wizard's catalog are
both derived from it, and no model ids are hardcoded anywhere. Token usage is
captured from every response — `stream_options` asks for the usage chunk, which
arrives with an empty `choices` list — and used to *calibrate* the `len//4`
estimate rather than replace it: `prompt_tokens` describes the request that was
sent, so substituting it would under-count everything appended since and go
stale high after compaction, while the ratio it implies (`context.token_scale`,
clamped to `[0.5, 2.0]`) stays right in both directions. `read_image` lets the
agent read diagrams and screenshots: it makes its own vision call via the
`[vision]` slot (falling back to `fast` when that provider can see) and returns
prose, so `Tool.run() -> str` and the agent loop are unchanged. `[vision]` is a
model slot, so it sits in `CONDITIONAL_PATHS` beside `fast`/`deep` — a repo may
suggest a builtin, never aim it at a provider it declared. An image has no
lines, so the evidence ledger records the path only, and deliberately does not
mine read_image's output: a `path:line` inside a vision model's prose was never
displayed to anyone. `core/providers/adapters.py` is the only place
`pyrrhon/core/` may reach pipecat, and only `pipecat.adapters`; it is a seam
whose `chat`/`stream` still raise, and `create_llm` never returns one. Note the
shape of that exception before you re-check the layering rule: the module is
named as a *string* on the provider row (`native_adapter`) and imported through
`importlib`, so `grep -rn "^\s*\(from\|import\) pipecat" pyrrhon/core/ pyrrhon/config/`
still prints nothing — but a bare `grep -rn "pipecat"` now matches those strings
and reads like a break. `tests/test_adapter_driver.py` is the enforcing check.
`load_adapter` degrades with an actionable message rather than a bare
`ModuleNotFoundError`, because an adapter carries no *frame* dependency but does
import the provider's own SDK: anthropic's needs `anthropic` installed.

**Voice integration (M15a).** The STT/TTS ladder is gone: providers are rows in
`voice/registry.py` and built generically by `voice/factory.py`. Smart turn
detection (`LocalSmartTurnAnalyzerV3` inside `UserTurnProcessor`) is on by
default with `[voice] turn_detection = "vad"` as the fallback; both modes name
their stop strategy explicitly, because `UserTurnStrategies` defaults to smart
turn when told nothing. RNNoise filters the mic and Pipecat's per-service
latency observers are on. `[voice] idle_timeout_sec` (off by default) is wired
end to end: Pipecat's `UserIdleController` detects the silence and `bridge.py`
supplies the line, from `IDLE_LINES` — which is a nag *cap*, not a rotation,
because the controller rearms its timer on every `BotStoppedSpeakingFrame`, so
an uncapped agent would talk to itself. Those lines bypass the gate like the
tool fillers, so the same static guard forbids a `path:line` in either.
Verified citations are stripped from speech and
delivered as `Citation` events, which the TUI renders as a clickable
`📍 path:line` line and the code viewer follows — see the delivery contract
above. `telemetry.otlp_endpoint` is privileged config, and
`partition_repo_config` now derives its dotted-leaf quarantine from
`PRIVILEGED_PATHS` instead of a hand-written block per key.

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
gate. Note the real shape of that constraint — gate-*before*-speech is what
blocks S2S, not verification itself. The direction of travel is to move
verification upstream (tool results as the only admissible source of code facts,
the prompt forbidding claims about unloaded code) so the egress gate becomes a
cheap safety net rather than the mechanism. That is what would make S2S viable
later; it is M16 work, not a licence to weaken the gate now.

**Planned next. M16a and M16b are closed; M16c is the next thing to start.**
Spec: `docs/superpowers/specs/2026-08-29-pyrrhon-m16-agent-harness-design.md`;
the five plans are `m16a` through `m16e` in `docs/superpowers/plans/`. M16b's
runtime pass (the grounding eval, the tuning of the policy numbers, and driving
both channels) is the one part left open, and it is blocked on a provider key
rather than on code.

**M16a was verified against a real Groq account on 2026-08-30, and the run
found a fault the plan did not know about** — see the plan's "Runtime
verification" section for the full record. Four things from it are worth
carrying:

The account's `x-ratelimit-limit-tokens` is **8000**, against which the harness
used to budget 90000. No plausible constant would have been right, which is the
case for the learned limit in one number.

A mid-stream failure has **no HTTP status**. When a 200 response's SSE body
carries an `error` event the SDK raises a bare `openai.APIError`, and
`APIStatusError` is its *subclass*, so `except APIStatusError` silently missed
it. Groq answers that way when gpt-oss reaches for a built-in tool on a request
carrying no `tools` array, which is every turn `needs_tools()` withholds the
belt from — so a greeting could die with `PROVIDER_ERROR_MESSAGE`. `classify`
now has a no-status branch, and it is the one place the prose tiebreakers run
with no status behind them.

**The resume seam the plan predicted actually happened.** Wording alone did not
prevent it: a round ending mid-clause on "...guarantees that its" was continued
with "by verifying once, the assistant guarantees that its knowledge base...",
duplicating the clause. Trimming the sealed partial back to its last complete
sentence would fix it and would trade against `_seal_partial`'s invariant that
history records what was *heard*, since on the streaming path that clause was
already spoken. M16b took that decision and **declined the trim** — see the end
of its section above for why, and where the seam belongs instead.

**Preconnect is worth its lines**: 127ms median off time-to-first-token, warm
beating cold in all six samples.

One check remains and it needs a human, not a key. The resume ladder is
verified live on the streaming path, which is the path voice uses, but nobody
has *heard* it. Run `uv run pyrrhon --voice .` with `[model] max_tokens` set low
and confirm no fragment is spoken as though it were finished.

M15a and M15b are both done on branch `m15`, and the 2026-08-22 pass closed the
three gaps that were left in Phase 3's own honesty claim: tier 3 pushes a real
utterance and covers STT, `[voice] idle_timeout_sec` has a handler, and
`availability()` distinguishes `ready` from `ready, unverified`.

One thing is deliberately NOT done, and it is not a blocker: the
native-provider work behind the adapter seam. Translating Pyrrhon's
`list[dict]` history into Pipecat's `LLMContext` needs designing against the
real adapter, and the M15b plan's own check-in point parks it. Until then
`AdapterLLM.chat`/`stream` raise and Anthropic and Gemini are reached through
their OpenAI-compatible endpoints, which is why the anthropic row's note says
prompt caching is unavailable there. **Do not fold this into M16.** It is an
LLM-lane feature with its own design question; M16's job is the harness, and
the seam exists precisely so the harness never has to know which driver it
holds.

Next is the rest of **M16 — the harness**: the tool contract (M16c), the
context firewall (M16d), and verification moved upstream (M16e). The turn state
machine (M16b) is done. That is the moat; M15 exists to make the seam thin
enough that M16 never thinks about audio. The S2S paragraph above is M16e's
brief.

Deferred on purpose, with triggers recorded in the M15a plan: the
`SoundfileMixer` thinking bed, until someone decides what it should sound like.
`bridge.py`'s filler watchdog already covers the silence it would fill, so
nothing is broken while it waits.

**The `settings=X.Settings(...)` migration is done** (2026-08-22), and it was a
subtraction. The spec predicted `factory._build` plus a `settings_cls` column;
in fact pipecat 1.7.0 made the field names uniform, so `model_kwarg` and
`voice_kwarg` were deleted and no column was added. Nothing Pyrrhon calls is
deprecated any more — the three warnings still in the test output all come from
inside pipecat.

Earlier entry points (`jarvis.py`, `main.py`) were removed. Do not treat their
git history as the intended design.
