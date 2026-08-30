"""The learned token limit: header capture, the failure ratchet, arithmetic."""

from __future__ import annotations

import json

import httpx
import pytest

from pyrrhon.core.context import history_tokens
from pyrrhon.core.providers.errors import (
    ContextLengthExceededError,
    RateLimitExceededError,
)
from pyrrhon.core.providers.limits import FAILURE_BACKOFF, LearnedLimit


def test_a_fresh_limit_knows_nothing():
    limit = LearnedLimit()
    assert limit.limit is None
    assert limit.budget(reserved=1000) is None


def test_a_header_sets_the_ceiling():
    limit = LearnedLimit()
    limit.observe_headers({"x-ratelimit-limit-tokens": "6000"})
    assert limit.limit == 6000


@pytest.mark.parametrize("raw", ["", "lots", "0", "-5", None])
def test_a_useless_header_leaves_it_unlearned(raw):
    """A proxy sending prose must not raise on the turn's critical path, and
    must not be mistaken for a ceiling of zero."""
    limit = LearnedLimit()
    limit.observe_headers({"x-ratelimit-limit-tokens": raw})
    assert limit.limit is None


def test_a_failure_ratchets_below_what_failed():
    limit = LearnedLimit()
    limit.observe_headers({"x-ratelimit-limit-tokens": "6000"})
    limit.observe_failure(5200)
    assert limit.limit is not None and limit.limit < 5200
    assert limit.limit == int(5200 * FAILURE_BACKOFF)


def test_a_larger_later_failure_does_not_raise_the_ceiling():
    limit = LearnedLimit()
    limit.observe_failure(5200)
    tightest = limit.limit
    limit.observe_failure(9000)
    assert limit.limit == tightest


def test_a_header_arriving_after_a_failure_does_not_undo_the_ratchet():
    """The mitigation for a provider that reports an allowance it will not
    honour: a request refused once at size N outranks a header claiming N is
    fine."""
    limit = LearnedLimit()
    limit.observe_failure(5200)
    limit.observe_headers({"x-ratelimit-limit-tokens": "131072"})
    assert limit.limit == int(5200 * FAILURE_BACKOFF)


def test_a_failure_with_nothing_to_learn_is_ignored():
    limit = LearnedLimit()
    limit.observe_failure(0)
    limit.observe_failure(None)
    assert limit.limit is None


def test_budget_subtracts_and_never_goes_negative():
    limit = LearnedLimit()
    limit.observe_headers({"x-ratelimit-limit-tokens": "6000"})
    assert limit.budget(reserved=1000) == 5000
    assert limit.budget(reserved=99999) == 0
    assert limit.budget() == 6000


# --- Capture on the wire ----------------------------------------------------


def _ok_body() -> dict:
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "hi"},
             "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
    }


_SSE = (
    'data: {"id":"x","object":"chat.completion.chunk","created":0,"model":"m",'
    '"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)

RATE_HEADERS = {"x-ratelimit-limit-tokens": "6000"}


def _serving(headers: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("stream"):
            return httpx.Response(
                200,
                content=_SSE.encode(),
                headers={**headers, "content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=_ok_body(), headers=headers)

    return handler


async def test_chat_captures_the_header(mock_llm):
    llm = mock_llm(_serving(RATE_HEADERS))
    await llm.chat([{"role": "user", "content": "hi"}])
    assert llm.limits.limit == 6000


async def test_stream_captures_the_header(mock_llm):
    """Headers land with the HTTP response, before the first chunk, so the
    streaming path is not the poor relation here."""
    llm = mock_llm(_serving(RATE_HEADERS))
    async for _ in llm.stream([{"role": "user", "content": "hi"}]):
        pass
    assert llm.limits.limit == 6000


async def test_a_provider_without_the_header_stays_unlearned(mock_llm):
    llm = mock_llm(_serving({}))
    await llm.chat([{"role": "user", "content": "hi"}])
    assert llm.limits.limit is None


REFUSED = [{"role": "user", "content": "x" * 20_800}]  # ~5200 estimated tokens


async def test_a_too_large_failure_teaches_the_ceiling(mock_llm):
    """The size is derived from the very messages that were refused, so it is
    a local: two concurrent turns through one shared client cannot teach each
    other's sizes."""

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            413, json={"error": {"message": "Request Entity Too Large"}}
        )

    llm = mock_llm(refuse)
    with pytest.raises(ContextLengthExceededError):
        await llm.chat(REFUSED)
    assert llm.limits.limit == int(history_tokens(REFUSED) * FAILURE_BACKOFF)


async def test_a_rate_limit_teaches_nothing_about_size(mock_llm, provider_waits):
    """A 429 says the bucket is empty, not that this request was too big."""

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "Rate limit reached"}})

    llm = mock_llm(refuse)
    with pytest.raises(RateLimitExceededError):
        await llm.chat(REFUSED)
    assert llm.limits.limit is None
