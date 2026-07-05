"""Deep-model escalation: the fast model consults the deep slot via a tool.

Escalation is a tool, not a router — the fast model stays the low-latency
voice and decides when a question deserves the deep model (spec: two model
slots; Settings.deep_slot falls back to fast when unset).
"""

from __future__ import annotations

from pyrrhon.core.agent.prompts import DEEP_SYSTEM_PROMPT
from pyrrhon.core.tools.base import Tool


class ThinkDeeperTool(Tool):
    name = "think_deeper"
    description = (
        "Consult the deep reasoning model for multi-file architectural analysis. "
        "Pass the question AND all evidence you gathered (code excerpts, path:line "
        "locations, git history) as `context` — the deep model sees only that."
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

    def __init__(self, deep_llm):
        self.deep_llm = deep_llm  # anything with async chat(messages, tools=None) -> LLMReply

    async def run(self, question: str, context: str) -> str:
        messages = [
            {"role": "system", "content": DEEP_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{question}\n\n# Context gathered by the fast model\n\n{context}",
            },
        ]
        try:
            reply = await self.deep_llm.chat(messages)
        except Exception as exc:  # provider/network failure must not kill the turn
            return f"ERROR: deep model call failed: {exc}"
        return reply.text or "ERROR: deep model returned no text."
