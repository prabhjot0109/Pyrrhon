# Pyrrhon M3 — Voice: Pipecat Pipeline, Barge-in, TruncateSpeech, Turn Cancellation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Interface drift warning:** written before M0–M2 landed. Before executing, revalidate every Consumes signature against the actual codebase and update this plan if drifted. Pipecat APIs especially: re-verify against the installed version.

**Goal:** Talk to Pyrrhon out loud and interrupt it mid-sentence. A Pipecat pipeline (local mic/speakers → Silero VAD → Groq Whisper STT → agent core bridge → OpenAI TTS) drives the same headless core the REPL and TUI use. Barge-in cancels the in-flight turn (`Session.abort_current_turn`), and history is rewritten to exactly what the user heard (`TruncateSpeech`). This is the milestone where VISION.md success criteria 1–3 become testable.

**Architecture:** A new `pyrrhon/core/session.py` owns conversation state and turn lifecycle — every channel (REPL, TUI, voice) now holds a `Session` instead of a raw history list. `pyrrhon/voice/` is a thin Pipecat channel: a custom `PyrrhonBridgeProcessor` sits between STT and TTS, feeding final transcriptions into `Session.run_turn`, pushing `SpeechChunk` text to TTS as `TextFrame`s, and forwarding `ScreenArtifact`/`Citation` events to a TUI callback. On Pipecat's `InterruptionFrame` (VAD-detected barge-in) it aborts the turn and reconciles history via a playback-position estimate. Hard rule unchanged: `core/` imports nothing from `voice/`, `tui/`, `repl.py`, or `commands/` — and nothing from `pipecat`.

**Tech Stack:** Python ≥ 3.12, uv, pipecat-ai with `local` (PyAudio), `silero`, `groq`, `openai` extras, pydantic v2, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-03-pyrrhon-v1-design.md` — the "Voice layer (Pipecat)", "Real-time discipline", event-contract (`TruncateSpeech`), and split-path grounding sections are binding here.

## Verified Pipecat API (researched 2026-07-03 via Context7, `/pipecat-ai/pipecat` + `docs.pipecat.ai`)

Do not code from memory; these were verified against current Pipecat docs. Re-verify each against the **installed** version before use (`uv run python -c "import pipecat; print(pipecat.__version__)"` and read the installed sources under `.venv`):

- **Local transport:** `LocalAudioTransport(LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True))` (verified in `pipecat/examples/getting-started/01a-local-audio.py` and `06a-voice-agent-local.py`). `LocalAudioTransportParams` subclasses `TransportParams`, which accepts `vad_analyzer`. Import path in current releases: `pipecat.transports.local.audio`. Requires the `local` extra (PyAudio; wheels exist for CPython 3.12 on Windows).
- **VAD:** `SileroVADAnalyzer(sample_rate: int | None = None, params: VADParams | None = None)` from `pipecat.audio.vad.silero`; `VADParams(confidence=..., start_secs=..., stop_secs=...)` from `pipecat.audio.vad.vad_analyzer`. Sample rate must be 8000 or 16000 Hz.
- **STT:** `GroqSTTService` from `pipecat.services.groq.stt` — Groq Whisper; `api_key` param or `GROQ_API_KEY` env; model via `settings=GroqSTTService.Settings(model=...)` (bare `model=` kwarg deprecated since 0.0.105).
- **TTS:** `OpenAITTSService` from `pipecat.services.openai.tts`; configured via `settings=OpenAITTSService.Settings(...)` (voice, instructions, speed). **OpenAI TTS provides no word-level timestamps**, so the plan implements the spec's duration-based played-text estimator. Pipecat's word-timestamp facility (`TTSService.add_word_timestamps` → `TTSTextFrame`s emitted in strict playback order) exists for services that support it (Cartesia, ElevenLabs, Hume) — the `PlaybackObserver` below consumes `TTSTextFrame` so swapping in such a service upgrades truncation accuracy for free.
- **Interruption / barge-in:** current releases push **`InterruptionFrame`** (a `SystemFrame`) through the pipeline "to interrupt the pipeline… when a user starts speaking to cancel any in-progress bot output. It can also be pushed by any processor." Older releases (≤ ~0.0.6x) named this `StartInterruptionFrame` / `StopInterruptionFrame` — check the installed `pipecat.frames.frames` and import whichever exists. Related frames: `UserStartedSpeakingFrame`, `UserStoppedSpeakingFrame`, `VADUserStartedSpeakingFrame`. Any processor may call `broadcast_interruption()`. Turn-start strategies (`VADUserTurnStartStrategy(enable_interruptions=True)`, default True) gate interruption in the newest API; classic releases gate it with `PipelineParams(allow_interruptions=True)`.
- **Custom processor:** subclass `pipecat.processors.frame_processor.FrameProcessor`; override `async def process_frame(self, frame: Frame, direction: FrameDirection)`, always calling `await super().process_frame(frame, direction)` first; forward with `await self.push_frame(frame, direction)` (default direction `FrameDirection.DOWNSTREAM`). STT services emit `TranscriptionFrame` (final; fields `text`, `user_id`, `timestamp`) and `InterimTranscriptionFrame` (partial). TTS services synthesize incoming `TextFrame`s (sentence-aggregated between `LLMFullResponseStartFrame`/`LLMFullResponseEndFrame`) and handle interruption frames themselves (flush).
- **Running:** stable releases: `PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))` + `PipelineRunner(handle_sigint=False)` — `handle_sigint=False` is required on Windows (docs-main examples: `handle_sigint=False if sys.platform == "win32" else True`). Docs-main (2.0 track) renames these `PipelineWorker` / `WorkerRunner` (`await runner.add_workers(worker)`, `await runner.run()`). This plan codes against `PipelineTask`/`PipelineRunner`; if the installed version only has the worker API, adapt Task 6 accordingly.
- **Install:** `uv add "pipecat-ai[local,silero,groq,openai]"` (verified extras syntax `uv add "pipecat-ai[option,...]"` from the Pipecat README; the `local` extra is real — Pipecat's own dev setup excludes it with `--no-extra local`).

## Assumed-from-M1/M2 interfaces (treat as given; revalidate per the drift warning)

- `Agent.__init__(llm, tools, system_prompt, repo_root, max_tool_rounds=8, grounding_gate: GroundingGate | None = None, allow_retry: bool = True)`; the kwarg is stored as the attribute `agent.allow_retry`.
- `GroundingGate.check(text) -> GroundedText(speech_text, citations, unverified)` (M1) — not touched here; the speech path only disables the *retry loop*, never verification.
- Command registry (M1): `pyrrhon/commands/registry.py` exposing `dispatch(line: str, ctx) -> str | None` and a `@command(name, help=...)` decorator registering `async def handler(args: str, ctx) -> str`. `ctx` carries at least `session` and `settings`; M3 adds `ctx.voice: VoiceController | None`.
- TUI (M2): `PyrrhonApp(repo_root, agent)` in `pyrrhon/tui/app.py`, holding `self.history: list[dict]` and calling `self.agent.run_turn(self.history, text)` somewhere in its turn handler.

## Global Constraints

(Copied verbatim from the M0 plan; still binding.)

- Python `>=3.12` (`pyproject.toml`, `.python-version`); dependency management via `uv` only (`uv add`, `uv sync`, `uv run`).
- Hard rule: **`pyrrhon/core/` must never import from `pyrrhon/repl.py`, `pyrrhon/tui/`, `pyrrhon/voice/`, or `pyrrhon/commands/`.**
- Tests run with `uv run pytest` (single test: `uv run pytest path::test_name`).
- All file reads/writes use `encoding="utf-8"`; repo-relative paths are displayed POSIX-style (`utils/helpers.py`), including on Windows.
- Tools return **error strings** (prefixed `ERROR:`) instead of raising, so the LLM can read and recover from failures.
- **Real-time discipline (spec hard rule):** no sync filesystem/CPU work inline in an `async def` anywhere in `core/` — wrap it in `asyncio.to_thread()`. Voice arrives in M3 and a ~100ms event-loop stall becomes an audible audio glitch; tools written blocking now would have to be rewritten then.
- No grounding *verification* in M0 (that is M1); M0 only extracts citations for files that exist.
- Commit after every task (green tests only).

M3 additions:

- The "no grounding verification in M0" bullet above is historical: M1's gate landed. M3 must not weaken it — the speech path skips only the **retry loop** (`allow_retry=False`, split-path policy), never verification itself.
- **`pipecat` may only be imported inside `pyrrhon/voice/`.** Verify at the end: `grep -rn "pipecat" pyrrhon/core/ pyrrhon/tui/ pyrrhon/commands/ pyrrhon/repl.py pyrrhon/cli.py` returns nothing (the `commands/voice_cmd.py` handler talks to `VoiceController` via `ctx`, not to pipecat).
- **Voice failures never crash the app.** Every audio/pipecat failure surfaces as `VoiceUnavailableError` with a clear message; the text channel keeps working.
- **No audio hardware in CI.** Unit tests never open a mic or speaker; the full audio loop is a documented manual smoke test (Task 7).

## File Structure (delta over M0–M2)

```text
pyrrhon/
├── cli.py                    # MODIFIED: --voice flag
├── repl.py                   # MODIFIED: holds a Session instead of a raw history list
├── commands/
│   ├── debug_cmd.py          # NEW: /debug-history dev command
│   └── voice_cmd.py          # NEW: /voice on|off
├── config/
│   └── settings.py           # MODIFIED: VoiceSettings (stt_model, tts_voice, chars_per_sec)
├── core/
│   ├── events.py             # MODIFIED: + TruncateSpeech (the one channel→core event)
│   └── session.py            # NEW: Session — cancellable turns, history truncation
├── tui/
│   └── app.py                # MODIFIED (M2 file): holds a Session; voice wiring
└── voice/
    ├── __init__.py           # NEW: VoiceController; re-exports run_voice
    ├── playback.py           # NEW: PlaybackTracker — played-text position estimator
    ├── bridge.py             # NEW: PyrrhonBridgeProcessor + PlaybackObserver
    └── pipeline.py           # NEW: run_voice — Pipecat pipeline assembly

tests/
├── test_events_truncate.py   # NEW
├── test_session.py           # NEW
├── test_channels_session.py  # NEW: REPL holds Session; /debug-history formatting
├── test_playback.py          # NEW
├── test_voice_bridge.py      # NEW
└── test_voice_pipeline.py    # NEW: degradation, speech-path policy, controller toggle
```

---

### Task 1: `TruncateSpeech` joins the event contract

**Files:**
- Modify: `pyrrhon/core/events.py`
- Test: `tests/test_events_truncate.py`

**Interfaces:**
- Consumes: existing `pyrrhon.core.events` module (M0 Task 4).
- Produces: `@dataclass(frozen=True) TruncateSpeech(played_text: str)` in `pyrrhon.core.events`, added to the `Event` union. Documented as the one reverse-direction (channel → core) event.

- [ ] **Step 1: Write the failing test**

`tests/test_events_truncate.py`:

```python
import dataclasses
import typing

import pytest

from pyrrhon.core.events import Event, TruncateSpeech


def test_truncate_speech_is_a_frozen_value():
    event = TruncateSpeech(played_text="Auth starts in the middleware")
    assert event.played_text == "Auth starts in the middleware"
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.played_text = "something else"


def test_truncate_speech_is_part_of_the_event_union():
    assert TruncateSpeech in typing.get_args(Event)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_events_truncate.py -v`
Expected: FAIL with `ImportError: cannot import name 'TruncateSpeech'`

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/core/events.py`, add after the `AskUser` dataclass:

```python
@dataclass(frozen=True)
class TruncateSpeech:
    """The one reverse-direction event (channel → core).

    Emitted by the voice layer on barge-in. `played_text` is the prose the
    user actually heard before interrupting — word-level playback timestamps
    where the TTS service provides them, a duration-based estimate otherwise.
    The session rewrites the last assistant message to exactly this text:
    history never assumes knowledge of unspoken words.
    """

    played_text: str
```

and extend the union at the bottom of the file:

```python
Event = (
    SpeechChunk
    | ScreenArtifact
    | Citation
    | ToolCallStarted
    | ToolCallFinished
    | AskUser
    | TruncateSpeech
)
```

(If M1 added further members to the union — e.g. a grounding-warning event — keep them; only *add* `TruncateSpeech`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_events_truncate.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/events.py tests/test_events_truncate.py
git commit -m "feat: TruncateSpeech event — the channel-to-core barge-in contract"
```

---

### Task 2: `Session` — cancellable turns and speech truncation

**Files:**
- Create: `pyrrhon/core/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `Agent` with `async run_turn(history: list[dict], user_text: str) -> AsyncIterator[Event]` that mutates `history` in place (M0 Task 8; M1 added `grounding_gate`/`allow_retry` kwargs); `Tool` ABC (M0 Task 5); `FakeLLM` (M0 Task 4, tests only).
- Produces (pinned):
  - `class Session`: `__init__(self, agent: Agent)`; attributes `history: list[dict]`, `mode: str = "understand"`, `_current: asyncio.Task | None`
  - `async def run_turn(self, user_text: str) -> AsyncIterator[Event]` — wraps `agent.run_turn` in a cancellable asyncio task + queue
  - `def abort_current_turn(self) -> None` — cancels the in-flight turn immediately (in-flight tool calls cancelled); a cancelled turn appends **nothing further** to history, and trailing messages from the aborted turn that would corrupt the chat API contract (assistant `tool_calls` without results, orphaned tool results) are rolled back
  - `def truncate_last_assistant(self, played_text: str) -> None` — rewrites the last assistant message content to exactly `played_text + " …[interrupted]"` (only if the last message is a plain assistant text message)
  - `INTERRUPTED_MARKER = " …[interrupted]"` module constant

- [ ] **Step 1: Write the failing test**

`tests/test_session.py`:

```python
import asyncio
from pathlib import Path

import pytest

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import SpeechChunk
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.session import INTERRUPTED_MARKER, Session
from pyrrhon.core.tools.base import Tool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class SlowEchoTool(Tool):
    """A scripted tool that hangs so tests can cancel it mid-flight."""

    name = "slow_echo"
    description = "Test tool: waits a long time, then answers."
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self):
        self.started = asyncio.Event()
        self.completed = False

    async def run(self, **kwargs) -> str:
        self.started.set()
        await asyncio.sleep(30)
        self.completed = True
        return "slow result"


def make_session(replies, tools) -> tuple[Session, FakeLLM]:
    fake = FakeLLM(replies)
    agent = Agent(
        llm=fake,
        tools=tools,
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
    )
    return Session(agent), fake


async def test_normal_turn_streams_events_and_grows_history():
    session, _ = make_session([LLMReply(text="It prints a greeting.")], tools=[])
    assert session.mode == "understand"
    events = [event async for event in session.run_turn("what does app.py do?")]
    assert SpeechChunk(text="It prints a greeting.") in events
    assert [m["role"] for m in session.history] == ["system", "user", "assistant"]


async def test_abort_cancels_in_flight_tool_and_appends_nothing_further():
    slow = SlowEchoTool()
    replies = [
        LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),)),
        LLMReply(text="never reached"),
    ]
    session, _ = make_session(replies, tools=[slow])

    events: list = []

    async def consume():
        async for event in session.run_turn("take your time"):
            events.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(slow.started.wait(), timeout=2)
    session.abort_current_turn()
    await asyncio.wait_for(consumer, timeout=2)

    # Give any (wrongly) surviving work a chance to run, then assert nothing landed.
    for _ in range(5):
        await asyncio.sleep(0)
    assert slow.completed is False  # the in-flight tool call was cancelled
    # The dangling assistant tool_calls message was rolled back; the aborted
    # turn appended nothing further after the user message.
    assert [m["role"] for m in session.history] == ["system", "user"]


async def test_abort_when_idle_is_a_noop():
    session, _ = make_session([LLMReply(text="hi")], tools=[])
    session.abort_current_turn()  # nothing running — must not raise
    events = [event async for event in session.run_turn("hello")]
    assert events  # session still usable after the no-op abort


async def test_second_turn_works_after_abort():
    slow = SlowEchoTool()
    replies = [
        LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),)),
        LLMReply(text="answer to the second question"),
    ]
    session, _ = make_session(replies, tools=[slow])

    consumer = asyncio.create_task(
        asyncio.wait_for(_drain(session.run_turn("first")), timeout=2)
    )
    await asyncio.wait_for(slow.started.wait(), timeout=2)
    session.abort_current_turn()
    await consumer

    events = [event async for event in session.run_turn("second")]
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == "answer to the second question"
    assert session.history[-1]["content"] == "answer to the second question"


async def test_run_turn_while_running_raises():
    slow = SlowEchoTool()
    replies = [LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),))]
    session, _ = make_session(replies, tools=[slow])
    consumer = asyncio.create_task(_drain(session.run_turn("first")))
    await asyncio.wait_for(slow.started.wait(), timeout=2)
    with pytest.raises(RuntimeError, match="already running"):
        async for _ in session.run_turn("second"):
            pass
    session.abort_current_turn()
    await asyncio.wait_for(consumer, timeout=2)


async def test_truncate_last_assistant_rewrites_content():
    session, _ = make_session([LLMReply(text="alpha beta gamma delta")], tools=[])
    async for _ in session.run_turn("talk"):
        pass
    session.truncate_last_assistant("alpha beta")
    assert session.history[-1]["content"] == "alpha beta" + INTERRUPTED_MARKER


async def test_truncate_is_noop_when_last_message_is_not_assistant_text():
    session, _ = make_session([], tools=[])
    session.history[:] = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    session.truncate_last_assistant("heard")
    assert session.history[-1] == {"role": "user", "content": "u"}


async def _drain(aiter) -> None:
    async for _ in aiter:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.session'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/core/session.py`:

```python
"""Conversation state + turn lifecycle, shared by every channel (REPL, TUI, voice).

Real-time discipline (spec hard rules):
- Turns are cancellable: `abort_current_turn()` cancels the asyncio task
  running the reasoning loop — including in-flight tool calls — the moment
  a channel asks (e.g. VAD detects barge-in). A cancelled turn appends
  nothing further to history; late results are discarded.
- History records what was heard, not what was generated: on barge-in the
  voice channel reports played text via TruncateSpeech and the session
  truncates the last assistant message accordingly.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Event

INTERRUPTED_MARKER = " …[interrupted]"

_TURN_DONE = object()


class Session:
    """Owns the conversation history; wraps Agent.run_turn in a cancellable task."""

    def __init__(self, agent: Agent):
        self.agent = agent
        self.history: list[dict] = []
        self.mode: str = "understand"
        self._current: asyncio.Task | None = None

    async def run_turn(self, user_text: str) -> AsyncIterator[Event]:
        """Run one turn, streaming events. The agent runs in its own task so
        `abort_current_turn()` can cancel it while a channel is consuming."""
        if self._current is not None and not self._current.done():
            raise RuntimeError(
                "A turn is already running; call abort_current_turn() first."
            )

        queue: asyncio.Queue = asyncio.Queue()

        async def _produce() -> None:
            try:
                async for event in self.agent.run_turn(self.history, user_text):
                    queue.put_nowait(event)
            finally:
                # Runs on normal completion AND on cancellation.
                # put_nowait never suspends, so it is safe in a cancelled task.
                queue.put_nowait(_TURN_DONE)

        self._current = asyncio.create_task(_produce())
        try:
            while True:
                item = await queue.get()
                if item is _TURN_DONE:
                    return
                yield item
        finally:
            # Consumer went away early (generator closed / consuming task
            # cancelled): never leave the agent running headless.
            if not self._current.done():
                self._current.cancel()
                self._repair_history()

    def abort_current_turn(self) -> None:
        """Cancel the in-flight turn. Safe to call when idle.

        After `task.cancel()` the producer raises CancelledError at its
        current await point (llm.chat / tool.run) and never executes another
        statement of the agent loop — so nothing further is appended to
        history and late tool results are discarded, per spec.
        """
        task = self._current
        if task is None or task.done():
            return
        task.cancel()
        self._repair_history()

    def truncate_last_assistant(self, played_text: str) -> None:
        """Rewrite the last assistant message to exactly what the user heard.

        Called by the voice channel on barge-in (TruncateSpeech). No-op unless
        the last history message is a plain assistant text message — history
        never assumes knowledge of unspoken words.
        """
        if not self.history:
            return
        last = self.history[-1]
        if last.get("role") != "assistant":
            return
        if last.get("tool_calls") or not isinstance(last.get("content"), str):
            return
        last["content"] = played_text + INTERRUPTED_MARKER

    def _repair_history(self) -> None:
        """Roll back trailing messages of an aborted turn that would corrupt
        the chat API contract: an assistant tool_calls message whose tool
        results never (fully) arrived, or orphaned tool results. History ends
        on the last complete plain message (system/user/assistant text)."""
        while self.history:
            last = self.history[-1]
            if last.get("role") == "tool":
                self.history.pop()
            elif last.get("role") == "assistant" and last.get("tool_calls"):
                self.history.pop()
            else:
                break
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the whole suite (guard against regressions)**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/session.py tests/test_session.py
git commit -m "feat: Session with cancellable turns and speech truncation"
```

---

### Task 3: REPL and TUI hold a `Session`; `/debug-history` dev command

**Files:**
- Modify: `pyrrhon/repl.py`, `pyrrhon/tui/app.py` (M2 file — revalidate exact shape)
- Create: `pyrrhon/commands/debug_cmd.py`
- Test: `tests/test_channels_session.py`

**Interfaces:**
- Consumes: `Session` (Task 2); `build_agent(repo_root, llm=None)` (M0 Task 9); `PyrrhonApp(repo_root, agent)` (M2, assumed); command registry `@command` + `dispatch(line, ctx)` (M1, assumed — `ctx.session: Session`).
- Produces:
  - `repl._turn(session: Session, user: str, console: Console) -> None` (replaces the `(agent, history, user, console)` signature); `run_repl` builds one `Session`
  - `PyrrhonApp` stores `self.session: Session` instead of `self.agent` + `self.history`; convenience property `agent` delegating to `self.session.agent`
  - `debug_cmd.format_history(history: list[dict]) -> str` (pure, unit-tested) and the registered `/debug-history` command returning it for `ctx.session.history`

- [ ] **Step 1: Write the failing test**

`tests/test_channels_session.py`:

```python
import io
from pathlib import Path

from rich.console import Console

from pyrrhon.commands.debug_cmd import format_history
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.session import Session
from pyrrhon.repl import _turn, build_agent
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


async def test_repl_turn_consumes_a_session():
    fake = FakeLLM([LLMReply(text="greet lives at utils/helpers.py:1.")])
    session = Session(build_agent(FIXTURE, llm=fake))
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False)

    await _turn(session, "where is greet defined?", console)

    out = buffer.getvalue()
    assert "greet lives at" in out
    assert session.history[-1]["role"] == "assistant"


def test_format_history_shows_roles_and_previews():
    history = [
        {"role": "system", "content": "You are Pyrrhon."},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "    1| def greet..."},
        {"role": "assistant", "content": "line one\nline two"},
    ]
    out = format_history(history)
    assert "[0] system: You are Pyrrhon." in out
    assert "[2] assistant: <tool calls: read_file>" in out
    assert "line one\\nline two" in out  # newlines escaped, one row per message


def test_format_history_empty():
    assert format_history([]) == "(history empty)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_channels_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pyrrhon.commands.debug_cmd'` (and `_turn` signature mismatch once that resolves)

- [ ] **Step 3: Refactor the REPL (exact diff against the M0 `pyrrhon/repl.py`)**

```diff
 from pyrrhon.core.agent.loop import Agent
 from pyrrhon.core.agent.soul import build_system_prompt
 from pyrrhon.core.events import Citation, SpeechChunk, ToolCallStarted
 from pyrrhon.core.providers.llm import MissingAPIKeyError, create_llm
+from pyrrhon.core.session import Session
 from pyrrhon.core.tools.repo import GlobTool, GrepTool, ReadFileTool
@@ def run_repl(repo: str) -> None:
     console.print(
         f"[bold]Pyrrhon[/bold] — discussing [cyan]{repo_root.name}[/cyan]. "
         "Commands: /init (personalize), /quit"
     )
-    history: list[dict] = []
+    session = Session(agent)
     while True:
         try:
             user = console.input("[bold cyan]you> [/bold cyan]").strip()
         except (EOFError, KeyboardInterrupt):
             break
         if not user:
             continue
         if user in {"/quit", "/exit"}:
             break
         if user == "/init":
             path, created = init_pyrrhon_dir(repo_root)
             verb = "created" if created else "already exists"
             console.print(f"soul file {verb}: {path} — edit it, then restart the session.")
             continue
-        asyncio.run(_turn(agent, history, user, console))
+        asyncio.run(_turn(session, user, console))


-async def _turn(agent: Agent, history: list[dict], user: str, console: Console) -> None:
-    async for event in agent.run_turn(history, user):
+async def _turn(session: Session, user: str, console: Console) -> None:
+    async for event in session.run_turn(user):
         if isinstance(event, ToolCallStarted):
             console.print(f"[dim]→ {event.name}({event.args})[/dim]")
         elif isinstance(event, SpeechChunk):
             console.print(Markdown(event.text))
         elif isinstance(event, Citation):
             console.print(f"[green]📍 {event.file}:{event.line}[/green]")
```

If M1's command `dispatch()` already replaced the inline `/init` branch, keep the dispatch call and only apply the `Session` changes — the `ctx` handed to `dispatch` must now carry `session` (and `voice=None` for the REPL, see Task 7).

- [ ] **Step 4: Refactor the TUI (expected diff against M2's `pyrrhon/tui/app.py` — revalidate names against the real file before applying)**

```diff
+from pyrrhon.core.session import Session
+
 class PyrrhonApp(App):
     def __init__(self, repo_root: Path, agent: Agent) -> None:
         super().__init__()
         self.repo_root = repo_root
-        self.agent = agent
-        self.history: list[dict] = []
+        self.session = Session(agent)
+
+    @property
+    def agent(self) -> Agent:
+        # Anything in the M2 app that referenced self.agent keeps working.
+        return self.session.agent
@@ wherever the M2 app runs a turn (worker/handler):
-        async for event in self.agent.run_turn(self.history, text):
+        async for event in self.session.run_turn(text):
             self._render_event(event)
```

Every other `self.history` reference in the M2 app becomes `self.session.history`. The command `ctx` the TUI builds gains `session=self.session`.

- [ ] **Step 5: Add the `/debug-history` dev command**

`pyrrhon/commands/debug_cmd.py`:

```python
"""/debug-history — dev command: dump the session history one row per message.

Exists for M3's manual barge-in smoke test: after interrupting Pyrrhon
mid-sentence you run /debug-history and confirm the last assistant message
is exactly the played prefix plus the " …[interrupted]" marker.
"""

from __future__ import annotations

from pyrrhon.commands.registry import command

_PREVIEW = 120


def format_history(history: list[dict]) -> str:
    """Pure formatter (unit-tested): one line per message, newlines escaped."""
    if not history:
        return "(history empty)"
    lines: list[str] = []
    for index, message in enumerate(history):
        role = message.get("role", "?")
        content = message.get("content")
        if content is None and message.get("tool_calls"):
            names = ", ".join(
                tc["function"]["name"] for tc in message["tool_calls"]
            )
            content = f"<tool calls: {names}>"
        text = str(content).replace("\n", "\\n")
        if len(text) > _PREVIEW:
            text = text[: _PREVIEW - 3] + "..."
        lines.append(f"[{index}] {role}: {text}")
    return "\n".join(lines)


@command("debug-history", help="Dev: dump the session history (roles + content)")
async def debug_history_cmd(args: str, ctx) -> str:
    return format_history(ctx.session.history)
```

(The `@command` decorator and `ctx.session` follow the assumed M1 registry contract — adjust the two decorated lines to the real registry API if it drifted; `format_history` stays as-is.)

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_channels_session.py -v`
Expected: 3 passed

- [ ] **Step 7: Run the whole suite (the M2 TUI tests must still pass after the refactor)**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add pyrrhon/repl.py pyrrhon/tui pyrrhon/commands/debug_cmd.py tests/test_channels_session.py
git commit -m "refactor: REPL and TUI hold a Session; add /debug-history dev command"
```

---

### Task 4: Voice dependencies, voice settings, `PlaybackTracker`

**Files:**
- Modify: `pyproject.toml` (via `uv add`), `pyrrhon/config/settings.py`
- Create: `pyrrhon/voice/__init__.py` (empty for now; filled in Task 6), `pyrrhon/voice/playback.py`
- Test: `tests/test_playback.py`

**Interfaces:**
- Consumes: `Settings` (M0 Task 2).
- Produces:
  - `VoiceSettings(BaseModel)` with `stt_model: str = "whisper-large-v3-turbo"`, `tts_voice: str = "nova"`, `chars_per_sec: float = 15.0`; `Settings` gains field `voice: VoiceSettings = VoiceSettings()`
  - `PlaybackTracker(chars_per_sec: float = 15.0, clock: Callable[[], float] = time.monotonic)` in `pyrrhon.voice.playback` with `reset()`, `speech_queued(text)`, `playback_started()`, `word_played(word)`, `playback_finished()`, `played_text() -> str` — pure Python, no pipecat imports, fully unit-testable

- [ ] **Step 1: Add the Pipecat dependency (verified extras)**

Run:

```bash
uv add "pipecat-ai[local,silero,groq,openai]"
uv sync
```

Expected: resolves and installs without error (the `local` extra pulls PyAudio — wheels exist for CPython 3.12 on Windows; if PyAudio fails to build on another platform, that platform needs PortAudio headers, but do not block the milestone on it: everything except the live pipeline is testable without it).

Then verify the installed API surface once, and fix this plan's imports if drifted:

```bash
uv run python -c "import pipecat; print(pipecat.__version__)"
uv run python -c "from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams; print('transport ok')"
uv run python -c "from pipecat.frames.frames import InterruptionFrame; print('InterruptionFrame')" || uv run python -c "from pipecat.frames.frames import StartInterruptionFrame; print('StartInterruptionFrame (old name — update voice/bridge.py imports)')"
```

- [ ] **Step 2: Write the failing test**

`tests/test_playback.py`:

```python
from pyrrhon.voice.playback import PlaybackTracker


class FakeClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_nothing_played_before_playback_starts():
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=FakeClock())
    tracker.speech_queued("alpha beta gamma delta")
    assert tracker.played_text() == ""


def test_duration_estimate_cuts_at_a_word_boundary():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("alpha beta gamma delta")  # len 22
    tracker.playback_started()
    clock.now = 1.2  # 12 chars elapsed → inside "gamma" → cut back to "alpha beta"
    assert tracker.played_text() == "alpha beta"


def test_estimate_beyond_text_returns_everything():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("short answer")
    tracker.playback_started()
    clock.now = 60.0
    assert tracker.played_text() == "short answer"


def test_finished_playback_returns_full_text_regardless_of_clock():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("alpha beta")
    tracker.playback_started()
    tracker.playback_finished()
    clock.now = 0.1
    assert tracker.played_text() == "alpha beta"


def test_word_timestamps_beat_the_estimate_when_available():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("alpha beta gamma delta")
    tracker.playback_started()
    tracker.word_played("alpha")
    tracker.word_played("beta")
    tracker.word_played("gamma")
    clock.now = 0.1  # estimate would say almost nothing — timestamps win
    assert tracker.played_text() == "alpha beta gamma"


def test_multiple_speech_chunks_are_joined():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("alpha beta")
    tracker.speech_queued("gamma delta")
    tracker.playback_started()
    clock.now = 1.2
    assert tracker.played_text() == "alpha beta"


def test_reset_clears_everything():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("alpha beta")
    tracker.playback_started()
    tracker.word_played("alpha")
    tracker.reset()
    assert tracker.played_text() == ""
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_playback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.voice.playback'`

- [ ] **Step 4: Write minimal implementation**

Create empty `pyrrhon/voice/__init__.py`, then `pyrrhon/voice/playback.py`:

```python
"""Playback-position tracking: how much of the answer did the user actually hear?

Spec: on barge-in, history is rewritten to the played text. Word-level
playback timestamps are used where the TTS service provides them (Pipecat
emits TTSTextFrame per word in playback order for e.g. Cartesia/ElevenLabs);
OpenAI TTS — the M3 default — provides none, so we fall back to a
duration-based character estimate cut at a word boundary.

Pure Python on purpose: no pipecat imports, fully unit-testable.
"""

from __future__ import annotations

import time
from collections.abc import Callable

# ~180 spoken words/min ≈ 3 words/sec ≈ 15 chars/sec including spaces.
# Deliberately conservative: underestimating what was heard only means the
# rewritten history admits to slightly less than the user heard — safe.
DEFAULT_CHARS_PER_SEC = 15.0


class PlaybackTracker:
    def __init__(
        self,
        chars_per_sec: float = DEFAULT_CHARS_PER_SEC,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._chars_per_sec = chars_per_sec
        self._clock = clock
        self._queued: list[str] = []
        self._played_words: list[str] = []
        self._started_at: float | None = None
        self._finished = False

    def reset(self) -> None:
        self._queued = []
        self._played_words = []
        self._started_at = None
        self._finished = False

    def speech_queued(self, text: str) -> None:
        """Record prose sent to TTS (one SpeechChunk)."""
        stripped = text.strip()
        if stripped:
            self._queued.append(stripped)

    def playback_started(self) -> None:
        """The bot's audio started coming out of the speakers."""
        if self._started_at is None:
            self._started_at = self._clock()
        self._finished = False

    def word_played(self, word: str) -> None:
        """A word-timestamped TTS service confirmed this word was played."""
        stripped = word.strip()
        if stripped:
            self._played_words.append(stripped)

    def playback_finished(self) -> None:
        """The bot finished speaking — everything queued was heard."""
        self._finished = True

    def played_text(self) -> str:
        """Best estimate of the prose the user has heard so far."""
        if self._played_words:
            return " ".join(self._played_words)
        full = " ".join(self._queued)
        if not full:
            return ""
        if self._finished:
            return full
        if self._started_at is None:
            return ""
        elapsed = max(self._clock() - self._started_at, 0.0)
        chars = int(elapsed * self._chars_per_sec)
        if chars >= len(full):
            return full
        boundary = full.rfind(" ", 0, chars + 1)
        if boundary <= 0:
            return full[:chars]
        return full[:boundary]
```

- [ ] **Step 5: Add voice settings**

In `pyrrhon/config/settings.py`, add after `ProviderConfig`:

```python
class VoiceSettings(BaseModel):
    """M3 voice-channel knobs (TOML section [voice])."""

    stt_model: str = "whisper-large-v3-turbo"  # Groq Whisper model
    tts_voice: str = "nova"                    # OpenAI TTS voice
    chars_per_sec: float = 15.0                # played-text estimator rate
```

and on `Settings` add the field:

```python
    voice: VoiceSettings = VoiceSettings()
```

Append to `tests/test_settings.py`:

```python
def test_voice_settings_defaults_and_override(tmp_path: Path):
    settings = load_settings(repo_root=tmp_path, home=tmp_path / "nohome")
    assert settings.voice.tts_voice == "nova"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        '[voice]\ntts_voice = "onyx"\nchars_per_sec = 12.5\n', encoding="utf-8"
    )
    settings = load_settings(repo_root=repo, home=tmp_path / "nohome")
    assert settings.voice.tts_voice == "onyx"
    assert settings.voice.chars_per_sec == 12.5
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_playback.py tests/test_settings.py -v`
Expected: all passed (7 new playback tests + settings suite incl. the new one)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock pyrrhon/voice pyrrhon/config/settings.py tests/test_playback.py tests/test_settings.py
git commit -m "feat: voice deps, voice settings, playback-position tracker"
```

---

### Task 5: `PyrrhonBridgeProcessor` — Pipecat frames ⇄ agent core

**Files:**
- Create: `pyrrhon/voice/bridge.py`
- Test: `tests/test_voice_bridge.py`

**Interfaces:**
- Consumes: `Session` (Task 2), `PlaybackTracker` (Task 4), events (Task 1 + M0), Pipecat: `FrameProcessor`, `FrameDirection` (`pipecat.processors.frame_processor`); frames `InterruptionFrame`, `TranscriptionFrame`, `InterimTranscriptionFrame`, `TextFrame`, `LLMFullResponseStartFrame`, `LLMFullResponseEndFrame`, `BotStartedSpeakingFrame`, `BotStoppedSpeakingFrame`, `TTSTextFrame` (`pipecat.frames.frames` — all names verified against current docs; on older installs `InterruptionFrame` is `StartInterruptionFrame`).
- Produces:
  - `PyrrhonBridgeProcessor(session: Session, *, on_event: Callable[[Event], None] | None = None, tracker: PlaybackTracker | None = None)` — sits between STT and TTS: final transcriptions start `session.run_turn`; `SpeechChunk` text is pushed to TTS; `ScreenArtifact`/`Citation`/`ToolCall*`/`AskUser` events go to `on_event` (the TUI); on interruption frames it calls `session.abort_current_turn()` and `session.truncate_last_assistant(played_text)` and reports `TruncateSpeech` via `on_event`
  - `PlaybackObserver(tracker: PlaybackTracker)` — sits between TTS and the output transport, recording word-timestamped `TTSTextFrame`s into the shared tracker (no-op with OpenAI TTS; upgrades accuracy with timestamp-capable services)

Design note (why `_handle_frame` exists): `FrameProcessor.process_frame` does base-class lifecycle bookkeeping that assumes a linked pipeline that has seen a `StartFrame`. All M3-owned logic therefore lives in `_handle_frame(frame, direction)`, which `process_frame` calls after `super().process_frame(...)`. Unit tests exercise `_handle_frame` directly on a subclass that records `push_frame` calls — no pipeline, no audio.

- [ ] **Step 1: Write the failing test**

`tests/test_voice_bridge.py`:

```python
import asyncio
from pathlib import Path

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Citation, TruncateSpeech
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.session import INTERRUPTED_MARKER, Session
from pyrrhon.voice.bridge import PyrrhonBridgeProcessor
from pyrrhon.voice.playback import PlaybackTracker
from tests.helpers import FakeLLM
from tests.test_playback import FakeClock
from tests.test_session import SlowEchoTool

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"

DOWN = FrameDirection.DOWNSTREAM
UP = FrameDirection.UPSTREAM


class RecordingBridge(PyrrhonBridgeProcessor):
    """Test double: records pushed frames instead of needing a live pipeline."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pushed: list = []

    async def push_frame(self, frame, direction=DOWN):
        self.pushed.append(frame)


def make_bridge(replies, tools=(), clock=None):
    fake = FakeLLM(list(replies))
    agent = Agent(
        llm=fake,
        tools=list(tools),
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
    )
    session = Session(agent)
    seen_events: list = []
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock or FakeClock())
    bridge = RecordingBridge(session, on_event=seen_events.append, tracker=tracker)
    return bridge, session, seen_events


def transcription(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="local", timestamp="2026-07-03T00:00:00Z")


async def test_final_transcription_runs_a_turn_and_pushes_speech_to_tts():
    bridge, session, seen = make_bridge(
        [LLMReply(text="greet is defined at utils/helpers.py:1.")]
    )
    await bridge._handle_frame(transcription("where is greet defined"), DOWN)
    await asyncio.wait_for(bridge._turn_task, timeout=2)

    texts = [f.text for f in bridge.pushed if isinstance(f, TextFrame)]
    assert texts == ["greet is defined at utils/helpers.py:1."]
    kinds = [type(f) for f in bridge.pushed]
    assert kinds.index(LLMFullResponseStartFrame) < kinds.index(TextFrame)
    assert kinds.index(TextFrame) < kinds.index(LLMFullResponseEndFrame)
    # Screen-bound events went to the TUI callback, not to TTS:
    assert Citation(file="utils/helpers.py", line=1) in seen
    assert session.history[-1]["content"] == "greet is defined at utils/helpers.py:1."


async def test_interim_transcriptions_never_start_turns():
    bridge, session, _ = make_bridge([])
    frame = InterimTranscriptionFrame(
        text="where is", user_id="local", timestamp="2026-07-03T00:00:00Z"
    )
    await bridge._handle_frame(frame, DOWN)
    assert bridge._turn_task is None
    assert session.history == []


async def test_interruption_mid_turn_cancels_tool_and_repairs_history():
    slow = SlowEchoTool()
    bridge, session, seen = make_bridge(
        [
            LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),)),
            LLMReply(text="never reached"),
        ],
        tools=[slow],
    )
    await bridge._handle_frame(transcription("take your time"), DOWN)
    await asyncio.wait_for(slow.started.wait(), timeout=2)

    await bridge._handle_frame(InterruptionFrame(), DOWN)
    for _ in range(5):
        await asyncio.sleep(0)

    assert slow.completed is False
    assert [m["role"] for m in session.history] == ["system", "user"]
    truncates = [e for e in seen if isinstance(e, TruncateSpeech)]
    assert truncates == [TruncateSpeech(played_text="")]  # nothing was spoken yet
    # The interruption frame itself was passed through so TTS flushes:
    assert any(isinstance(f, InterruptionFrame) for f in bridge.pushed)


async def test_interruption_during_playback_truncates_to_played_estimate():
    clock = FakeClock(0.0)
    bridge, session, seen = make_bridge(
        [LLMReply(text="alpha beta gamma delta")], clock=clock
    )
    await bridge._handle_frame(transcription("talk to me"), DOWN)
    await asyncio.wait_for(bridge._turn_task, timeout=2)

    await bridge._handle_frame(BotStartedSpeakingFrame(), UP)
    clock.now = 1.2  # 12 chars at 10 chars/sec → "alpha beta"
    await bridge._handle_frame(InterruptionFrame(), DOWN)

    assert session.history[-1]["content"] == "alpha beta" + INTERRUPTED_MARKER
    assert TruncateSpeech(played_text="alpha beta") in seen


async def test_interruption_while_idle_is_ignored():
    bridge, session, seen = make_bridge([LLMReply(text="alpha beta")])
    await bridge._handle_frame(transcription("first question"), DOWN)
    await asyncio.wait_for(bridge._turn_task, timeout=2)
    # Bot finished and is silent. The user simply starts the next turn —
    # Pipecat still emits an interruption frame, but there is nothing to abort
    # and the fully-heard assistant message must NOT be rewritten.
    await bridge._handle_frame(InterruptionFrame(), DOWN)

    assert session.history[-1]["content"] == "alpha beta"
    assert not [e for e in seen if isinstance(e, TruncateSpeech)]
```

Note: `TranscriptionFrame(text=..., user_id=..., timestamp=...)` field names and `InterruptionFrame()` taking no arguments are per current Pipecat docs — if the installed version differs (e.g. positional-only, or `StartInterruptionFrame`), fix the test helpers *and* `bridge.py` imports together.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_voice_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.voice.bridge'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/voice/bridge.py`:

```python
"""The bridge between Pipecat's frame world and Pyrrhon's event world.

Pipeline position: transport.input() → STT → PyrrhonBridgeProcessor → TTS
→ PlaybackObserver → transport.output().

Downstream through the bridge: final TranscriptionFrames (consumed — they
become agent turns) and interruption frames (acted on, then passed through
so the TTS/output flush). Upstream through the bridge: Bot*SpeakingFrames
from the output transport (observed for playback timing, passed through).

Barge-in (spec, real-time discipline): on an interruption frame we cancel
the in-flight turn (Session.abort_current_turn — in-flight tool calls die),
compute how much prose was actually played (PlaybackTracker), rewrite the
last assistant message to exactly that (Session.truncate_last_assistant),
and report TruncateSpeech to the screen channel.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from pyrrhon.core.events import Event, SpeechChunk, TruncateSpeech
from pyrrhon.core.session import Session
from pyrrhon.voice.playback import PlaybackTracker


class PyrrhonBridgeProcessor(FrameProcessor):
    def __init__(
        self,
        session: Session,
        *,
        on_event: Callable[[Event], None] | None = None,
        tracker: PlaybackTracker | None = None,
    ):
        super().__init__()
        self._session = session
        self._on_event: Callable[[Event], None] = on_event or (lambda event: None)
        self.tracker = tracker or PlaybackTracker()
        self._turn_task: asyncio.Task | None = None
        self._bot_speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self._handle_frame(frame, direction)

    async def _handle_frame(self, frame: Frame, direction: FrameDirection):
        """All M3 logic lives here so unit tests can drive it without a
        linked pipeline (base process_frame does lifecycle bookkeeping)."""
        if isinstance(frame, InterruptionFrame):
            await self._on_interruption()
            await self.push_frame(frame, direction)  # let TTS/output flush
        elif isinstance(frame, TranscriptionFrame):
            self._start_turn(frame.text)  # consumed: the utterance IS the turn
        elif isinstance(frame, InterimTranscriptionFrame):
            pass  # partials never start turns
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self.tracker.playback_started()
            await self.push_frame(frame, direction)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self.tracker.playback_finished()
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    def _start_turn(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._turn_task is not None and not self._turn_task.done():
            # Defensive: a transcription raced ahead of its interruption frame.
            self._turn_task.cancel()
            self._session.abort_current_turn()
        self.tracker.reset()
        self._turn_task = asyncio.create_task(self._run_turn(text))

    async def _run_turn(self, text: str) -> None:
        await self.push_frame(LLMFullResponseStartFrame())
        async for event in self._session.run_turn(text):
            if isinstance(event, SpeechChunk):
                # Speakable prose → TTS. Everything else is screen-bound.
                self.tracker.speech_queued(event.text)
                await self.push_frame(TextFrame(event.text))
            else:
                self._on_event(event)
        await self.push_frame(LLMFullResponseEndFrame())

    async def _on_interruption(self) -> None:
        turn_running = self._turn_task is not None and not self._turn_task.done()
        if not (turn_running or self._bot_speaking):
            # Bot idle, nothing in flight: the user is just starting an
            # ordinary next turn. Never rewrite a fully-heard answer.
            return
        if turn_running:
            self._turn_task.cancel()
        self._session.abort_current_turn()
        played = self.tracker.played_text()
        self._session.truncate_last_assistant(played)
        self._on_event(TruncateSpeech(played_text=played))
        self._bot_speaking = False
        self.tracker.reset()


class PlaybackObserver(FrameProcessor):
    """Between TTS and transport.output(): records word-timestamped playback.

    OpenAI TTS emits no TTSTextFrames, so with the M3 default stack this
    records nothing and the tracker's duration estimate is used. Dropping in
    a word-timestamp-capable TTS (Cartesia/ElevenLabs/Hume) makes truncation
    word-accurate with zero further changes.
    """

    def __init__(self, tracker: PlaybackTracker):
        super().__init__()
        self._tracker = tracker

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame):
            self._tracker.word_played(frame.text)
        await self.push_frame(frame, direction)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_voice_bridge.py -v`
Expected: 5 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/voice/bridge.py tests/test_voice_bridge.py
git commit -m "feat: PyrrhonBridgeProcessor bridging Pipecat frames to the agent core"
```

---

### Task 6: `run_voice` — pipeline assembly, split-path policy, graceful degradation

**Files:**
- Create: `pyrrhon/voice/pipeline.py`
- Modify: `pyrrhon/voice/__init__.py` (VoiceController + re-exports)
- Test: `tests/test_voice_pipeline.py`

**Interfaces:**
- Consumes: `Session` (Task 2), `Settings`/`VoiceSettings` (Task 4), `PyrrhonBridgeProcessor`/`PlaybackObserver` (Task 5); Pipecat (verified, re-check installed): `LocalAudioTransport`/`LocalAudioTransportParams` (`pipecat.transports.local.audio`), `SileroVADAnalyzer` (`pipecat.audio.vad.silero`), `VADParams` (`pipecat.audio.vad.vad_analyzer`), `GroqSTTService` (`pipecat.services.groq.stt`), `OpenAITTSService` (`pipecat.services.openai.tts`), `Pipeline` (`pipecat.pipeline.pipeline`), `PipelineParams`/`PipelineTask` (`pipecat.pipeline.task`), `PipelineRunner` (`pipecat.pipeline.runner`).
- Produces:
  - `async def run_voice(session: Session, settings: Settings) -> None` (pinned; plus keyword-only optional `on_event: Callable[[Event], None] | None = None` for the TUI callback) in `pyrrhon.voice.pipeline` — builds local audio in → Silero VAD → Groq STT → bridge → OpenAI TTS → playback observer → local audio out and runs it until cancelled
  - `class VoiceUnavailableError(RuntimeError)` — raised for missing keys / missing audio stack / device failures; **callers degrade to text mode, the app never crashes**
  - `speech_path(session)` context manager — sets `session.agent.allow_retry = False` while voice runs and restores it after (split-path policy: speech never takes the grounding retry loop; a retry costs a full LLM turnaround and breaks the latency budget)
  - `VoiceController(session, settings, *, on_event=None, notify=print)` in `pyrrhon.voice` with `running: bool`, `start() -> str`, `async stop() -> str` — owns the background task that `/voice on|off` toggles

- [ ] **Step 1: Write the failing test**

`tests/test_voice_pipeline.py`:

```python
import asyncio
from types import SimpleNamespace

import pytest

import pyrrhon.voice.pipeline as pipeline_mod
from pyrrhon.voice import VoiceController
from pyrrhon.voice.pipeline import VoiceUnavailableError, run_voice, speech_path


def fake_session() -> SimpleNamespace:
    # Duck-typed stand-in: run_voice/speech_path only touch .agent.allow_retry
    # before any audio work happens.
    return SimpleNamespace(agent=SimpleNamespace(allow_retry=True))


async def test_missing_groq_key_degrades_with_clear_message(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(VoiceUnavailableError, match="GROQ_API_KEY"):
        await run_voice(fake_session(), SimpleNamespace(voice=None))


async def test_missing_openai_key_degrades_with_clear_message(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(VoiceUnavailableError, match="OPENAI_API_KEY"):
        await run_voice(fake_session(), SimpleNamespace(voice=None))


def test_speech_path_disables_retry_and_restores_even_on_error():
    session = fake_session()
    with speech_path(session):
        assert session.agent.allow_retry is False
    assert session.agent.allow_retry is True

    with pytest.raises(RuntimeError):
        with speech_path(session):
            assert session.agent.allow_retry is False
            raise RuntimeError("pipeline blew up")
    assert session.agent.allow_retry is True


async def test_controller_start_stop_toggles_background_task(monkeypatch):
    ran = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_run_voice(session, settings, *, on_event=None):
        ran.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(pipeline_mod, "run_voice", fake_run_voice)
    controller = VoiceController(fake_session(), SimpleNamespace(voice=None))

    message = controller.start()
    assert "on" in message.lower()
    await asyncio.wait_for(ran.wait(), timeout=2)
    assert controller.running is True
    assert "already" in controller.start().lower()  # double-start is friendly

    message = await controller.stop()
    assert "off" in message.lower()
    await asyncio.wait_for(cancelled.wait(), timeout=2)
    assert controller.running is False
    assert "not" in (await controller.stop()).lower()  # double-stop is friendly


async def test_controller_reports_unavailable_voice_instead_of_crashing(monkeypatch):
    async def failing_run_voice(session, settings, *, on_event=None):
        raise VoiceUnavailableError("Groq Whisper STT needs GROQ_API_KEY set — staying in text mode.")

    notices: list[str] = []
    monkeypatch.setattr(pipeline_mod, "run_voice", failing_run_voice)
    controller = VoiceController(
        fake_session(), SimpleNamespace(voice=None), notify=notices.append
    )
    controller.start()
    for _ in range(10):
        await asyncio.sleep(0)
    assert controller.running is False
    assert notices and "GROQ_API_KEY" in notices[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_voice_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.voice.pipeline'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/voice/pipeline.py`:

```python
"""run_voice: the Pipecat pipeline over the headless core.

local mic → Silero VAD → Groq Whisper STT → PyrrhonBridgeProcessor
→ OpenAI TTS → PlaybackObserver → local speakers

Error policy (spec): voice failures — no mic, missing key, missing audio
stack — degrade to text mode with a clear message via VoiceUnavailableError.
They never crash the app; the text channels keep working.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable

from pyrrhon.config.settings import Settings
from pyrrhon.core.events import Event
from pyrrhon.core.session import Session
from pyrrhon.voice.bridge import PlaybackObserver, PyrrhonBridgeProcessor
from pyrrhon.voice.playback import PlaybackTracker


class VoiceUnavailableError(RuntimeError):
    """Voice could not start or died; the caller stays in text mode."""


@contextlib.contextmanager
def speech_path(session: Session):
    """Split-path grounding policy (spec): while voice drives the session,
    the agent must never take the grounding retry loop — a retry costs a
    full LLM turnaround (~200-400ms) and breaks the latency budget. The
    grounding *gate* still runs; unverifiable file:line claims are stripped
    from speech and replaced with an honest 'I couldn't verify that.'"""
    previous = session.agent.allow_retry
    session.agent.allow_retry = False
    try:
        yield
    finally:
        session.agent.allow_retry = previous


def _require_env(name: str, what: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise VoiceUnavailableError(
            f"{what} needs {name} set — staying in text mode."
        )
    return value


async def run_voice(
    session: Session,
    settings: Settings,
    *,
    on_event: Callable[[Event], None] | None = None,
) -> None:
    """Build and run the voice pipeline until cancelled (/voice off)."""
    groq_key = _require_env("GROQ_API_KEY", "Groq Whisper STT")
    openai_key = _require_env("OPENAI_API_KEY", "OpenAI TTS")

    try:
        # Imported here, not at module top: the `local` extra (PyAudio) may
        # be absent; that must degrade, not crash at import time.
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.services.groq.stt import GroqSTTService
        from pipecat.services.openai.tts import OpenAITTSService
        from pipecat.transports.local.audio import (
            LocalAudioTransport,
            LocalAudioTransportParams,
        )
    except ImportError as exc:
        raise VoiceUnavailableError(
            f"Voice dependencies missing ({exc}). "
            'Run: uv add "pipecat-ai[local,silero,groq,openai]" — staying in text mode.'
        ) from exc

    voice = settings.voice if getattr(settings, "voice", None) else None
    stt_model = voice.stt_model if voice else "whisper-large-v3-turbo"
    tts_voice = voice.tts_voice if voice else "nova"
    chars_per_sec = voice.chars_per_sec if voice else 15.0

    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
        )
    )
    stt = GroqSTTService(
        api_key=groq_key,
        settings=GroqSTTService.Settings(model=stt_model),
    )
    tts = OpenAITTSService(
        api_key=openai_key,
        settings=OpenAITTSService.Settings(voice=tts_voice),
    )
    tracker = PlaybackTracker(chars_per_sec=chars_per_sec)
    bridge = PyrrhonBridgeProcessor(session, on_event=on_event, tracker=tracker)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            bridge,
            tts,
            PlaybackObserver(tracker),
            transport.output(),
        ]
    )
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))
    # handle_sigint=False: required on Windows, and Pyrrhon owns its lifecycle
    # via /voice off, not Ctrl-C inside the pipeline.
    runner = PipelineRunner(handle_sigint=False)

    with speech_path(session):
        try:
            await runner.run(task)
        except Exception as exc:  # no mic / device died / provider hiccup
            # CancelledError is BaseException — /voice off passes through.
            raise VoiceUnavailableError(
                f"Voice pipeline failed ({exc}) — staying in text mode."
            ) from exc
```

Version notes for the executor (from the verified-API section): if the installed Pipecat only has the 2.0 worker API, replace `PipelineTask`/`PipelineRunner` with `PipelineWorker(pipeline, params=PipelineParams(...))` + `WorkerRunner(handle_sigint=False)` / `await runner.add_workers(worker)` / `await runner.run()`. If `PipelineParams` has no `allow_interruptions` (newest API), interruptions are on by default via `VADUserTurnStartStrategy(enable_interruptions=True)` — drop the kwarg and verify barge-in in the smoke test. If `LocalAudioTransportParams` rejects `vad_analyzer`, check `TransportParams` fields on the installed version (`vad_enabled=True, vad_analyzer=..., vad_audio_passthrough=True` on some releases).

- [ ] **Step 4: Write the controller**

Replace `pyrrhon/voice/__init__.py` with:

```python
"""Voice channel: Pipecat pipeline + the on/off controller channels toggle."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from pyrrhon.config.settings import Settings
from pyrrhon.core.events import Event
from pyrrhon.core.session import Session
from pyrrhon.voice import pipeline as _pipeline
from pyrrhon.voice.pipeline import VoiceUnavailableError, run_voice

__all__ = ["VoiceController", "VoiceUnavailableError", "run_voice"]


class VoiceController:
    """Owns the background task running the voice pipeline (/voice on|off)."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        on_event: Callable[[Event], None] | None = None,
        notify: Callable[[str], None] = print,
    ):
        self._session = session
        self._settings = settings
        self._on_event = on_event
        self._notify = notify
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> str:
        if self.running:
            return "Voice is already on."
        # _pipeline.run_voice (module attribute) so tests can monkeypatch it.
        self._task = asyncio.create_task(
            _pipeline.run_voice(
                self._session, self._settings, on_event=self._on_event
            )
        )
        self._task.add_done_callback(self._on_done)
        return "Voice: on. Talk normally — barge in whenever you like."

    async def stop(self) -> str:
        if not self.running:
            return "Voice is not running."
        task = self._task
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, VoiceUnavailableError):
            pass
        return "Voice: off. Back to text."

    def _on_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if isinstance(exc, VoiceUnavailableError):
            self._notify(str(exc))
        elif exc is not None:
            self._notify(f"Voice stopped unexpectedly: {exc}. Text mode still works.")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_voice_pipeline.py -v`
Expected: 5 passed

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add pyrrhon/voice tests/test_voice_pipeline.py
git commit -m "feat: run_voice Pipecat pipeline with split-path policy and text-mode degradation"
```

---

### Task 7: `/voice on|off` command, `--voice` flag, manual barge-in smoke test

**Files:**
- Create: `pyrrhon/commands/voice_cmd.py`
- Modify: `pyrrhon/cli.py`, `pyrrhon/tui/app.py` (voice wiring), `CLAUDE.md`
- Test: `tests/test_voice_cmd.py` (append-style: handler tested directly with a stub ctx)

**Interfaces:**
- Consumes: `VoiceController` (Task 6); command registry `@command` (M1, assumed); `ctx` now carries `voice: VoiceController | None` (`None` in the plain REPL — see note below).
- Produces: registered `/voice on|off` command; `pyrrhon` CLI accepts `--voice` (start with the pipeline on); TUI constructs a `VoiceController` and forwards bridge events into its panes.
- Channel note: the M0 REPL runs `asyncio.run(...)` per turn — no persistent event loop, so a long-lived voice pipeline task cannot survive between turns there. **Voice is a TUI-channel feature** (Textual runs a persistent asyncio loop). The REPL's `ctx.voice` is `None` and `/voice` answers honestly.

- [ ] **Step 1: Write the failing test**

`tests/test_voice_cmd.py`:

```python
from types import SimpleNamespace

from pyrrhon.commands.voice_cmd import voice_cmd


class StubController:
    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self) -> str:
        self.started = True
        return "Voice: on."

    async def stop(self) -> str:
        self.stopped = True
        return "Voice: off."


async def test_voice_on_starts_the_controller():
    controller = StubController()
    ctx = SimpleNamespace(voice=controller)
    assert "on" in (await voice_cmd("on", ctx)).lower()
    assert controller.started is True


async def test_voice_off_stops_the_controller():
    controller = StubController()
    ctx = SimpleNamespace(voice=controller)
    assert "off" in (await voice_cmd("off", ctx)).lower()
    assert controller.stopped is True


async def test_voice_without_controller_degrades_honestly():
    ctx = SimpleNamespace(voice=None)
    out = await voice_cmd("on", ctx)
    assert "not available" in out.lower()


async def test_voice_bad_args_show_usage():
    ctx = SimpleNamespace(voice=StubController())
    assert "usage" in (await voice_cmd("sideways", ctx)).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_voice_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.commands.voice_cmd'`

- [ ] **Step 3: Write the command**

`pyrrhon/commands/voice_cmd.py`:

```python
"""/voice on|off — toggle the Pipecat voice pipeline for this session."""

from __future__ import annotations

from pyrrhon.commands.registry import command


@command("voice", help="Toggle the voice pipeline: /voice on|off")
async def voice_cmd(args: str, ctx) -> str:
    controller = getattr(ctx, "voice", None)
    if controller is None:
        return (
            "Voice is not available in this channel — run the TUI "
            "(plain `pyrrhon`) and try /voice on there."
        )
    choice = args.strip().lower()
    if choice == "on":
        return controller.start()
    if choice == "off":
        return await controller.stop()
    return "Usage: /voice on|off"
```

(As in Task 3: only the `@command(...)` line depends on the assumed M1 registry — adapt it if the real decorator differs; the handler body and its tests stand.)

- [ ] **Step 4: Wire `--voice` through the CLI and TUI**

`pyrrhon/cli.py` diff (shown against the M0 file; if M2 renamed `run_repl` to a TUI launcher, apply the same two hunks to that call site):

```diff
     parser.add_argument("repo", nargs="?", default=".", help="Path to the repo to discuss")
     parser.add_argument("--version", action="version", version=f"pyrrhon {__version__}")
+    parser.add_argument(
+        "--voice",
+        action="store_true",
+        help="Start with the voice pipeline on (equivalent to /voice on)",
+    )
     args = parser.parse_args(argv)

     # Imported lazily so `--version` works before the REPL exists (Task 9 wires it).
     from pyrrhon.repl import run_repl

-    run_repl(args.repo)
+    run_repl(args.repo, voice=args.voice)
```

TUI wiring (expected diff against M2's `pyrrhon/tui/app.py` — revalidate; the intent is fixed even if names drift):

```diff
+from pyrrhon.voice import VoiceController
+
 class PyrrhonApp(App):
-    def __init__(self, repo_root: Path, agent: Agent) -> None:
+    def __init__(self, repo_root: Path, agent: Agent, start_voice: bool = False) -> None:
         super().__init__()
         self.repo_root = repo_root
         self.session = Session(agent)
+        self._start_voice = start_voice
+        self.voice = VoiceController(
+            self.session,
+            load_settings(repo_root),
+            on_event=self._on_voice_event,
+            notify=self._notify_voice,
+        )
+
+    def _on_voice_event(self, event) -> None:
+        # Bridge events arrive on the same asyncio loop Textual runs on;
+        # hand them to the normal renderer (citations jump the code viewer,
+        # ScreenArtifacts render, TruncateSpeech marks the transcript).
+        self.call_later(self._render_event, event)
+
+    def _notify_voice(self, message: str) -> None:
+        self.call_later(self.notify, message)
@@ in on_mount:
+        if self._start_voice:
+            self.notify(self.voice.start())
```

The `ctx` the TUI passes to `dispatch()` gains `voice=self.voice`; the REPL's ctx gains `voice=None`. `run_repl(repo, voice=False)` accepts and forwards the flag (in the plain REPL it only prints the honest "voice needs the TUI" line when `voice=True`).

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_voice_cmd.py -v`
Expected: 4 passed

Then run the full suite: `uv run pytest -q` — Expected: all tests pass.

- [ ] **Step 6: Manual smoke test — the full audio loop (documented, not automated)**

Prerequisites: `GROQ_API_KEY` and `OPENAI_API_KEY` set, headphones on (spec: headphones-first sidesteps echo cancellation), a real open-source repo you didn't write.

Checklist (record pass/fail for each; criteria references are VISION.md success criteria):

1. `uv run pyrrhon <repo> --voice` — app starts, says voice is on. If a key is missing or the mic is absent, it prints the clear degradation message and text input still works (spec error-handling rule; re-test this once by unsetting `GROQ_API_KEY`).
2. Ask out loud: "how does <feature> work?" — spoken answer plays; the TUI shows the citation chip and the code viewer jumps to the cited `file:line`; open the file and confirm the citation is correct (**criterion 1**).
3. While it is mid-sentence, barge in with "wait — why is it done that way?" — audio stops within ~1s, the in-flight answer is abandoned, and it responds to the interruption (**criterion 2**).
4. Immediately run `/debug-history` — the last assistant message is a *prefix* of what it was saying plus ` …[interrupted]`, not the full generated text (TruncateSpeech contract; duration-estimate accuracy within a few words is acceptable).
5. Ask about something that does not exist ("how does the blockchain sync work?" in a repo with none) — it says it doesn't know / can't verify; **no invented file path is spoken** (**criterion 3**, split-path: `allow_retry=False` strips unverifiable citations from speech without a retry round-trip).
6. `/voice off` — pipeline stops cleanly, text turns still work; `/voice on` — voice resumes in the same session with the same history.
7. Speak two turns back-to-back with no barge-in — no truncation marker appears in `/debug-history` (the idle-interruption guard works).
8. Watch the terminal during a long tool-heavy answer — no audio stutter (real-time discipline: if it stutters, something is blocking the loop; find it before closing the milestone).

- [ ] **Step 7: Record real commands in CLAUDE.md**

In `CLAUDE.md`, update the current-state paragraph to mention M3 and the voice invocation:

```markdown
- Run the app: `uv run pyrrhon [repo-path]` (TUI; add `--voice` for the voice
  pipeline — needs `GROQ_API_KEY` + `OPENAI_API_KEY` and the pipecat `local`
  extra). `/voice on|off` toggles voice inside the TUI; `/debug-history` dumps
  the session history.
- Run tests: `uv run pytest` (single test: `uv run pytest path::test_name`)

Current state: M3 (voice: Pipecat pipeline, barge-in, TruncateSpeech) — see
`docs/superpowers/plans/2026-07-03-pyrrhon-m3-pipecat-voice.md`.
```

- [ ] **Step 8: Verify the import seams, then commit**

Run:

```bash
grep -rn "pipecat" pyrrhon/core/ pyrrhon/tui/ pyrrhon/commands/ pyrrhon/repl.py pyrrhon/cli.py
grep -rn "pyrrhon.repl\|pyrrhon.commands\|pyrrhon.tui\|pyrrhon.voice" pyrrhon/core/
```

Expected: both return nothing. Then:

```bash
git add pyrrhon/commands/voice_cmd.py pyrrhon/cli.py pyrrhon/repl.py pyrrhon/tui tests/test_voice_cmd.py CLAUDE.md
git commit -m "feat: /voice command, --voice flag, barge-in smoke checklist"
```

---

## Definition of Done (M3)

Mechanical checks:

- `uv run pytest -q` fully green, including the new `test_session.py` (cancellation + truncation), `test_playback.py` (estimator), `test_voice_bridge.py` (bridge with fake frames), `test_voice_pipeline.py` (degradation + split-path + controller), `test_voice_cmd.py`.
- `grep -rn "pipecat" pyrrhon/core/ pyrrhon/tui/ pyrrhon/commands/ pyrrhon/repl.py pyrrhon/cli.py` → nothing; `grep -rn "pyrrhon.repl\|pyrrhon.commands\|pyrrhon.tui\|pyrrhon.voice" pyrrhon/core/` → nothing.
- Every step of the Task 7 manual smoke checklist recorded as passing on a real repo.

Mapping to VISION.md success criteria (1–3 become verifiable at this milestone; 4 is M6):

1. **"Ask out loud, get a spoken answer citing a correct `file:line`."** Smoke steps 1–2: mic → Groq Whisper → agent (grounding gate from M1 still active on the speech path — only the retry loop is skipped) → OpenAI TTS speaks the prose while the TUI shows the verified citation; confirmed by opening the cited file.
2. **"Interrupt mid-answer; it stops and responds to the interruption."** Smoke steps 3–4, backed by unit tests: Pipecat's VAD-driven interruption frame → `Session.abort_current_turn()` cancels the reasoning task and in-flight tool calls (`test_abort_cancels_in_flight_tool_and_appends_nothing_further`), and history is rewritten to exactly the played text plus ` …[interrupted]` (`test_interruption_during_playback_truncates_to_played_estimate`) — history never assumes knowledge of unspoken words.
3. **"When it doesn't know, it says so instead of inventing a file path."** Smoke step 5: the split-path policy (`allow_retry=False`, `speech_path` context manager, unit-tested) means unverifiable claims are stripped from speech and replaced with an honest "I couldn't verify that" — with no latency-breaking retry loop — so confident hallucination cannot reach the speakers.
