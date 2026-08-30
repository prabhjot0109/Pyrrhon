"""Per-turn tool-usage guards, shared by the fast loop and the deep subagent.

These are the runaway-cost brakes: an LLM that re-issues the same call, or
dumps megabytes of grep output into context, burns tokens without progress.
The guard never blocks NEW work — only exact repeats and oversized output.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import nullcontext

from pyrrhon.core.tools.results import ResultStore

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

ALREADY_SHOWN_NOTE = (
    "NOTE: lines {first}-{last} of {path} are already in the tool results above "
    "this turn, so this call was skipped. Use what you have, or ask for lines "
    "outside that range."
)

# Tools whose arguments name a span of a file, so a repeat can be recognised by
# CONTAINMENT rather than only by exact equality. Both spell the span the same
# way, which tests/test_tool_schemas.py would surface immediately if a third
# tool arrived spelling it differently.
RANGE_TOOLS = frozenset({"read_file", "git_blame"})

# Stands in for "to the end of the file", matching evidence.py's own sentinel.
_WHOLE_FILE = 10**9


def _requested_range(name: str, args: dict) -> tuple[int, int]:
    """What span of the file this call asks for.

    The two tools default differently and it is not a spelling difference:
    read_file with only a start reads on to EOF, while git_blame with only a
    start blames that one line, because that is what `-L n,n` means. Reading
    them as if they agreed would suppress a whole-file read as a repeat of one
    blamed line.
    """
    start, end = args.get("start_line"), args.get("end_line")
    if name == "git_blame":
        if start is None:
            return 1, _WHOLE_FILE
        return int(start), int(end if end is not None else start)
    return int(start or 1), int(end) if end else _WHOLE_FILE


class ToolGuard:
    def __init__(
        self,
        max_result_chars: int = MAX_TOOL_RESULT_CHARS,
        max_total_chars: int = MAX_TURN_TOOL_CHARS,
        store: ResultStore | None = None,
        covered: Callable[[str], list[tuple[int, int]]] | None = None,
    ):
        self.max_result_chars = max_result_chars
        self.max_total_chars = max_total_chars
        # The turn's evidence ledger, reached through one narrow accessor
        # rather than handed over whole. None disables containment, which is
        # what the deep subagent gets: it keeps no ledger.
        self._covered = covered
        # Session-scoped, so a pointer minted this turn still resolves three
        # turns later. None means truncate, which is what the deep subagent
        # does: it has no read_result on its belt to follow a pointer with.
        self.store = store
        self._seen: set[tuple[str, str]] = set()
        self._spent = 0

    def duplicate_note(self, name: str, args: dict) -> str | None:
        """What to tell the model instead of running this call, or None to run it.

        Returns the note rather than a bool because the two ways a call is
        redundant need different words. "You already called this with exactly
        these arguments" is false of a containment hit — the arguments are new,
        the LINES are not — and a note that misdescribes what happened is a
        note the model cannot act on.
        """
        key = (name, json.dumps(args, sort_keys=True, default=str))
        if key in self._seen:
            return DUPLICATE_NOTE.format(name=name)
        self._seen.add(key)
        return self._already_shown(name, args)

    def _already_shown(self, name: str, args: dict) -> str | None:
        """A range the model has ALREADY been shown, asked for again.

        Exact-argument matching catches a repeat nobody makes. What actually
        happens is a re-request at a slightly different range — read lines
        1-400, then ask for 40-90 of the same file — which costs a round trip
        and a tool call to hand back bytes that are already in context.

        Asked of the EVIDENCE LEDGER rather than of the previous arguments,
        because the two differ and the difference is not academic: read_file
        clamps at MAX_READ_LINES, so a call for 1-1000 displayed 1-400, and an
        argument-based check would then refuse 401-600 as a repeat of lines
        nobody ever saw. The ledger records what was displayed, which is the
        only version of this question that has a safe answer.
        """
        if self._covered is None or name not in RANGE_TOOLS:
            return None
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return None
        try:
            first, last = _requested_range(name, args)
        except (TypeError, ValueError):
            return None  # unparseable range: let the call through, as before
        for start, end in self._covered(path):
            if start <= first and last <= end:
                return ALREADY_SHOWN_NOTE.format(path=path, first=start, last=end)
        return None

    async def clip(self, result: str, name: str = "", args: dict | None = None) -> str:
        """The per-call cap, applied to CONTEXT rather than to the result.

        What goes into history is the head either way, so the turn's budget is
        unchanged — `_spent` still counts characters the model has to carry,
        which is what TurnState reads for the budget stop. What changed is
        where the tail goes: a file plus a pointer, instead of the floor.

        Truncation survives as the fallback, for the deep subagent (no store)
        and for a session that has spent the store's aggregate cap.
        """
        if len(result) <= self.max_result_chars:
            self._spent += len(result)
            return result
        if self.store is not None:
            ref = await self.store.persist(result, self.max_result_chars, name, args)
            if ref is not None:
                self._spent += len(ref.head)
                return ref.head
        clipped = (
            result[: self.max_result_chars]
            + "\n…[truncated — result exceeded the per-call cap]"
        )
        self._spent += len(clipped)
        return clipped

    @property
    def spent(self) -> int:
        """Tool-result characters this turn has consumed.

        Read by the turn state machine, which owns the budget decision now.
        `exhausted` stays for the deep subagent's own loop, which has no
        TurnState and does not want one.
        """
        return self._spent

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
    - *Guard determinism.* `duplicate_note` mutates `_seen`, and `clip` mutates
      `_spent` and mints result ids. Both run serially — duplicates before
      dispatch, clipping after, in call order — so the outcome does not depend
      on which tool happens to finish first. `clip` awaits a file write now,
      which is the one thing here that is not pure bookkeeping; serialising it
      is what keeps `r1` the first oversized result of the round rather than
      the fastest one.
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
    notes = [guard.duplicate_note(call.name, call.arguments) for call in calls]
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

    live = [i for i, note in enumerate(notes) if note is None]
    finished = await asyncio.gather(*(dispatch(calls[i]) for i in live))

    # A duplicate is answered from bookkeeping alone, and deliberately does not
    # pass through clip() — it costs no tool budget, exactly as before.
    results = [note or "" for note in notes]
    for index, raw in zip(live, finished, strict=True):  # ascending, so clip() stays in call order
        call = calls[index]
        # Awaited one at a time, not gathered: clip mutates _spent and mints
        # result ids, so its outcome must not depend on which write finishes
        # first. The await is a file write behind a to_thread, so serialising
        # it costs the round only on the rare oversized result.
        results[index] = await guard.clip(raw, call.name, call.arguments)
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
