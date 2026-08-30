"""The learned token limit: header capture, the failure ratchet, arithmetic."""

from __future__ import annotations

import pytest

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
