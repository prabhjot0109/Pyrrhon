"""The status-code taxonomy, against the real SDK exception shapes.

Constructed rather than mocked on purpose: the bug this closes was that a
`BadRequestError`-only `except` never saw a 413 or a 429, and a mock would
have agreed with whatever the code assumed. `status_code` comes off the
attached `httpx.Response`, so these are the objects the SDK itself builds.
"""

from __future__ import annotations

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from pyrrhon.core.providers.errors import classify, retry_after_seconds
from tests.conftest import PROVIDER_BASE_URL

REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def _response(status: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, request=REQUEST, headers=headers or {})


def _status_error(status: int, message: str, body=None, headers=None) -> APIStatusError:
    return APIStatusError(message, response=_response(status, headers), body=body)


def test_tool_validation_400_by_code():
    exc = BadRequestError(
        "Tool call validation failed",
        response=_response(400),
        body={"code": "tool_use_failed", "message": "search is not a tool"},
    )
    fault = classify(exc)
    assert fault is not None and fault.kind == "tool_validation"
    assert fault.retry_after is None


def test_tool_validation_400_by_prose():
    exc = BadRequestError(
        "tool 'python' was not in request.tools", response=_response(400), body=None
    )
    assert classify(exc).kind == "tool_validation"


def test_context_length_400():
    exc = BadRequestError(
        "This model's maximum context length is 8192 tokens",
        response=_response(400),
        body=None,
    )
    fault = classify(exc)
    assert fault is not None and fault.kind == "too_large"


def test_413_is_too_large():
    """The status decides. This is the shape that used to escape untyped."""
    fault = classify(_status_error(413, "Request Entity Too Large"))
    assert fault is not None and fault.kind == "too_large"
    assert fault.status_code == 413


def test_429_carries_retry_after_seconds():
    exc = RateLimitError(
        "Rate limit reached for model",
        response=_response(429, {"retry-after": "20"}),
        body=None,
    )
    fault = classify(exc)
    assert fault is not None and fault.kind == "rate_limited"
    assert fault.retry_after == 20.0


def test_429_without_a_header_has_no_delay():
    exc = RateLimitError("slow down", response=_response(429), body=None)
    fault = classify(exc)
    assert fault.kind == "rate_limited" and fault.retry_after is None


def test_429_prefers_millisecond_header():
    exc = RateLimitError(
        "slow down",
        response=_response(429, {"retry-after": "20", "retry-after-ms": "1500"}),
        body=None,
    )
    assert classify(exc).retry_after == 1.5


def test_credentials_401_and_403():
    unauthorized = AuthenticationError(
        "Invalid API Key", response=_response(401), body=None
    )
    assert classify(unauthorized).kind == "credentials"
    assert classify(_status_error(403, "forbidden")).kind == "credentials"


def test_5xx_is_an_outage():
    exc = InternalServerError("upstream boom", response=_response(500), body=None)
    assert classify(exc).kind == "outage"


def test_transport_errors_are_outages():
    assert classify(httpx.ConnectError("no route", request=REQUEST)).kind == "outage"
    assert classify(httpx.ReadTimeout("slow", request=REQUEST)).kind == "outage"
    assert classify(APITimeoutError(request=REQUEST)).kind == "outage"
    assert classify(APIConnectionError(request=REQUEST)).kind == "outage"


def test_unrecognised_failures_classify_as_nothing():
    """None means 'the caller re-raises verbatim'. An unknown 400 must not be
    laundered into a kind the loop believes it can recover from."""
    assert classify(_status_error(400, "your JSON is malformed")) is None
    assert classify(_status_error(404, "no such model")) is None
    assert classify(ValueError("not a provider failure at all")) is None


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({}, None),
        ({"retry-after": "0"}, None),
        ({"retry-after": "not a number"}, None),
        ({"retry-after": "2.5"}, 2.5),
        ({"retry-after-ms": "0"}, None),
        ({"retry-after-ms": "250"}, 0.25),
    ],
)
def test_retry_after_parsing(headers, expected):
    assert retry_after_seconds(headers) == expected


def test_retry_after_http_date():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    future = datetime.now(timezone.utc) + timedelta(seconds=30)
    parsed = retry_after_seconds({"retry-after": format_datetime(future)})
    assert parsed is not None and 25 < parsed <= 31
    past = datetime.now(timezone.utc) - timedelta(seconds=30)
    assert retry_after_seconds({"retry-after": format_datetime(past)}) is None


# --- The client raises from the taxonomy ------------------------------------
#
# Driven through httpx.MockTransport rather than a stubbed `.create`, so the
# openai SDK builds the exception itself and the tests keep holding when the
# acquisition path changes (Task 5 switches chat() to .with_raw_response).

import json

from pyrrhon.core.providers.errors import (
    ContextLengthExceededError,
    InvalidToolCallError,
    RateLimitExceededError,
)


def _failing(status: int, body: dict, headers: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers or {})

    return handler


async def _drive_stream(llm):
    return [event async for event in llm.stream([{"role": "user", "content": "hi"}])]


async def _drive_chat(llm):
    return await llm.chat([{"role": "user", "content": "hi"}])


TOOL_BODY = {"error": {"message": "tool call validation failed", "code": "tool_use_failed"}}
LONG_BODY = {"error": {"message": "Please reduce the length of the messages"}}
BIG_BODY = {"error": {"message": "Request Entity Too Large"}}
LIMIT_BODY = {"error": {"message": "Rate limit reached for model", "code": "rate_limit_exceeded"}}
KEY_BODY = {"error": {"message": "Invalid API Key", "code": "invalid_api_key"}}
ODD_BODY = {"error": {"message": "malformed json in messages[0]"}}


@pytest.mark.parametrize("drive", [_drive_chat, _drive_stream])
@pytest.mark.parametrize(
    "status,body,expected",
    [
        (400, TOOL_BODY, InvalidToolCallError),
        (400, LONG_BODY, ContextLengthExceededError),
        (413, BIG_BODY, ContextLengthExceededError),
        (429, LIMIT_BODY, RateLimitExceededError),
    ],
)
async def test_recoverable_failures_become_typed_errors(
    mock_llm, provider_waits, drive, status, body, expected
):
    with pytest.raises(expected):
        await drive(mock_llm(_failing(status, body)))


@pytest.mark.parametrize("drive", [_drive_chat, _drive_stream])
async def test_rate_limit_carries_its_delay(mock_llm, provider_waits, drive):
    llm = mock_llm(_failing(429, LIMIT_BODY, {"retry-after": "20"}))
    with pytest.raises(RateLimitExceededError) as caught:
        await drive(llm)
    assert caught.value.retry_after == 20.0


@pytest.mark.parametrize("drive", [_drive_chat, _drive_stream])
@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, KEY_BODY, AuthenticationError),
        (400, ODD_BODY, BadRequestError),
        (500, {"error": {"message": "boom"}}, InternalServerError),
    ],
)
async def test_unrecoverable_failures_reach_the_caller_verbatim(
    mock_llm, drive, status, body, expected
):
    """A bad key and an outage are not the loop's to recover from, and an
    unrecognised 400 must not be mistaken for one that is."""
    with pytest.raises(expected):
        await drive(mock_llm(_failing(status, body)))


@pytest.mark.parametrize("drive", [_drive_chat, _drive_stream])
async def test_transport_failure_reaches_the_fallback_chain(mock_llm, drive):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with pytest.raises(APIConnectionError):
        await drive(mock_llm(refuse))


async def test_a_good_reply_still_works(mock_llm):
    """The guard against a taxonomy that swallows the happy path."""

    def ok(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["model"] == "m"
        return httpx.Response(
            200,
            json={
                "id": "x", "object": "chat.completion", "created": 0, "model": "m",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "hello"},
                     "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            },
        )

    reply = await _drive_chat(mock_llm(ok))
    assert reply.text == "hello"
    assert reply.usage is not None and reply.usage.prompt == 5


# --- A retry policy that understands a token bucket -------------------------

from pyrrhon.core.events import ProviderRetrying  # noqa: E402
from pyrrhon.core.providers.llm import (  # noqa: E402
    MAX_RATE_LIMIT_WAIT,
    MAX_RATE_LIMIT_WAITS,
)

OK_BODY = {
    "id": "x", "object": "chat.completion", "created": 0, "model": "m",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hello"},
         "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
}


def _rate_limited_then_ok(headers: dict, refusals: int = 1):
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        if seen["n"] <= refusals:
            return httpx.Response(429, json=LIMIT_BODY, headers=headers)
        return httpx.Response(200, json=OK_BODY)

    return handler


async def test_a_429_is_waited_out_and_announced(mock_llm, provider_waits):
    retries: list[ProviderRetrying] = []
    llm = mock_llm(_rate_limited_then_ok({"retry-after": "1"}))
    llm.on_retry = lambda delay, reason: retries.append(
        ProviderRetrying(delay_seconds=delay, reason=reason)
    )

    reply = await _drive_chat(llm)

    assert reply.text == "hello"
    assert provider_waits == [1.0]
    assert len(retries) == 1 and retries[0].delay_seconds == 1.0


async def test_the_wait_is_bounded(mock_llm, provider_waits):
    """Past the bound a second provider is a better answer than a longer wait,
    and FallbackLLM is the thing that can supply one."""
    llm = mock_llm(_rate_limited_then_ok({"retry-after": "1"}, refusals=99))
    with pytest.raises(RateLimitExceededError):
        await _drive_chat(llm)
    assert len(provider_waits) == MAX_RATE_LIMIT_WAITS


async def test_a_wait_too_long_to_be_worth_taking_is_declined(mock_llm, provider_waits):
    """Waiting the ceiling out and THEN reporting failure is strictly worse
    than reporting now with the real number in hand."""
    long_wait = str(int(MAX_RATE_LIMIT_WAIT) + 25)
    llm = mock_llm(_rate_limited_then_ok({"retry-after": long_wait}, refusals=99))
    with pytest.raises(RateLimitExceededError) as caught:
        await _drive_chat(llm)
    assert provider_waits == []
    assert caught.value.retry_after == float(long_wait)


async def test_the_streaming_path_waits_too(mock_llm, provider_waits):
    llm = mock_llm(_rate_limited_then_ok({"retry-after": "1"}, refusals=99))
    with pytest.raises(RateLimitExceededError):
        await _drive_stream(llm)
    assert len(provider_waits) == MAX_RATE_LIMIT_WAITS


# --- Preconnect -------------------------------------------------------------


async def test_preconnect_cannot_raise_or_warn(caplog):
    """The whole contract. It is fire-and-forget: a failure means the first
    real request pays the handshake exactly as it does today, and an
    unreachable provider must not put a warning on screen before the user has
    asked for anything."""
    import logging

    from pyrrhon.core.providers.llm import OpenAICompatLLM

    caplog.set_level(logging.DEBUG, logger="pyrrhon.providers")
    # Port 9 is discard: refused immediately rather than left to time out.
    llm = OpenAICompatLLM(
        model="m", api_key="k", base_url="http://127.0.0.1:9/v1", max_retries=0
    )
    task = llm.preconnect()
    await task

    assert task.done() and task.exception() is None
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    await llm._client.close()


async def test_preconnect_warms_the_configured_base_url(mock_llm):
    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(str(request.url))
        return httpx.Response(200, json={"object": "list", "data": []})

    llm = mock_llm(handler)
    await llm.preconnect()
    assert reached and reached[0].startswith(PROVIDER_BASE_URL)


# --- A failure with no status at all ----------------------------------------
#
# The SDK raises a bare APIError when a 200 response's SSE body carries an
# `error` event. APIStatusError is a SUBCLASS of APIError, so the narrower
# `except` never saw this and the turn died with PROVIDER_ERROR_MESSAGE.
# Observed live against Groq while verifying M16a.

from openai import APIError  # noqa: E402


def _mid_stream(message: str, body=None) -> APIError:
    return APIError(message, request=REQUEST, body=body)


def test_a_mid_stream_tool_refusal_is_recoverable():
    """Groq's answer when gpt-oss reaches for a built-in on a request that
    carried no `tools` array — which is every turn `needs_tools()` withholds
    the belt from, so a greeting can trip it."""
    fault = classify(_mid_stream("Tool choice is none, but model called a tool"))
    assert fault is not None and fault.kind == "tool_validation"
    assert fault.status_code is None


def test_a_mid_stream_size_refusal_is_recoverable():
    fault = classify(_mid_stream("Please reduce the length of the messages"))
    assert fault is not None and fault.kind == "too_large"


def test_an_unrecognised_mid_stream_error_still_classifies_as_nothing():
    assert classify(_mid_stream("something else went wrong")) is None


@pytest.mark.parametrize("drive", [_drive_chat, _drive_stream])
async def test_the_client_raises_typed_from_a_mid_stream_error(mock_llm, drive):
    """End to end: the SSE body carries an error and the client must still
    produce InvalidToolCallError rather than letting a bare APIError through."""
    payload = json.dumps(
        {"error": {"message": "Tool choice is none, but model called a tool"}}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("stream"):
            return httpx.Response(
                200,
                content=f"data: {payload}\n\ndata: [DONE]\n\n".encode(),
                headers={"content-type": "text/event-stream"},
            )
        # The whole-reply path cannot receive this shape, so drive it through
        # the same taxonomy by raising what the SDK would.
        return httpx.Response(400, json={"error": {"message": "Tool call validation failed"}})

    with pytest.raises(InvalidToolCallError):
        await drive(mock_llm(handler))
