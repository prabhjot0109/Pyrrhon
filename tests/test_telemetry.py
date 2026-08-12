"""TurnTrace: the per-turn latency breakdown behind last_turn_latency_ms.

These tests pin the *shape* of the measurement, not the numbers — asserting a
wall-clock threshold would make the suite flaky on a loaded CI box. Where a
duration matters we assert an ordering or a relationship between two spans
measured in the same run, which is stable regardless of machine speed.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import SpeechChunk
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.session import Session
from pyrrhon.core.telemetry import RoundTrace, TurnTrace
from pyrrhon.core.tools.base import Tool
from pyrrhon.core.tools.repo import ReadFileTool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class SlowTool(Tool):
    """A tool with a controllable, non-zero duration."""

    description = "sleeps"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, name: str, delay: float = 0.05):
        self.name = name
        self.delay = delay

    async def run(self, **kwargs) -> str:
        await asyncio.sleep(self.delay)
        return f"{self.name} done"


def make_agent(replies, tools=None, gate=None, **kwargs) -> Agent:
    return Agent(
        llm=FakeLLM(replies),
        tools=tools if tools is not None else [ReadFileTool(FIXTURE)],
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
        grounding_gate=gate,
        **kwargs,
    )


async def drain(agent: Agent, history: list[dict], text: str) -> list:
    return [event async for event in agent.run_turn(history, text)]


# -- the dataclass itself ----------------------------------------------------


def test_fresh_trace_has_nothing_recorded():
    trace = TurnTrace()
    assert trace.rounds == []
    assert trace.first_speech_ms is None
    assert trace.total_ms is None
    assert trace.ttft_ms is None
    assert trace.llm_ms == 0.0
    assert trace.tool_calls == 0


def test_first_speech_is_stamped_once():
    """Later chunks must not move the metric — it is time to FIRST speech."""
    trace = TurnTrace()
    trace.mark_first_speech()
    first = trace.first_speech_ms
    assert first is not None
    trace.mark_first_speech()
    assert trace.first_speech_ms == first


def test_ttft_is_stamped_once_per_round():
    round_trace = RoundTrace(index=0)
    round_trace.mark_ttft()
    first = round_trace.llm_ttft_ms
    assert first is not None
    round_trace.mark_ttft()
    assert round_trace.llm_ttft_ms == first


def test_spans_record_even_when_the_block_raises():
    """Barge-in cancels the turn mid-await; the time spent still counts."""
    trace = TurnTrace()
    try:
        with trace.time_preamble():
            time.sleep(0.002)  # do real work first, so >0 is meaningful
            raise asyncio.CancelledError
    except asyncio.CancelledError:
        pass
    assert trace.preamble_ms > 0.0


def test_tool_spans_aggregate_by_name_but_keep_every_call():
    round_trace = RoundTrace(index=0)
    with round_trace.time_tool("grep"):
        pass
    with round_trace.time_tool("grep"):
        pass
    with round_trace.time_tool("read_file"):
        pass
    assert len(round_trace.tools) == 3          # every call kept
    assert set(round_trace.tool_ms) == {"grep", "read_file"}  # aggregated view
    assert round_trace.tool_ms["grep"] >= 0.0


def test_parallel_speedup_is_one_when_no_tools_ran():
    """Callers average this across rounds, so it must never divide by zero."""
    assert RoundTrace(index=0).parallel_speedup == 1.0


# -- wiring into the agent loop ----------------------------------------------


async def test_a_plain_turn_records_preamble_round_and_first_speech():
    agent = make_agent([LLMReply(text="It prints a greeting.")])
    events = await drain(agent, [], "what does app.py do?")

    assert any(isinstance(e, SpeechChunk) for e in events)
    trace = agent.last_trace
    assert trace is not None
    assert trace.total_ms is not None and trace.total_ms >= 0.0
    assert len(trace.rounds) == 1
    assert trace.rounds[0].llm_total_ms >= 0.0
    # Non-streaming: the whole reply lands at once, so ttft is recorded too and
    # the metric stays comparable across both paths.
    assert trace.ttft_ms is not None
    assert trace.prompt_chars > 0
    assert trace.schema_chars > 0
    assert trace.streamed is False


async def test_tool_round_records_each_call_and_the_round_wall_clock():
    replies = [
        LLMReply(
            tool_calls=(
                ToolCall(id="c1", name="slow_a", arguments={}),
                ToolCall(id="c2", name="slow_b", arguments={}),
            )
        ),
        LLMReply(text="done."),
    ]
    agent = make_agent(replies, tools=[SlowTool("slow_a"), SlowTool("slow_b")])
    await drain(agent, [], "run both tools")

    trace = agent.last_trace
    assert trace.tool_calls == 2
    assert len(trace.rounds) == 2                       # tool round + answer round
    tool_round = trace.rounds[0]
    assert set(tool_round.tool_ms) == {"slow_a", "slow_b"}
    assert all(span.ms > 0.0 for span in tool_round.tools)
    # Concurrent dispatch (Stage 2.1): the round costs about the slowest call,
    # not the sum. Two equal-length tools should land near 2x; assert well
    # under that so a loaded machine can't make this flaky, while still
    # failing loudly if dispatch ever goes back to being sequential.
    assert tool_round.parallel_speedup > 1.5
    assert tool_round.tool_wall_ms < tool_round.tool_total_ms * 0.75


async def test_gate_time_is_attributed_to_the_round():
    agent = make_agent(
        [LLMReply(text="greet lives at utils/helpers.py:1.")],
        gate=GroundingGate(FIXTURE),
    )
    await drain(agent, [], "where is greet?")
    assert agent.last_trace.gate_ms > 0.0


async def test_trace_is_finished_even_when_the_provider_fails():
    """FakeLLM with no scripted replies raises; the turn degrades honestly and
    the trace must still be closed out rather than left half-written."""
    agent = make_agent([])
    events = await drain(agent, [], "anything")
    assert any(isinstance(e, SpeechChunk) for e in events)
    assert agent.last_trace.total_ms is not None


async def test_as_dict_is_json_serialisable():
    """The latency harness writes these to disk for --compare."""
    agent = make_agent([LLMReply(text="hi.")])
    await drain(agent, [], "hello")
    blob = json.dumps(agent.last_trace.as_dict())
    assert "first_speech_ms" in blob
    assert "tool_wall_ms" in blob


async def test_summary_renders_without_a_completed_turn():
    """/debug may ask for this on a turn that was cut short — no crash, no
    None leaking into the string."""
    assert "—" in TurnTrace().summary()


# -- wiring into the session -------------------------------------------------


async def test_session_publishes_the_trace_and_keeps_latency_assignable():
    session = Session(make_agent([LLMReply(text="one.")]))
    assert session.last_turn_trace is None
    async for _ in session.run_turn("first"):
        pass

    trace = session.last_turn_trace
    assert trace is not None
    assert trace.first_speech_ms is not None
    # last_turn_latency_ms must stay a plain attribute, not a property derived
    # from the trace: tests/test_latency.py assigns it directly as a sentinel.
    session.last_turn_latency_ms = -1.0
    assert session.last_turn_latency_ms == -1.0


async def test_session_publishes_a_partial_trace_after_barge_in():
    """A turn killed mid-flight is the one you most want to inspect."""

    class HangingLLM:
        async def chat(self, messages, tools=None):
            await asyncio.sleep(10)

    session = Session(make_agent([]))
    session.agent.llm = HangingLLM()

    async def consume():
        async for _ in session.run_turn("slow question"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    session.abort_current_turn()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert session.last_turn_trace is not None
    assert session.last_turn_trace.preamble_ms >= 0.0


# -- schema memoisation (M10 Stage 2.2) --------------------------------------


async def test_tool_schemas_are_rebuilt_only_when_the_belt_changes():
    """A CPU tidy, not a prompt-cache fix: the rebuilt list already serialised
    byte-identically. The belt is mutable (plugins, MCP, design mode), so its
    identity is the cache key."""
    agent = make_agent([LLMReply(text="a."), LLMReply(text="b.")])
    first = agent._tool_schemas()
    assert agent._tool_schemas() is first  # same object, not rebuilt

    agent.tools["extra"] = SlowTool("extra", 0.0)
    rebuilt = agent._tool_schemas()
    assert rebuilt is not first
    assert {s["function"]["name"] for s in rebuilt} == set(agent.tools)
