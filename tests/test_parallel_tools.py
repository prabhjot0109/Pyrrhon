"""Concurrent tool dispatch within a round (M10 Stage 2.1).

A round used to cost the sum of its tool latencies — loop.py dispatched with a
plain `for` loop containing an `await`. It now costs roughly the slowest call.
These tests pin the four invariants that had to survive the change, because
each of them is easy to break and none of them is visible in the happy path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pyrrhon.core.agent.escalate import ThinkDeeperTool
from pyrrhon.core.agent.guards import MAX_CONCURRENT_TOOLS, ToolGuard, run_tool_round
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import ToolCallFinished, ToolCallStarted
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.session import Session
from pyrrhon.core.tools.base import Tool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class SlowTool(Tool):
    description = "sleeps then reports"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, name: str, delay: float = 0.05):
        self.name = name
        self.delay = delay

    async def run(self, **kwargs) -> str:
        await asyncio.sleep(self.delay)
        return f"{self.name} done"


class ExplodingTool(Tool):
    name = "boom"
    description = "raises"
    parameters = {"type": "object", "properties": {}}

    async def run(self, **kwargs) -> str:
        raise ValueError("kaboom")


class ConcurrencyProbe(Tool):
    """Records the high-water mark of simultaneous executions."""

    description = "probes"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, name: str, state: dict):
        self.name = name
        self.state = state

    async def run(self, **kwargs) -> str:
        self.state["live"] += 1
        self.state["peak"] = max(self.state["peak"], self.state["live"])
        await asyncio.sleep(0.02)
        self.state["live"] -= 1
        return "ok"


def calls_for(*names: str) -> tuple[ToolCall, ...]:
    return tuple(
        ToolCall(id=f"c{i}", name=name, arguments={})
        for i, name in enumerate(names)
    )


def make_agent(replies, tools) -> Agent:
    return Agent(
        llm=FakeLLM(replies),
        tools=tools,
        system_prompt="p",
        repo_root=FIXTURE,
    )


# -- the win itself ----------------------------------------------------------


async def test_a_round_costs_the_slowest_call_not_the_sum():
    tools = [SlowTool(f"t{i}", 0.08) for i in range(4)]
    agent = make_agent(
        [LLMReply(tool_calls=calls_for("t0", "t1", "t2", "t3")),
         LLMReply(text="done.")],
        tools,
    )
    started = asyncio.get_running_loop().time()
    [e async for e in agent.run_turn([], "q")]
    elapsed = asyncio.get_running_loop().time() - started
    # Sum would be ~0.32s; max is ~0.08s. Generous bound to stay non-flaky.
    assert elapsed < 0.20


# -- invariant 1: history order ----------------------------------------------


async def test_tool_results_land_in_call_order_regardless_of_finish_order():
    """The slowest tool is called FIRST, so completion order is the reverse of
    call order — exactly the case a naive append-from-task gets wrong."""
    tools = [SlowTool("slow", 0.10), SlowTool("medium", 0.05), SlowTool("fast", 0.01)]
    agent = make_agent(
        [LLMReply(tool_calls=calls_for("slow", "medium", "fast")),
         LLMReply(text="done.")],
        tools,
    )
    history: list[dict] = []
    [e async for e in agent.run_turn(history, "q")]

    results = [m["content"] for m in history if m.get("role") == "tool"]
    assert results == ["slow done", "medium done", "fast done"]
    ids = [m["tool_call_id"] for m in history if m.get("role") == "tool"]
    assert ids == ["c0", "c1", "c2"]


async def test_events_are_all_starts_then_all_finishes_in_call_order():
    tools = [SlowTool("slow", 0.08), SlowTool("fast", 0.01)]
    agent = make_agent(
        [LLMReply(tool_calls=calls_for("slow", "fast")), LLMReply(text="done.")],
        tools,
    )
    events = [e async for e in agent.run_turn([], "q")]
    names = [
        (type(e).__name__, e.name)
        for e in events
        if isinstance(e, (ToolCallStarted, ToolCallFinished))
    ]
    assert names == [
        ("ToolCallStarted", "slow"),
        ("ToolCallStarted", "fast"),
        ("ToolCallFinished", "slow"),
        ("ToolCallFinished", "fast"),
    ]


# -- invariant 2: guard determinism ------------------------------------------


async def test_duplicates_are_answered_without_dispatch_and_cost_no_budget():
    guard = ToolGuard()
    ran: list[str] = []

    async def runner(name, args):
        ran.append(name)
        return "x" * 100

    calls = calls_for("grep", "grep", "glob")
    results = await run_tool_round(calls, runner, guard)

    assert ran == ["grep", "glob"]           # the repeat never dispatched
    assert "already called grep" in results[1]
    # The duplicate note bypasses clip(), so it consumes no tool budget —
    # matching the sequential behaviour exactly.
    assert guard._spent == 200


async def test_clip_accounting_is_independent_of_completion_order():
    """clip() mutates _spent; running it after the gather, in call order,
    keeps the total identical however the tools interleave."""

    async def runner(name, args):
        await asyncio.sleep(0.03 if name == "slow" else 0.0)
        return "y" * 50

    guard = ToolGuard()
    await run_tool_round(calls_for("slow", "fast"), runner, guard)
    assert guard._spent == 100


# -- invariant 3: cancellation -----------------------------------------------


async def test_barge_in_during_a_parallel_round_leaves_history_repairable():
    """gather propagates cancellation into the children; because results are
    all-or-nothing, history ends with an orphan tool_calls message that
    _repair_history pops."""

    class Hanging(Tool):
        name = "hang"
        description = "hangs"
        parameters = {"type": "object", "properties": {}}

        async def run(self, **kwargs) -> str:
            await asyncio.Event().wait()

    agent = make_agent(
        [LLMReply(tool_calls=calls_for("hang", "hang"))], [Hanging()]
    )
    # Distinct ids so neither call is treated as a duplicate.
    agent.llm = FakeLLM([
        LLMReply(tool_calls=(
            ToolCall(id="c0", name="hang", arguments={"a": 1}),
            ToolCall(id="c1", name="hang", arguments={"a": 2}),
        ))
    ])
    session = Session(agent)

    async def drive():
        async for _ in session.run_turn("q"):
            pass

    task = asyncio.create_task(drive())
    await asyncio.sleep(0.05)
    session.abort_current_turn()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # No orphan assistant(tool_calls) and no stranded tool results: the chat
    # API contract holds, so the next turn is sendable.
    assert not any(m.get("tool_calls") for m in session.history)
    assert not any(m.get("role") == "tool" for m in session.history)


# -- invariant 4: no ExceptionGroup escapes ----------------------------------


async def test_a_raising_tool_becomes_an_error_string_not_a_crash():
    agent = make_agent(
        [LLMReply(tool_calls=calls_for("boom", "ok")), LLMReply(text="handled.")],
        [ExplodingTool(), SlowTool("ok", 0.0)],
    )
    history: list[dict] = []
    [e async for e in agent.run_turn(history, "q")]

    results = [m["content"] for m in history if m.get("role") == "tool"]
    assert results[0].startswith("ERROR: boom failed: ValueError")
    assert results[1] == "ok done"  # its sibling still completed


async def test_one_exploding_tool_does_not_cancel_its_siblings():
    """TaskGroup semantics would kill the round; gather over guarded
    coroutines must not."""
    guard = ToolGuard()

    async def runner(name, args):
        if name == "boom":
            raise ValueError("kaboom")
        await asyncio.sleep(0.02)
        return f"{name} ok"

    results = await run_tool_round(calls_for("boom", "a", "b"), runner, guard)
    assert results[0].startswith("ERROR:")
    assert results[1] == "a ok"
    assert results[2] == "b ok"


# -- the executor-starvation guard -------------------------------------------


async def test_concurrency_is_capped_to_leave_the_grounding_gate_headroom():
    state = {"live": 0, "peak": 0}
    names = [f"p{i}" for i in range(MAX_CONCURRENT_TOOLS + 4)]
    tools = [ConcurrencyProbe(n, state) for n in names]
    agent = make_agent(
        [LLMReply(tool_calls=calls_for(*names)), LLMReply(text="done.")], tools
    )
    [e async for e in agent.run_turn([], "q")]
    assert state["peak"] <= MAX_CONCURRENT_TOOLS


# -- the deep subagent got the same treatment --------------------------------


async def test_the_deep_subagent_round_is_concurrent_too():
    deep = FakeLLM([
        LLMReply(tool_calls=calls_for("d0", "d1", "d2")),
        LLMReply(text="report."),
    ])
    tool = ThinkDeeperTool(deep, tools=[SlowTool(f"d{i}", 0.06) for i in range(3)])
    started = asyncio.get_running_loop().time()
    report = await tool.run(question="q", context="c")
    elapsed = asyncio.get_running_loop().time() - started
    assert report == "report."
    assert elapsed < 0.15  # sum would be ~0.18s
