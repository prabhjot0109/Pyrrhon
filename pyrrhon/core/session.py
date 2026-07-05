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
import time
from collections.abc import AsyncIterator

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Event, SpeechChunk

INTERRUPTED_MARKER = " …[interrupted]"

_TURN_DONE = object()


class Session:
    """Owns the conversation history; wraps Agent.run_turn in a cancellable task."""

    def __init__(self, agent: Agent):
        self.agent = agent
        self.history: list[dict] = []
        self.mode: str = "understand"
        self._current: asyncio.Task | None = None
        # Latency of the last turn: user_text -> first SpeechChunk, in ms.
        # Channels read this for the status bar; M3's voice budget is judged
        # against it. None until the first turn produces speech.
        self.last_turn_latency_ms: float | None = None

    async def run_turn(self, user_text: str) -> AsyncIterator[Event]:
        """Drive one turn, timing user_text -> first SpeechChunk."""
        started = time.monotonic()
        first_speech_seen = False
        async for event in self._run_turn_events(user_text):
            if not first_speech_seen and isinstance(event, SpeechChunk):
                self.last_turn_latency_ms = (time.monotonic() - started) * 1000.0
                first_speech_seen = True
            yield event

    async def _run_turn_events(self, user_text: str) -> AsyncIterator[Event]:
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
