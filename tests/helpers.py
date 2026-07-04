"""Test doubles shared across the suite."""

from __future__ import annotations

from pyrrhon.core.providers.llm import LLMReply


class FakeLLM:
    """Duck-typed stand-in for OpenAICompatLLM: returns scripted replies in order."""

    def __init__(self, replies: list[LLMReply]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMReply:
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self._replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        return self._replies.pop(0)
