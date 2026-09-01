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

`README.md` is the pitch. `docs/superpowers/VISION.md` is the source of truth
for scope, the two acts in detail, and the verifiable v1 success criteria. Read
it before making any product or scope decision. Note the path: it is under
`docs/`, which is never committed, so a fresh clone does not have it and this
reference dangles there by design.

Dont commit any docs to any brach and docs is commneted out of gitignore deliabertaly so you can read them so dont commits gitignore and docs to any branch.

## Map

Start here rather than by grepping. The layering rule below is the one thing
that must stay true.

| Path | Responsibility |
|---|---|
| `pyrrhon/cli.py` | Argument parsing, picks a channel. |
| `pyrrhon/headless.py` | The non-interactive channel: `--print`, one question, one answer. |
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
| `pyrrhon/evals/strangers.py` | M17's two frozen stranger repos, and how to fetch them. |
| `pyrrhon/core/providers/registry.py` | The LLM provider table. Data only; `BUILTIN_PROVIDERS` and the wizard's menu derive from it. |
| `pyrrhon/core/providers/adapters.py` | The one place `core/` may import pipecat, and only `pipecat.adapters`. Seam only so far. |
| `pyrrhon/voice/registry.py` | The STT/TTS provider table. Data only; imports no Pipecat. |
| `pyrrhon/voice/factory.py` | Generic construction from that table: key checks, lazy import, clean degradation. |
| `pyrrhon/voice/` | Pipecat pipeline: mic, RNNoise, Silero VAD, smart turn, STT, bridge, TTS, barge-in. |
| `pyrrhon/core/agent/loop.py` | The turn: LLM and tools, streaming, error recovery, pre-flight compaction. |
| `pyrrhon/core/agent/policy.py` | The turn state machine proper: the policy table, `TurnState`, and `decide()`. |
| `pyrrhon/core/agent/subagent.py` | The bounded read-only subagent runner, shared by `think_deeper` and `explore`. |
| `pyrrhon/core/tools/explore.py` | The scout: a locating question answered in one round instead of many. |
| `pyrrhon/core/session.py` | History, modes, cancellable turns, latency, `open_session`. |
| `pyrrhon/core/transcript.py` | The saved session: prose only, no coordinates. `/export` and `--resume` read it. |
| `pyrrhon/core/agent/briefing.py` | What the model is told about the session it is in (M18). |
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
3. Both subagent belts are *derived* from that list through `_subagent_belt`,
   so a read-only tool is inherited automatically. If `think_deeper` must not
   have it, add the name to `DEEP_EXCLUDED`; `EXPLORE_EXCLUDED` derives from
   that and names what the scout additionally skips. A tool that dispatches a
   subagent belongs in `DISPATCH_TOOLS` (`core/agent/subagent.py`) as well, or
   depth stops being 1.
4. Add its name to `EXPECTED_BELT` in `tests/test_safety.py`. That belt is a
   reviewed set, so this step is a deliberate checkpoint rather than bookkeeping.
5. Add a spoken filler to `TOOL_FILLERS` in `pyrrhon/voice/bridge.py`. A test
   requires one per belt tool, and no filler may contain a `path:line`.
6. Run `tests/test_tool_schemas.py`. There is nothing to add there — it
   parametrises over whatever `build_agent` registers, which is the point — but
   it is where a schema that disagrees with its own `run()` signature shows up.
   **The signature is the truth and the schema is the copy.** If they disagree,
   fix the schema; if the signature has a default the schema calls required,
   the default is what is wrong, because it turns a correctable
   `ERROR: bad arguments` into whatever the empty value happens to do.

The belt's total schema size is capped by a test: it rides on every tool-bearing
turn, so it is a latency property, not a style one. The ceiling moves with the
belt rather than the newcomer being trimmed to fit under it — see the measured
history in `tests/test_safety.py`, which records what each addition cost and
what it bought. `Tool.schema` seals every arguments object with
`additionalProperties: false`, so a tool cannot forget to; a tool whose schema
genuinely takes free-form extras (an MCP server's own `inputSchema`) says so and
keeps saying so.

If the tool writes anything, add its module to `WRITE_ALLOWLIST` in
`tests/test_safety.py` — a grep-level fence, in the same shape as the
subprocess one, and the same kind of deliberate checkpoint as `EXPECTED_BELT`.

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
uv run python -m pyrrhon.evals.strangers                       # fetch the frozen repos
uv run pyrrhon --print "question" .                            # headless, one turn
uv run pyrrhon --continue .                                    # resume the last session
```

Both eval runners take `--model provider/model` to override the fast slot for
one run, which is what M17 item 5 needs (the same set against a weaker model)
and what makes a two-model comparison possible without editing config between
runs. The grounding runner **exits non-zero and refuses to certify a score
when any turn ended in `stop_reason=error`** — see M17 below for why that is a
check rather than a note in a plan.

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

Everything through M21 is implemented and tested, bar the measurements named
under "Planned next" — M16a–M16e (the harness), M17 (the evidence apparatus),
M18 (the opening context), M19 (the session as a product) and M21 (Act 2 to
parity). M20 is deliberately not started; see the note at the end of M21's
section for why. The parts worth knowing about before you change them:

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

One thing found while doing this was left to the session layer and is **fixed
as of 2026-09-01**, along with a worse one beside it. `_start_turn`'s defensive
path could not start a replacement turn: it cancels its predecessor and calls
`abort_current_turn()`, and *nothing that cancels a turn awaits it*, so
`Session._run_turn_events` saw `_current` merely cancelled and raised
`RuntimeError: A turn is already running`. A transcription racing ahead of its
own interruption is a normal thing for a person to do, and it answered their
second sentence with a stack trace.

A turn has **three** states, not two: absent, live, and winding down. The third
was missing. A predecessor with `cancelling()` set is now awaited through
`asyncio.wait` — unbounded on purpose, since its only remaining work is a
`put_nowait` in a finally plus whatever `asyncio.to_thread` is already running,
and that thread has to finish before a replacement starts anyway or two turns
mutate `history` at once. `asyncio.wait` rather than `await current`, which
would re-raise the predecessor's `CancelledError` as though it were ours.

The worse one was `_current` doing double duty. It means "the live turn", which
is what `abort_current_turn` needs, and the generator's `finally` was also
reading it to cancel *its own* producer. Once a replacement overwrites it, a
superseded generator finalized afterwards cancels the turn that replaced it and
rolls back its history — the corpse killing its successor. The producer is held
in a local now. Both are pinned by tests in `tests/test_session.py`.

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

**How far up the ladder a caller may go is a `mode`, and the pre-flight gets
`FIT_CHEAP` — rung 2 only.** `FIT_FULL` is every rung, still gated on the
estimate, and is what `Session._compact` runs in dead time; `FIT_FORCED` is
every rung with the estimate ignored, for the safety net after a refusal. One
mode rather than two booleans, because of the four combinations only three mean
anything: "ignore the estimate but stop early" is a contradiction, since the
estimate is the only reason to stop early.

Both rungs above the cheap one were promoted into the pre-flight by M16b's plan
and both had to come back out, for the same reason stated twice.

Rung 4 costs a round trip, which M10 moved off the critical path on purpose —
it used to sit in front of the first token of every over-budget turn, and
`test_the_turn_itself_makes_no_summarize_call` pins that. The plan puts it back
*and* describes the loop as compacting "locally and cheaply"; those cannot both
hold.

Rung 3 costs the current turn's own evidence, which is worse and was invisible
until Task 7 ran against a real account. That account's learned ceiling is 8000
tokens, so `request_budget` allowed the history 2012 against a system prompt of
~1250: the history was over budget from round one and never came back under, so
rung 3 fired on **every** round, stripped every tool result but the most recent,
and still left the request over budget. The model lost what it had just read and
re-read it. Rung 2's last-user boundary is precisely that protection, and rung 3
ignores it by design because it was written as *recovery* compaction — that is
what makes it wrong as a routine rung and right behind a refusal.

`Session._compact` therefore runs the whole ladder rather than
`maybe_summarize` alone, and that is the necessary other half rather than a
tidy-up: with the pre-flight at rung 2, dead time is the only place rung 3 runs
outside a provider refusal, and without it a session on a small ceiling piles up
bulky results that nothing elides until the provider says no. The cost is that
`_cancel_compaction` can now land with tool results elided rather than with
history byte-identical; elision is idempotent, grounding-neutral (the ledger is
separate from history), and exactly what the next refusal would have done.

`Agent.request_budget` nets the belt's schemas off the top, because they ride
the same request as the history and a budget that ignored them would aim the
compactor at a number the request was always going to exceed. `Session.
_schedule_compaction` reads the **same** function: it used to budget against
`context_budget_tokens`, a looser number, which would schedule a round trip to
fix a history the turn had just fitted.

Watch the size of what `request_budget` returns on a small-ceiling account
before assuming a compaction bug is a compaction bug. 8000 minus
`CONTEXT_RESERVE_TOKENS` minus ~1.9k of schema leaves ~2k for history, which is
less than the system prompt plus one tool result. Nothing in the ladder can fix
a budget smaller than the irreducible prompt, and a rung that keeps firing to no
effect is the symptom.

`MAX_CONTEXT_RECOVERIES` is **1**. The `ContextLengthExceededError` handler is
what its name says now: reaching it means the *estimate* was wrong, not that
nothing was tried, so it runs the same ladder once with `force=True` — every
rung, regardless of what the estimate says — and then degrades honestly.

**Task 7 ran live on 2026-08-30 and found the rung-3 defect above** — see the
plan's "Runtime verification" section for the record, including why the
3/6-vs-5/6 eval comparison is confounded and must not be read as an improvement.
Two things from it are worth carrying. `TurnTrace.stop_reason` paid for itself
within the hour: a 3/6 run that looked like a regression was six turns with
`stop_reason=error`, `rounds=1`, `tool_calls=0` — dead turns from a spent
allowance, which the score alone hid completely. And M16a's rate-limit path is
verified live as a side effect: a spent allowance ends a turn with "it should
clear in about 952 seconds" rather than a wait.

The account has **two** ceilings and they fail differently, which cost an hour
to work out. `x-ratelimit-limit-tokens` advertises 8 000 tokens per minute and
is what M16a learns from; a 200 000-tokens-per-day budget appears in no header
and only in a 429 body. Once the daily one is spent a small request still
succeeds while a belt-bearing one does not, which reads exactly like a
per-minute bucket about to refill and is not. Read the 429 body before
diagnosing from request size.

The policy numbers are still first guesses. The signal for tuning them is
`Stop(reason="rounds")` in real traces rather than intuition, and that needs
allowance rather than code. M16a's handoff — trimming a sealed partial back to its last
complete sentence so the resume seam cannot duplicate a clause — is **declined**,
not deferred: on the streaming path that clause was already spoken, and
`_seal_partial`'s "history records what was heard" invariant is what barge-in
truncation and the transcript's honesty both rest on. One duplicated clause is
not worth trading it for. The seam belongs to M16e's move of verification
upstream, or to a wording change in `RESUME_INSTRUCTION`, whichever a live
listen argues for.

**The tool contract (M16c, 2026-08-30).** Three changes, each closing a way the
belt was easy to misuse or expensive to use.

**The schema and the signature are held together by a test, not by review.**
`tests/test_tool_schemas.py` parametrises over whatever `build_agent` registers
and asserts three things: the schema promises no parameter `run()` refuses,
every `required` key is a parameter without a default, and every parameter
without a default is declared required. `run_parameters` reads the signature off
a **bound** method, so `self` disappears without being stripped by name. The
whole belt held exactly one disagreement and it pointed the *opposite* way from
`repo_map`'s: `read_image`'s schema required `path` and `question` while `run()`
defaulted both to `""`, so a call with no path reached `_load("")` and came back
"not an image I can read" — a confident diagnosis of the wrong problem. The
defaults are gone.

`additionalProperties: false` is set in `Tool.schema`, not on each tool, because
it is true of every one of them and a per-tool opt-in is twenty places to forget
it once. **`setdefault` semantics, not an override**: an MCP server owns its own
`inputSchema`, so a remote tool that really does take free-form extras keeps
saying so. `repo_map`'s description says it takes none, because empty
`properties` alone reads as "nothing worth describing" — the schema stops the
call, the description stops the *attempt*, and an attempt costs a round even
when it is rejected.

**`ToolGuard.clip` files an oversized result instead of dropping its tail.**
`core/tools/results.py` writes the whole thing under `<repo>/.pyrrhon/results/`
and puts a pointer on the end of the head, and `read_result` reads on from it.
Context costs exactly what it cost before, because the head is the same head;
what is new is that the model can *see* how much it is missing. Four things
about it are load-bearing. Every path comes from the store's own counter and
`page` resolves an id through the in-memory index first, so `../../etc/passwd`
is an unknown id rather than a traversal — `WRITE_ALLOWLIST` in
`tests/test_safety.py` is the fence, in the same shape as the subprocess one.
The Agent reads the store **off the belt the turn was actually offered**, so a
turn that withholds the pager is never handed a pointer it cannot follow. A
`read_result` page is recorded as evidence for the call that *produced* it
(`results.attribute`), because the ledger's branches are keyed by tool name and
its `read_file` branch reads `path` out of the arguments — a page carries an id
instead, so recording it under `read_result` would silently drop every line in
the window. And the aggregate cap degrades to **truncation**, today's behaviour,
so the worst case is no worse than now. The deep subagent keeps truncating for
the same reason it keeps no store: a pager there could only follow pointers the
fast loop minted.

`PAGE_CHARS` is 7000 against a per-call cap of 8000, and the gap is load-bearing
rather than arbitrary: **a page comes back through `clip` like any other tool
result**, so a page sized at the cap is persisted itself and the model pages
through pages. `guards.py` imports `results.py`, so the two constants cannot see
each other and the relationship is pinned by a test. The store also writes its
own `.gitignore` containing `*`, because `.pyrrhon/` is deliberately not ignored
— `memory.md` and `trusted` are meant to be committable — and derived session
scratch has no business in a user's `git status`.

Nothing in either channel closes a session, so `ResultStore.close()` is a
courtesy the crash path never pays. The store sweeps sibling directories older
than a day on its first write instead — an age check rather than a liveness one,
because a store younger than that may be a second Pyrrhon on the same repo right
now.

**A re-read costs nothing, and the ledger is what says so.** `read_file` trims
the requested window against `EvidenceLedger.covered(path)` and
`ToolGuard.duplicate_note` refuses a range already contained in one. That method
returns the **note** rather than a bool, because a flag cannot say why a call was
skipped: the caller reached for the only note it had and told the model "you
already called read_file with exactly these arguments" about a call whose
arguments were new and whose lines were not. Both ask what was **displayed**, never
what was previously *asked for*: `read_file` clamps at `MAX_READ_LINES`, so a
call for 1-1000 displayed 1-400, and an argument-based check would then refuse
401-600 as a repeat of lines nobody ever saw. Three details are decisions rather
than mechanics. The trim is **edges only, never an interior hole** — `grep`
records the single lines it matched, and carving those out of the middle would
split a window in two, when a hit shown out of context is not the same as having
read around it. `read_file` and `git_blame` spell the span identically and mean
different things by it, since `-L n,n` blames one line while `read_file` with
only a start reads to EOF, so `_requested_range` keeps them apart. And the deep
subagent gets its **own** `ReadFileTool` instance, the one exception to the two
belts sharing instances: the accessor is bound to the fast loop's ledger, whose
contents the subagent's history does not contain, and "already shown this turn"
about lines it cannot see is strictly worse than showing them twice.

The tool reaches the ledger through one narrow callable that `build_agent`
patches in, the same shape `RepoMapTool` uses for `mentions` and for the same
reason: a tool must not hold a back-reference to the thing that calls it.

**The context firewall (M16d, 2026-08-30).** `escalate.py` had run a bounded
read-only subagent in a fresh context since M4, with its own belt and a compact
cited report handed back. That is a context firewall wearing the name
`think_deeper`, so the milestone extracted the loop rather than inventing a
second mechanism. `core/agent/subagent.py` is the runner; `escalate.py` keeps
what was genuinely deep-specific (which prompt, how question and notes compose
into one task, the round budget) and `core/tools/explore.py` is a second,
cheaper caller of the same thing.

**`explore` takes the FAST slot and `think_deeper` the deep one, and that is
the whole reason there are two tools rather than one with a flag.** Locating is
search; routing it to the deep model makes every exploratory question pay
escalation latency, which is the cost the tool exists to avoid. Its belt is
narrower than the deep one on latency grounds rather than safety ones —
`read_image` is the slowest thing on the belt, and git history answers a
question that is not "where does this live".

Measured on this repo: a five-call investigation across `bridge.py`, `loop.py`
and `renderer.py` costs the parent's history 6990 tokens carried raw, and 1011
in the **worst case the code permits** (a report at the hard cap). 5979 tokens
saved against a schema that costs 186 per tool-bearing turn, so one dispatch
pays for roughly thirty-two turns of carrying it. The live half — whether a
real model reaches for it at the right moment — is still owed.

Four things about it are load-bearing.

**Depth is structurally 1, enforced in two places that fail differently.**
`subagent.check_depth` refuses either dispatcher on either subagent belt at
construction, and `DEEP_EXCLUDED` (which `EXPLORE_EXCLUDED` derives from) stops
`build_agent` from ever assembling one that would be refused. A test asserts
both directions from the assembled agent. `DISPATCH_TOOLS` names the pair once,
so a third dispatcher cannot arrive without someone deciding what depth means
for it.

**The report is bounded in code, and the bound has a floor as well as a
ceiling.** `MAX_REPORT_CHARS = 4000` against a per-call cap of 8000, and the
gap is the same relationship M16c pinned for `PAGE_CHARS`: a report comes back
through the parent's `ToolGuard.clip` like any other tool result, so one sized
at the cap would be persisted to the result store and the model would page
through a summary of a summary. `explore.py` and `guards.py` cannot see each
other's constants, so a test holds them apart. A prompt asking for 200 words is
a request; this is the contract.

**Citations survive the firewall through a SECOND evidence bucket, not a shared
ledger.** This is the part that looks like a style choice until it bites. The
subagent verifies `loop.py:431` with its own `read_file` and reports it; the
parent's ledger never saw that read, so under `require_provenance` the gate
strips a citation that *was* verified — a firewall that makes grounding worse.
The obvious fix, and what M16d's plan recommended, is to have the runner record
into a ledger the caller supplies. **Do not do that.** Since M16c the ledger
has two consumers asking opposite questions of it: `GroundingGate.check` via
`observed()` wants the subagent's evidence, while `ToolGuard.duplicate_note`
and `ReadFileTool._seen` via `covered()` must not have it, because the parent
was handed a report and not the source. One bucket answers both the same way
and the parent then skips a `read_file` for lines it was never shown — the
exact hazard `bootstrap.py` gives each subagent its own `ReadFileTool` to
avoid. So `EvidenceLedger.absorb` folds into `_elsewhere`: `observed()` reads
both buckets, `covered()` reads only what this context displayed, and
`fingerprint()` reads both, because a round that spent itself on one `explore`
call and came back with three new locations is the most productive round a turn
can have. The gate's own `LINE_UNSEEN_HEDGE` says "this session", not "this
context", which is what makes the split honest rather than convenient.

A report contributes no *mined* evidence — `explore` and `think_deeper` sit in
`_REPORTED` and `record_tool_result` returns early for both. Their citations
are provenance already, with a real tool result behind each; mining the prose
as well would license every location the subagent merely guessed, which is the
same reasoning as `read_image`'s branch and the same conclusion.

**`SubagentProgress` reaches a channel by callback, and that is forced rather
than chosen.** The runner is awaited inside a tool call and an async generator
cannot yield through one; buffering and yielding after the round would deliver
the whole burst at the moment it stops being worth anything. It rides
`Agent.on_progress`, wired by each channel to its renderer — the shape
`orient_in_background` already uses, which is the one precedent for rendering
from outside the message pump. It is still an `Event` through the one dispatch
table, so a channel that shows nothing says so once. The TUI updates the
`ToolRow` it already has (hence `TurnView.peek_tool`, since `claim_tool` pops
and a dispatch reports several rounds before it resolves), and
`ToolRow.progress` drops a report that lands after the call resolved: the
callback path makes that race possible, and overwriting a result with news
about how it was reached is worse than showing nothing.

`explore` stays on the spoken belt. It is bounded harder than `think_deeper` on
both axes a spoken turn cares about — six rounds of the fast model against
twelve of the deep one, half the tool-char budget — and ships a filler like the
other three. The open question `policy.py` records for `think_deeper` applies
to it unchanged: a dispatch is one tool call, so it fits inside one round and
the round cap does not bound it.

**Verification upstream (M16e, 2026-08-31).** The gate is unchanged, and
deliberately so: `require_provenance` is still privileged and still off, and
nothing here relaxes a check. What changed is the model's *reasons* to trip
one, in three places.

**The prompt states admissibility as a rule.** "Use your tools to LOOK before
you cite" is guidance a model satisfies by having looked at some point. The
rule is that a location may be stated only if a tool result in **this turn**
showed it — which is exactly what the evidence ledger already encodes by being
rebuilt fresh each turn, and the prompt was the half that had not caught up.
Recollection is inadmissible because the file may have changed and a stale
in-range line passes every check the gate makes.

**The counterweight ships in the same block and is not decoration.** The
central risk of the milestone is a model told never to state an unread location
retreating into hedging everything, which reads as honesty and is a loss. So
the rule bounds citations, not confidence. `tests/test_prompt_policy.py` pins
the *direction* of both halves rather than their wording, the same precedent
M16b set for the land-nudge.

The tool-use section grows the policy M16b, M16c and M16d each need the model
to know, argued rather than listed, because the surrounding section earns its
length by explaining why: search before read, read the range the search pointed
at, past about three files dispatch `explore`, a truncated result is paged
rather than re-run, and `repo_map` takes no arguments.

**Three ways a location was still reaching history outside a tool result, all
three reachable, one of them carrying a fabricated path.** The test was written
before the fix and the list is the finding.

The narration beside a tool call was gated for *speech* and recorded raw:
`assistant_tool_message` took `reply.text`, so a coordinate the model invented
came back to it a round later reading as something the conversation had
established. It takes the gated text now; a subagent runner with no gate passes
nothing and is unchanged.

A streamed answer that gates away to nothing fell back to `reply.text`, which
nothing had gated. Reachable whenever the answer is little more than its
citation. The fallback is gated now and its citations are **dropped rather than
re-emitted**, because if deltas arrived `_stream_round` already emitted them and
a second copy is a duplicate row.

`SUMMARY_PROMPT` demanded every `path:line` be kept EXACTLY. That was right
while history was the model's source of code facts and it is precisely wrong
now: a summary outlives the tool result that justified it, and a stale in-range
line is indistinguishable from a right one. The file name is the part that
stays true and costs one read to re-anchor. The prompt says why and
`citations.strip_line_numbers` makes it an invariant rather than a request — an
instruction the model may ignore is not an invariant.
`tests/test_history_invariant.py` exempts exactly two roles and the exemption
is the point: a `tool` message IS the admissible source, and a `user` message
is the user's own words.

**`GateCounters` exists because M16e is judged by a number the gate was not
keeping.** The three-way sort was already there; the tally of exactly one arm
(`last_unseen`) was, so "the intervention rate fell" was unfalsifiable before it
was asserted. A record rather than three ints, because the arms are one
taxonomy: a change that moves `stripped` down and `hedged` up has improved
nothing, and only reading them together says so. The eval prints the rate and
the raw arms on one line for the same reason, plus mean tool calls and mean
rounds per turn — mean, not median, since one question ground out over six
rounds is the failure the tool policy exists to prevent and a median hides it.
The eval also grew a `history` key per case, because the one probe this rule is
most about — a follow-up the model could answer from recollection — was
inexpressible against empty history.

**Measured 2026-09-01 against a working Groq account, and the criterion turned
out to be unmeasurable.** The eval was run twice on the fixture set with
`prompts.py` as the only variable: once with the file at `f7032e2` (the
pre-M16e prompt) and once at HEAD. Model `openai/gpt-oss-120b`, n=8 per arm,
one run each.

| | before (pre-M16e prompt) | after (HEAD) |
|---|---|---|
| cases passed | 6/8 | **7/8** |
| gate intervention rate | **0/15 checks (0.0%)** | **0/26 checks (0.0%)** |
| citations promoted | 2 | **3** |
| hedged / stripped | 0 / 0 | 0 / 0 |
| mean rounds per turn | 5.12 | **4.12** |
| mean tool calls per turn | 3.88 | **3.38** |
| `Stop(reason="rounds")` | 2 | **0** |
| turns ending in `error` | 1 | **0** |

**The intervention rate was already 0.0% before the change, so it cannot
fall.** That is the finding, and it is not the same as M16e failing. The gate
promoted every reference the model produced and hedged or stripped none, in
both arms. A criterion phrased as "the rate falls substantially" has no
headroom to fall through, so this eval can neither confirm nor refute it.

An attempt was made to find headroom where it should exist. The self set's five
fabrication-class cases name a REAL file and ask about something not in it, so
a guess lands on a plausible in-range line and the path verifies — the one
shape the fixture set cannot produce. The before arm intervened **0/5** as
well, with the model correctly declining rather than guessing. **Both bait arms
are void and must not be quoted**: four of five before-turns and all five
after-turns ended in `stop_reason=error`, and 5/5 "passed" is exactly the
`must_not_cite: "*"` confound. Read `stop_reason` before reading any score.

**What did move is M16c's and M16d's target, not M16e's own.** A fifth of the
rounds went away on a THREE-FILE repo, both cases that had been hitting the
round cap now finish under it, and accuracy and citation count both went up
rather than down — so assumption 3 did not bite. The counterweight held: 26
gate checks against 15 means the answers got *longer*, not vaguer.

**S2S therefore stays blocked, and the reason is now specific.** The plan is
explicit that a rate which does not fall leaves the block in place, and that
stands. But the honest reading is not "verification upstream did not work" — it
is that this model, on this eval, was never tripping the gate, so the eval
cannot tell whether the gate is load-bearing. Settling the S2S question needs
cases where a model actually fabricates. A null across thirteen baseline cases
is weak evidence at best, and it is not grounds for removing a check that costs
1-2ms.

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

**The evidence apparatus (M17, 2026-09-02).** Every number above it was
measured on `tests/fixtures/sample_repo` (three files) or on Pyrrhon itself,
where the author's knowledge contaminates every judgment about whether an
answer was good. VISION's four criteria are about a repo neither party wrote,
and none of them had ever been run on one.

`pyrrhon/evals/strangers.py` freezes two: `encode/httpx` at `b5addb64`
(Python, ~18k lines) and `spf13/cobra` at `adbc8813` (Go, ~17k). Seventeen
cases each, in `evals/strangers/`. **Freezing the SHA is not a detail** — a
case says `httpx/_client.py:971`, so a run against a moving `main` compares
today's model against yesterday's line numbers and calls the difference a
regression. Every later comparison in the roadmap measures against the
baseline these two produce.

Cobra is the harder half deliberately: one flat package of very large files,
so a question is answered by finding the right *function* and never by finding
the right directory, which is exactly the query lexical search is worst at.
M20 is gated on what that reports.

**Every line number was read out of the frozen tree with grep and not one came
from asking Pyrrhon.** A set built by recording what the agent said measures
only whether the agent is consistent with itself, which it always is. That is
the one part of this that cannot be automated later.

Three things the sets deliberately do NOT contain, written down in
`evals/strangers/README.md` rather than left to be rediscovered. Questions
whose honest answer is "httpx does not implement this, it delegates at
`_transports/default.py:165`" are absent, because the runner scores citations
and not prose: the right answer and a confident wrong one cite the same line.
Three otherwise-obvious baits are absent for the mirror reason — `yaml` and
`plugin` against cobra, response caching against httpx — because in each case
one real line carries the word, and a model that found it would be scored as
fabricating when it had found the only evidence there is.

**The S2S criterion is restated, and the restatement is a check rather than a
sentence.** M16e's version was "the intervention rate falls substantially", and
it was 0.0% in both arms, so it had no headroom to fall through — discovered
only after a day's token budget had gone, and only by reading two reports side
by side. `grounding.measurability_note` says it at the moment it prints the
number: a zero rate over enough checks means the run cannot tell whether the
gate is load-bearing, and therefore cannot support a before/after claim about
the rate either. Too few checks is a different statement and gets a different
line. A comparison needs a non-zero arm, which is what five fabrication baits
per stranger repo exist to produce. If no available model fabricates even
there, that is a finding rather than a failed run, and it argues the gate is
cheap insurance rather than load-bearing — unblocking S2S on different grounds
than the plan expected.

**`grounding.dead_turn_warning` closes the confound that has now bitten three
times.** A set of `must_not_cite: "*"` cases passes PERFECTLY when nothing
answers at all, so a total outage scores like a partial success. M16e's pass
read a 3/6 as a regression when it was six turns of `stop_reason=error`, and
quoted a 5/5 from a bait arm where every turn had died. Both times the plan
already said "read `stop_reason` before reading any score". Guidance that fails
on its own terms twice is a check nobody wrote yet, so it is one now: it prints
above the score, and the run exits non-zero, so a green CI line cannot certify
an outage. Verified against a real 402 on 2026-09-02 — the runner reported 4/8,
said the 4 was not a measurement, and exited 1.

**The opening context (M18, 2026-09-02).** `bootstrap.orient_in_background`
rendered `build_orientation` as a `ScreenArtifact` for the USER and gave the
model one line, the repo root path. So every session opened with the human
looking at a ranked census of the repo and the model looking at nothing, and
round one went on re-deriving what was already drawn on screen.

`core/agent/briefing.py` renders two blocks. The environment is the half a
model cannot infer — today's date most of all, because "what changed last
week" is unanswerable without it and a model with no date answers from its
training cutoff and sounds certain doing so. The brief is the repo map,
bounded to `MAX_BRIEF_CHARS` and stripped.

Four things about it are load-bearing.

**The brief carries no coordinate, and that is enforced rather than
requested.** A system prompt outlives the read that justified a line number in
exactly the way M16e's compaction summaries did, so `strip_line_numbers` runs
over it — plus a second pass for the map's own `name:line` symbol rows, which
carry no extension and are therefore invisible to the citation regex AND to
the gate. A line number the gate cannot see is the one worth removing here.

**Unknown renders as absent, never as a default.** `capture_git_state` answers
`(None, None)` for a directory that is not a repo, and the renderer then says
nothing about the branch. "Clean working tree" invented from a failed
subprocess is a claim the model repeats and the user cannot check.

**The block is appended after the delivery style, never folded into
`system_prompt`.** `system_prompt` is the prefix a provider caches and the
style block already varies with `/voice`, so everything that changes within a
session sits on one side of that boundary instead of splitting the prefix into
a cache family per session state.

**Both halves ride the existing orientation task** — one index walk, two
consumers, where until now only one existed. The model's copy therefore lands
on whatever turn follows rather than on turn one, which is the trade the
screen brief has always made.

The prompt itself gained four things `tests/test_prompt_policy.py` pins by
direction: a worked exemplar (four lines of transcript, where three paragraphs
had described the same shape), the capability statement (the belt has no
editor and nothing said so, so the model could offer a change it had no way to
make), the three failure-recovery cases (an empty grep means the thing may be
NAMED differently, not that it is absent), and a dispatch table. `TEXT_STYLE`
gained the thread — "offer the next hop, one at a time" lived only in
`VOICE_STYLE`, so the podcast quality was voice-only by accident — and a soft
ceiling, since "you can be thorough" with nothing behind it invites a survey.

**The risk M18 runs is the mirror of M16e's**: a prompt carrying a repo map can
make a model stop looking and cite from the map. The counterweight ships in the
same block, the invariant test is the guard, and a rise in the gate's
`stripped` arm is the symptom. **That measurement is owed** and blocked on the
same thing everything else is.

**The session as a product (M19, 2026-09-02).** `history` died with the
process, for a product whose use case is a week-long onboarding. `--continue`
and `--resume <id>` bring one back; `/clear`, `/compact`, `/cost`, `/export`,
`/covered` and `/sessions` are the controls.

**Only the prose is persisted, and that is the design rather than a shortcut.**
A saved assistant message is gated prose, which M16e already strips of
coordinates on the way into history and which `transcript._prose` strips again
on the way out of the file — so a resumed session physically cannot cite from
what it remembers and has to reopen the file. That is M16e's admissibility rule
enforced by the shape of the data instead of by an instruction the model may
ignore, and it is why this was safe to build now and would not have been
before. It is also what makes the file worth reading, which is what `/export`
hands over.

**The log is not a projection of history and the divergence is deliberate.**
Compaction summarizes early turns away; the log keeps them. What was said and
what the model currently holds are different things.

**A turn is written at the START of the next one.** Barge-in truncation reaches
`Session` after the turn's generator has finished, so a record written at the
end of its own turn preserves words the user cut off — and the transcript must
never be the one artifact claiming Pyrrhon said something it was stopped from
saying. `_flush_transcript` re-reads the answer off history for the same
reason.

`/compact` is the only place `FIT_FULL` runs on demand, because the turn's own
pre-flight is capped at rung 2 for the reasons M16b paid to learn; it reports a
measurement rather than a reassurance, since the complaint was invisibility.
`/cost` finally spends the counts that rode every response since M15b and were
discarded one line after `token_scale` took the ratio out of them — requests
beside tokens, because a per-minute REQUEST ceiling blocks a session with
plenty of token allowance left, and it says out loud that a daily budget
appears in no header at all. `/covered` is the questions rather than a summary
of the conclusions: a summary needs an LLM call and goes stale against a repo
that moved, where the questions stay true and are what someone scans for
"where was I". It rides the resume notice rather than waiting to be asked for.

`open_session` lives in `session.py` rather than in each channel for the reason
`start_channel` exists: two copies of an ordered startup sequence diverge
quietly, and the divergence reads as a bug in one channel rather than a missing
edit.

**Act 2 to parity (M21, 2026-09-02).** `DESIGN_PROMPT` never said whether the
user was designing INTO the open repo or from nothing — a distinction that
changes every question after the first. It now names it and puts it before the
first question: an extension is looked at before it is interrogated, because
the constraints that matter most are already on disk. The counterweight ships
beside it, since a rule that only says "look at the repo" makes a model force a
connection to code that has nothing to do with what is being built.

`evals/design.yaml` went from five cases to ten, and the runner grew the two
keys it needed. `history` lets a case be a later turn: "the reasoning is
established, so write the spec" is by definition not turn one, so until now the
only measurable behaviour was refusing to write one, and a model that
challenges everything and produces nothing scored 5/5. `must_look` asserts the
open repo was read before a design that extends it — any read tool counts,
because the check is whether it looked and not which door it used, and
`write_spec` is excluded because writing the answer is not reading the
constraints.

**Ops.** `pyrrhon --print` is the headless channel: the answer to stdout and
nothing else, progress to stderr and suppressed when stderr is not a terminal,
`--json` for citations plus the turn's trace. The report is written once at the
end rather than streamed, because a partial answer on stdout is worse than none
for a caller that will act on it. The trust gate refuses instead of prompting,
since a headless run from an interactive shell would otherwise stop dead on a
consent prompt nobody is watching.

`.github/workflows/live-smoke.yml` runs one grounded question against one real
provider nightly. It asserts on the CITATION rather than the prose: a provider
returning a polite paragraph and no tool call is exactly the break it exists to
find, and no check that only asks whether an answer came back can see it. A
missing key skips rather than fails, and the key is declared at job level
because a step's `if` reads the env context as it stood *before* the step.

**The 3.14 cap is still earned, checked 2026-09-02.** `requires-python` is
`>=3.12,<3.14` because `pyaudio` compiles against `portaudio.h` and ships no
wheel for 3.14, so a clean install on 3.14 dies in a C compiler rather than in
our code. PyPI still has `pyaudio` at 0.2.14 with wheels for cp38 through
cp313 and nothing newer. Re-check the wheel tags rather than the version — a
release that adds cp314 may not bump the minor — and lift the cap when one
appears.

**M20 (retrieval) is deliberately NOT started**, and the reason is the roadmap's
own sequencing rather than appetite. Lexical search is the wrong tool for
"where is the thing I can't name", and the two cheap candidates — symbol-name
fuzzy matching over the existing tree-sitter index, and a ranked docstring and
comment pass — are cheap enough to be tempting. Building either before M17 has
reported where the aim is actually bad is optimising against a fixture, which
is the discipline M16b applied to its own policy numbers. Cobra exists in the
stranger set precisely to produce that report.

Gemini Live speech-to-speech is **still parked**, and M16e did not unpark it.
The constraint's real shape is unchanged — gate-*before*-speech is what blocks
S2S, not verification itself — and M16e built the mechanism that was meant to
remove the reason for it: tool results as the only admissible source of code
facts, stated as a rule in the prompt and closed off in history. What is
missing is the evidence.

**M17 restated the criterion, because M16e's version was unmeasurable.** "The
intervention rate falls substantially" presumes a non-zero starting rate and
the rate was 0.0% in both arms. The restatement: the gate must intervene on a
set where a model demonstrably fabricates, so a non-zero baseline is required
before any before/after comparison is quoted — and `measurability_note` now
says so at the moment the runner prints the number, rather than leaving it to
whoever reads two reports side by side. If no available model fabricates even
on the stranger repos, that is the finding, and it argues the gate is cheap
insurance rather than a load-bearing component, which unblocks S2S on
different grounds than the plan expected.

Neither number has been taken, because as of 2026-09-02 both configured
provider keys are dead — Groq 401 on a truncated key, Cerebras 402 on a spent
account. The mechanism landing is not the criterion being met. Until the
numbers exist the block stands, and it is not a licence to weaken the gate.

**Planned next. M16a–M16e, M17, M18, M19 and M21 are code-complete. What is
missing is measurement, and as of 2026-09-02 that is blocked on CREDENTIALS
rather than on code, budget, or design.** Spec:
`docs/superpowers/specs/2026-08-29-pyrrhon-m16-agent-harness-design.md`; the
plans are `m16a` through `m16e` plus `2026-09-01-pyrrhon-post-m16-roadmap.md`
in `docs/superpowers/plans/`.

**Both configured providers are dead, and they fail differently — check which
before assuming a budget problem.** Verified 2026-09-02 by running the fixture
grounding eval against each:

* **Groq** returns `401 Invalid API Key`. The stored key in
  `~/.pyrrhon/credentials.toml` is **18 characters**, and a real `gsk_` key is
  around 56, so it is truncated rather than expired. This is a *different*
  failure from the daily-budget exhaustion recorded on 2026-09-01 and it will
  not clear on its own. Re-paste the key with `pyrrhon --setup`.
* **Cerebras** returns `402 Payment required to access this resource`. The key
  is well-formed and `models.list()` succeeds against it, offering
  `gpt-oss-120b` and `gemma-4-31b` — so the account authenticates and simply
  has no quota. A working `models.list()` is not evidence of a usable account,
  which is worth knowing because `preconnect()` uses exactly that call.

Everything below runs the moment one key works. The apparatus is built and
`--model provider/model` means none of it needs a config edit between runs.

The owed passes, in the order one sitting should take them:

1. **The M17 baseline.** `uv run python -m pyrrhon.evals.strangers`, then the
   grounding eval against each stranger repo. This is the number every later
   comparison is measured against, so it comes first and nothing is changed
   between it and the runs that follow.
2. **VISION's four criteria by hand** on both stranger repos, in both
   channels, transcripts written down verbatim including where it was wrong.
   Criterion 3 is the one to bait hardest.
3. **M18's own measurement**, `prompts.py` and `briefing.py` as the only
   variables, against the M17 set: mean rounds, mean tool calls, citation
   count, `stop_reason` distribution, accuracy. The exit condition is that
   first-round tool calls re-deriving repo shape drop measurably while
   accuracy holds. **Watch `stripped`** — a rise is the symptom of the risk
   M18 runs, which is a model citing from the map instead of looking.
4. **The four M16 passes.** M16b's policy-number tuning (`Stop(reason="rounds")`
   in real traces is the signal) and both channels driven by hand; M16c's
   transcript replay confirming no read exceeds what the preceding search
   pointed at, plus the comparison confirming re-read suppression costs the
   gate no citation; M16d's multi-file question through both channels with
   `/debug-history` showing one `explore` result rather than a dozen tool
   results; M16e's remaining half, both channels by hand against code that
   does not exist and a question spanning several files.
5. **The S2S criterion, restated.** Run the fabrication baits against a weaker
   model — `--model` exists for this — and require a non-zero baseline
   intervention rate before any before/after comparison is quoted. A null
   across both stranger repos is itself the finding.
6. **M16a's listen test**, which needs a human rather than a key:
   `uv run pyrrhon --voice .` with `[model] max_tokens` set low, confirming no
   fragment is spoken as though it were finished.

Two rules for reading any of it, both paid for. **Read `stop_reason` before
reading any score** — the runner now refuses to certify a run with dead turns,
but the discipline still applies to anything you read by eye. And **read the
429 body before diagnosing from request size**, because the per-minute headers
read FULL when the daily budget is spent.

One known intermittent, observed several times on 2026-09-02 and never
reproducible in isolation: `tests/test_tui_voice_turns.py::test_a_late_turn_finished_cannot_close_the_turn_that_replaced_it`
fails, and `tests/test_adapter_driver.py` reports two teardown ERRORs, in some
full-suite runs on Windows and not others. Both pass alone and both pass on a
re-run of the identical command, which places them with the asyncio
proactor-teardown noise the suite already produces. Not chased, on M17's own
"collect, do not repair" rule; the likely cause in the first case is a single
`await pilot.pause()` where the deferred voice hook needs two.

Two things the 2026-09-01 run confirmed live as a side effect, both worth more
than the milestone they came from. M16a's 429 path declined a `retry-after` of
**471 seconds** outright rather than clamping it, and said "it should clear in
about 471 seconds" — the design decision that waiting and then reporting
failure is strictly worse than reporting now, verified a second time. And
M16b's context-overflow safety net fired for the first time in a real session,
logging `context overflow: safety net reached rung 'summarize'` and recovering.

The two-ceilings trap bit again and in a nastier form than the M16b record
describes. When the daily budget is spent, the 429 arrives with
`x-ratelimit-remaining-tokens: 8000` and `x-ratelimit-remaining-requests: 980`
— both per-minute buckets reading FULL — while a small request still succeeds
and a belt-bearing one does not. The headers cannot be used to tell whether the
account has budget. Read the 429 body.

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

**M16 — the harness** is code-complete. The provider boundary (M16a), the turn
state machine (M16b), the tool contract (M16c), the context firewall (M16d)
and verification upstream (M16e) are all built and tested. That is the moat;
M15 exists to make the seam thin enough that M16 never thinks about audio.
What is not done is the evidence. M16e's eval pass ran on 2026-09-01: the tool
policy is **proven** (a fifth of the rounds gone, `Stop(reason="rounds")` from
two to zero, accuracy up), and its own criterion is **unmeasurable**, because
the gate's intervention rate was already 0.0% before the change. Three runtime
passes remain, plus driving both channels by hand, and as of 2026-09-02 they
wait on a working key rather than on allowance.

**Post-M16 (M17-M21) closed everything on the roadmap that code could close,
and the shape of what is left changed with it.** The gap used to be "the
harness is built and unproven"; it is now "the harness is built, the apparatus
to prove it is built, and no provider will answer". Two stranger repos are
frozen with 34 human-derived cases, the eval runners take `--model` and refuse
to certify a run whose turns died, the model finally gets the repo map the user
always got, a session survives the night, Act 2 knows a repo is open, and there
is a headless channel and a nightly live smoke. Not one of those is a
measurement, which is the honest summary: M17 built the instrument and did not
get to take the reading.

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
