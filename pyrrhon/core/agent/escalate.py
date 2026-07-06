"""Deep-model escalation: a bounded subagent behind the think_deeper tool.

The fast model stays the low-latency voice and decides when to dispatch
(escalation is a tool, not a router). With `tools`, the deep model runs its
own read-only investigation loop in a FRESH context — isolated from the
conversation history — and returns a compact cited report. Depth is
structurally 1: the belt may not contain think_deeper. Without `tools` it
degrades to the M4 single-shot consultant.

Cancellation: run() is awaited inside Agent.run_turn, which runs inside the
Session's cancellable task — barge-in kills the whole investigation.
"""

from __future__ import annotations

from pyrrhon.core.agent.guards import (
    DUPLICATE_NOTE,
    ToolGuard,
    assistant_tool_message,
)
from pyrrhon.core.agent.prompts import DEEP_AGENT_PROMPT, DEEP_SYSTEM_PROMPT
from pyrrhon.core.tools.base import Tool

DEEP_MAX_ROUNDS = 12

_REPORT_NUDGE = (
    "Investigation budget exhausted. Write your report now from the evidence "
    "above; cite only path:line locations you actually saw."
)


class ThinkDeeperTool(Tool):
    name = "think_deeper"
    description = (
        "Dispatch the deep-reasoning subagent for multi-file architectural "
        "analysis. Pass the question plus everything you already know as "
        "`context` — the subagent verifies and extends it with its own "
        "read-only repo tools and returns a cited report."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The hard question to analyze",
            },
            "context": {
                "type": "string",
                "description": "Code excerpts, path:line locations, and findings gathered so far",
            },
        },
        "required": ["question", "context"],
    }

    def __init__(self, deep_llm, tools: list[Tool] | None = None,
                 max_rounds: int = DEEP_MAX_ROUNDS):
        if any(tool.name == self.name for tool in tools or []):
            raise ValueError("think_deeper must not be in its own tool belt")
        self.deep_llm = deep_llm  # anything with async chat(messages, tools=None)
        self.tools = {tool.name: tool for tool in (tools or [])}
        self.max_rounds = max_rounds

    async def run(self, question: str, context: str) -> str:
        prompt = DEEP_AGENT_PROMPT if self.tools else DEEP_SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"{question}\n\n# Context gathered by the fast model\n\n{context}",
            },
        ]
        schemas = [tool.schema() for tool in self.tools.values()] or None
        guard = ToolGuard()
        try:
            for _ in range(self.max_rounds):
                reply = await self.deep_llm.chat(messages, tools=schemas)
                if not reply.tool_calls:
                    return reply.text or "ERROR: deep model returned no text."
                messages.append(assistant_tool_message(reply))
                for call in reply.tool_calls:
                    if guard.is_duplicate(call.name, call.arguments):
                        result = DUPLICATE_NOTE.format(name=call.name)
                    else:
                        result = guard.clip(
                            await self._run_tool(call.name, call.arguments)
                        )
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )
                if guard.exhausted:
                    break
            reply = await self.deep_llm.chat(
                [*messages, {"role": "user", "content": _REPORT_NUDGE}], tools=None
            )
            return reply.text or "ERROR: deep model returned no text."
        except Exception as exc:  # provider/network failure must not kill the turn
            return f"ERROR: deep model call failed: {exc}"

    async def _run_tool(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"ERROR: no tool named '{name}'."
        try:
            return await tool.run(**args)
        except TypeError as exc:
            return f"ERROR: bad arguments for {name}: {exc}"
