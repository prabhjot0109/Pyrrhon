"""`explore` — the cheap dispatch over the shared subagent runner.

`think_deeper` sends the DEEP model to reason about a design. `explore` sends
the FAST model to find where something is. The distinction is the whole reason
there are two tools rather than one with a flag: a locating question routed to
the deep slot pays escalation latency for work that is search, which is exactly
the cost this tool exists to avoid.

What it buys the parent is a context saving, not an answer it could not have
reached. A question spanning ten files costs the main conversation ten tool
results and ten rounds of grinding; the same question dispatched here costs it
one short cited report, and the ten results are discarded with the subagent's
context. That asymmetry is the firewall.

Two bounds are structural rather than advisory. The report is capped in code,
because a prompt asking for 200 words is a request and this is a contract. And
the cap sits below the per-call tool-result cap, so a report can never be
persisted and paged through — the same relationship M16c pinned between
PAGE_CHARS and MAX_TOOL_RESULT_CHARS, for the same reason.
"""

from __future__ import annotations

from collections.abc import Callable

from pyrrhon.core.agent.guards import MAX_TURN_TOOL_CHARS, ToolGuard
from pyrrhon.core.agent.prompts import EXPLORE_AGENT_PROMPT
from pyrrhon.core.agent.subagent import check_depth, run_subagent
from pyrrhon.core.grounding.evidence import EvidenceLedger
from pyrrhon.core.tools.base import Tool

# Tight, and tighter than DEEP_MAX_ROUNDS by design: a scout that needs a
# ninth search is answering the wrong question. Six is a repo map, two
# searches, three reads and a report.
EXPLORE_MAX_ROUNDS = 6

# Half a turn's budget. The subagent spends this inside its own context, so it
# never lands on the parent — but an unbounded scout is still a user waiting.
EXPLORE_TOOL_CHARS = MAX_TURN_TOOL_CHARS // 2

# The firewall's contract, in code. Must stay BELOW MAX_TOOL_RESULT_CHARS: a
# report comes back through the parent's ToolGuard.clip like any other tool
# result, so one sized at the cap would be persisted to the result store and
# the model would page through a summary. Pinned by a test, not by arithmetic
# in a comment — the two constants live in different modules and cannot see
# each other.
MAX_REPORT_CHARS = 4_000

_OVERLONG = "\n…[report truncated — the scout was asked for 200 words]"


class ExploreTool(Tool):
    name = "explore"
    description = (
        "Dispatch a fast read-only scout to answer a locating question that "
        "spans several files, in one round instead of many. It searches the "
        "repo in a separate context and returns a short report citing "
        "path:line for each finding. Reach for it when answering would take "
        "three or more searches; one known file is cheaper to read directly."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The locating question, e.g. 'where is barge-in handled end to end?'",
            },
            "hint": {
                "type": "string",
                "description": "Paths, symbols or findings you already have, to save the scout a search",
            },
        },
        "required": ["question"],
    }

    def __init__(self, llm, tools: list[Tool] | None = None,
                 max_rounds: int = EXPLORE_MAX_ROUNDS):
        check_depth(tools or [])
        self.llm = llm  # the FAST slot: locating is search, not reasoning
        self.tools = {tool.name: tool for tool in (tools or [])}
        self.max_rounds = max_rounds
        # Both patched by build_agent, in the shape RepoMapTool's `mentions`
        # established: a tool must not hold a back-reference to the agent that
        # calls it. `_on_progress` reaches a channel's renderer; `_absorb`
        # hands the parent what this run verified.
        self._on_progress: Callable[[str, int, str], object] | None = None
        self._absorb: Callable[[EvidenceLedger], object] | None = None

    async def run(self, question: str, hint: str = "") -> str:
        # The subagent's OWN ledger. Never the parent's: what the scout read is
        # verified, but it was not displayed in the parent's context, and
        # M16c's re-read suppression acts on what was displayed.
        ledger = EvidenceLedger()
        report = await run_subagent(
            self.llm,
            list(self.tools.values()),
            EXPLORE_AGENT_PROMPT,
            _task(question, hint),
            max_rounds=self.max_rounds,
            guard=ToolGuard(max_total_chars=EXPLORE_TOOL_CHARS),
            label="explore",
            ledger=ledger,
            on_round=self._round_reporter(),
        )
        if self._absorb is not None:
            self._absorb(ledger)
        return _bounded(report)

    def _round_reporter(self) -> Callable[[int, str], object] | None:
        sink = self._on_progress
        if sink is None:
            return None
        return lambda number, detail: sink(self.name, number, detail)


def _task(question: str, hint: str) -> str:
    if not hint.strip():
        return question
    return f"{question}\n\n# What the conversational model already has\n\n{hint}"


def _bounded(report: str) -> str:
    if len(report) <= MAX_REPORT_CHARS:
        return report
    return report[: MAX_REPORT_CHARS - len(_OVERLONG)] + _OVERLONG
