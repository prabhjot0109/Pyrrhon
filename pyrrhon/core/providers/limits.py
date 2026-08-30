"""How big a request this endpoint will actually accept, learned rather than declared.

`registry.py` deliberately records no model ids, because model ids rot faster
than anything else here — and so it records no context windows either. The
harness budgeted a flat 90000 tokens whether the configured model had an 8k
window or a 131k one, and whether the account's per-minute token allowance was
6k or 600k. Meanwhile the provider returns `x-ratelimit-limit-tokens` on every
response and nothing read it.

`context.token_scale` is the same instinct applied to the other half of the
problem: it learns how many characters make a token. This learns how many
tokens the endpoint will take. Together they give a budget whose three terms
are all measured.

Two sources, and the order between them is the point:

1. **A header.** The account's ceiling under its current tier.
2. **A recorded failure.** A request that was rejected once at size N tells
   you more than a header claiming N is fine — which is the mitigation for a
   provider that reports an allowance it will not honour.

`limit` is the minimum of whichever are set, so a failure always wins and a
later header cannot undo a ratchet. Within a session the failure ceiling only
ever moves down.

Pure: a mapping of headers in, integers out. No SDK types, no I/O, no clock.
"""

from __future__ import annotations

from typing import Mapping

# Sent by Groq and OpenAI. A provider that omits it simply leaves the
# header-derived ceiling unset, which is the pre-M16a behaviour.
LIMIT_HEADER = "x-ratelimit-limit-tokens"

# A request estimated at N tokens was refused, so N is not a safe ceiling —
# it is the first size known to be too big. The margin also absorbs the fact
# that the estimate is len//4 and not a tokenizer.
FAILURE_BACKOFF = 0.9


def _positive_int(raw: object) -> int | None:
    """A header value, or None if it is missing or not a positive count.

    Providers send this as a bare integer; a proxy that sends prose, a float,
    or nothing at all must leave the limit unlearned rather than raise on the
    turn's critical path.
    """
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class LearnedLimit:
    """One per client. Mutable, and only ever ratchets down within a session."""

    def __init__(self) -> None:
        self._from_header: int | None = None
        self._from_failure: int | None = None

    @property
    def limit(self) -> int | None:
        """The largest request believed to be accepted, or None if unlearned.

        None is a real answer and must stay distinguishable from a small
        number: a meter measuring against a denominator nobody chose is worse
        than a meter that says it does not know.
        """
        known = [v for v in (self._from_header, self._from_failure) if v is not None]
        return min(known) if known else None

    def observe_headers(self, headers: Mapping[str, str] | None) -> None:
        value = _positive_int((headers or {}).get(LIMIT_HEADER))
        if value is not None:
            self._from_header = value

    def observe_failure(self, estimated_tokens: int | None) -> None:
        """Record that a request of roughly this size was refused as too large."""
        if not estimated_tokens or estimated_tokens <= 0:
            return
        ceiling = max(1, int(estimated_tokens * FAILURE_BACKOFF))
        self._from_failure = (
            ceiling if self._from_failure is None else min(self._from_failure, ceiling)
        )

    def budget(self, reserved: int = 0) -> int | None:
        """Room for history, once `reserved` is set aside for the reply and
        the tool schemas. Never negative: a budget of zero means compact
        everything compactable, which is a coherent instruction; a negative
        one is not."""
        limit = self.limit
        if limit is None:
            return None
        return max(0, limit - reserved)
