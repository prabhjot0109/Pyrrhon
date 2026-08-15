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


def hard_compact_tool_results(history: list[dict], keep_recent: int = 1) -> int:
    """Recovery compaction for a mid-turn context-window overflow: elide EVERY
    bulky tool result except the most recent `keep_recent`, including the
    current turn's (which compact_tool_results deliberately leaves intact).

    Unlike compact_tool_results this ignores the last-user boundary — when the
    provider rejects the prompt as too long, room must be reclaimed from the
    fresh results too. The most recent result is kept so the model still has the
    evidence it was about to reason over. Mutates in place; returns the count
    elided. Grounding is unaffected (the gate verifies against the repo).
    """
    tool_idxs = [
        i
        for i, m in enumerate(history)
        if m.get("role") == "tool"
        and isinstance(m.get("content"), str)
        and len(m["content"]) > TOOL_STUB_MIN
    ]
    to_elide = tool_idxs[:-keep_recent] if keep_recent else tool_idxs
    for i in to_elide:
        content = history[i]["content"]
        dropped = len(content) - TOOL_STUB_KEEP
        history[i]["content"] = (
            content[:TOOL_STUB_KEEP]
            + f"\n…[{dropped} chars elided to fit the context window — "
            "re-run the tool if needed]"
        )
    return len(to_elide)


SUMMARY_PROMPT = (
    "Summarize the conversation below so it can be continued later. Keep: the "
    "user's goals, decisions made, key findings about the codebase, and EVERY "
    "path:line citation EXACTLY as written (e.g. utils/helpers.py:12). Drop "
    "tool output noise and dead ends. 300 words maximum. Output only the summary."
)

SUMMARY_HEADER = "Summary of earlier conversation (older turns were compacted):\n"


def _render(messages: list[dict]) -> str:
    lines: list[str] = []
    for message in messages:
        role = message.get("role", "?")
        content = message.get("content")
        if isinstance(content, str) and content:
            lines.append(f"{role}: {content}")
        for call in message.get("tool_calls") or ():
            name = call.get("function", {}).get("name", "?")
            args = call.get("function", {}).get("arguments", "")
            lines.append(f"{role} called tool {name}({args})")
    return "\n".join(lines)


async def maybe_summarize(
    history: list[dict], llm, budget_tokens: int, keep_last: int = 8
) -> bool:
    """Compress old turns into one system summary when over budget.

    history[0] (the base system prompt) and the last `keep_last` messages
    survive verbatim; system messages in the compacted span (mode prompts)
    are kept in place, not summarized away. The split never strands a tool
    result without its parent assistant tool_calls message. Any LLM failure
    leaves history untouched — compaction is an optimization, never a
    correctness requirement.
    """
    if history_tokens(history) <= budget_tokens:
        return False
    split = max(len(history) - keep_last, 1)
    while split > 1 and history[split].get("role") == "tool":
        split -= 1
    if split <= 1:
        return False
    middle = history[1:split]
    try:
        reply = await llm.chat(
            [
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": _render(middle)},
            ],
            tools=None,
        )
    except Exception:
        return False
    if not reply.text:
        return False
    kept_system = [m for m in middle if m.get("role") == "system"]
    history[1:split] = [
        *kept_system,
        {"role": "system", "content": SUMMARY_HEADER + reply.text},
    ]
    return True
