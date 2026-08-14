"""Per-turn tool-usage guards, shared by the fast loop and the deep subagent.

These are the runaway-cost brakes: an LLM that re-issues the same call, or
dumps megabytes of grep output into context, burns tokens without progress.
The guard never blocks NEW work — only exact repeats and oversized output.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext

MAX_TOOL_RESULT_CHARS = 8_000    # per tool result
MAX_TURN_TOOL_CHARS = 40_000     # cumulative per turn / per subagent run

# Tool calls dispatched at once within one round. Capped, not unbounded:
# read_file, grep, find_symbol AND GroundingGate.check all share the default
# asyncio.to_thread executor, and the gate sits directly on the speech
# critical path. Six concurrent greps saturating the pool would stall the
# thing the user is waiting to hear. Leave the gate headroom.
MAX_CONCURRENT_TOOLS = 4

DUPLICATE_NOTE = (
    "NOTE: you already called {name} with exactly these arguments this turn; "
    "the result has not changed. Use what you have or try different arguments."
)


class ToolGuard:
    def __init__(
        self,
        max_result_chars: int = MAX_TOOL_RESULT_CHARS,
        max_total_chars: int = MAX_TURN_TOOL_CHARS,
    ):
        self.max_result_chars = max_result_chars
        self.max_total_chars = max_total_chars
        self._seen: set[tuple[str, str]] = set()
        self._spent = 0

    def is_duplicate(self, name: str, args: dict) -> bool:
        key = (name, json.dumps(args, sort_keys=True, default=str))
        if key in self._seen:
            return True
        self._seen.add(key)
        return False

    def clip(self, result: str) -> str:
        if len(result) > self.max_result_chars:
            result = (
                result[: self.max_result_chars]
                + "\n…[truncated — result exceeded the per-call cap]"
            )
        self._spent += len(result)
        return result

    @property
    def exhausted(self) -> bool:
        return self._spent >= self.max_total_chars


async def run_tool_round(calls, runner, guard: ToolGuard, round_trace=None) -> list[str]:
    """Run one round's tool calls CONCURRENTLY; return results in call order.

    A round used to cost the sum of its tool latencies because dispatch was a
    plain `for` loop with an `await` inside. It now costs roughly the slowest
    call. Four invariants have to survive that, each with a specific mechanism:

    - *History order.* Results come back indexed by call position, so the
      caller extends history in call order. Nothing is ever appended from
      inside a task.
    - *Guard determinism.* `is_duplicate` mutates `_seen` and `clip` mutates
      `_spent`. Both are pure bookkeeping with no I/O, so both run serially —
      duplicates before dispatch, clipping after — and the outcome does not
      depend on which tool happens to finish first.
    - *Cancellation.* `gather` propagates CancelledError into its children, so
      barge-in still kills in-flight tools. Results are all-or-nothing, so
      history is either fully extended or not extended at all; the partial
      case that `_repair_history` exists for cannot arise here.
    - *No ExceptionGroup.* Every dispatch is wrapped so a raising tool becomes
      an `ERROR:` string, mirroring the existing TypeError handling one level
      down. Callers therefore need no `except*` anywhere.

    Note this changes WHEN work happens, never HOW MUCH: the sequential loop
    already ran calls #2..#N after the budget was blown on #1, since
    `exhausted` is only checked after the round.
    """
    # Serial and first: pure bookkeeping, and it decides what gets dispatched.
    duplicates = [guard.is_duplicate(call.name, call.arguments) for call in calls]
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TOOLS)

    async def dispatch(call) -> str:
        async with semaphore:
            timer = round_trace.time_tool(call.name) if round_trace else nullcontext()
            try:
                with timer:
                    return await runner(call.name, call.arguments)
            except asyncio.CancelledError:
                raise  # barge-in — must not be swallowed as a tool error
            except Exception as exc:
                return f"ERROR: {call.name} failed: {type(exc).__name__}: {exc}"

    live = [i for i, dup in enumerate(duplicates) if not dup]
    finished = await asyncio.gather(*(dispatch(calls[i]) for i in live))

    # A duplicate is answered from bookkeeping alone, and deliberately does not
    # pass through clip() — it costs no tool budget, exactly as before.
    results = [DUPLICATE_NOTE.format(name=call.name) for call in calls]
    for index, raw in zip(live, finished, strict=True):  # ascending, so clip() stays in call order
        results[index] = guard.clip(raw)
    return results


def assistant_tool_message(reply) -> dict:
    """Chat-API-shaped assistant message carrying tool calls.

    Lives here (not loop.py) so escalate.py can use it without a circular
    import — loop.py imports ThinkDeeperTool from escalate.py.
    """
    return {
        "role": "assistant",
        "content": reply.text,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in reply.tool_calls
        ],
    }
