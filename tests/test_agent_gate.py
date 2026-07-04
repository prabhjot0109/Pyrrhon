from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Citation, SpeechChunk
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.tools.repo import ReadFileTool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_agent(replies, *, allow_retry: bool = True) -> tuple[Agent, FakeLLM]:
    fake = FakeLLM(replies)
    agent = Agent(
        llm=fake,
        tools=[ReadFileTool(FIXTURE)],
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
        grounding_gate=GroundingGate(FIXTURE),
        allow_retry=allow_retry,
    )
    return agent, fake


async def collect(agent: Agent, history: list[dict], text: str) -> list:
    return [event async for event in agent.run_turn(history, text)]


async def test_verified_reply_passes_gate_without_retry():
    agent, fake = make_agent([LLMReply(text="greet is at utils/helpers.py:1.")])
    events = await collect(agent, [], "where is greet?")
    assert SpeechChunk(text="greet is at utils/helpers.py:1.") in events
    assert Citation(file="utils/helpers.py", line=1) in events
    assert len(fake.calls) == 1  # verified — no retry round-trip


async def test_unverified_reply_triggers_exactly_one_retry():
    agent, fake = make_agent(
        [
            LLMReply(text="greet is at bogus/nowhere.py:7."),
            LLMReply(text="Correction: greet is at utils/helpers.py:1."),
        ]
    )
    history: list[dict] = []
    events = await collect(agent, history, "where is greet?")

    assert len(fake.calls) == 2
    retry_messages = fake.calls[1]["messages"]
    # The retry sees its own draft, then a user-role correction naming the failure:
    assert retry_messages[-2] == {
        "role": "assistant",
        "content": "greet is at bogus/nowhere.py:7.",
    }
    assert retry_messages[-1]["role"] == "user"
    assert "bogus/nowhere.py:7" in retry_messages[-1]["content"]
    assert fake.calls[1]["tools"] is None  # single round-trip, no new tool loop

    assert SpeechChunk(text="Correction: greet is at utils/helpers.py:1.") in events
    assert Citation(file="utils/helpers.py", line=1) in events
    # The draft and correction never entered the caller's history:
    assert [m["role"] for m in history] == ["system", "user", "assistant"]
    assert history[-1] == {
        "role": "assistant",
        "content": "Correction: greet is at utils/helpers.py:1.",
    }


async def test_retry_result_is_gated_without_second_retry():
    agent, fake = make_agent(
        [
            LLMReply(text="see bogus.py:3."),
            LLMReply(text="still bogus: other/fake.py:9."),
        ]
    )
    events = await collect(agent, [], "where?")
    assert len(fake.calls) == 2  # exactly one retry, never two
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert "other/fake.py:9" not in speech[-1].text
    assert speech[-1].text.endswith("I couldn't verify that location.")
    assert not any(isinstance(e, Citation) for e in events)


async def test_allow_retry_false_strips_immediately():
    agent, fake = make_agent(
        [LLMReply(text="see bogus.py:3 for details.")], allow_retry=False
    )
    events = await collect(agent, [], "where?")
    assert len(fake.calls) == 1  # speech path: no retry round-trip, ever
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == "see for details. I couldn't verify that location."


async def test_no_gate_keeps_m0_behavior():
    fake = FakeLLM([LLMReply(text="see bogus.py:3.")])
    agent = Agent(llm=fake, tools=[], system_prompt="t", repo_root=FIXTURE)
    events = [event async for event in agent.run_turn([], "hi")]
    assert events == [SpeechChunk(text="see bogus.py:3.")]  # ungated, uncited
