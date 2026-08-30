"""The shared subagent runner: bounds, isolation, cancellation, provenance.

`tests/test_escalation.py` is the behaviour-preserving regression suite for the
extraction — it exercises the runner through `think_deeper` and passed
unedited. These tests are about the runner's own contract, which two callers
now depend on and neither one fully covers.
"""

from __future__ import annotations

import asyncio

import pytest

from pyrrhon.core.agent.guards import ToolGuard
from pyrrhon.core.agent.subagent import (
    DISPATCH_TOOLS,
    REPORT_NUDGE,
    check_depth,
    run_subagent,
)
from pyrrhon.core.grounding.evidence import EvidenceLedger
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.tools.base import Tool
from tests.helpers import FakeLLM


class FakeReader(Tool):
    name = "read_file"
    description = "fake"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def __init__(self, body: str = "contents"):
        self.calls: list[dict] = []
        self._body = body

    async def run(self, path: str) -> str:
        self.calls.append({"path": path})
        return f"    1| {self._body} of {path}\n    2| second line"


def _call(index: int, path: str) -> ToolCall:
    return ToolCall(id=f"c{index}", name="read_file", arguments={"path": path})


async def test_the_round_cap_stops_the_loop_and_forces_a_report():
    """The cap is a bound on TOOL rounds, and the report is one call past it."""
    llm = FakeLLM(
        [LLMReply(tool_calls=(_call(i, f"f{i}.py"),)) for i in range(3)]
        + [LLMReply(text="Report: three files, one seam.")]
    )
    report = await run_subagent(
        llm, [FakeReader()], "system", "task", max_rounds=3
    )
    assert report == "Report: three files, one seam."
    assert len(llm.calls) == 4  # three tool rounds, then the forced report
    assert llm.calls[-1]["tools"] is None
    assert llm.calls[-1]["messages"][-1]["content"] == REPORT_NUDGE


async def test_a_spent_tool_budget_ends_the_investigation_early():
    """The char budget bounds a run the round cap would have let continue."""
    llm = FakeLLM(
        [LLMReply(tool_calls=(_call(i, f"f{i}.py"),)) for i in range(1)]
        + [LLMReply(text="Report from a spent budget.")]
    )
    guard = ToolGuard(max_result_chars=50, max_total_chars=10)
    report = await run_subagent(
        llm, [FakeReader()], "system", "task", max_rounds=6, guard=guard
    )
    assert report == "Report from a spent budget."
    assert len(llm.calls) == 2  # one round exhausted it, then the report
    assert guard.exhausted


async def test_the_runner_holds_no_reference_to_the_callers_history():
    """Isolation is a property of the signature, not a discipline.

    The runner takes a system prompt and a task STRING. There is no caller
    list to alias, so the parent's history cannot be mutated and none of it
    can leak into the subagent's context by accident.
    """
    parent_history = [
        {"role": "system", "content": "the parent's prompt"},
        {"role": "user", "content": "a secret the subagent must not see"},
    ]
    llm = FakeLLM([LLMReply(text="report")])
    await run_subagent(llm, [], "subagent prompt", "the question", max_rounds=2)

    assert parent_history == [
        {"role": "system", "content": "the parent's prompt"},
        {"role": "user", "content": "a secret the subagent must not see"},
    ]
    sent = llm.calls[0]["messages"]
    assert sent == [
        {"role": "system", "content": "subagent prompt"},
        {"role": "user", "content": "the question"},
    ]


async def test_cancelling_the_awaiting_task_cancels_an_in_flight_tool():
    """Barge-in must kill the investigation, not orphan a running tool."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowTool(Tool):
        name = "grep"
        description = "fake"
        parameters = {"type": "object", "properties": {}}

        async def run(self) -> str:
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return "never"  # pragma: no cover

    llm = FakeLLM(
        [LLMReply(tool_calls=(ToolCall(id="c1", name="grep", arguments={}),))]
    )
    task = asyncio.create_task(
        run_subagent(llm, [SlowTool()], "system", "task", max_rounds=2)
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(cancelled.wait(), timeout=2)


async def test_a_supplied_ledger_records_what_the_subagent_saw():
    """Provenance, kept in the subagent's OWN ledger.

    The parent absorbs this afterwards. Recording straight into the parent's
    would make lines the subagent read look like lines the parent was shown,
    which is what M16c's re-read suppression acts on.
    """
    llm = FakeLLM(
        [
            LLMReply(tool_calls=(_call(1, "app.py"),)),
            LLMReply(text="Report: app.py:1 is the entry point."),
        ]
    )
    ledger = EvidenceLedger()
    await run_subagent(
        llm, [FakeReader()], "system", "task", max_rounds=3, ledger=ledger
    )
    assert ledger.observed("app.py", 1)
    assert ledger.observed("app.py", 2)
    assert not ledger.observed("app.py", 9)


async def test_round_progress_is_reported_with_the_tools_that_ran():
    llm = FakeLLM(
        [
            LLMReply(tool_calls=(_call(1, "a.py"), _call(2, "b.py"))),
            LLMReply(text="report"),
        ]
    )
    seen: list[tuple[int, str]] = []
    await run_subagent(
        llm,
        [FakeReader()],
        "system",
        "task",
        max_rounds=3,
        on_round=lambda number, detail: seen.append((number, detail)),
    )
    assert seen == [(1, "read_file")]  # deduplicated: two calls, one tool name


async def test_a_raising_progress_callback_never_kills_the_investigation():
    llm = FakeLLM(
        [LLMReply(tool_calls=(_call(1, "a.py"),)), LLMReply(text="report")]
    )

    def explode(number: int, detail: str) -> None:
        raise RuntimeError("the renderer fell over")

    assert (
        await run_subagent(
            llm, [FakeReader()], "system", "task", max_rounds=3, on_round=explode
        )
        == "report"
    )


async def test_the_label_names_the_caller_in_every_error_string():
    class Exploding:
        async def chat(self, messages, tools=None):
            raise RuntimeError("provider down")

    failed = await run_subagent(
        Exploding(), [], "system", "task", max_rounds=2, label="explore"
    )
    assert failed == "ERROR: explore call failed: provider down"

    silent = await run_subagent(
        FakeLLM([LLMReply(text=None)]), [], "s", "t", max_rounds=2, label="explore"
    )
    assert silent == "ERROR: explore returned no text."


def test_depth_stays_structurally_one():
    class Dispatcher(Tool):
        description = "no"
        parameters = {"type": "object", "properties": {}}

        async def run(self) -> str:  # pragma: no cover - never called
            return ""

    assert DISPATCH_TOOLS == {"think_deeper", "explore"}
    check_depth([FakeReader()])  # a plain read-only belt is fine
    for name in DISPATCH_TOOLS:
        offender = type("Offender", (Dispatcher,), {"name": name})()
        with pytest.raises(ValueError, match="depth is 1"):
            check_depth([FakeReader(), offender])
