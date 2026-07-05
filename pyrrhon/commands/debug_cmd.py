"""/debug-history — dev command: dump the session history one row per message.

Exists for M3's manual barge-in smoke test: after interrupting Pyrrhon
mid-sentence you run /debug-history and confirm the last assistant message
is exactly the played prefix plus the " …[interrupted]" marker.
"""

from __future__ import annotations

from pyrrhon.commands.registry import CommandContext, command

_PREVIEW = 120


def format_history(history: list[dict]) -> str:
    """Pure formatter (unit-tested): one line per message, newlines escaped."""
    if not history:
        return "(history empty)"
    lines: list[str] = []
    for index, message in enumerate(history):
        role = message.get("role", "?")
        content = message.get("content")
        if content is None and message.get("tool_calls"):
            names = ", ".join(
                tc["function"]["name"] for tc in message["tool_calls"]
            )
            content = f"<tool calls: {names}>"
        text = str(content).replace("\n", "\\n")
        if len(text) > _PREVIEW:
            text = text[: _PREVIEW - 3] + "..."
        lines.append(f"[{index}] {role}: {text}")
    return "\n".join(lines)


@command("debug-history", "Dev: dump the session history (roles + content)")
def debug_history_command(args: str, ctx: CommandContext) -> str:
    if ctx.session is None:
        return "ERROR: this channel has no session to inspect."
    return format_history(ctx.session.history)
