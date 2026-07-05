"""Context engineering: token budgeting, tool-result eviction, summarization.

Provider-agnostic by design: tokens are estimated as len(text)//4 — good
enough for budgeting, and it avoids a tokenizer dependency that would have
to match whichever provider the user configured.

Eviction never breaks grounding: the GroundingGate verifies citations
against the repo itself (pyrrhon/core/grounding/), not against history, so
eliding a stale grep dump costs nothing but a possible tool re-run.
"""

from __future__ import annotations

import json

TOOL_STUB_KEEP = 300  # chars of a tool result kept when elided
TOOL_STUB_MIN = 600   # results at or below this size are never elided


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def history_tokens(history: list[dict]) -> int:
    total = 0
    for message in history:
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        for call in message.get("tool_calls") or ():
            total += estimate_tokens(json.dumps(call, default=str))
    return total


def compact_tool_results(history: list[dict]) -> int:
    """Elide bulky tool results from turns BEFORE the latest user message.

    The current turn's results stay intact — the model may still be reading
    them. Idempotent: an elided stub is shorter than TOOL_STUB_MIN, so it
    never matches the size check again. Mutates history in place; returns
    the number of messages elided.
    """
    last_user = None
    for i, message in enumerate(history):
        if message.get("role") == "user":
            last_user = i
    if last_user is None:
        return 0
    elided = 0
    for message in history[:last_user]:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str) or len(content) <= TOOL_STUB_MIN:
            continue
        dropped = len(content) - TOOL_STUB_KEEP
        message["content"] = (
            content[:TOOL_STUB_KEEP]
            + f"\n…[{dropped} chars elided after use — re-run the tool if needed]"
        )
        elided += 1
    return elided
