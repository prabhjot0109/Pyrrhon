from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Citation, SpeechChunk, ToolCallFinished, ToolCallStarted
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.tools.repo import ReadFileTool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_agent(replies, max_tool_rounds: int = 8) -> tuple[Agent, FakeLLM]:
    fake = FakeLLM(replies)
    agent = Agent(
        llm=fake,
        tools=[ReadFileTool(FIXTURE)],
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
        max_tool_rounds=max_tool_rounds,
    )
    return agent, fake


async def collect(agent: Agent, history: list[dict], text: str) -> list:
    return [event async for event in agent.run_turn(history, text)]


async def test_direct_answer_yields_speech_and_updates_history():
    agent, fake = make_agent([LLMReply(text="It prints a greeting.")])
    history: list[dict] = []
    events = await collect(agent, history, "what does app.py do?")
    assert events == [SpeechChunk(text="It prints a greeting.")]
    roles = [m["role"] for m in history]
    assert roles == ["system", "user", "assistant"]


async def test_tool_round_then_answer_with_citation():
    replies = [
        LLMReply(
            tool_calls=(
                ToolCall(id="call_1", name="read_file", arguments={"path": "utils/helpers.py"}),
            )
        ),
        LLMReply(text="greet is defined at utils/helpers.py:1."),
    ]
    agent, fake = make_agent(replies)
    events = await collect(agent, [], "where is greet defined?")

    assert ToolCallStarted(name="read_file", args={"path": "utils/helpers.py"}) in events
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert "def greet" in finished[0].result_preview
    assert Citation(file="utils/helpers.py", line=1) in events
    # The tool result was fed back to the LLM as a tool message:
    tool_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "call_1"


async def test_unknown_tool_reports_error_to_llm():
    replies = [
        LLMReply(tool_calls=(ToolCall(id="c1", name="nuke_repo", arguments={}),)),
        LLMReply(text="Sorry, I can't do that."),
    ]
    agent, fake = make_agent(replies)
    await collect(agent, [], "delete everything")
    tool_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["content"].startswith("ERROR:")


async def test_tool_budget_produces_honest_bailout():
    looping_call = LLMReply(
        tool_calls=(ToolCall(id="c", name="read_file", arguments={"path": "app.py"}),)
    )
    agent, _ = make_agent([looping_call, looping_call], max_tool_rounds=2)
    events = await collect(agent, [], "loop forever")
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert "tool budget" in speech[-1].text
