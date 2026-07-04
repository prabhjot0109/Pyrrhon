"""Decorator-registered slash commands — extension seam #3 from the spec.

Channel-agnostic: the text REPL and the TUI both call dispatch(); M3/M5/M6
add commands (/voice, /mcp, /mode) here, and M7's plugin loader registers
into the same table. Handlers return response strings (errors prefixed
'ERROR:'), never raise, and never print — the channel renders the string.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrrhon.core.agent.loop import Agent


@dataclass
class CommandContext:
    repo_root: Path
    agent: "Agent"
    ui: object  # duck-typed: needs notify(text: str); may carry last_citation


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


def dispatch(line: str, ctx: CommandContext) -> str | None:
    """Route `line` to a command. None means 'not a command — send to the agent'."""
    line = line.strip()
    if not line.startswith("/"):
        return None
    name, _, args = line[1:].partition(" ")
    cmd = _COMMANDS.get(name)
    if cmd is None:
        return f"Unknown command '/{name}' — try /help."
    return cmd.handler(args.strip(), ctx)


@command("help", "List available commands")
def help_command(args: str, ctx: CommandContext) -> str:
    return "\n".join(
        f"/{cmd.name} — {cmd.help_text}"
        for cmd in sorted(_COMMANDS.values(), key=lambda c: c.name)
    )
