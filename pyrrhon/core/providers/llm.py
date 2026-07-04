"""Provider-agnostic LLM access via the OpenAI-compatible chat completions API.

One adapter covers OpenAI, Groq, OpenRouter, Cerebras, and Gemini's compat
endpoint — a new provider is a config entry, not new code.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from openai import AsyncOpenAI

from pyrrhon.config.settings import ModelSlot, Settings


class MissingAPIKeyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class LLMReply:
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


class OpenAICompatLLM:
    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMReply:
        kwargs: dict = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        response = await self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        calls = tuple(
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=json.loads(tc.function.arguments or "{}"),
            )
            for tc in (message.tool_calls or ())
        )
        return LLMReply(text=message.content, tool_calls=calls)


def create_llm(slot: ModelSlot, settings: Settings) -> OpenAICompatLLM:
    provider = settings.provider_for(slot)
    api_key = os.environ.get(provider.api_key_env, "")
    if not api_key:
        raise MissingAPIKeyError(
            f"Set {provider.api_key_env} to use provider '{slot.provider}'."
        )
    return OpenAICompatLLM(model=slot.model, api_key=api_key, base_url=provider.base_url)
