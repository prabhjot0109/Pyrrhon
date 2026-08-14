"""Turn-type classification: withhold the tool belt when it cannot be needed.

The belt is ~1.5k tokens of JSON schema sent on every round. On the 25-40% of
voice turns that are acknowledgements it is pure waste, and it leaves the door
open to a spurious tool call on a turn with nothing to look up.

The classifier only ever WITHHOLDS tools. It never picks one, never answers,
and never edits the prompt — so the worst case for a misclassification is an
answer given without repo access, which the model handles by asking a
clarifying question rather than by inventing a claim about the code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.turn_type import (
    AMBIGUOUS_FOLLOWUP,
    REPO_QUESTION,
    RESUME,
    SOCIAL,
    classify,
    needs_tools,
)
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.tools.repo import ReadFileTool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def asked(question: str = "Want me to dig into the tool loop?") -> list[dict]:
    return [{"role": "assistant", "content": question}]


@pytest.mark.parametrize(
    "text",
    ["hi", "Hello", "thanks!", "thank you", "yes", "yep", "ok", "go on",
     "keep going", "sounds good", "cool", "got it", "makes sense", "bye", ""],
)
def test_greetings_and_acknowledgements_need_no_tools(text):
    assert classify(text) == SOCIAL
    assert not needs_tools(SOCIAL)


@pytest.mark.parametrize(
    "text",
    [
        # THE hazard case: anchored matching means a greeting with a real
        # question attached must NOT be treated as social.
        "hi, where is the auth middleware",
        "thanks — now show me the agent loop",
        "yes, and what calls greet()?",
        "ok but why does session.py cancel the task",
        "where is greet defined",
        "explain the grounding gate",
        "what breaks if I change helpers.py",
    ],
)
def test_anything_carrying_a_real_question_keeps_the_belt(text):
    assert classify(text) == REPO_QUESTION
    assert needs_tools(REPO_QUESTION)


def test_a_bare_yes_answering_our_own_question_is_ambiguous():
    """A short reply to an offer should produce a clarifying question, not a
    search launched on a guess."""
    assert classify("that one", asked()) == AMBIGUOUS_FOLLOWUP
    assert not needs_tools(AMBIGUOUS_FOLLOWUP)


def test_a_short_reply_is_only_ambiguous_after_a_question():
    """With no question pending, the same words are just a normal turn."""
    assert classify("that one", []) == REPO_QUESTION
    assert classify("that one", [{"role": "assistant", "content": "Done."}]) == REPO_QUESTION


def test_a_short_reply_naming_code_still_gets_the_belt():
    """Brevity alone must not withhold tools when the user is clearly
    pointing at code."""
    for text in ["the loop.py one", "show run_turn", "what about the cache?",
                 "the parse() call", "in tui/app.py"]:
        assert classify(text, asked()) == REPO_QUESTION, text


def test_a_tool_call_message_does_not_count_as_a_question():
    history = [{"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]}]
    assert classify("that one", history) == REPO_QUESTION


# -- wiring into the loop ----------------------------------------------------


async def make_and_run(text: str, history: list[dict] | None = None):
    llm = FakeLLM([LLMReply(text="Sure.")])
    agent = Agent(
        llm=llm, tools=[ReadFileTool(FIXTURE)], system_prompt="p", repo_root=FIXTURE
    )
    history = history if history is not None else []
    [e async for e in agent.run_turn(history, text)]
    return llm, agent


async def test_a_social_turn_sends_no_tool_schemas():
    llm, agent = await make_and_run("hi")
    assert llm.calls[0]["tools"] is None
    assert agent.last_trace.turn_type == "social"
    assert agent.last_trace.schema_chars == 0


async def test_a_repo_question_sends_the_full_belt():
    llm, agent = await make_and_run("where is greet defined?")
    assert llm.calls[0]["tools"]  # non-empty
    assert agent.last_trace.turn_type == "repo_question"
    assert agent.last_trace.schema_chars > 0


ASKED = [{"role": "assistant", "content": "Want me to trace what calls the tool loop?"}]


@pytest.mark.parametrize(
    "reply",
    ["yes", "yes please", "yeah do that", "sure, walk me through it", "do it",
     "go ahead", "yep", "okay do that"],
)
def test_accepting_our_own_offer_gets_the_tool_belt(reply):
    assert classify(reply, ASKED) == RESUME
    assert needs_tools(RESUME) is True


@pytest.mark.parametrize("reply", ["no", "no thanks", "not now", "thanks", "nah"])
def test_declining_our_own_offer_stays_social(reply):
    assert classify(reply, ASKED) == SOCIAL
    assert needs_tools(SOCIAL) is False


def test_a_bare_yes_with_no_question_behind_it_is_still_social():
    assert classify("yes", [{"role": "assistant", "content": "That is the loop."}]) == SOCIAL


def test_a_greeting_is_social_even_after_a_question():
    assert classify("hi", ASKED) == SOCIAL
