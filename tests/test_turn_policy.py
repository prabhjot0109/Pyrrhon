"""The turn policy table, the turn's state, and the one function that decides.

These are pure: no LLM, no I/O, no history. Everything the loop needs to know
about "should this turn keep going" is decidable from these two dataclasses,
which is the point — today that decision is reconstructed from five loose
locals scattered through `_run_turn`.
"""

import pytest

from pyrrhon.core.agent.policy import (
    LAND_NUDGE,
    MAX_BARREN_ROUNDS,
    Continue,
    Stop,
    TurnPolicy,
    TurnState,
    decide,
)

POLICY = TurnPolicy(max_rounds=8, max_tool_chars=40_000, withheld=frozenset(),
                    nudge_at=0.75)


def test_a_fresh_turn_continues():
    outcome = decide(TurnState(), POLICY)
    assert isinstance(outcome, Continue)
    assert outcome.nudge is None


def test_the_round_cap_stops_the_turn():
    outcome = decide(TurnState(rounds=8), POLICY)
    assert outcome == Stop(reason="rounds")


def test_the_tool_char_budget_stops_the_turn():
    outcome = decide(TurnState(rounds=1, tool_chars=40_000), POLICY)
    assert outcome == Stop(reason="budget")


def test_a_productive_round_resets_the_barren_counter():
    """Written first, because it is the check that keeps the diminishing-returns
    rule from cutting off an agent that is working.

    Three or four tool rounds is a normal investigation here. What is NOT
    normal is three consecutive rounds that opened no new line range and named
    no new path — that is a loop, and the evidence ledger can see it while a
    token count cannot.
    """
    state = TurnState()
    for _ in range(MAX_BARREN_ROUNDS - 1):
        state.note_round(productive=False)
    state.note_round(productive=True)
    assert state.barren_rounds == 0
    assert isinstance(decide(state, POLICY), Continue)


def test_consecutive_barren_rounds_stop_the_turn():
    state = TurnState()
    for _ in range(MAX_BARREN_ROUNDS):
        state.note_round(productive=False)
    assert decide(state, POLICY) == Stop(reason="diminishing")


def test_the_round_cap_outranks_diminishing_returns():
    """Both true at once is the common case at the end of a runaway turn, and
    the trace should name the hard cap that actually fired."""
    state = TurnState(rounds=8, barren_rounds=MAX_BARREN_ROUNDS)
    assert decide(state, POLICY) == Stop(reason="rounds")


def test_approaching_the_round_budget_nudges_the_model_to_land_it():
    """The direction is load-bearing and inverted from the reference.

    Claude Code's equivalent nudge says "keep working, do not summarize",
    because its failure mode is a model that stops early. Pyrrhon's failure
    mode is the reverse: a voice turn that spends four more rounds has already
    lost. Same mechanism, opposite direction — assert on the direction, not
    just on the nudge's presence.
    """
    outcome = decide(TurnState(rounds=6), POLICY)
    assert isinstance(outcome, Continue)
    assert outcome.nudge == LAND_NUDGE
    assert "answer now" in LAND_NUDGE.lower()
    assert "do not summarize" not in LAND_NUDGE.lower()


def test_approaching_the_tool_char_budget_nudges_too():
    outcome = decide(TurnState(rounds=1, tool_chars=30_000), POLICY)
    assert isinstance(outcome, Continue)
    assert outcome.nudge == LAND_NUDGE


def test_the_same_nudge_is_never_issued_twice():
    state = TurnState(rounds=6)
    assert decide(state, POLICY).nudge == LAND_NUDGE
    state.rounds = 7
    assert decide(state, POLICY).nudge is None


def test_a_nudge_is_issued_once_per_key():
    state = TurnState()
    assert state.issue_nudge("invalid_tool") is True
    assert state.issue_nudge("invalid_tool") is False
    assert state.issue_nudge("land") is True


@pytest.mark.parametrize("nudge_at", [0.0, 1.0])
def test_the_nudge_fraction_is_honoured_at_both_extremes(nudge_at):
    """0.0 nudges immediately; 1.0 never nudges before the cap fires."""
    policy = TurnPolicy(max_rounds=8, max_tool_chars=40_000,
                        withheld=frozenset(), nudge_at=nudge_at)
    outcome = decide(TurnState(rounds=1), policy)
    assert (outcome.nudge is not None) is (nudge_at == 0.0)
