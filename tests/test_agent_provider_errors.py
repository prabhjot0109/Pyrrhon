"""A provider error mid-turn must degrade to an honest spoken line, never a
silent turn death (which is what an unhandled exception becomes once the
producer task swallows it — the user hears/sees nothing).
"""

from pathlib import Path

from pyrrhon.core.agent.loop import (
    PROVIDER_ERROR_MESSAGE,
    TOOL_RETRY_EXHAUSTED_MESSAGE,
    Agent,
)
from pyrrhon.core.events import SpeechChunk
from pyrrhon.core.providers.llm import InvalidToolCallError, LLMReply, ToolCall
from pyrrhon.core.tools.repo import ReadFileTool

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class ScriptedLLM:
    """Returns replies in order; an entry that is an Exception is raised."""

    def __init__(self, script):
        self._script = list(script)
        self.model = "scripted"
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_agent(script):
    agent = Agent(
        llm=ScriptedLLM(script),
        tools=[ReadFileTool(FIXTURE)],
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
    )
    return agent


async def collect(agent, history, text):
    return [event async for event in agent.run_turn(history, text)]


async def test_invalid_tool_call_recovers_after_one_nudge():
    # gpt-oss-style: first call is rejected, then the model behaves.
    agent = make_agent(
        [InvalidToolCallError("tool 'search' was not in request.tools"),
         LLMReply(text="It prints a greeting.")]
    )
    events = await collect(agent, [], "what is this?")
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech and speech[-1].text == "It prints a greeting."


async def test_repeated_invalid_tool_call_degrades_visibly():
    agent = make_agent(
        [InvalidToolCallError("bad tool"), InvalidToolCallError("bad tool again")]
    )
    events = await collect(agent, [], "what is this?")
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech and speech[-1].text == TOOL_RETRY_EXHAUSTED_MESSAGE


async def test_generic_provider_error_degrades_visibly():
    agent = make_agent([RuntimeError("connection reset")])
    history: list[dict] = []
    events = await collect(agent, history, "what is this?")
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech and speech[-1].text == PROVIDER_ERROR_MESSAGE
    # History still ends coherently on the spoken message.
    assert history[-1]["role"] == "assistant"
