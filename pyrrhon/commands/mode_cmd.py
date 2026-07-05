"""/mode — switch between understand and design mode."""

from __future__ import annotations

from pyrrhon.commands.registry import CommandContext, command


@command("mode", "Switch mode: /mode understand|design")
def mode_cmd(args: str, ctx: CommandContext) -> str:
    if ctx.session is None:
        return "ERROR: no active session."
    mode = args.strip()
    if not mode:
        return f"Current mode: {ctx.session.mode}. Usage: /mode understand|design"
    try:
        ctx.session.set_mode(mode)
    except ValueError as exc:
        return f"ERROR: {exc}"
    return f"Mode set to {mode}."
