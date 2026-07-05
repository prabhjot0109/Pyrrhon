"""Decorator-registered slash commands — extension seam #3 from the spec.

Channel-agnostic: the text REPL and the TUI both call dispatch(); M3/M5/M6
add commands (/voice, /mcp, /mode) here, and M7's plugin loader registers
into the same table. Handlers return response strings (errors prefixed
'ERROR:'), never raise, and never print — the channel renders the string.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrrhon.core.agent.loop import Agent
    from pyrrhon.core.mcp import MCPManager
    from pyrrhon.core.session import Session
    from pyrrhon.plugins import LoadedPlugin


@dataclass
class CommandContext:
    repo_root: Path
    agent: "Agent"
    ui: object  # duck-typed: needs notify(text: str); may carry last_citation
    session: "Session | None" = None
    # Duck-typed VoiceController (start() -> str, async stop() -> str);
    # None in channels without a persistent voice pipeline (the plain REPL).
    voice: object | None = None
    # The channel-owned MCPManager; None when no channel wired one (tests).
    mcp: "MCPManager | None" = None
    # Plugins the channel loaded at startup (M7); /plugins renders them.
    plugins: "list[LoadedPlugin]" = field(default_factory=list)


@dataclass(frozen=True)
class Command:
    name: str
    help_text: str
    handler: Callable[[str, CommandContext], str]


_COMMANDS: dict[str, Command] = {}


def command(name: str, help_text: str):
    """Register a slash command. Handler: (args, ctx) -> response string."""

    def register(fn: Callable[[str, CommandContext], str]):
        _COMMANDS[name] = Command(name=name, help_text=help_text, handler=fn)
        return fn

    return register


async def dispatch(line: str, ctx: CommandContext) -> str | None:
    """Route `line` to a command. None means 'not a command — send to the agent'.

    Handlers may be sync or async — async ones exist for commands that must
    await real teardown before answering (M3's /voice off waits for the
    audio pipeline to release the mic). Both channels call dispatch from
    async code, so awaiting here costs nothing.
    """
    line = line.strip()
    if not line.startswith("/"):
        return None
    name, _, args = line[1:].partition(" ")
    cmd = _COMMANDS.get(name)
    if cmd is None:
        return f"Unknown command '/{name}' — try /help."
    result = cmd.handler(args.strip(), ctx)
    if inspect.isawaitable(result):
        result = await result
    return result


@command("help", "List available commands")
def help_command(args: str, ctx: CommandContext) -> str:
    return "\n".join(
        f"/{cmd.name} — {cmd.help_text}"
        for cmd in sorted(_COMMANDS.values(), key=lambda c: c.name)
    )
