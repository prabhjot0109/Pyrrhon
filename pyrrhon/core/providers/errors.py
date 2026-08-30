"""What the provider said, as a type rather than as prose.

The old path matched five substrings against `str(exc)` inside three
`except BadRequestError` blocks. Groq reports an oversized request as **413**
and token-per-minute exhaustion as **429**; neither is a `BadRequestError`, so
the string tests never ran and a recoverable refusal reached the loop as a
generic exception. The taxonomy here is keyed on what HTTP actually
guarantees:

| Status | kind |
|---|---|
| 400, tool-validation body | `tool_validation` |
| 400, size body / 413 | `too_large` |
| 429 | `rate_limited` |
| 401 / 403 | `credentials` |
| 5xx, timeouts, connection | `outage` |

String matching survives only as a *tiebreaker inside 400*, which is the one
status genuinely ambiguous between "your tools are wrong" and "your prompt is
too long".

Layer B: this module names openai and httpx types so that `core/agent/` never
has to. `classify` returns `None` for anything it does not recognise, and the
caller re-raises verbatim — an unknown failure must not be laundered into a
kind the loop thinks it can recover from.
"""

from __future__ import annotations

import email.utils
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping

import httpx
from openai import APIConnectionError, APIStatusError

FaultKind = Literal[
    "tool_validation", "too_large", "rate_limited", "credentials", "outage"
]


class InvalidToolCallError(RuntimeError):
    """The model emitted a tool call the provider rejected as not in the tool
    list — e.g. gpt-oss on Groq hallucinating its built-in `search`/`python`.
    Distinct from an outage: the loop recovers by nudging with the real tool
    names, so it must not be swallowed as a generic 4xx."""


class ContextLengthExceededError(RuntimeError):
    """The prompt (history + tool results) exceeded what the endpoint accepts.
    A 4xx, but recoverable: the loop compacts the conversation and retries the
    round rather than dying. Raised for a 413 as well as a context-length 400
    — from the loop's point of view they are the same problem."""


class RateLimitExceededError(RuntimeError):
    """The account's rate or token allowance is spent and a bounded wait did
    not clear it.

    Carries `retry_after` so the channel can say *when* rather than only
    *that*. Unlike the other two this is an availability problem rather than
    user error, which is why `FallbackLLM` falls over on it — see the comment
    at that call site."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class ProviderFault:
    """One provider refusal, reduced to what the caller has to decide about.

    A single value the loop switches on, rather than five exception classes
    that would put the same switch in five `except` blocks.
    """

    kind: FaultKind
    message: str
    retry_after: float | None = None
    status_code: int | None = None


def _is_tool_validation(exc: APIStatusError) -> bool:
    if getattr(exc, "code", None) == "tool_use_failed":
        return True
    text = str(exc)
    return "not in request.tools" in text or "Tool call validation failed" in text


def _is_too_large(exc: APIStatusError) -> bool:
    if getattr(exc, "code", None) in ("context_length_exceeded", "string_above_max_length"):
        return True
    text = str(exc).lower()
    # Providers word this differently: OpenAI "maximum context length",
    # others "context length"/"context window"/"reduce the length". Groq's
    # 413 body says "request entity too large" even when the status already
    # settles it, and a proxy in front of a local server may 400 with it.
    return (
        "context_length_exceeded" in text
        or "maximum context length" in text
        or "context window" in text
        or "reduce the length" in text
        or "too many tokens" in text
        or "request entity too large" in text
        or "request_entity_too_large" in text
    )


def retry_after_seconds(headers: Mapping[str, str] | None) -> float | None:
    """Seconds to wait, from the headers a 429 carries.

    Same precedence the openai SDK itself uses: `retry-after-ms` first
    (millisecond precision, which is what Groq sends), then `retry-after` as
    seconds, then `retry-after` as an HTTP-date. A value that parses to
    something non-positive is treated as absent — a wait of zero is not a
    wait, and a date already in the past means the header is stale.
    """
    if not headers:
        return None
    raw_ms = headers.get("retry-after-ms")
    if raw_ms:
        try:
            seconds = float(raw_ms) / 1000.0
        except ValueError:
            seconds = 0.0
        if seconds > 0:
            return seconds
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    return seconds if seconds > 0 else None


def classify(exc: BaseException) -> ProviderFault | None:
    """The one place an SDK exception becomes a Pyrrhon fault.

    `None` means "not a fault this harness models" — the caller re-raises the
    original, which is what keeps an unrecognised 4xx from being mistaken for
    something recoverable.
    """
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        message = str(exc)
        if status >= 500:
            return ProviderFault("outage", message, status_code=status)
        if status == 429:
            return ProviderFault(
                "rate_limited",
                message,
                retry_after=retry_after_seconds(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
                status_code=status,
            )
        if status == 413:
            return ProviderFault("too_large", message, status_code=status)
        if status in (401, 403):
            return ProviderFault("credentials", message, status_code=status)
        if status == 400:
            # The one ambiguous status, and the only place prose still decides.
            if _is_tool_validation(exc):
                return ProviderFault("tool_validation", message, status_code=status)
            if _is_too_large(exc):
                return ProviderFault("too_large", message, status_code=status)
        return None
    if isinstance(exc, (APIConnectionError, httpx.ConnectError, httpx.TimeoutException)):
        return ProviderFault("outage", str(exc))
    return None
