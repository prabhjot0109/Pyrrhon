"""The reasoning loop: LLM ⇄ tools, emitting the core event stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from pyrrhon.core.events import (
    Event,
    SpeechChunk,
    ToolCallFinished,
    ToolCallStarted,
)
from pyrrhon.core.grounding.citations import extract_citations
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.tools.base import Tool

PREVIEW_LEN = 200


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
    ):
        self.llm = llm
        self.tools = {tool.name: tool for tool in tools}
        self.system_prompt = system_prompt
        self.repo_root = repo_root
        self.max_tool_rounds = max_tool_rounds

    async def run_turn(
        self, history: list[dict], user_text: str
    ) -> AsyncIterator[Event]:
        if not history:
            history.append({"role": "system", "content": self.system_prompt})
        history.append({"role": "user", "content": user_text})
        schemas = [tool.schema() for tool in self.tools.values()]

        for _ in range(self.max_tool_rounds):
            reply = await self.llm.chat(history, tools=schemas)
            if not reply.tool_calls:
                text = reply.text or "(no answer)"
                history.append({"role": "assistant", "content": text})
                yield SpeechChunk(text=text)
                for citation in await asyncio.to_thread(extract_citations, text, self.repo_root):
                    yield citation
                return

            history.append(_assistant_tool_message(reply))
            for call in reply.tool_calls:
                yield ToolCallStarted(name=call.name, args=call.arguments)
                result = await self._run_tool(call.name, call.arguments)
                history.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
                yield ToolCallFinished(name=call.name, result_preview=result[:PREVIEW_LEN])

        text = (
            "I hit my tool budget for this question — ask me to continue "
            "and I'll keep digging."
        )
        history.append({"role": "assistant", "content": text})
        yield SpeechChunk(text=text)

    async def _run_tool(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"ERROR: no tool named '{name}'."
        try:
            return await tool.run(**args)
        except TypeError as exc:
            return f"ERROR: bad arguments for {name}: {exc}"


def _assistant_tool_message(reply: LLMReply) -> dict:
    return {
        "role": "assistant",
        "content": reply.text,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in reply.tool_calls
        ],
    }
