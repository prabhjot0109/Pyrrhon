"""A context-window overflow mid-turn is recovered by compacting and retrying,
not surfaced as a dead 'model returned an error' turn (the hap.png failure)."""

from pathlib import Path

from pyrrhon.core.agent.loop import CONTEXT_FULL_MESSAGE, Agent
from pyrrhon.core.context import TOOL_STUB_MIN, hard_compact_tool_results
from pyrrhon.core.events import SpeechChunk
from pyrrhon.core.providers.errors import ContextLengthExceededError
from pyrrhon.core.providers.llm import (
    LLMReply,
    ToolCall,
)
from pyrrhon.core.tools.base import Tool

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class BigEchoTool(Tool):
    name = "big"
    description = "returns a large blob"
    parameters = {"type": "object", "properties": {"tag": {"type": "string"}},
                  "required": ["tag"]}

    async def run(self, tag: str) -> str:
        return "X" * 4000  # comfortably over TOOL_STUB_MIN


class ScriptedLLM:
    """Returns scripted replies; a reply of ContextLengthExceededError is raised
    instead of returned, to simulate the provider rejecting an over-long prompt."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_hard_compact_elides_all_but_most_recent():
    history = [
        {"role": "tool", "content": "A" * 1000},
        {"role": "tool", "content": "B" * 1000},
        {"role": "tool", "content": "C" * 1000},
    ]
    elided = hard_compact_tool_results(history, keep_recent=1)
    assert elided == 2
    assert "elided to fit the context window" in history[0]["content"]
    assert "elided to fit the context window" in history[1]["content"]
    assert history[2]["content"] == "C" * 1000  # most recent kept intact
    assert len(history[0]["content"]) < TOOL_STUB_MIN


async def test_overflow_mid_turn_recovers_and_answers():
    # Two big results accumulate, then the prompt overflows; recovery elides the
    # older result and the retry succeeds.
    llm = ScriptedLLM([
        LLMReply(tool_calls=(ToolCall(id="c1", name="big", arguments={"tag": "a"}),)),
        LLMReply(tool_calls=(ToolCall(id="c2", name="big", arguments={"tag": "b"}),)),
        ContextLengthExceededError("maximum context length exceeded"),
        LLMReply(text="Recovered: here's the answer."),
    ])
    agent = Agent(
        llm=llm,
        tools=[BigEchoTool()],
        system_prompt="p",
        repo_root=FIXTURE,
        context_budget_tokens=0,  # isolate: no top-of-turn summarize
    )
    events = [e async for e in agent.run_turn([], "explain the big thing")]
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == "Recovered: here's the answer."
    assert CONTEXT_FULL_MESSAGE not in [s.text for s in speech]
    assert llm.calls == 4  # two toolcalls, overflow, successful retry


async def test_unrecoverable_overflow_degrades_honestly():
    # Overflow on the very first call with nothing to compact -> honest message.
    llm = ScriptedLLM([
        ContextLengthExceededError("context_length_exceeded"),
        ContextLengthExceededError("context_length_exceeded"),
        ContextLengthExceededError("context_length_exceeded"),
    ])
    agent = Agent(
        llm=llm,
        tools=[BigEchoTool()],
        system_prompt="p",
        repo_root=FIXTURE,
        context_budget_tokens=0,
    )
    events = [e async for e in agent.run_turn([], "q")]
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == CONTEXT_FULL_MESSAGE
