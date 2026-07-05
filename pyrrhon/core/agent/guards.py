"""Per-turn tool-usage guards, shared by the fast loop and the deep subagent.

These are the runaway-cost brakes: an LLM that re-issues the same call, or
dumps megabytes of grep output into context, burns tokens without progress.
The guard never blocks NEW work — only exact repeats and oversized output.
"""

from __future__ import annotations

import json

MAX_TOOL_RESULT_CHARS = 8_000    # per tool result
MAX_TURN_TOOL_CHARS = 40_000     # cumulative per turn / per subagent run

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
