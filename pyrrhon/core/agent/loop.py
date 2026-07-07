"""The reasoning loop: LLM ⇄ tools, emitting the core event stream.

M1: a GroundingGate can sit between the LLM's final text and the emitted
events. Split-path recovery policy (spec, amended 2026-07-03): screen
channels construct the Agent with allow_retry=True and get one
self-correction LLM round-trip; the M3 speech channel constructs it with
allow_retry=False and unverifiable references are stripped immediately —
a retry costs a full LLM turnaround and breaks the voice latency budget.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path

from pyrrhon.core.agent.escalate import ThinkDeeperTool
from pyrrhon.core.agent.guards import DUPLICATE_NOTE, ToolGuard, assistant_tool_message
from pyrrhon.core.agent.prompts import ESCALATION_NOTE, TEXT_STYLE, VOICE_STYLE
from pyrrhon.core.context import compact_tool_results, maybe_summarize
from pyrrhon.core.providers.llm import InvalidToolCallError
from pyrrhon.core.events import (
    AskUser,
    Event,
    SpeechChunk,
    ToolCallFinished,
    ToolCallStarted,
)
from pyrrhon.core.grounding.citations import extract_citations
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.tools.base import Tool

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


def _invalid_tool_nudge(names: list[str]) -> str:
    return (
        "That tool call was rejected: you named a tool that does not exist. "
        f"Only these tools are available: {', '.join(names)}. "
        "Call one of those, using its exact name, or answer directly."
    )

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


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
        context_budget_tokens: int = 32000,
        context_keep_last: int = 8,
    ):
        self.llm = llm
        self.tools = {tool.name: tool for tool in tools}
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
        if deep_llm is not None:
            deep_tool = ThinkDeeperTool(deep_llm, tools=deep_tools)
            self.tools[deep_tool.name] = deep_tool
            self.system_prompt = system_prompt + "\n" + ESCALATION_NOTE

    async def run_turn(
        self, history: list[dict], user_text: str
    ) -> AsyncIterator[Event]:
        # The base prompt is channel-agnostic; the delivery style (spoken vs.
        # written) is chosen per turn from the current voice_active flag and
        # refreshed on the leading system message. Refreshing (not just
        # injecting on empty history) lets a live /voice on|off toggle change
        # the style mid-session. maybe_summarize always keeps history[0], so it
        # stays the base system message we can safely rewrite here.
        style = VOICE_STYLE if self.voice_active else TEXT_STYLE
        system_content = f"{self.system_prompt}\n{style}"
        if not history:
            history.append({"role": "system", "content": system_content})
        elif history[0].get("role") == "system":
            history[0]["content"] = system_content
        else:
            history.insert(0, {"role": "system", "content": system_content})
        history.append({"role": "user", "content": user_text})
        compact_tool_results(history)
        if self.context_budget_tokens:
            await maybe_summarize(
                history,
                self.llm,
                self.context_budget_tokens,
                keep_last=self.context_keep_last,
            )
        schemas = [tool.schema() for tool in self.tools.values()]
        guard = ToolGuard()
        nudged_invalid_tool = False

        for _ in range(self.max_tool_rounds):
            try:
                reply = await self.llm.chat(history, tools=schemas)
            except InvalidToolCallError:
                # The model called a tool that isn't in our list (a gpt-oss
                # built-in, typically). Nudge once with the real names and let
                # it retry; if it happens again, degrade honestly.
                if not nudged_invalid_tool:
                    nudged_invalid_tool = True
                    history.append(
                        {"role": "user", "content": _invalid_tool_nudge(list(self.tools))}
                    )
                    continue
                async for event in self._emit_final(history, TOOL_RETRY_EXHAUSTED_MESSAGE):
                    yield event
                return
            except Exception as exc:  # provider/network error — never die silently
                logger.warning("llm.chat failed mid-turn: %s: %s", type(exc).__name__, exc)
                async for event in self._emit_final(history, PROVIDER_ERROR_MESSAGE):
                    yield event
                return
            if not reply.tool_calls:
                async for event in self._emit_final(history, reply.text or "(no answer)"):
                    yield event
                return

            if reply.text:
                # Narration spoken while tools run. It passes the gate too:
                # a fabricated citation must never be spoken, even mid-turn.
                narration = reply.text
                if self.grounding_gate is not None:
                    narration = (await self.grounding_gate.check(narration)).speech_text
                if narration.strip():
                    yield SpeechChunk(text=narration)

            history.append(assistant_tool_message(reply))
            for call in reply.tool_calls:
                yield ToolCallStarted(name=call.name, args=call.arguments)
                if guard.is_duplicate(call.name, call.arguments):
                    result = DUPLICATE_NOTE.format(name=call.name)
                else:
                    result = guard.clip(await self._run_tool(call.name, call.arguments))
                history.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
                yield ToolCallFinished(name=call.name, result_preview=result[:PREVIEW_LEN])
            if guard.exhausted:
                break

        # Budget exhausted (rounds or output volume): ONE answer-only call so
        # the evidence gathered so far isn't wasted on a canned apology.
        text = await self._forced_answer(history)
        async for event in self._emit_final(history, text):
            yield event

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
        self, history: list[dict], text: str
    ) -> AsyncIterator[Event]:
        """Gate, record, and emit the turn's final text (was inline in run_turn)."""
        if self.grounding_gate is None:
            # Backward-compatible M0 path: no verification.
            history.append({"role": "assistant", "content": text})
            yield SpeechChunk(text=text)
            for citation in await asyncio.to_thread(
                extract_citations, text, self.repo_root
            ):
                yield citation
            if self.mode == "design":
                question = extract_question(text)
                if question is not None:
                    yield AskUser(question=question)
            return

        gated = await self.grounding_gate.check(text)
        if gated.unverified and self.allow_retry:
            # Exactly ONE self-correction round-trip (screen path).
            # The draft and the correction never enter `history` —
            # history records what the user was shown, not drafts.
            retry_messages = [
                *history,
                {"role": "assistant", "content": text},
                {"role": "user", "content": _retry_prompt(gated.unverified)},
            ]
            # tools=None: the retry is a single LLM call, never a new
            # tool loop — the model fixes from context or hedges.
            retry_reply = await self.llm.chat(retry_messages, tools=None)
            text = retry_reply.text or text
            # Gate the retry result WITHOUT further retry.
            gated = await self.grounding_gate.check(text)

        history.append({"role": "assistant", "content": gated.speech_text})
        yield SpeechChunk(text=gated.speech_text)
        for citation in gated.citations:
            yield citation
        if self.mode == "design":
            question = extract_question(gated.speech_text)
            if question is not None:
                yield AskUser(question=question)

    async def _run_tool(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"ERROR: no tool named '{name}'."
        try:
            return await tool.run(**args)
        except TypeError as exc:
            return f"ERROR: bad arguments for {name}: {exc}"
