"""Context fitting: the pre-flight ladder, and the safety net behind it.

Since M16b the ladder runs in front of every request, so a mid-turn overflow
means the ESTIMATE was wrong rather than that nothing was tried. The handler
runs the same ladder once at maximum aggression and then degrades honestly,
instead of being the mechanism (the hap.png failure).
"""

from pathlib import Path

from pyrrhon.core.agent.loop import CONTEXT_FULL_MESSAGE, Agent
from pyrrhon.core.context import (
    TOOL_STUB_MIN,
    compact_tool_results,
    history_tokens,
)
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


def test_the_hard_rung_elides_all_but_the_most_recent():
    """keep_recent is a parameter now, not a second function. It ignores the
    last-user boundary that the cheap rung respects, which was the only thing
    hard_compact_tool_results ever did differently."""
    history = [
        {"role": "tool", "content": "A" * 1000},
        {"role": "tool", "content": "B" * 1000},
        {"role": "tool", "content": "C" * 1000},
    ]
    elided = compact_tool_results(history, keep_recent=1)
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


async def test_an_over_budget_history_is_compacted_before_the_first_request():
    """The ladder runs in FRONT of the request, not behind its failure.

    What goes on the wire has to be under budget, and the expensive rung must
    not have run — steps 2 and 3 are pure and local, so a turn that they could
    fix should never cost a summarize round trip.
    """
    llm = ScriptedLLM([LLMReply(text="Answered.")])
    history: list[dict] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "a", "tool_calls": []},
        {"role": "tool", "content": "X" * 20_000},
        {"role": "assistant", "content": "an earlier answer"},
    ]
    agent = Agent(
        llm=llm,
        tools=[BigEchoTool()],
        system_prompt="p",
        repo_root=FIXTURE,
        context_budget_tokens=2000,
    )
    events = [e async for e in agent.run_turn(history, "and now?")]

    assert [e for e in events if isinstance(e, SpeechChunk)][-1].text == "Answered."
    assert "elided" in history[3]["content"]
    assert history_tokens(history) <= 2000
    assert llm.calls == 1  # the answer, and no summarize round trip
    assert agent.last_trace is not None
    assert agent.last_trace.compaction == "compact"


async def test_a_history_that_fits_is_left_alone():
    llm = ScriptedLLM([LLMReply(text="Answered.")])
    agent = Agent(
        llm=llm,
        tools=[BigEchoTool()],
        system_prompt="p",
        repo_root=FIXTURE,
        context_budget_tokens=1_000_000,
    )
    history: list[dict] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "earlier question"},
        {"role": "tool", "content": "X" * 20_000},
    ]
    [e async for e in agent.run_turn(history, "and now?")]
    assert history[2]["content"] == "X" * 20_000
    assert agent.last_trace is not None
    assert agent.last_trace.compaction == ""
