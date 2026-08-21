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
import logging
import time
from collections.abc import AsyncIterator

from pyrrhon.core.agent.design_prompts import DESIGN_PROMPT
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.context import history_tokens, maybe_summarize
from pyrrhon.core.events import Event, SpeechChunk
from pyrrhon.core.telemetry import TurnTrace

logger = logging.getLogger("pyrrhon.session")

INTERRUPTED_MARKER = " …[interrupted]"

VALID_MODES: frozenset[str] = frozenset({"understand", "design"})
UNDERSTAND_MARKER = "Return to understand mode."

# Tags the one system message that carries the current mode. A marker rather
# than a remembered index: maybe_summarize splices history[1:split], so any
# index we cached would be stale after the first compaction.
MODE_PREFIX = "[mode]\n"

_TURN_DONE = object()


class Session:
    """Owns the conversation history; wraps Agent.run_turn in a cancellable task."""

    def __init__(self, agent: Agent):
        self.agent = agent
        self.history: list[dict] = []
        self.mode: str = "understand"
        self._current: asyncio.Task | None = None
        # History summarization, moved off the critical path. It used to run
        # inside Agent.run_turn in front of the first token of every
        # over-budget turn; it now runs here, after the turn, during the time
        # the user spends reading or talking.
        self._compaction: asyncio.Task | None = None
        # Latency of the last turn: user_text -> first SpeechChunk, in ms.
        # Channels read this for the status bar; M3's voice budget is judged
        # against it. None until the first turn produces speech.
        #
        # Stays a plain attribute, never a property derived from last_turn_trace:
        # tests/test_latency.py assigns it directly as a sentinel to prove the
        # next turn re-measures.
        self.last_turn_latency_ms: float | None = None
        # The same turn broken down into its parts (preamble / per-round LLM /
        # tools / gate / retry). None until the first turn completes.
        self.last_turn_trace: TurnTrace | None = None
        # Duration of the last background compaction. Lives here, not on
        # TurnTrace: compaction runs AFTER the turn whose trace was already
        # finished and published, so recording it there produced a metric that
        # was structurally always zero.
        self.last_compaction_ms: float | None = None

    def set_mode(self, mode: str) -> None:
        """Switch understand <-> design by REWRITING one layered system message.

        The base teaching prompt from turn one always stays underneath. Design
        gets the full skeptic policy; understand gets a one-line marker (the
        base prompt already carries the teaching policy, so no re-injection is
        needed).

        Exactly one mode message ever exists: appending per switch grew history
        without bound, and system messages are deliberately preserved by
        maybe_summarize (context.py:153), so nothing would ever have trimmed
        them.
        """
        if mode not in VALID_MODES:
            raise ValueError(
                f"Unknown mode '{mode}'. Valid modes: "
                f"{', '.join(sorted(VALID_MODES))}."
            )
        if mode == self.mode:
            return
        if not self.history:
            # First run_turn normally injects the base prompt; if the user
            # switches mode before saying anything, inject it now so the
            # mode message never becomes the conversation's foundation.
            self.history.append(
                {"role": "system", "content": self.agent.system_prompt}
            )
        self.mode = mode
        self.agent.mode = mode
        body = DESIGN_PROMPT if mode == "design" else UNDERSTAND_MARKER
        content = MODE_PREFIX + body
        for message in self.history:
            if message.get("role") == "system" and str(
                message.get("content", "")
            ).startswith(MODE_PREFIX):
                message["content"] = content
                return
        self.history.append({"role": "system", "content": content})

    async def run_turn(self, user_text: str) -> AsyncIterator[Event]:
        """Drive one turn, timing user_text -> first SpeechChunk."""
        # perf_counter, not monotonic: both are monotonic, but on Windows
        # monotonic is a ~15.6ms-granular tick counter — too coarse for a
        # sub-second voice budget. perf_counter resolves to ~100ns.
        started = time.perf_counter()
        first_speech_seen = False
        try:
            async for event in self._run_turn_events(user_text):
                if not first_speech_seen and isinstance(event, SpeechChunk):
                    self.last_turn_latency_ms = (
                        time.perf_counter() - started
                    ) * 1000.0
                    first_speech_seen = True
                    if self.agent.last_trace is not None:
                        self.agent.last_trace.mark_first_speech()
                yield event
        finally:
            # Publish in a finally: a turn cut short by barge-in is exactly the
            # turn whose breakdown you want to look at.
            self.last_turn_trace = self.agent.last_trace

    async def _run_turn_events(self, user_text: str) -> AsyncIterator[Event]:
        """Run one turn, streaming events. The agent runs in its own task so
        `abort_current_turn()` can cancel it while a channel is consuming."""
        if self._current is not None and not self._current.done():
            raise RuntimeError(
                "A turn is already running; call abort_current_turn() first."
            )
        # A background compaction must never overlap a turn: maybe_summarize
        # splices history[1:split] on the same list the agent loop iterates.
        self._cancel_compaction()

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
        completed_normally = False
        try:
            while True:
                item = await queue.get()
                if item is _TURN_DONE:
                    completed_normally = True
                    return
                yield item
        finally:
            # Consumer went away early (generator closed / consuming task
            # cancelled): never leave the agent running headless.
            if not self._current.done():
                self._current.cancel()
                self._repair_history()
            # Only on the normal path. A cancelled turn has just had its tail
            # rolled back by _repair_history, and the user is already talking
            # again — the last thing that moment needs is a background LLM call.
            if completed_normally:
                self._schedule_compaction()

    def _schedule_compaction(self) -> None:
        """Kick off history summarization in the background, at most one.

        Runs during the user's think/read/speak time, which is dead time for
        us and typically far longer than the call takes.
        """
        budget = self.agent.context_budget_tokens
        # Cheap pure scan: don't spawn a task just to have maybe_summarize
        # decide there is nothing to do.
        if not budget or history_tokens(self.history, self.agent.token_scale) <= budget:
            return
        try:
            self._compaction = asyncio.create_task(self._compact(budget))
        except RuntimeError:  # no running loop (generator finalized late)
            self._compaction = None

    async def _compact(self, budget: int) -> None:
        started = time.perf_counter()
        try:
            await maybe_summarize(
                self.history,
                self.agent.llm,
                budget,
                keep_last=self.agent.context_keep_last,
                scale=self.agent.token_scale,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # never let an optimization kill the session
            logger.debug("background compaction failed", exc_info=True)
        finally:
            self.last_compaction_ms = (time.perf_counter() - started) * 1000.0

    def _cancel_compaction(self) -> None:
        """Cancel an in-flight background compaction.

        Cancelling is safe, and is deliberately preferred over awaiting:
        awaiting would hand the next turn exactly the round trip this change
        removed. maybe_summarize has a single await — the llm.chat call — and
        it happens BEFORE any mutation of history; the splice that follows is
        synchronous. So a cancellation lands with history byte-identical,
        which is the contract context.py already documents ("Any LLM failure
        leaves history untouched — compaction is an optimization, never a
        correctness requirement"). If history really does outgrow the window,
        Agent.run_turn's ContextLengthExceededError handler compacts
        synchronously, which is where that cost belongs.
        """
        task = self._compaction
        self._compaction = None
        if task is not None and not task.done():
            task.cancel()

    def abort_current_turn(self) -> None:
        """Cancel the in-flight turn. Safe to call when idle.

        After `task.cancel()` the producer raises CancelledError at its
        current await point (llm.chat / tool.run) and never executes another
        statement of the agent loop — so nothing further is appended to
        history and late tool results are discarded, per spec.
        """
        self._cancel_compaction()
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
