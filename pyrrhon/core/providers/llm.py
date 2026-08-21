"""Provider-agnostic LLM access via the OpenAI-compatible chat completions API.

One adapter covers OpenAI, Groq, OpenRouter, Cerebras, and Gemini's compat
endpoint — a new provider is a config entry, not new code.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, BadRequestError

from pyrrhon.config.settings import ModelSlot, Settings

logger = logging.getLogger("pyrrhon.providers")


class MissingAPIKeyError(RuntimeError):
    pass


class InvalidToolCallError(RuntimeError):
    """The model emitted a tool call the provider rejected as not in the tool
    list — e.g. gpt-oss on Groq hallucinating its built-in `search`/`python`.
    Distinct from an outage: the loop recovers by nudging with the real tool
    names, so it must not be swallowed as a generic 4xx."""


class ContextLengthExceededError(RuntimeError):
    """The prompt (history + tool results) exceeded the model's context window.
    A 4xx, but recoverable: the loop compacts the conversation and retries the
    round rather than dying — so it must not be swallowed as a generic error
    (which is what stranded the turn in the field). Mirrors Codex catching
    ContextWindowExceeded and compacting before continuing."""


def _is_tool_validation_error(exc: BadRequestError) -> bool:
    if getattr(exc, "code", None) == "tool_use_failed":
        return True
    text = str(exc)
    return "not in request.tools" in text or "Tool call validation failed" in text


def _is_context_length_error(exc: BadRequestError) -> bool:
    if getattr(exc, "code", None) == "context_length_exceeded":
        return True
    text = str(exc).lower()
    # Providers word this differently: OpenAI "maximum context length",
    # others "context length"/"context window"/"reduce the length".
    return (
        "context_length_exceeded" in text
        or "maximum context length" in text
        or "context window" in text
        or "reduce the length" in text
        or "too many tokens" in text
    )


def _raise_if_typed(exc: BadRequestError) -> None:
    """Re-raise a provider 4xx as a typed error the agent loop recovers from,
    or return so the caller re-raises it verbatim. Shared by chat() + stream()."""
    if _is_tool_validation_error(exc):
        raise InvalidToolCallError(str(exc)) from exc
    if _is_context_length_error(exc):
        raise ContextLengthExceededError(str(exc)) from exc


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class TokenUsage:
    """Exact counts from the provider. None when the provider omits them.

    Worth capturing because context.py budgets with len(text)//4 while the real
    number arrives on every response. The estimate is not replaced by it —
    prompt_tokens describes the request that was SENT, not the history that
    exists now — it is CALIBRATED by it: see context.token_scale.
    """

    prompt: int
    completion: int
    total: int


def _usage_from(response) -> TokenUsage | None:
    """Read a usage block off a response or a streamed chunk.

    getattr throughout: the SDK's model objects and a local server's looser
    JSON both arrive here, and a missing field must degrade to "no usage"
    rather than raise on the turn's critical path.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return TokenUsage(
        prompt=getattr(usage, "prompt_tokens", 0) or 0,
        completion=getattr(usage, "completion_tokens", 0) or 0,
        total=getattr(usage, "total_tokens", 0) or 0,
    )


@dataclass(frozen=True)
class LLMReply:
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: TokenUsage | None = None


class OpenAICompatLLM:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_retries: int = 2,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=max_retries
        )

    def _generation_kwargs(self) -> dict:
        """Only send knobs that were actually configured.

        Omitting them entirely (rather than sending None) keeps the request
        payload byte-identical to the pre-[model] behaviour for anyone who
        hasn't set them, and avoids providers that reject an explicit null.
        """
        kwargs: dict = {}
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        return kwargs

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMReply:
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            **self._generation_kwargs(),
        }
        if tools:
            kwargs["tools"] = tools
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except BadRequestError as exc:
            _raise_if_typed(exc)
            raise
        message = response.choices[0].message
        calls = tuple(
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=json.loads(tc.function.arguments or "{}"),
            )
            for tc in (message.tool_calls or ())
        )
        return LLMReply(
            text=message.content, tool_calls=calls, usage=_usage_from(response)
        )

    async def stream(self, messages: list[dict], tools: list[dict] | None = None):
        """Stream a reply as ('text', delta) events, then one ('reply', LLMReply)
        with the complete text and accumulated tool calls.

        Enables sentence-by-sentence speech (low time-to-first-token) while the
        agent still gets the whole structured reply to drive the tool loop. Same
        typed errors as chat(). Tool-call fragments arrive spread across chunks
        (id/name/arguments in pieces), so they are reassembled by index."""
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            # Providers omit usage from a streamed response unless asked. Groq,
            # OpenAI, OpenRouter and Anthropic's compat endpoint all honour
            # this; ones that don't ignore an unknown field rather than 400.
            "stream_options": {"include_usage": True},
            **self._generation_kwargs(),
        }
        if tools:
            kwargs["tools"] = tools
        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except BadRequestError as exc:
            _raise_if_typed(exc)
            raise
        text_parts: list[str] = []
        usage: TokenUsage | None = None
        # index -> {"id", "name", "args"} accumulated across delta chunks
        frags: dict[int, dict] = {}
        try:
            async for chunk in stream:
                # The usage-bearing chunk carries an EMPTY choices list, so
                # this has to run before the guard below or it is never seen.
                if getattr(chunk, "usage", None) is not None:
                    usage = _usage_from(chunk)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:
                    text_parts.append(delta.content)
                    yield ("text", delta.content)
                for tc in delta.tool_calls or ():
                    slot = frags.setdefault(
                        tc.index, {"id": None, "name": None, "args": ""}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments
        except BadRequestError as exc:
            _raise_if_typed(exc)
            raise
        calls = tuple(
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=json.loads(slot["args"] or "{}"),
            )
            for index, slot in sorted(frags.items())
            if slot["name"]
        )
        yield (
            "reply",
            LLMReply(
                text="".join(text_parts) or None, tool_calls=calls, usage=usage
            ),
        )


def create_llm(
    slot: ModelSlot, settings: Settings, max_retries: int = 2
) -> OpenAICompatLLM:
    provider = settings.provider_for(slot)
    if provider.api_key_env:
        api_key = os.environ.get(provider.api_key_env, "")
        if not api_key:
            raise MissingAPIKeyError(
                f"Set {provider.api_key_env} to use provider '{slot.provider}'."
            )
    else:
        api_key = "local"  # SDK requires non-empty; local servers ignore it
    return OpenAICompatLLM(
        model=slot.model,
        api_key=api_key,
        base_url=provider.base_url,
        max_retries=max_retries,
        max_tokens=settings.model.max_tokens,
        temperature=settings.model.temperature,
    )


# Failures the fallback chain inspects. The openai SDK wraps httpx transport
# errors in APIConnectionError (APITimeoutError subclasses it); we catch the
# raw httpx layer too so a bare httpx error from a future adapter also falls
# over. APIStatusError is in the tuple, but chat() only falls over on 5xx —
# 4xx re-raises.
_FALLBACK_ERRORS = (
    httpx.ConnectError,
    httpx.TimeoutException,
    APIConnectionError,
    APIStatusError,
)


class FallbackLLM:
    """A chain of OpenAICompatLLMs behind the agent's duck-typed chat().

    Sticky: once a provider fails we stay on its successor for the rest of
    the session instead of paying a connect timeout on every turn (spec:
    "provider failure -> configured fallback chain with a one-sentence
    spoken notice"). 4xx errors re-raise immediately — a bad key is user
    error, not an outage.
    """

    def __init__(self, chain: list[OpenAICompatLLM], on_switch=None):
        if not chain:
            raise ValueError("FallbackLLM needs at least one provider in the chain")
        self.chain = list(chain)
        self.on_switch = on_switch  # callable(provider_index) | None
        self._active = 0

    @property
    def model(self) -> str:
        return self.chain[self._active].model

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMReply:
        index = self._active
        while True:
            try:
                return await self.chain[index].chat(messages, tools=tools)
            except _FALLBACK_ERRORS as exc:
                if isinstance(exc, APIStatusError) and exc.status_code < 500:
                    raise  # 4xx: not a provider outage — never fall over
                if index + 1 >= len(self.chain):
                    raise  # chain exhausted: re-raise the last error
                index += 1
                self._active = index
                logger.warning(
                    "provider failed (%s); switching to '%s'",
                    type(exc).__name__,
                    self.chain[index].model,
                )
                if self.on_switch is not None:
                    self.on_switch(index)

    async def stream(self, messages: list[dict], tools: list[dict] | None = None):
        """Streaming counterpart to chat(). Falls over only if a provider fails
        BEFORE its first event — once tokens are flowing we can't cleanly switch
        mid-utterance, so a mid-stream failure propagates (rare; the next turn
        falls over via chat()'s sticky _active)."""
        index = self._active
        while True:
            gen = self.chain[index].stream(messages, tools=tools)
            try:
                first = await gen.__anext__()
            except StopAsyncIteration:
                return
            except _FALLBACK_ERRORS as exc:
                if isinstance(exc, APIStatusError) and exc.status_code < 500:
                    raise
                if index + 1 >= len(self.chain):
                    raise
                index += 1
                self._active = index
                logger.warning(
                    "provider failed (%s); switching to '%s'",
                    type(exc).__name__,
                    self.chain[index].model,
                )
                if self.on_switch is not None:
                    self.on_switch(index)
                continue
            self._active = index  # committed to this provider for the stream
            yield first
            async for event in gen:
                yield event
            return


def create_llm_with_fallbacks(
    slot_name: str, settings: Settings
) -> FallbackLLM | OpenAICompatLLM:
    """Build the LLM for a slot, honoring [fallbacks] from settings."""
    slots = {"fast": settings.fast, "deep": settings.deep_slot}
    if slot_name not in slots:
        raise KeyError(f"unknown model slot '{slot_name}' (expected 'fast' or 'deep')")
    slot = slots[slot_name]

    entries = settings.fallbacks.get(slot_name, [])
    if not entries:
        return create_llm(slot, settings)

    # The chain replaces the SDK's internal retries (max_retries=0): retrying
    # a dead provider before falling back doubles worst-case latency.
    chain = [create_llm(slot, settings, max_retries=0)]
    for entry in entries:
        provider, sep, model = entry.partition("/")
        entry_slot = ModelSlot(provider=provider, model=model if sep else slot.model)
        try:
            chain.append(create_llm(entry_slot, settings, max_retries=0))
        except MissingAPIKeyError as exc:
            logger.warning("skipping fallback provider '%s': %s", provider, exc)
    if len(chain) == 1:
        return chain[0]  # nothing usable behind the primary — behave like M0
    return FallbackLLM(chain)
