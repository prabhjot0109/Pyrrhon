"""/voice on|off — toggle the Pipecat voice pipeline for this session."""

from __future__ import annotations

from pyrrhon.commands.registry import CommandContext, command


@command("voice", "Toggle the voice pipeline: /voice on|off")
async def voice_command(args: str, ctx: CommandContext) -> str:
    controller = getattr(ctx, "voice", None)
    if controller is None:
        return (
            "Voice is not available in this channel — run the TUI "
            "(plain `pyrrhon`) and try /voice on there."
        )
    choice = args.strip().lower()
    if choice == "on":
        return controller.start()
    if choice == "off":
        return await controller.stop()
    return "Usage: /voice on|off"
