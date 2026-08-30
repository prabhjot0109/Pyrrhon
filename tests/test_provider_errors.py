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
