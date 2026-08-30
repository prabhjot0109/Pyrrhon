"""Deep-model escalation: `think_deeper` over the shared subagent runner.

The fast model stays the low-latency voice and decides when to dispatch
(escalation is a tool, not a router). With `tools`, the deep model runs its
own read-only investigation loop in a FRESH context — isolated from the
conversation history — and returns a compact cited report. Without `tools` it
degrades to the M4 single-shot consultant, which is the same runner with an
empty belt: the first reply carries no tool calls, so it is the answer.

M16d moved the loop itself to `subagent.py`. What is left here is what was
always deep-specific: which prompt, how the question and the fast model's
notes are composed into one task, and the round budget a deep pass gets.
"""

from __future__ import annotations

from collections.abc import Callable

from pyrrhon.core.agent.prompts import DEEP_AGENT_PROMPT, DEEP_SYSTEM_PROMPT
from pyrrhon.core.agent.subagent import check_depth, run_subagent
from pyrrhon.core.tools.base import Tool

DEEP_MAX_ROUNDS = 12


class ThinkDeeperTool(Tool):
    name = "think_deeper"
    description = (
        "Dispatch the deep-reasoning subagent for multi-file architectural "
        "analysis. Pass the question plus everything you already know as "
        "`context` — the subagent verifies and extends it with its own "
        "read-only repo tools and returns a cited report."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The hard question to analyze",
            },
            "context": {
                "type": "string",
                "description": "Code excerpts, path:line locations, and findings gathered so far",
            },
        },
        "required": ["question", "context"],
    }

    def __init__(self, deep_llm, tools: list[Tool] | None = None,
                 max_rounds: int = DEEP_MAX_ROUNDS,
                 on_progress: Callable[[str, int, str], object] | None = None):
        check_depth(tools or [])
        self.deep_llm = deep_llm  # anything with async chat(messages, tools=None)
        self.tools = {tool.name: tool for tool in (tools or [])}
        self.max_rounds = max_rounds
        # Round-boundary progress, wired by whoever built the Agent. A deep
        # pass can run for tens of seconds behind one unchanging tool row.
        self._on_progress = on_progress

    async def run(self, question: str, context: str) -> str:
        return await run_subagent(
            self.deep_llm,
            list(self.tools.values()),
            DEEP_AGENT_PROMPT if self.tools else DEEP_SYSTEM_PROMPT,
            f"{question}\n\n# Context gathered by the fast model\n\n{context}",
            max_rounds=self.max_rounds,
            label="deep model",
            on_round=self._round_reporter(),
        )

    def _round_reporter(self) -> Callable[[int, str], object] | None:
        # Bound to a local before the closure: reading the attribute inside
        # the lambda would re-check a field that may be None by then, and the
        # narrowing above would not carry into the call.
        sink = self._on_progress
        if sink is None:
            return None
        return lambda number, detail: sink(self.name, number, detail)
