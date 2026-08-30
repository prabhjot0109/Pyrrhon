from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.policy import policy_for
from pyrrhon.core.agent.turn_type import REPO_QUESTION, SOCIAL
from pyrrhon.core.events import Citation, SpeechChunk, ToolCallFinished, ToolCallStarted
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.tools.base import Tool
from pyrrhon.core.tools.repo import ReadFileTool
from pyrrhon.core.tools.web import WebSearchTool
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


async def test_a_bad_argument_error_names_the_accepted_parameters(sample_repo):
    """The model can only correct itself against names it is told.

    Seen in a real session: the schema says start_line/end_line, the model
    sent line_start/line_end, and what came back was Python's raw TypeError —
    which says what was *not* accepted and nothing about what would be. The
    next attempt was another guess.
    """
    from pyrrhon.core.tools.base import run_tool
    from pyrrhon.core.tools.repo import ReadFileTool

    tools = {"read_file": ReadFileTool(sample_repo)}
    result = await run_tool(tools, "read_file", {"path": "a.py", "line_start": 1})

    assert result.startswith("ERROR: bad arguments for read_file:")
    assert "start_line" in result
    assert "end_line" in result
    assert "path" in result


async def test_a_good_call_is_unaffected(sample_repo):
    from pyrrhon.core.tools.base import run_tool
    from pyrrhon.core.tools.repo import ReadFileTool

    tools = {"read_file": ReadFileTool(sample_repo)}
    result = await run_tool(tools, "read_file", {"path": "utils/helpers.py"})
    assert not result.startswith("ERROR")


async def test_an_unknown_tool_is_still_named():
    from pyrrhon.core.tools.base import run_tool

    assert await run_tool({}, "nope", {}) == "ERROR: no tool named 'nope'."


# --- the policy table, selected per turn ----------------------------------


class TraceTool(Tool):
    """Returns a new repo location every call, so every round is productive.

    Needed because the diminishing-returns check is real: a tool that returns
    the same thing each round would end these turns after three barren rounds
    rather than at the round cap under test.
    """

    name = "trace"
    description = "returns a location"
    parameters = {"type": "object", "properties": {"n": {"type": "integer"}},
                  "required": ["n"]}

    async def run(self, n: int) -> str:
        return f"utils/helpers.py:{n}"


def _trace_agent(rounds: int, *, voice: bool) -> tuple[Agent, FakeLLM]:
    replies = [
        LLMReply(tool_calls=(ToolCall(id=f"c{i}", name="trace", arguments={"n": i}),))
        for i in range(rounds)
    ]
    replies.append(LLMReply(text="Landed."))
    fake = FakeLLM(replies)
    agent = Agent(
        llm=fake,
        tools=[TraceTool(), WebSearchTool()],
        system_prompt="p",
        repo_root=FIXTURE,
        voice_active=voice,
    )
    return agent, fake


def _tool_rounds(fake: FakeLLM) -> int:
    """LLM calls that carried a belt. The forced answer passes tools=None."""
    return sum(1 for call in fake.calls if call["tools"] is not None)


async def test_a_spoken_repo_question_stops_at_the_voice_round_cap():
    spoken = policy_for(REPO_QUESTION, voice_active=True)
    agent, fake = _trace_agent(spoken.max_rounds + 4, voice=True)
    await collect(agent, [], "where does the turn state machine decide?")
    assert _tool_rounds(fake) == spoken.max_rounds
    assert agent.last_trace is not None
    assert agent.last_trace.stop_reason == "rounds"


async def test_the_same_question_typed_goes_further():
    typed = policy_for(REPO_QUESTION, voice_active=False)
    agent, fake = _trace_agent(typed.max_rounds + 4, voice=False)
    await collect(agent, [], "where does the turn state machine decide?")
    assert _tool_rounds(fake) == typed.max_rounds
    assert typed.max_rounds > policy_for(REPO_QUESTION, voice_active=True).max_rounds


async def test_a_spoken_turn_is_offered_a_narrower_belt_that_still_exists():
    """The withheld names must be names the live belt actually has.

    A withhold list naming tools that were renamed away would narrow nothing
    and read as though it did, so assert the offered belt is a strict subset —
    not merely that some filtering happened.
    """
    agent, fake = _trace_agent(1, voice=True)
    await collect(agent, [], "where does the turn state machine decide?")
    offered = {schema["function"]["name"] for schema in fake.calls[0]["tools"]}
    assert offered < set(agent.tools)
    assert "web_search" not in offered
    assert "trace" in offered


async def test_a_social_turn_is_offered_no_belt_at_all():
    fake = FakeLLM([LLMReply(text="Hi.")])
    agent = Agent(llm=fake, tools=[TraceTool()], system_prompt="p", repo_root=FIXTURE)
    await collect(agent, [], "hi")
    assert fake.calls[0]["tools"] is None


async def test_the_constructor_still_pins_the_round_cap():
    """Kept as an override so a test or a plugin can still pin it. It stops
    being the mechanism; it does not stop working."""
    agent, fake = _trace_agent(6, voice=False)
    agent.max_tool_rounds = 2
    await collect(agent, [], "where does the turn state machine decide?")
    assert _tool_rounds(fake) == 2


def test_the_schema_cache_key_tracks_the_belt_and_nothing_else():
    """Silent in both directions if it is wrong: a stale key serves the
    previous turn's belt, and no key rebuilds the list every round."""
    agent = Agent(llm=FakeLLM([]), tools=[TraceTool(), WebSearchTool()],
                  system_prompt="p", repo_root=FIXTURE)
    typed = policy_for(REPO_QUESTION, voice_active=False)
    spoken = policy_for(REPO_QUESTION, voice_active=True)

    full = agent._tool_schemas(typed)
    key_after_full = agent._schema_cache_key
    assert agent._tool_schemas(typed) is full  # same belt: no rebuild
    assert agent._schema_cache_key == key_after_full

    narrow = agent._tool_schemas(spoken)
    assert agent._schema_cache_key != key_after_full
    assert narrow is not None and len(narrow) < len(full or ())
    assert agent._tool_schemas(policy_for(SOCIAL, voice_active=False)) is None
