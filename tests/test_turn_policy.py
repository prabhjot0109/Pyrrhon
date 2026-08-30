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
    policy_for,
)
from pyrrhon.core.agent.turn_type import (
    REPO_QUESTION,
    RESUME,
    SOCIAL,
    TURN_TYPES,
    needs_tools,
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


# --- the table ------------------------------------------------------------

BELT = [
    "read_file", "read_image", "grep", "glob", "remember",
    "find_symbol", "symbol_context", "list_dependencies", "repo_map",
    "git_log", "git_blame", "git_show",
    "web_search", "web_fetch", "write_spec", "think_deeper",
]


@pytest.mark.parametrize("turn_type", TURN_TYPES)
@pytest.mark.parametrize("voice_active", [False, True])
def test_every_turn_type_has_a_row_for_both_channels(turn_type, voice_active):
    """A fifth turn type fails the suite until someone gives it a policy.

    Parametrised over turn_type's own constants rather than over the table's
    keys, so the test asks the question in the direction that can actually
    catch drift.
    """
    policy = policy_for(turn_type, voice_active=voice_active)
    assert isinstance(policy, TurnPolicy)
    assert policy.max_rounds >= 0
    assert 0.0 <= policy.nudge_at <= 1.0


def test_a_social_turn_gets_no_belt_at_all():
    assert policy_for(SOCIAL, voice_active=False).belt_for(BELT) is None
    assert policy_for(SOCIAL, voice_active=True).belt_for(BELT) is None


def test_a_spoken_repo_question_stops_sooner_than_a_typed_one():
    spoken = policy_for(REPO_QUESTION, voice_active=True)
    typed = policy_for(REPO_QUESTION, voice_active=False)
    assert spoken.max_rounds < typed.max_rounds
    assert spoken.max_tool_chars <= typed.max_tool_chars


def test_a_resumed_question_keeps_the_belt():
    """"yes, go on" is a repo question the user just re-anchored.

    turn_type.classify has a whole docstring on why: VOICE_STYLE tells the
    model to offer the next thread and explain it when the user agrees, and an
    answer given without repo access is the ungrounded failure the gate cannot
    catch — it verifies citations that appear, not claims that cite nothing.
    """
    assert policy_for(RESUME, voice_active=True).belt_for(BELT) is not None
    assert needs_tools(RESUME) is True


def test_every_withheld_name_is_a_name_the_belt_actually_has():
    """A withhold list naming tools that were renamed away narrows nothing and
    reads as though it did — silent in exactly the way an allow list of stale
    names would be loud. No row withholds anything today (see _SPOKEN on why
    the voice split was measured and dropped), so this passes vacuously; it is
    here for the row that puts one back."""
    for turn_type in TURN_TYPES:
        for voice in (False, True):
            withheld = policy_for(turn_type, voice_active=voice).withheld
            assert withheld is None or withheld <= set(BELT)


def test_a_spoken_repo_question_keeps_the_whole_belt():
    """Measured, not assumed. Withholding the three tools that cannot finish
    inside a spoken turn saved ~348 schema tokens — a quarter of the estimate —
    and voice/bridge.py already ships a spoken filler for each of them, so a
    previous milestone had made them voice-usable on purpose. The spoken row
    bounds a turn by rounds and tool volume instead, which is a limit rather
    than a removed capability."""
    spoken = policy_for(REPO_QUESTION, voice_active=True)
    assert spoken.belt_for(BELT) == BELT


def test_the_whole_table_produces_at_most_three_distinct_belts():
    """M10 section 2.2: the prompt prefix is byte-stable so providers hit their
    prefix cache, and the tool block sits inside that prefix. Every distinct
    belt is a separate cache family.

    Today there are three — no belt, the voice subset, the full belt. A fourth
    has to argue against the cache before it is added, which is why this is a
    test rather than a comment: filtering the schema list looks like a pure
    narrowing at the call site and is in fact a decision about how many cache
    families the session will have.
    """
    shapes = {
        tuple(policy.belt_for(BELT) or ())
        for turn_type in TURN_TYPES
        for voice in (False, True)
        for policy in [policy_for(turn_type, voice_active=voice)]
    }
    assert len(shapes) <= 3


def test_needs_tools_is_derived_from_the_table():
    for turn_type in TURN_TYPES:
        expected = policy_for(turn_type, voice_active=False).withheld is not None
        assert needs_tools(turn_type) is expected


def test_whether_a_turn_gets_tools_never_depends_on_the_channel():
    """What makes the single-argument needs_tools() honest.

    The voice row narrows the belt; it must never remove it. A spoken repo
    question that lost its tools would answer from memory, which is the one
    failure mode the whole grounding stack exists to prevent.
    """
    for turn_type in TURN_TYPES:
        typed = policy_for(turn_type, voice_active=False).withheld is None
        spoken = policy_for(turn_type, voice_active=True).withheld is None
        assert typed is spoken
