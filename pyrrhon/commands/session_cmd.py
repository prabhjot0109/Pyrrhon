"""Commands that give the user control over the session: /clear /compact /cost
/export /sessions.

M19's second half. Compaction was fully automatic and completely invisible,
there was no way to start a fresh thread without restarting the process, and
the token counts every reply reported were used to calibrate an estimate and
then dropped on the floor. All three are the same shape of problem: the
session was a thing that happened TO the user rather than a thing they held.

Two of these are deliberately narrow.

`/clear` drops history and nothing else. The mode survives, because someone
who switched to design mode and then cleared is starting a new design rather
than leaving one, and the transcript survives because /clear is about what the
MODEL carries, never about destroying what the user has already been told.

`/compact` is the only place `FIT_FULL` runs on demand. The turn's own
pre-flight is capped at rung 2 for a reason M16b paid to learn — rung 3 strips
the evidence of the turn that is running, and rung 4 puts a round trip in
front of the first token — so this hands the user the rungs the loop is not
allowed to take on its own.
"""

from __future__ import annotations

from pathlib import Path

from pyrrhon.commands.registry import CommandContext, command
from pyrrhon.core.context import FIT_FULL, fit_to_budget, history_tokens
from pyrrhon.core.transcript import list_sessions

NO_SESSION = "ERROR: this channel has no session to act on."


@command("clear", "Start a fresh thread — drops history, keeps the mode")
def clear_command(args: str, ctx: CommandContext) -> str:
    if ctx.session is None:
        return NO_SESSION
    dropped = ctx.session.clear()
    if not dropped:
        return "Nothing to clear — this thread is already empty."
    return (
        f"Cleared {dropped} message(s). The saved transcript is untouched; "
        "I have simply forgotten them."
    )


@command("compact", "Summarize the thread now, freeing context")
async def compact_command(args: str, ctx: CommandContext) -> str:
    """The whole ladder, on demand, measured either side so the answer is a
    number rather than a reassurance.

    `force=True` via FIT_FULL is not passed: a user asking to compact a
    history that already fits should be told it already fits, not charged for
    a summarization round trip that frees nothing.
    """
    if ctx.session is None:
        return NO_SESSION
    agent = ctx.agent
    scale = agent.token_scale
    before = history_tokens(ctx.session.history, scale)
    trace = ctx.session.last_turn_trace
    budget = agent.request_budget(trace.schema_chars if trace else 0)
    if not budget:
        return "ERROR: no context budget is configured or learned yet."
    if before <= budget:
        return (
            f"Nothing to do — the thread is about {before} tokens against a "
            f"budget of {budget}."
        )
    rung = await fit_to_budget(
        ctx.session.history,
        agent.llm,
        budget,
        mode=FIT_FULL,
        keep_last=agent.context_keep_last,
        scale=scale,
    )
    after = history_tokens(ctx.session.history, scale)
    return (
        f"Compacted: about {before} tokens down to {after}, against a budget "
        f"of {budget}. Reached the '{rung or 'nothing'}' rung."
    )


def _ceiling_line(agent) -> str:
    """What the provider will accept, when anything has established it.

    None is a real answer and stays visible as one. A percentage against a
    denominator nobody measured is a claim, which is the same reason
    `known_context_budget` exists separately from `context_budget_tokens`.
    """
    known = agent.known_context_budget
    if known is None:
        return "Context ceiling: not yet established by this provider."
    return f"Context ceiling: about {known} tokens per request."


@command("cost", "What this session has spent, in tokens")
def cost_command(args: str, ctx: CommandContext) -> str:
    """The counts have ridden every response since M15b and nothing showed them.

    Requests are reported beside tokens because the two run out separately: a
    per-minute REQUEST ceiling can block a session that has plenty of token
    allowance left, which reads as a mystery when only tokens are on screen.
    And the ceilings a provider does advertise are not the whole truth — this
    account has a daily budget that appears in no header at all — so the line
    says what is known and does not imply the rest.
    """
    spend = ctx.agent.spend
    if not spend.requests:
        return "Nothing spent yet this session."
    lines = [
        f"{spend.requests} request(s), {spend.total} tokens "
        f"({spend.prompt} in, {spend.completion} out).",
        _ceiling_line(ctx.agent),
    ]
    if not spend.total:
        lines.append(
            "This provider reports no token usage, so only the request count "
            "is real."
        )
    else:
        lines.append(
            "Per-request ceilings are the only ones a provider advertises. A "
            "daily or monthly budget will not appear here until it is spent."
        )
    return "\n".join(lines)


@command("export", "Write this session's transcript as markdown: /export [path]")
def export_command(args: str, ctx: CommandContext) -> str:
    """The artifact a user wants after a two-hour walkthrough.

    Defaults inside `<repo>/.pyrrhon/exports/` rather than the repo root: the
    fence says nothing writes outside `.pyrrhon/`, and a command that drops
    files where the user runs `git status` is a surprise even when it is
    permitted. An explicit path argument overrides it, because a user naming a
    destination has made the decision themselves.
    """
    if ctx.session is None:
        return NO_SESSION
    transcript = ctx.session.transcript
    if transcript is None:
        return "ERROR: this session is not being saved, so there is nothing to export."
    # Flush first: the turn that just finished is still pending, and exporting
    # a walkthrough without its last answer is the one thing this must not do.
    ctx.session.close()
    markdown = transcript.to_markdown(ctx.repo_root)
    if args.strip():
        destination = Path(args.strip()).expanduser()
    else:
        destination = ctx.repo_root / ".pyrrhon" / "exports" / f"{transcript.session_id}.md"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        return f"ERROR: could not write {destination}: {exc}"
    return f"Exported {len(transcript.entries())} turn(s) to {destination}"


@command("covered", "What this session has established so far")
def covered_command(args: str, ctx: CommandContext) -> str:
    """The thread, made visible.

    The voice style has told the model to offer the next hop "like a podcast"
    since M3, and nothing anywhere recorded what had already been covered — so
    a two-hour session had no spine except the user's memory of it. Reading it
    off the transcript rather than keeping a second list means it cannot drift
    from what was actually said, and it survives a resume for free.
    """
    if ctx.session is None:
        return NO_SESSION
    if ctx.session.transcript is None:
        return "ERROR: this session is not being saved, so nothing is recorded."
    # The turn that just finished is still pending, and "what have we covered"
    # asked right after an answer must include that answer's question.
    ctx.session.close()
    return ctx.session.transcript.covered_ground() or "Nothing covered yet."


@command("sessions", "List saved sessions for this repo")
def sessions_command(args: str, ctx: CommandContext) -> str:
    """The list `--resume` chooses from, so it prints the id in the form
    `--resume` takes: the leading date is enough, and the random suffix that
    exists only to stop two sessions colliding never has to be retyped."""
    saved = list_sessions(ctx.repo_root)
    if not saved:
        return "No saved sessions for this repo yet."
    lines = [f"{len(saved)} saved session(s) — resume with `pyrrhon --resume <id>`:"]
    for info in saved[:15]:
        lines.append(
            f"  {info.session_id}  {info.turns} turn(s)  {info.preview}"
        )
    if len(saved) > 15:
        lines.append(f"  …and {len(saved) - 15} older.")
    return "\n".join(lines)
