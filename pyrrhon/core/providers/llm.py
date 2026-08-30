"""Provider-agnostic LLM access via the OpenAI-compatible chat completions API.

One adapter covers OpenAI, Groq, OpenRouter, Cerebras, and Gemini's compat
endpoint — a new provider is a config entry, not new code.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass

import httpx
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI

from pyrrhon.config.settings import ModelSlot, Settings
from pyrrhon.core.context import history_tokens
from pyrrhon.core.providers.errors import (
    ContextLengthExceededError,
    InvalidToolCallError,
    RateLimitExceededError,
    classify,
)
from pyrrhon.core.providers.limits import LearnedLimit

logger = logging.getLogger("pyrrhon.providers")

# How many times ONE request may wait out a 429 before giving up. One: past
# that, a second provider is a better answer than a longer wait, and
# FallbackLLM is the thing that can supply one.
MAX_RATE_LIMIT_WAITS = 1

# The longest wait worth taking. A retry-after above this is not clamped and
# served — it is declined outright, because waiting 20 seconds and THEN
# reporting a failure is strictly worse than reporting it now with the real
# number in hand. The user gets "clears in about 45 seconds" immediately.
MAX_RATE_LIMIT_WAIT = 20.0

# A 429 with no retry-after at all. Long enough to be worth taking against a
# token bucket, short enough not to strand a voice turn.
DEFAULT_RATE_LIMIT_WAIT = 5.0


class MissingAPIKeyError(RuntimeError):
    pass




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
    """One reply, and how it ended.

    `finish_reason` is the provider's own word for why generation stopped —
    "stop" for a finished answer, "length" for one cut off at max_tokens,
    "tool_calls" for a round that wants tools. Carrying it is what lets the
    loop tell a fragment from an answer; without it a half-sentence goes
    through the grounding gate and gets spoken as though it were finished.
    None when the provider omits it, in which case the loop behaves as it did
    before this field existed.
    """

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: TokenUsage | None = None
    finish_reason: str | None = None


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
        # One per client, so a fallback chain learns each provider's ceiling
        # separately — they are different accounts with different tiers.
        self.limits = LearnedLimit()
        # callable(delay_seconds: float, reason: str) | None. Same attachment
        # shape as FallbackLLM.on_switch: the channel sets it and decides what
        # to draw. A wait this long with nothing on screen reads as a hang.
        self.on_retry = None
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=max_retries
        )

    def _raise_if_typed(self, exc: APIError, messages: list[dict]) -> None:
        """Re-raise a provider failure as a typed error the agent loop recovers
        from, or return so the caller re-raises it verbatim.

        The kinds that get a type are exactly the ones something downstream
        knows how to act on. `credentials` and `outage` deliberately fall
        through: a bad key is user error the message already names, and an
        outage is FallbackLLM's to answer, which it does by inspecting the SDK
        exception — so wrapping it here would take that decision away from it.

        A `too_large` also teaches the learned limit. The size is derived here
        from the very `messages` that were refused rather than being threaded
        in from the loop: it is the same number the loop computes, it costs
        nothing on the happy path, and a local cannot be cross-contaminated by
        a second turn sharing this client.
        """
        fault = classify(exc)
        if fault is None:
            return
        if fault.kind == "tool_validation":
            raise InvalidToolCallError(fault.message) from exc
        if fault.kind == "too_large":
            self.limits.observe_failure(history_tokens(messages))
            raise ContextLengthExceededError(fault.message) from exc
        if fault.kind == "rate_limited":
            raise RateLimitExceededError(fault.message, fault.retry_after) from exc

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

    def preconnect(self) -> "asyncio.Task":
        """Warm the connection pool while the user is still reading the splash.

        The first request of a session otherwise pays DNS, TCP and TLS inside
        its own latency budget — roughly 100-200ms on a warm network and
        considerably more on a cold one. start_channel already runs the repo
        scan, the index load and the splash, and the user then has to type or
        speak, so that window is free.

        Fire and forget by construction. The task swallows everything and logs
        at debug, because its failure mode is that the first real request pays
        the handshake exactly as it does today — an unreachable provider must
        not put a warning on screen before the user has asked for anything.
        The handle comes back so the caller can cancel it on teardown instead
        of leaving a pending task behind.
        """
        return asyncio.create_task(self._warm())

    async def _warm(self) -> None:
        try:
            # models.list() rather than a bare socket: it is public SDK
            # surface, it costs no tokens, and it warms DNS, TCP, TLS and the
            # HTTP/1.1 pool in one go. Providers that do not implement it 404,
            # which warms the pool just as well.
            await self._client.models.list()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("preconnect skipped (%s)", type(exc).__name__)

    async def _create(self, kwargs: dict, messages: list[dict]):
        """One request, waiting out a 429 if the wait is worth taking.

        .with_raw_response, not .create: the rate-limit headers ride every
        response and are the only place the endpoint says how big a request it
        will accept. .parse() returns exactly what the plain call would have,
        so every caller below is unchanged.

        The SDK's own max_retries stays for transient 5xx — its backoff is
        sub-ten-second and uniform, which is right for an upstream blip and
        useless against a token bucket refilling over a full minute. Raising
        it would also lengthen every 5xx retry, which is a different failure
        with a different right answer.

        `asyncio.sleep` rather than a timer: the wait sits inside the Session's
        cancellable task, so a barge-in kills it like anything else.
        """
        waits = 0
        while True:
            try:
                return await self._client.chat.completions.with_raw_response.create(
                    **kwargs
                )
            except APIError as exc:
                fault = classify(exc)
                delay = (fault.retry_after or DEFAULT_RATE_LIMIT_WAIT) if fault else 0.0
                if (
                    fault is not None
                    and fault.kind == "rate_limited"
                    and waits < MAX_RATE_LIMIT_WAITS
                    and delay <= MAX_RATE_LIMIT_WAIT
                ):
                    waits += 1
                    logger.warning("rate limited; retrying in %.0fs", delay)
                    if self.on_retry is not None:
                        self.on_retry(delay, fault.message)
                    await asyncio.sleep(delay)
                    continue
                self._raise_if_typed(exc, messages)
                raise

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
        raw = await self._create(kwargs, messages)
        self.limits.observe_headers(raw.headers)
        response = raw.parse()
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
            text=message.content,
            tool_calls=calls,
            usage=_usage_from(response),
            finish_reason=response.choices[0].finish_reason,
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
        raw = await self._create(kwargs, messages)
        # HTTP response headers land before the body, so the streaming path is
        # not the poor relation here: the ceiling is known before chunk one.
        self.limits.observe_headers(raw.headers)
        stream = raw.parse()
        text_parts: list[str] = []
        usage: TokenUsage | None = None
        # Arrives on the LAST content delta, before the usage chunk — whose
        # `choices` list is empty, so it cannot clobber this. Kept as "last
        # non-None wins" rather than "last chunk wins" for the same reason.
        finish_reason: str | None = None
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
                if chunk.choices[0].finish_reason is not None:
                    finish_reason = chunk.choices[0].finish_reason
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
        except APIError as exc:
            self._raise_if_typed(exc, messages)
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
                text="".join(text_parts) or None,
                tool_calls=calls,
                usage=usage,
                finish_reason=finish_reason,
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
# 4xx re-raises, EXCEPT the rate-limited case, which arrives typed rather than
# as a status error and is the one 4xx a second provider genuinely answers.
_FALLBACK_ERRORS = (
    httpx.ConnectError,
    httpx.TimeoutException,
    APIConnectionError,
    APIStatusError,
    RateLimitExceededError,
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
    def on_retry(self):
        return self.chain[self._active].on_retry

    @on_retry.setter
    def on_retry(self, callback) -> None:
        """Fan out to every link, so a channel wires this the same one line
        whether the user configured a chain or a single provider."""
        for link in self.chain:
            link.on_retry = callback

    @property
    def model(self) -> str:
        return self.chain[self._active].model

    @property
    def limits(self):
        """The ACTIVE provider's learned limit.

        Each link learns its own, because they are different accounts on
        different tiers. Reading through to the active one means a fallover
        re-points the budget at the provider actually answering, rather than
        leaving it measured against a link nobody is using.
        """
        return self.chain[self._active].limits

    def preconnect(self) -> "asyncio.Task":
        """Warm the ACTIVE link only. Warming a fallback that may never be
        reached spends a request to save a handshake nobody pays."""
        return self.chain[self._active].preconnect()

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMReply:
        index = self._active
        while True:
            try:
                return await self.chain[index].chat(messages, tools=tools)
            except _FALLBACK_ERRORS as exc:
                # The rule was "never fall over on a 4xx", because a 4xx is
                # normally a bad key and a bad key on provider two is no
                # better than on provider one. A 429 is the exception: the
                # account's allowance is spent, which is an availability
                # problem, and it reaches here typed because the link already
                # waited out whatever wait was worth taking.
                if isinstance(exc, APIStatusError) and exc.status_code < 500:
                    raise
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
                # See chat(): a rate limit is the one 4xx worth falling over on.
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
