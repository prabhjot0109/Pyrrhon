"""The bounded read-only subagent runner — Pyrrhon's context firewall.

`escalate.py` grew this loop first, under the name `think_deeper`: a fresh
context, its own read-only belt, a round cap, and one compact cited report
handed back. That is a context firewall wearing a different name, so M16d
extracts it rather than inventing a second mechanism. `explore` is a second,
cheaper caller of the same runner.

What the firewall buys is asymmetric and worth naming precisely. A search that
touches ten files costs the *subagent's* context ten tool results and costs the
*parent's* context one report. The parent never sees the raw output, which is
the entire point and is why `tests/test_explore.py` asserts it rather than
assuming it.

Three properties the callers depend on:

- **Isolation.** The runner is handed a system prompt and a task string, never
  the parent's history list. There is no shared list to mutate, so isolation is
  a fact about the signature rather than a discipline the caller has to keep.
- **Depth is structurally 1.** `check_depth` refuses a belt containing a
  dispatch tool, so no subagent can dispatch another. Called from each tool's
  `__init__`, which fails at build time rather than on the first live call.
- **Cancellation propagates.** `run()` is awaited inside `Agent.run_turn`,
  inside the Session's cancellable task, and `run_tool_round` gathers — so a
  barge-in kills the whole investigation, as it did before the extraction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from pyrrhon.core.agent.guards import (
    ToolGuard,
    assistant_tool_message,
    run_tool_round,
)
from pyrrhon.core.grounding.evidence import EvidenceLedger
from pyrrhon.core.tools.base import Tool, run_tool

# Tools that dispatch a subagent. A subagent's belt may contain neither, and
# the set is named once so a third dispatcher cannot be added without deciding
# what depth means for it.
DISPATCH_TOOLS = frozenset({"think_deeper", "explore"})

REPORT_NUDGE = (
    "Investigation budget exhausted. Write your report now from the evidence "
    "above; cite only path:line locations you actually saw."
)


def check_depth(tools: Iterable[Tool]) -> None:
    """Refuse a subagent belt that could dispatch another subagent."""
    offenders = sorted({tool.name for tool in tools if tool.name in DISPATCH_TOOLS})
    if offenders:
        raise ValueError(
            f"a subagent belt must not contain {', '.join(offenders)} — depth is 1"
        )


async def run_subagent(
    llm,
    tools: list[Tool],
    system_prompt: str,
    task: str,
    *,
    max_rounds: int,
    guard: ToolGuard | None = None,
    label: str = "subagent",
    ledger: EvidenceLedger | None = None,
    on_round: Callable[[int, str], object] | None = None,
) -> str:
    """Run one bounded investigation in a fresh context; return its report.

    `label` names the caller in error strings, because "the deep model failed"
    and "explore failed" are different things to read in a transcript.

    `ledger` is the subagent's OWN evidence, never the parent's. What it
    recorded is provenance the parent can absorb (see
    `EvidenceLedger.absorb`), and keeping it separate is what stops a line the
    subagent read from being treated as a line the parent was shown — those
    are different claims, and M16c's re-read suppression acts on the second.

    `on_round` is a callback rather than a yield: this runs inside an awaited
    tool call, and an async generator cannot yield through one.
    """
    belt = {tool.name: tool for tool in tools}
    schemas = [tool.schema() for tool in belt.values()] or None
    guard = guard if guard is not None else ToolGuard()
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    async def dispatch(name: str, args: dict) -> str:
        return await run_tool(belt, name, args)

    try:
        for number in range(1, max_rounds + 1):
            reply = await llm.chat(messages, tools=schemas)
            if not reply.tool_calls:
                return reply.text or f"ERROR: {label} returned no text."
            messages.append(assistant_tool_message(reply))
            # Concurrent, same as the fast loop: a subagent round that reads
            # four files should cost one read, not four.
            results = await run_tool_round(reply.tool_calls, dispatch, guard)
            messages.extend(
                {"role": "tool", "tool_call_id": call.id, "content": result}
                for call, result in zip(reply.tool_calls, results, strict=True)
            )
            if ledger is not None:
                for call, result in zip(reply.tool_calls, results, strict=True):
                    # No `attribute` indirection: read_result is off every
                    # subagent belt, so a result is always its own call's.
                    ledger.record_tool_result(call.name, call.arguments, result)
            _report_round(on_round, number, reply.tool_calls)
            if guard.exhausted:
                break
        reply = await llm.chat(
            [*messages, {"role": "user", "content": REPORT_NUDGE}], tools=None
        )
        return reply.text or f"ERROR: {label} returned no text."
    except Exception as exc:  # provider/network failure must not kill the turn
        return f"ERROR: {label} call failed: {exc}"


def _report_round(
    on_round: Callable[[int, str], object] | None, number: int, calls
) -> None:
    """Tell the caller a round finished, and never let that kill the run.

    The callback reaches a channel's renderer directly, the same shape
    `orient_in_background` uses. A renderer that raises must cost the user a
    progress line, not the investigation they are waiting for.
    """
    if on_round is None:
        return
    try:
        on_round(number, ", ".join(dict.fromkeys(call.name for call in calls)))
    except Exception:  # pragma: no cover - a progress line is never load-bearing
        pass
