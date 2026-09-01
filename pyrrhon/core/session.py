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
from pyrrhon.core.context import FIT_FULL, fit_to_budget, history_tokens
from pyrrhon.core.events import Citation, Event, SpeechChunk
from pyrrhon.core.telemetry import TurnTrace
from pyrrhon.core.transcript import Transcript, resolve_session

logger = logging.getLogger("pyrrhon.session")

INTERRUPTED_MARKER = " …[interrupted]"

VALID_MODES: frozenset[str] = frozenset({"understand", "design"})
UNDERSTAND_MARKER = "Return to understand mode."

# Tags the one system message that carries the current mode. A marker rather
# than a remembered index: maybe_summarize splices history[1:split], so any
# index we cached would be stale after the first compaction.
MODE_PREFIX = "[mode]\n"

_TURN_DONE = object()


def open_session(
    agent: Agent,
    repo_root,
    resume: str | None = None,
    save: bool = True,
    home=None,
) -> tuple["Session", str]:
    """The Session a channel should serve, plus the one line it should say.

    Lives here rather than in each channel because the sequence has three
    branches and both screen channels need the same three. `start_channel`
    exists for exactly this reason: two copies of an ordered startup sequence
    diverge quietly, and the divergence reads as a bug in one channel rather
    than as a missing edit.

    `resume` is a session id, or "" for "the most recent one". A resume that
    finds nothing starts a fresh session and SAYS so — silently starting empty
    is how a user loses an afternoon believing they are continuing it.
    """
    if not save:
        return Session(agent), ""
    if resume is None:
        return Session(agent, transcript=Transcript.start(repo_root, home)), ""
    path = resolve_session(repo_root, resume or None, home)
    if path is None:
        wanted = f" matching {resume!r}" if resume else ""
        return (
            Session(agent, transcript=Transcript.start(repo_root, home)),
            f"No saved session{wanted} for this repo — starting a new one.",
        )
    session = Session(agent)
    transcript = Transcript(path)
    turns = session.resume(transcript)
    # The covered ground rides the resume notice rather than waiting to be
    # asked for. "Where was I" is the first thing a returning user needs and
    # the last thing they will type a command to find out.
    anchor = transcript.covered_ground()
    notice = f"Resumed {path.stem} — {turns} earlier turn(s) in context."
    return session, (notice + "\n\n" + anchor if anchor else notice)


class Session:
    """Owns the conversation history; wraps Agent.run_turn in a cancellable task."""

    def __init__(self, agent: Agent, transcript: Transcript | None = None):
        self.agent = agent
        self.history: list[dict] = []
        self.mode: str = "understand"
        # Where the conversation is saved, or None for a session nobody asked
        # to keep (every test, and every channel until one opts in).
        self.transcript = transcript
        # The turn waiting to be written. Written at the START of the next
        # turn rather than at the end of its own, because barge-in truncation
        # reaches Session AFTER the turn's generator has finished — so a
        # record written at the end preserves words the user cut off, and the
        # one thing the transcript must not do is disagree with history about
        # what was heard.
        self._pending: tuple[str, str, tuple[Citation, ...]] | None = None
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

    def _flush_transcript(self) -> None:
        """Write the previous turn, as history finally holds it.

        The answer is re-read off history rather than trusted from the events,
        because a barge-in truncation rewrites the last assistant message after
        the turn is over. History is the authority on what was HEARD, which is
        the invariant `truncate_last_assistant` exists to keep, and a
        transcript that disagreed with it would be the one artifact claiming
        Pyrrhon said something the user never let it finish.
        """
        pending, self._pending = self._pending, None
        if pending is None or self.transcript is None:
            return
        question, answer, citations = pending
        last = self.history[-1] if self.history else {}
        if last.get("role") == "assistant" and isinstance(last.get("content"), str):
            answer = last["content"]
        if question or answer:
            self.transcript.record(question, answer, citations)

    def close(self) -> None:
        """End of session: the last turn has nothing after it to flush it."""
        self._flush_transcript()

    def resume(self, transcript: Transcript) -> int:
        """Continue a saved session, and return how many turns came back.

        The restored messages are prose with no coordinates in them, so this
        cannot smuggle evidence across the process boundary: a resumed session
        has to reopen a file before it may cite one, which is M16e's
        admissibility rule enforced by the shape of the data rather than by an
        instruction. That is why this is safe to build now and would not have
        been before.

        No system message is restored either. `Agent._run_turn` rewrites
        history[0] on every turn, so a stale one would be overwritten anyway —
        and until it was, the session would run on a prompt built by a
        different version of Pyrrhon.
        """
        entries = transcript.entries()
        self.transcript = transcript
        self.history = transcript.messages()
        return len(entries)

    def clear(self) -> int:
        """Start a fresh thread without restarting, returning what was dropped.

        History only. The mode survives, because a user who switched to design
        mode and then cleared is starting a new design, not leaving one; and
        the transcript survives, because /clear is about what the MODEL
        carries, never about destroying what the user has already been told.
        """
        dropped = len([m for m in self.history if m.get("role") != "system"])
        self._cancel_compaction()
        self.history = []
        return dropped

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
        self._flush_transcript()
        spoken: list[str] = []
        cited: list[Citation] = []
        try:
            async for event in self._run_turn_events(user_text):
                if isinstance(event, SpeechChunk):
                    spoken.append(event.text)
                elif isinstance(event, Citation):
                    cited.append(event)
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
            # Chunks are joined with the separator the core split on: a space
            # for a spoken turn, a blank line for a written one. Concatenating
            # with nothing fuses a paragraph into the list below it, and
            # nothing downstream can recover the boundary.
            joiner = " " if self.agent.voice_active else "\n\n"
            self._pending = (user_text, joiner.join(spoken), tuple(cited))

    async def _run_turn_events(self, user_text: str) -> AsyncIterator[Event]:
        """Run one turn, streaming events. The agent runs in its own task so
        `abort_current_turn()` can cancel it while a channel is consuming."""
        # Three states, not two, and the third is what was missing. A turn can
        # be absent, live, or winding DOWN — cancelled and not yet unwound.
        # Nothing that cancels a turn awaits it: `abort_current_turn` returns
        # the moment it calls `cancel()`, and `voice/bridge.py:_start_turn`
        # cancels its own task and then immediately starts the replacement. So
        # a transcription that raced ahead of its own interruption reached here
        # with `_current` merely cancelled, and the user's second sentence was
        # answered with a RuntimeError.
        current = self._current
        if current is not None and not current.done():
            if not current.cancelling():
                raise RuntimeError(
                    "A turn is already running; call abort_current_turn() first."
                )
            # Unbounded on purpose. A cancelled producer's only remaining work
            # is a `put_nowait` in a finally, plus whatever `asyncio.to_thread`
            # is already running — and that thread has to finish before a
            # replacement starts anyway, or two turns mutate `history` at once.
            # asyncio.wait rather than `await current`, which would re-raise
            # the predecessor's CancelledError as though it were ours.
            await asyncio.wait({current})
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

        # Held locally as well as on self. `self._current` means "the live
        # turn" and the replacement overwrites it, so a superseded generator
        # finalized afterwards would read the REPLACEMENT out of the finally
        # below and cancel it — the corpse killing the turn that replaced it.
        producer = asyncio.create_task(_produce())
        self._current = producer
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
            if not producer.done():
                producer.cancel()
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
        # The SAME budget the turn's own pre-flight ladder used, schemas
        # netted out and all. Asking a looser question here would schedule a
        # round trip to fix a history the turn had just fitted.
        trace = self.agent.last_trace
        budget = self.agent.request_budget(trace.schema_chars if trace else 0)
        # Cheap pure scan: don't spawn a task just to have maybe_summarize
        # decide there is nothing to do.
        if not budget or history_tokens(self.history, self.agent.token_scale) <= budget:
            return
        try:
            self._compaction = asyncio.create_task(self._compact(budget))
        except RuntimeError:  # no running loop (generator finalized late)
            self._compaction = None

    async def _compact(self, budget: int) -> None:
        """The whole ladder, in the user's read/think/speak time.

        Not just the summarize rung. The turn's own pre-flight is restricted to
        rung 2, so this is the only place rung 3 runs outside a provider
        refusal — without it a session on a small ceiling accumulates bulky
        tool results that nothing elides until the provider says no.
        """
        started = time.perf_counter()
        try:
            await fit_to_budget(
                self.history,
                self.agent.llm,
                budget,
                mode=FIT_FULL,
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
        removed.

        What "safe" means got weaker in M16b and is still enough. It used to
        mean byte-identical: maybe_summarize has a single await, the llm.chat
        call, and it happens BEFORE any mutation of history. _compact now runs
        the whole ladder, whose first two rungs mutate synchronously in front
        of that await, so a cancellation can land with tool results already
        elided. That is fine and is not the same class of thing — elision is
        idempotent, it is grounding-neutral because the EvidenceLedger is
        separate from history, and it is exactly what the next turn's own
        pre-flight or the safety net would have done anyway. The contract
        context.py documents still holds: "compaction is an optimization, never
        a correctness requirement."

        If history really does outgrow the window, Agent.run_turn's
        ContextLengthExceededError handler runs the ladder forced and
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
