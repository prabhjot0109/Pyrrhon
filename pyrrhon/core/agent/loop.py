"""The reasoning loop: LLM ⇄ tools, emitting the core event stream.

M1: a GroundingGate can sit between the LLM's final text and the emitted
events. Split-path recovery policy (spec, amended 2026-07-03): screen
channels construct the Agent with allow_retry=True and get one
self-correction LLM round-trip; the M3 speech channel constructs it with
allow_retry=False and unverifiable references are stripped immediately —
a retry costs a full LLM turnaround and breaks the voice latency budget.

Amended 2026-08-02 (M10): the retry is additionally conditional on the turn
NOT having streamed. Streaming is now on for every channel, not just voice,
and once a chunk has been printed or spoken there is no coherent way to
un-say it — rewriting it would violate "history records what was heard".
So a streamed turn strips and hedges rather than re-asking the model.
allow_retry still governs the non-streaming path, which is what test doubles
and providers without stream() take.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import nullcontext
from pathlib import Path

from pyrrhon.core.agent.escalate import ThinkDeeperTool
from pyrrhon.core.agent.guards import ToolGuard, assistant_tool_message, run_tool_round
from pyrrhon.core.agent.prompts import ESCALATION_NOTE, TEXT_STYLE, VOICE_STYLE
from pyrrhon.core.agent.turn_type import classify, needs_tools
from pyrrhon.core.context import (
    compact_tool_results,
    hard_compact_tool_results,
    history_tokens,
    maybe_summarize,
    token_scale,
)
from pyrrhon.core.events import (
    AskUser,
    Event,
    SpeechChunk,
    ToolCallFinished,
    ToolCallStarted,
)
from pyrrhon.core.grounding.citations import extract_citations, extract_references
from pyrrhon.core.grounding.evidence import EvidenceLedger
from pyrrhon.core.grounding.gate import (
    HEDGE,
    LINE_HEDGE,
    LINE_UNSEEN_HEDGE,
    GroundingGate,
)
from pyrrhon.core.providers.errors import (
    ContextLengthExceededError,
    InvalidToolCallError,
)
from pyrrhon.core.telemetry import RoundTrace, TurnTrace
from pyrrhon.core.tools.base import Tool, run_tool

logger = logging.getLogger("pyrrhon.agent")

PREVIEW_LEN = 200

BUDGET_MESSAGE = (
    "I hit my tool budget for this question — ask me to continue "
    "and I'll keep digging."
)

# A provider error must degrade to an honest spoken line, never a silent turn
# death (which is what a raw exception here becomes once the producer task
# swallows it). Voice especially: silence reads as "it's broken."
PROVIDER_ERROR_MESSAGE = (
    "I couldn't get a reply from my model just now — it returned an error. "
    "Try again, or check the provider and model in /settings."
)
TOOL_RETRY_EXHAUSTED_MESSAGE = (
    "My model kept trying to use a tool I don't have. Try rephrasing the "
    "question, or switch models in /settings."
)
CONTEXT_FULL_MESSAGE = (
    "This thread got too big for my model's context, even after trimming. "
    "Start a fresh question, or switch to a larger-context model in /settings."
)

# A streamed answer the round failed part-way through. Distinct from
# session.INTERRUPTED_MARKER, which means the USER cut in: this one means the
# model stopped, and the difference matters when reading a transcript back.
CUT_OFF_MARKER = " …[cut off by a provider error]"

# The same shape for the OTHER way an answer stops early. Distinct wording
# because the causes are distinct: this one is the configured reply-length
# limit, which the user can raise, and no error occurred at all.
TRUNCATED_MARKER = " …[cut off at the model's reply-length limit]"

TRUNCATED_MESSAGE = (
    "That answer hit my reply-length limit twice, so it stops early. "
    "Raise max_tokens under [model] in /settings, or ask me for a narrower "
    "piece of it."
)

# Copied from Claude Code's max_output_tokens_recovery in intent, and the
# wording is load-bearing: a model told only "continue" restates its previous
# paragraph and spends the whole new budget on the recap.
RESUME_INSTRUCTION = (
    "Your previous message stopped at the reply-length limit, mid-thought. "
    "Continue it from exactly where it stopped. Resume directly — no apology, "
    "no recap, no restating what you were doing. If a lot remains, cover the "
    "most important part first and keep it short."
)

# How many times one turn may recover from a context-window overflow by
# compacting and retrying before it gives up honestly.
MAX_CONTEXT_RECOVERIES = 2

# How many times ONE round may be resumed after stopping at max_tokens.
# One, deliberately. A second `length` on the same answer is a configuration
# fact rather than something to retry around, and the reference's other tier —
# silently re-running at a much larger max_tokens — is not adopted: max_tokens
# is a user-set value in [model], a voice turn has a latency budget a very
# large reply blows through, and silently overriding a configured number is
# the kind of helpfulness that makes a harness untrustworthy.
MAX_TRUNCATION_RESUMES = 1


def _invalid_tool_nudge(names: list[str]) -> str:
    return (
        "That tool call was rejected: you named a tool that does not exist. "
        f"Only these tools are available: {', '.join(names)}. "
        "Call one of those, using its exact name, or answer directly."
    )

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _pop_sentences(buffer: str) -> tuple[list[str], str]:
    """Split completed sentences off the front of a streaming buffer.

    Returns (complete_sentences, remainder). A sentence is "complete" once a
    .!? is followed by whitespace, so the trailing fragment (still being
    generated) stays in the buffer until its terminator streams in.

    This is the VOICE splitter: a sentence is the natural unit to hand TTS.
    Text channels use _pop_blocks instead — see why there.
    """
    parts = _SENTENCE_BOUNDARY.split(buffer)
    if len(parts) == 1:
        return [], buffer
    return parts[:-1], parts[-1]


_FENCE = "```"


def _pop_blocks(buffer: str) -> tuple[list[str], str]:
    """Split completed markdown blocks off the front of a streaming buffer.

    The TEXT splitter. Sentence-splitting is wrong on screen: TEXT_STYLE
    explicitly invites tables and fenced code blocks, and half a table or half
    a code fence renders as garbage — the REPL calls Markdown() once per
    SpeechChunk, so a chunk boundary is a rendering boundary.

    A block ends at a *blank* line (i.e. "\\n\\n", not a single line break)
    that is not inside a ``` fence. Because that is the only place we ever
    cut, the buffer always begins outside a fence — which is what makes fence
    state recomputable from the buffer alone, with no state threaded between
    calls.

    A blank line also terminates a markdown table and a list, so "blank line
    outside a fence" is sufficient on its own to avoid splitting either.
    """
    lines = buffer.split("\n")
    blocks: list[str] = []
    in_fence = False
    cut = 0  # index just past the last line we flushed
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(_FENCE):
            in_fence = not in_fence
            continue
        # `i < len(lines) - 1` is load-bearing: the final element is the text
        # after the last newline and is still being generated, so an empty one
        # is a trailing "\n", not a paragraph break. Flushing on it would split
        # a paragraph mid-way on every line ending.
        if not in_fence and not stripped and i < len(lines) - 1:
            block = "\n".join(lines[cut:i]).strip()
            if block:
                blocks.append(block)
            cut = i + 1
    return blocks, "\n".join(lines[cut:])


def extract_question(text: str) -> str | None:
    """Return the reply's final sentence if the reply ends with a question.

    Pure and deliberately dumb: only a stripped trailing '?' counts, and the
    "last sentence" is whatever follows the final .!?-plus-whitespace
    boundary. Channels use the result to render/say Pyrrhon's Socratic
    question distinctly (the AskUser event).
    """
    stripped = text.strip()
    if not stripped.endswith("?"):
        return None
    return _SENTENCE_BOUNDARY.split(stripped)[-1]


def _retry_prompt(unverified: tuple[str, ...]) -> str:
    refs = ", ".join(unverified)
    return (
        "Grounding check failed: these citations do not point at real "
        f"locations in the repo: {refs}. Rewrite your answer using only "
        "path:line locations you actually saw in tool output earlier in this "
        "conversation. If you are not sure of the exact location, say "
        "\"I'm not certain\" and drop the citation. Never invent a path."
    )


class Agent:
    """Owns no conversation state: `history` belongs to the caller and is
    mutated in place, so channels (REPL/TUI/voice) decide session lifetime."""

    def __init__(
        self,
        llm,
        tools: list[Tool],
        system_prompt: str,
        repo_root: Path,
        max_tool_rounds: int = 8,
        grounding_gate: GroundingGate | None = None,
        allow_retry: bool = True,
        voice_active: bool = False,
        deep_llm=None,
        deep_tools: list[Tool] | None = None,
        mode: str = "understand",
        context_budget_tokens: int = 90000,
        context_keep_last: int = 8,
    ):
        self.llm = llm
        self.tools = {tool.name: tool for tool in tools}
        # Repo paths the live turn is about, refreshed at the top of each turn.
        # RepoMapTool reads it through a callable build_agent patches in.
        self._mentions_now: frozenset[str] = frozenset()
        self.system_prompt = system_prompt
        self.repo_root = repo_root
        self.max_tool_rounds = max_tool_rounds
        self.grounding_gate = grounding_gate
        self.allow_retry = allow_retry
        # Mutable, like allow_retry: the voice pipeline's speech_path() flips
        # this on /voice on and restores it on /voice off. It selects the
        # spoken vs. written delivery style appended to the prompt each turn.
        self.voice_active = voice_active
        # Mutable: Session.set_mode reassigns it on /mode switches.
        self.mode = mode
        self.context_budget_tokens = context_budget_tokens
        self.context_keep_last = context_keep_last
        # How wrong len//4 is for whatever model is in the fast slot, learned
        # from the prompt_tokens each reply reports. 1.0 until the first reply
        # comes back, which is the only moment the estimate stands alone.
        # Read by Session when it decides whether to compact.
        self.token_scale: float = 1.0
        # Diagnostics for the LAST turn only — not conversation state, so this
        # doesn't violate "Agent owns no conversation state" above. Session
        # copies it out into `last_turn_trace` once the turn ends.
        self.last_trace: TurnTrace | None = None
        # What tool output actually SHOWED the model, this turn. Replaced (not
        # cleared) at the top of every turn; initialised here so a caller that
        # invokes _emit_final directly — several tests do — never trips on a
        # missing attribute.
        self._evidence = EvidenceLedger()
        # References this turn that were real-but-unopened. Diagnostics for the
        # eval harness, not conversation state — same status as last_trace.
        self.last_unseen: tuple[str, ...] = ()
        self._schema_cache: list[dict] = []
        self._schema_cache_key: tuple[str, ...] | None = None
        # Owned, not just consumed. Channels swap the deep slot at runtime
        # (/model deep, /settings llm deep), and the tool captured the model at
        # construction — so before this the swap silently did nothing while the
        # command replied "active". Keep the attribute and the live tool in
        # lockstep through one seam.
        self.deep_llm = deep_llm
        if deep_llm is not None:
            deep_tool = ThinkDeeperTool(deep_llm, tools=deep_tools)
            self.tools[deep_tool.name] = deep_tool
            self.system_prompt = system_prompt + "\n" + ESCALATION_NOTE

    def _seal_partial(
        self, history: list[dict], live: list[dict], marker: str = CUT_OFF_MARKER
    ) -> bool:
        """Close out a streamed answer whose round then failed.

        The text is already on screen or already spoken, so it stays in
        history — that is the "history records what was heard" rule, and it is
        what a barge-in would truncate against. What must NOT happen is
        appending a second assistant message beside it: two adjacent assistant
        turns are rejected by strict chat endpoints, and an error line recorded
        as an answer is a lie about what was said.

        Returns True when a partial was sealed, telling the caller to emit its
        message WITHOUT recording it.
        """
        if not live:
            return False
        slot = live[0]
        if not history or history[-1] is not slot:
            return False
        content = slot.get("content")
        if isinstance(content, str) and content.strip():
            slot["content"] = content + marker
            return True
        history.pop()  # nothing was ever spoken — drop the empty slot
        return False

    async def _seal_for_resume(
        self,
        history: list[dict],
        partial: str,
        stream_slot: dict | None,
        streaming: bool,
    ) -> AsyncIterator[Event]:
        """Close a truncated answer into history so the next round continues it.

        The two paths need opposite work, which is the whole reason this is a
        function. `_stream_round` has already gated each chunk, spoken it, and
        written it into its own assistant slot, so there is nothing to say and
        nothing to append. The whole-reply path has done neither — and skipping
        it there would drop the first half of the answer on the floor, since
        the resumed round returns only the continuation.

        No CUT_OFF_MARKER: nothing was cut off from the user's point of view.
        The continuation is about to arrive.
        """
        if streaming:
            if stream_slot is None and partial:
                history.append({"role": "assistant", "content": partial})
            return
        if not partial:
            return
        text = partial
        if self.grounding_gate is not None:
            text = (
                await self.grounding_gate.check(partial, self._evidence)
            ).speech_text
        if text.strip():
            history.append({"role": "assistant", "content": text})
            yield SpeechChunk(text=text)

    def set_deep_llm(self, llm) -> None:
        """Point escalation at a different model for the rest of the session.

        No-op on the tool when think_deeper was never registered (no deep key
        at build time) — the attribute still updates so the status bar is honest.

        The isinstance check is not just for the type checker: a plugin may
        contribute a tool under this name, and rewriting an attribute on
        something that is not the escalation tool would be a silent no-op at
        best.
        """
        self.deep_llm = llm
        tool = self.tools.get("think_deeper")
        if isinstance(tool, ThinkDeeperTool):
            tool.deep_llm = llm

    def _calibrate(self, reply, sent_estimate: int) -> None:
        """Update token_scale from what the provider just charged for.

        Silent when the provider reported nothing usable — a local server that
        omits `usage` leaves the previous scale in place rather than resetting
        it, because the last provider that DID answer is a better guide than
        the len//4 assumption.
        """
        scale = token_scale(getattr(reply, "usage", None), sent_estimate)
        if scale is not None:
            self.token_scale = scale

    async def run_turn(
        self, history: list[dict], user_text: str
    ) -> AsyncIterator[Event]:
        """Drive one turn. Thin wrapper: the body has many early returns, and
        the trace has to be closed out on every one of them — including the
        CancelledError path, since a barge-in mid-turn is still a turn worth
        measuring."""
        trace = TurnTrace()
        self.last_trace = trace
        try:
            async for event in self._run_turn(history, user_text, trace):
                yield event
        finally:
            trace.finish()

    async def _run_turn(
        self, history: list[dict], user_text: str, trace: TurnTrace
    ) -> AsyncIterator[Event]:
        # Fresh per turn, deliberately. Evidence from an earlier turn is not
        # evidence now: the file may have changed, and "I read it a while ago"
        # is precisely the reasoning that produces confident stale-line
        # citations. The gate's own line-count cache handles cross-turn reuse
        # of the cheap existence check; this one is about what the model was
        # SHOWN, which expires with the turn.
        self._evidence = EvidenceLedger()
        self.last_unseen = ()
        # The base prompt is channel-agnostic; the delivery style (spoken vs.
        # written) is chosen per turn from the current voice_active flag and
        # refreshed on the leading system message. Refreshing (not just
        # injecting on empty history) lets a live /voice on|off toggle change
        # the style mid-session. maybe_summarize always keeps history[0], so it
        # stays the base system message we can safely rewrite here.
        with trace.time_preamble():
            style = VOICE_STYLE if self.voice_active else TEXT_STYLE
            system_content = f"{self.system_prompt}\n{style}"
            if not history:
                history.append({"role": "system", "content": system_content})
            elif history[0].get("role") == "system":
                history[0]["content"] = system_content
            else:
                history.insert(0, {"role": "system", "content": system_content})
            history.append({"role": "user", "content": user_text})
            # After the append, so the question being asked right now counts.
            self._mentions_now = self._conversation_mentions(history)
            # Only the pure, local pass runs before round one. maybe_summarize
            # is a full LLM round trip and used to sit right here, in front of
            # the first token of every over-budget turn; Session now runs it
            # AFTER the turn instead (see Session._schedule_compaction). The
            # ContextLengthExceededError handler below is the safety net for
            # the case where skipping it actually overflows the window.
            compact_tool_results(history)
            # A greeting or a bare "yes" needs no tools. Withholding the belt
            # saves ~1.5k tokens of schema on the 25-40% of voice turns that
            # are acknowledgements, and removes any chance of a spurious tool
            # round on a turn with nothing to look up.
            turn_kind = classify(user_text, history)
            trace.turn_type = turn_kind
            schemas = self._tool_schemas() if needs_tools(turn_kind) else None
        # Zero when the belt was withheld — that saving is the point of the
        # turn-type check, so the trace should show it.
        trace.schema_chars = sum(len(str(schema)) for schema in schemas or ())
        trace.prompt_chars = sum(
            len(m["content"]) for m in history if isinstance(m.get("content"), str)
        )
        guard = ToolGuard()
        nudged_invalid_tool = False
        context_recoveries = 0
        # Per ROUND, not per turn: cleared below whenever a round lands intact,
        # so a long investigation is not denied a resume because an earlier
        # round used one.
        truncation_resumes = 0
        # Stream on EVERY channel, not just voice: buffering a whole reply
        # makes time-to-first-output equal to time-to-last-token, which is the
        # single biggest source of "it feels slow" on the screen paths too.
        # The hasattr guard is what keeps this safe — tests/helpers.py:FakeLLM
        # exposes only chat(), so every test double keeps the whole-reply path.
        streaming = hasattr(self.llm, "stream")
        trace.streamed = streaming

        for _ in range(self.max_tool_rounds):
            spoken_text: str | None = None
            stream_slot: dict | None = None
            # Holds the live assistant slot the moment _stream_round creates it,
            # so a round that dies mid-stream can still find what was spoken.
            # The sink only reports it on a *successful* round, which is exactly
            # the case that never needed sealing.
            live: list[dict] = []
            round_trace = trace.begin_round()
            # The unscaled estimate of exactly what this round sends, kept so
            # the reply's prompt_tokens can be turned into a ratio. Taken
            # before the call because the streaming path appends into
            # `history` as it goes.
            sent_estimate = history_tokens(history)
            try:
                with round_trace.time_llm():
                    if streaming:
                        sink: list = []
                        async for event in self._stream_round(
                            history, schemas, sink, round_trace, live
                        ):
                            yield event
                        reply, spoken_text, stream_slot = sink[0]
                    else:
                        reply = await self.llm.chat(history, tools=schemas)
                        # Non-streaming: the whole reply lands at once, so
                        # time-to-first-token IS the full round. Recording it
                        # keeps the metric comparable across both paths.
                        round_trace.mark_ttft()
            except InvalidToolCallError:
                # The model called a tool that isn't in our list (a gpt-oss
                # built-in, typically). Nudge once with the real names and let
                # it retry; if it happens again, degrade honestly.
                if not nudged_invalid_tool:
                    nudged_invalid_tool = True
                    self._seal_partial(history, live)
                    history.append(
                        {"role": "user", "content": _invalid_tool_nudge(list(self.tools))}
                    )
                    continue
                sealed = self._seal_partial(history, live)
                async for event in self._emit_final(
                    history, TOOL_RETRY_EXHAUSTED_MESSAGE, trace, streaming,
                    record=not sealed,
                ):
                    yield event
                return
            except ContextLengthExceededError:
                # The prompt outgrew the model's window mid-turn. Reclaim room
                # (elide bulky tool results + summarize old turns) and retry the
                # same round instead of stranding the turn. Codex's pattern.
                #
                # Sealing BEFORE the retry is deliberate: the retry re-runs the
                # round with a fresh slot, so the previous partial must already
                # be a closed message or the next stream appends into a history
                # that ends with a live, still-growing one.
                sealed = self._seal_partial(history, live)
                if context_recoveries < MAX_CONTEXT_RECOVERIES:
                    context_recoveries += 1
                    elided = hard_compact_tool_results(history)
                    summarized = await maybe_summarize(
                        history,
                        self.llm,
                        # Force a summarize pass even if the estimate is under
                        # budget — the provider just told us we're over.
                        budget_tokens=1,
                        keep_last=self.context_keep_last,
                    )
                    logger.warning(
                        "context overflow: recovered (elided=%d, summarized=%s, "
                        "attempt=%d)", elided, summarized, context_recoveries
                    )
                    if elided or summarized:
                        continue
                async for event in self._emit_final(
                    history, CONTEXT_FULL_MESSAGE, trace, streaming, record=not sealed
                ):
                    yield event
                return
            except Exception as exc:  # provider/network error — never die silently
                logger.warning("llm.chat failed mid-turn: %s: %s", type(exc).__name__, exc)
                sealed = self._seal_partial(history, live)
                async for event in self._emit_final(
                    history, PROVIDER_ERROR_MESSAGE, trace, streaming, record=not sealed
                ):
                    yield event
                return
            self._calibrate(reply, sent_estimate)
            # The other way a provider says no: HTTP 200, a well-formed body,
            # and an answer that simply stops. Nothing downstream can tell that
            # from a finished one — spoken aloud it is a confident sentence
            # that ends mid-thought, with no citation to check it against.
            #
            # Only when there are no tool calls. A `length` alongside tool
            # calls means the ARGUMENTS were cut off, which json.loads has
            # already rejected inside the client; resuming prose would be the
            # wrong recovery for it.
            if reply.finish_reason == "length" and not reply.tool_calls:
                partial = (spoken_text if streaming else reply.text) or ""
                if truncation_resumes < MAX_TRUNCATION_RESUMES:
                    truncation_resumes += 1
                    async for event in self._seal_for_resume(
                        history, partial, stream_slot, streaming
                    ):
                        yield event
                    history.append(
                        {"role": "user", "content": RESUME_INSTRUCTION}
                    )
                    continue
                # A second `length` on the same answer is a configuration fact.
                # Say it, name the key, and never present the fragment as
                # though it were finished.
                sealed = self._seal_partial(history, live, TRUNCATED_MARKER)
                text = (
                    TRUNCATED_MESSAGE
                    if sealed or not partial
                    else partial + TRUNCATED_MARKER + "\n\n" + TRUNCATED_MESSAGE
                )
                async for event in self._emit_final(
                    history, text, trace, streaming, record=not sealed
                ):
                    yield event
                return
            truncation_resumes = 0
            if not reply.tool_calls:
                if streaming:
                    # Sentences were gated + spoken (and citations emitted, and
                    # the answer written into stream_slot in history) inside
                    # _stream_round. Just backfill an empty slot and, in design
                    # mode, surface the Socratic question.
                    answer = spoken_text or reply.text or "(no answer)"
                    if stream_slot is None:
                        history.append({"role": "assistant", "content": answer})
                    question = extract_question(answer)
                    if question is not None:
                        yield AskUser(question=question)
                    return
                async for event in self._emit_final(
                    history, reply.text or "(no answer)", trace, streaming
                ):
                    yield event
                return

            if streaming:
                # The streamed text was narration, not a standalone answer —
                # drop its live slot; assistant_tool_message(reply) carries the
                # same text alongside the tool calls.
                if stream_slot is not None and history and history[-1] is stream_slot:
                    history.pop()
            elif reply.text:
                # Narration spoken while tools run. It passes the gate too:
                # a fabricated citation must never be spoken, even mid-turn.
                narration = reply.text
                if self.grounding_gate is not None:
                    with round_trace.time_gate():
                        narration = (
                            await self.grounding_gate.check(narration, self._evidence)
                        ).speech_text
                if narration.strip():
                    yield SpeechChunk(text=narration)

            history.append(assistant_tool_message(reply))
            # Every start before dispatch, every finish after it, both in call
            # order. Forced anyway — an async generator cannot yield from
            # inside a task — and free: ToolCallFinished has no render branch
            # in either channel, so only tests observe the ordering.
            for call in reply.tool_calls:
                yield ToolCallStarted(name=call.name, args=call.arguments)
            # tool_wall_ms brackets the whole phase while time_tool() records
            # each call. Now that dispatch is concurrent, wall approaches
            # max() while total stays sum() — the gap between the two is the
            # measured win.
            with round_trace.time_tool_round():
                results = await run_tool_round(
                    reply.tool_calls, self._run_tool, guard, round_trace
                )
            history.extend(
                {"role": "tool", "tool_call_id": call.id, "content": result}
                for call, result in zip(reply.tool_calls, results, strict=True)
            )
            for call, result in zip(reply.tool_calls, results, strict=True):
                # Record BEFORE the event: the next round's answer may cite
                # what this result showed, and the gate consults the ledger.
                self._evidence.record_tool_result(call.name, call.arguments, result)
                yield ToolCallFinished(
                    name=call.name, result_preview=result[:PREVIEW_LEN]
                )
            if guard.exhausted:
                break

        # Budget exhausted (rounds or output volume): ONE answer-only call so
        # the evidence gathered so far isn't wasted on a canned apology.
        with trace.time_forced_answer():
            text = await self._forced_answer(history)
        async for event in self._emit_final(history, text, trace, streaming):
            yield event

    # How far back a path stays "what the conversation is about". Bounded so a
    # long session does not end up marking every file mentioned — which is the
    # same as no personalisation, but with a bigger repo-map cache key.
    MENTION_WINDOW = 12

    def _conversation_mentions(self, history: list[dict]) -> frozenset[str]:
        """Repo paths named anywhere in the recent conversation.

        Reuses the citation regex rather than a new one: a path worth citing is
        a path worth ranking up, and one regex is one thing to keep correct.
        """
        found: set[str] = set()
        for message in history[-self.MENTION_WINDOW:]:
            content = message.get("content")
            if isinstance(content, str):
                found.update(rel for rel, _line in extract_references(content))
        return frozenset(found)

    def _tool_schemas(self) -> list[dict]:
        """The tool belt's JSON schemas, rebuilt only when the belt changes.

        Purely a CPU tidy — it is NOT a prompt-cache fix. The rebuilt list
        serialises byte-identically to the previous one, so providers were
        already seeing a stable tools payload; this just stops reconstructing
        ~15 nested dicts on every turn. `self.tools` is mutable (plugins and
        MCP servers contribute at build time, and design mode will swap
        write_spec in and out), so the belt's identity is the cache key.
        """
        key = tuple(self.tools)
        if self._schema_cache_key != key:
            self._schema_cache_key = key
            self._schema_cache = [tool.schema() for tool in self.tools.values()]
        return self._schema_cache

    async def _forced_answer(self, history: list[dict]) -> str:
        nudge = {
            "role": "user",
            "content": (
                "Tool budget for this question is exhausted. Answer now from "
                "the evidence above, citing only path:line locations you "
                "actually saw. If the evidence is insufficient, say what you "
                "would look at next."
            ),
        }
        try:
            # The nudge never enters `history` — same rule as retry prompts.
            reply = await self.llm.chat([*history, nudge], tools=None)
        except Exception:
            return BUDGET_MESSAGE
        return reply.text or BUDGET_MESSAGE

    async def _emit_final(
        self,
        history: list[dict],
        text: str,
        trace: TurnTrace | None = None,
        streaming: bool = False,
        record: bool = True,
    ) -> AsyncIterator[Event]:
        """Gate, record, and emit the turn's final text (was inline in run_turn).

        `trace` is optional so tests and callers that don't care about timing
        can invoke this directly; timing is then simply not recorded.

        `record=False` is used when a streamed partial answer has already been
        sealed into history: the message still reaches the user, but it is not
        recorded as a separate assistant turn.
        """
        gate_round = trace.rounds[-1] if trace and trace.rounds else None

        def time_gate():
            # A context manager is single-use, so hand out a fresh one per
            # call; nullcontext keeps the untraced path free of branching.
            return gate_round.time_gate() if gate_round else nullcontext()

        if self.grounding_gate is None:
            # Backward-compatible M0 path: no verification.
            if record:
                history.append({"role": "assistant", "content": text})
            yield SpeechChunk(text=text)
            for citation in await asyncio.to_thread(
                extract_citations, text, self.repo_root
            ):
                yield citation
            question = extract_question(text)
            if question is not None:
                yield AskUser(question=question)
            return

        with time_gate():
            gated = await self.grounding_gate.check(text, self._evidence)
        if gated.unverified and self.allow_retry and not streaming:
            # Exactly ONE self-correction round-trip (screen path).
            #
            # `not streaming`: on a streaming turn the model's narration is
            # already on screen or already spoken, so a turn that reaches here
            # (forced answer after budget exhaustion, or a provider error) has
            # made the user wait once already. Spending another full round trip
            # to polish citations the gate is about to strip anyway is the
            # wrong trade — strip, hedge, and get out.
            # The draft and the correction never enter `history` —
            # history records what the user was shown, not drafts.
            retry_messages = [
                *history,
                {"role": "assistant", "content": text},
                {"role": "user", "content": _retry_prompt(gated.unverified)},
            ]
            # tools=None: the retry is a single LLM call, never a new
            # tool loop — the model fixes from context or hedges.
            retry_timer = trace.time_retry() if trace else nullcontext()
            with retry_timer:
                retry_reply = await self.llm.chat(retry_messages, tools=None)
            text = retry_reply.text or text
            # Gate the retry result WITHOUT further retry.
            with time_gate():
                gated = await self.grounding_gate.check(text, self._evidence)

        # After the retry, not before: a retry replaces the text wholesale, so
        # counting the draft's downgrades too would double-count a turn that
        # then went on to cite correctly.
        self.last_unseen = (*self.last_unseen, *gated.unseen)

        if record:
            history.append({"role": "assistant", "content": gated.speech_text})
        yield SpeechChunk(text=gated.speech_text)
        for citation in gated.citations:
            yield citation
        question = extract_question(gated.speech_text)
        if question is not None:
            yield AskUser(question=question)

    async def _gate_sentence(
        self, sentence: str, round_trace: RoundTrace | None = None
    ) -> tuple[str, list]:
        """Gate one streamed sentence before it is spoken. Returns its
        speakable text (unverifiable citations stripped) and its verified
        citations. Grounding is preserved token-stream or not: nothing reaches
        TTS until the gate has cleared it."""
        stripped = sentence.strip()
        if not stripped:
            return "", []
        if self.grounding_gate is None:
            return stripped, []
        timer = round_trace.time_gate() if round_trace else nullcontext()
        with timer:
            gated = await self.grounding_gate.check(stripped, self._evidence)
        # Streamed sentences never retry, so every downgrade here is final.
        self.last_unseen = (*self.last_unseen, *gated.unseen)
        return gated.speech_text, list(gated.citations)

    async def _stream_round(
        self,
        history: list[dict],
        # None when the belt was withheld for an acknowledgement turn.
        schemas: list | None,
        sink: list,
        round_trace: RoundTrace | None = None,
        # Receives the live assistant slot as soon as it exists. `sink` only
        # reports it once the round completes, so a round that raises mid-stream
        # would otherwise leave the caller unable to find what was already said.
        live: list[dict] | None = None,
    ) -> AsyncIterator[Event]:
        """Stream one LLM round, emitting gated SpeechChunk/Citation events as
        each chunk completes (low time-to-first-token). Appends
        (reply, spoken_text, slot) to `sink` so the caller drives the tool loop.

        The chunk unit is channel-dependent: a sentence for voice (the natural
        unit for TTS), a markdown block for text (the natural unit for
        Markdown() rendering). Both feed the same _gate_sentence, so grounding
        is identical either way.

        As chunks are emitted they are also written into a live assistant
        message in `history` (`slot`), so a mid-answer barge-in can truncate
        history to exactly what was heard — parity with the non-streaming path.
        If the round turns out to be a tool call, the caller drops that slot
        (the text was narration, captured in the tool_calls message instead).
        Streamed output is final: it is already on screen or already spoken, so
        there is no coherent way to run the self-correction retry over it."""
        # Voice joins sentences with a space; text joins blocks with a blank
        # line, so the recorded history round-trips as the markdown that was
        # actually rendered.
        split, joiner = (
            (_pop_sentences, " ") if self.voice_active else (_pop_blocks, "\n\n")
        )
        buffer = ""
        spoken: list[str] = []
        slot: dict | None = None
        hedged: set[str] = set()  # hedges already spoken this turn
        # Gating is awaited inline, deliberately. The M10 plan proposed running
        # it as a task per chunk so the stream could keep draining during
        # verification; that was measured and reverted. A gate check costs
        # ~1ms against 50-500ms of token generation between chunks, so the
        # overlap recovers well under 1% of the gap — and deferring emission
        # until the next stream event breaks a real invariant: a model that
        # streams a chunk and then stalls would leave that chunk unspoken and
        # absent from history, so a barge-in would have nothing to truncate.
        # Stage 2.4's line-count cache attacks the same 1ms far more
        # effectively, and without touching this structure.
        async for kind, payload in self.llm.stream(history, tools=schemas):
            if kind == "text":
                if round_trace is not None:
                    # First token off the wire — the metric streaming exists to
                    # move. Idempotent, so calling it per chunk is harmless.
                    round_trace.mark_ttft()
                buffer += payload
                chunks, buffer = split(buffer)
            else:  # ("reply", LLMReply): flush the trailing fragment
                chunks, buffer = ([buffer] if buffer.strip() else []), ""
            for chunk in chunks:
                speech, citations = await self._gate_sentence(chunk, round_trace)
                # Each sentence is gated independently, so a turn with several
                # bad citations repeats one apology. Say it once: the FIRST
                # sentence carrying it keeps it, later ones are trimmed. The
                # information is identical; the repetition is just noise, and
                # aloud it sounds broken.
                for hedge in (HEDGE, LINE_HEDGE, LINE_UNSEEN_HEDGE):
                    if hedge in speech:
                        if hedge in hedged:
                            speech = speech.replace(hedge, "").strip()
                        else:
                            hedged.add(hedge)
                if speech.strip():
                    spoken.append(speech)
                    if slot is None:
                        slot = {"role": "assistant", "content": ""}
                        history.append(slot)
                        if live is not None:
                            live.append(slot)
                    slot["content"] = joiner.join(spoken)
                    yield SpeechChunk(text=speech)
                for citation in citations:
                    yield citation
            if kind == "reply":
                sink.append((payload, joiner.join(spoken).strip(), slot))
                return

    async def _run_tool(self, name: str, args: dict) -> str:
        return await run_tool(self.tools, name, args)
