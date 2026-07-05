from pathlib import Path

from pyrrhon.core.agent.loop import Agent, extract_question
from pyrrhon.core.events import AskUser
from pyrrhon.core.providers.llm import LLMReply
from tests.helpers import FakeLLM


def test_none_when_not_a_question():
    assert extract_question("The data is relational.") is None


def test_returns_single_sentence_question():
    assert extract_question("Why Mongo over Postgres?") == "Why Mongo over Postgres?"


def test_returns_last_sentence_only():
    text = (
        "Your data looks relational — users, orders, joins. "
        "What specific benefit are you expecting from Mongo over Postgres here?"
    )
    assert extract_question(text) == (
        "What specific benefit are you expecting from Mongo over Postgres here?"
    )


def test_trailing_whitespace_is_tolerated():
    assert extract_question("Ready to proceed?  \n") == "Ready to proceed?"


def test_question_mid_text_with_statement_ending_is_none():
    assert extract_question("Why Mongo? Because you said so.") is None


def make_agent(replies, mode: str) -> Agent:
    return Agent(
        llm=FakeLLM(replies),
        tools=[],
        system_prompt="You are a test agent.",
        repo_root=Path("."),
        mode=mode,
    )


async def collect(agent: Agent, text: str) -> list:
    return [event async for event in agent.run_turn([], text)]


async def test_design_mode_question_reply_yields_askuser():
    question = "What specific benefit are you expecting from Mongo over Postgres here?"
    agent = make_agent([LLMReply(text=f"Your data looks relational. {question}")], mode="design")
    events = await collect(agent, "let's use mongo")
    assert AskUser(question=question) in events
    # AskUser comes after the SpeechChunk (channels speak first, then highlight):
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("AskUser") > kinds.index("SpeechChunk")


async def test_understand_mode_never_yields_askuser():
    agent = make_agent([LLMReply(text="Want to see the code?")], mode="understand")
    events = await collect(agent, "explain app.py")
    assert not [e for e in events if isinstance(e, AskUser)]


async def test_design_mode_statement_reply_yields_no_askuser():
    agent = make_agent([LLMReply(text="Postgres it is. Good reasoning.")], mode="design")
    events = await collect(agent, "here's my justification")
    assert not [e for e in events if isinstance(e, AskUser)]
