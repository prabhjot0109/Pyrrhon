from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import SpeechChunk
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.session import Session
from pyrrhon.core.tools.repo import ReadFileTool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_session(replies) -> Session:
    agent = Agent(
        llm=FakeLLM(replies),
        tools=[ReadFileTool(FIXTURE)],
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
    )
    return Session(agent)


async def test_latency_none_before_first_turn_then_measured():
    session = make_session([LLMReply(text="It prints a greeting.")])
    assert session.last_turn_latency_ms is None
    events = [event async for event in session.run_turn("what does app.py do?")]
    assert any(isinstance(e, SpeechChunk) for e in events)
    assert isinstance(session.last_turn_latency_ms, float)
    assert session.last_turn_latency_ms >= 0.0


async def test_latency_updates_each_turn():
    session = make_session([LLMReply(text="one"), LLMReply(text="two")])
    async for _ in session.run_turn("first"):
        pass
    assert session.last_turn_latency_ms is not None
    session.last_turn_latency_ms = -1.0  # sentinel: prove the next turn re-measures
    async for _ in session.run_turn("second"):
        pass
    assert session.last_turn_latency_ms >= 0.0  # sentinel overwritten by a fresh value
