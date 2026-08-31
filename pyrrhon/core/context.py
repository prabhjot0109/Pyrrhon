"""Context engineering: token budgeting, tool-result eviction, summarization.

Provider-agnostic by design: tokens are estimated as len(text)//4 — good
enough for budgeting, and it avoids a tokenizer dependency that would have
to match whichever provider the user configured.

The estimate is CALIBRATED rather than replaced once a provider reports
usage. prompt_tokens is exact, but it describes the request that was sent,
not the history that exists now — substituting it would under-count
everything appended since, and would go stale high the moment compaction
shrinks the history. The ratio it implies (token_scale) does neither: it is
this model tokenizer's real chars-per-token, and it stays right as the
history grows and shrinks.

Eviction never breaks grounding: the GroundingGate verifies citations
against the repo itself (pyrrhon/core/grounding/), not against history, so
eliding a stale grep dump costs nothing but a possible tool re-run.
"""

from __future__ import annotations

import json
from typing import Literal

from pyrrhon.core.grounding.citations import strip_line_numbers

# The whole estimate, in one number. Provider-agnostic on purpose — see the
# module docstring on why this is calibrated rather than replaced.
CHARS_PER_TOKEN = 4

TOOL_STUB_KEEP = 300  # chars of a tool result kept when elided
TOOL_STUB_MIN = 600   # results at or below this size are never elided

# len//4 is never off by 10x for text or code. A ratio outside this range means
# the provider counted something other than what we measured, and trusting it
# would either disable compaction entirely or run it on every turn.
MIN_TOKEN_SCALE = 0.5
MAX_TOKEN_SCALE = 2.0


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def token_scale(usage, estimated: int) -> float | None:
    """This model's real chars-per-token, relative to the len//4 assumption.

    `usage` is the TokenUsage from a reply and `estimated` is what
    history_tokens() made of the very messages that reply answered. None when
    there is nothing trustworthy to learn — no usage block, a zero count, or an
    empty history — so the caller keeps whatever scale it already had.
    """
    if usage is None or not usage.prompt or estimated <= 0:
        return None
    return min(MAX_TOKEN_SCALE, max(MIN_TOKEN_SCALE, usage.prompt / estimated))


def history_tokens(history: list[dict], scale: float = 1.0) -> int:
    """Estimated tokens in `history`, optionally corrected by token_scale()."""
    total = 0
    for message in history:
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        for call in message.get("tool_calls") or ():
            total += estimate_tokens(json.dumps(call, default=str))
    return round(total * scale)


def compact_tool_results(history: list[dict], keep_recent: int | None = None) -> int:
    """Elide bulky tool results. Mutates in place; returns the count elided.

    Two strengths, one function, because they differ by exactly one thing —
    which results are off limits.

    `keep_recent=None` (the default, and the cheap rung) elides only results
    from turns BEFORE the latest user message. The current turn's stay intact:
    the model may still be reading them.

    `keep_recent=N` ignores that boundary and spares only the most recent N
    results wherever they are. Harder, and reached only when the boundary
    version was not enough — at which point room has to come from the fresh
    results too, and the most recent is kept so the model still has the
    evidence it was about to reason over.

    Idempotent either way: an elided stub is shorter than TOOL_STUB_MIN, so it
    never matches the size check again. Grounding is unaffected — the gate
    verifies citations against the repo, and the EvidenceLedger is separate
    from history entirely.
    """
    bulky = [
        i
        for i, message in enumerate(history)
        if message.get("role") == "tool"
        and isinstance(message.get("content"), str)
        and len(message["content"]) > TOOL_STUB_MIN
    ]
    if keep_recent is None:
        last_user = max(
            (i for i, m in enumerate(history) if m.get("role") == "user"), default=None
        )
        if last_user is None:
            return 0
        targets = [i for i in bulky if i < last_user]
        note = "elided after use"
    else:
        targets = bulky[:-keep_recent] if keep_recent else bulky
        note = "elided to fit the context window"
    for i in targets:
        content = history[i]["content"]
        dropped = len(content) - TOOL_STUB_KEEP
        history[i]["content"] = (
            content[:TOOL_STUB_KEEP]
            + f"\n…[{dropped} chars {note} — re-run the tool if needed]"
        )
    return len(targets)


# What the ladder did, for the trace. The last rung reached, not every rung
# that ran, because the last one is the one that says how much trouble the
# turn was in.
LADDER_COMPACT = "compact"
LADDER_HARD = "hard"
LADDER_SUMMARIZE = "summarize"

# How far up the ladder a caller may go. One mode rather than a pair of
# booleans, because only three of the four combinations are meaningful and the
# fourth ("ignore the estimate, but stop at the cheap rung") is a contradiction
# — the estimate is the only reason to stop early.
FitMode = Literal["cheap", "full", "forced"]

# Rung 2 only, gated on the estimate. What a turn runs in front of its own
# request. Rung 2's last-user boundary is what makes it safe there: it never
# touches the current turn's tool results, which the model is still reading.
FIT_CHEAP: FitMode = "cheap"

# Every rung, gated on the estimate. Dead time — Session's background pass.
# Rung 3 spends the current turn's evidence and rung 4 costs a round trip, so
# both belong somewhere the user is not waiting.
FIT_FULL: FitMode = "full"

# Every rung, and ignore the estimate. The safety net, after the provider has
# already rejected the request: the estimate is known wrong, so nothing it says
# should gate a rung.
FIT_FORCED: FitMode = "forced"


# Amended 2026-08-31 (M16e). This used to demand EVERY path:line be kept
# EXACTLY as written, which was right while history was the model's source of
# code facts. Under upstream verification it is the one instruction that
# manufactures a stale coordinate: a summary outlives the tool result that
# justified it, the file may have changed since, and a stale in-range line
# passes every check the gate makes. The file name is the part that stays true,
# and re-anchoring it costs one read.
SUMMARY_PROMPT = (
    "Summarize the conversation below so it can be continued later. Keep: the "
    "user's goals, decisions made, and key findings about the codebase — name "
    "the FILE a finding came from (e.g. utils/helpers.py) and never a line "
    "number, because the file may have changed since it was read. Drop tool "
    "output noise and dead ends. 300 words maximum. Output only the summary."
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
    history: list[dict],
    llm,
    budget_tokens: int,
    keep_last: int = 8,
    scale: float = 1.0,
) -> bool:
    """Compress old turns into one system summary when over budget.

    history[0] (the base system prompt) and the last `keep_last` messages
    survive verbatim; system messages in the compacted span (mode prompts)
    are kept in place, not summarized away. The split never strands a tool
    result without its parent assistant tool_calls message. Any LLM failure
    leaves history untouched — compaction is an optimization, never a
    correctness requirement.
    """
    if history_tokens(history, scale) <= budget_tokens:
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
    # Stripped as well as asked for. The prompt above tells the model why; this
    # is what makes it an invariant rather than a request, and it is cheap
    # enough to run on every summary that arrives.
    summary = strip_line_numbers(reply.text)
    history[1:split] = [
        *kept_system,
        {"role": "system", "content": SUMMARY_HEADER + summary},
    ]
    return True


async def fit_to_budget(
    history: list[dict],
    llm,
    budget_tokens: int,
    *,
    mode: FitMode = FIT_FULL,
    keep_last: int = 8,
    scale: float = 1.0,
) -> str:
    """Bring `history` under `budget_tokens`, cheapest rung first.

    Run BEFORE each request rather than after a failure, which is the whole
    point: three of the four rungs are pure, local and free, so the one that
    costs a round trip is genuinely last instead of being the mechanism.

    1. Under budget already — nothing to do.
    2. Elide tool results from earlier turns. Pure, local, idempotent, free.
    3. Elide harder, keeping only the most recent result.
    4. Summarize old turns. The only rung that costs a round trip.

    Returns the last rung that ran, or "" when none did.

    **`mode` decides how far up.** A turn's own pre-flight passes `FIT_CHEAP`
    and stops after rung 2, because the two rungs above it are not free in the
    way rung 2 is, and a live run proved the difference rather than argued it.

    Rung 4 costs a round trip. M10 moved that off the critical path on purpose:
    it used to sit in front of the first token of every over-budget turn, the
    worst possible place for it in a product whose metric is
    time-to-first-word.

    Rung 3 costs the current turn's evidence, which is worse and less obvious.
    Verified on a real account with a learned ceiling of 8000 tokens: the
    request budget for history came to ~2012, less than the system prompt plus
    one tool result, so the history was over budget from round one and never
    came back under. Rung 3 therefore ran on EVERY round, stripped every tool
    result but the most recent, and still left the request over budget — the
    model lost what it had just read and went back to re-read it. Rung 2's
    last-user boundary is exactly the protection that prevents this, so the
    pre-flight keeps it and stops there.

    Both costs are real; neither belongs in front of a waiting user. `FIT_FULL`
    runs them in dead time and `FIT_FORCED` runs them after the provider has
    already refused, which is where they pay. Note that `maybe_summarize`
    leaves history untouched on any failure, so a forced run cannot end worse
    than it started.
    """
    forced = mode == "forced"

    def over() -> bool:
        return forced or (
            bool(budget_tokens) and history_tokens(history, scale) > budget_tokens
        )

    if not over():
        return ""
    done = ""
    if compact_tool_results(history):
        done = LADDER_COMPACT
    if mode == "cheap" or not over():
        return done
    if compact_tool_results(history, keep_recent=1):
        done = LADDER_HARD
    if not over():
        return done
    if await maybe_summarize(
        history,
        llm,
        # Forced: the provider has spoken, so summarize regardless of what the
        # estimate makes of the history now.
        budget_tokens=1 if forced else budget_tokens,
        keep_last=keep_last,
        scale=scale,
    ):
        done = LADDER_SUMMARIZE
    return done
