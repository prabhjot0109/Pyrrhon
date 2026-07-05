"""/mcp — show attached MCP servers and the tools they contribute."""

from __future__ import annotations

from pyrrhon.commands.registry import CommandContext, command
from pyrrhon.core.tools.base import Tool


def render_mcp_roster(roster: dict[str, list[Tool]]) -> str:
    if not roster:
        return (
            "No MCP servers configured. Add an [mcp_servers.<name>] table to "
            ".pyrrhon.toml (command+args for stdio, or url for streamable HTTP)."
        )
    lines: list[str] = []
    for name, tools in roster.items():
        if tools:
            lines.append(f"{name}: {len(tools)} tool(s)")
            lines.extend(f"  - {tool.name}" for tool in tools)
        else:
            lines.append(f"{name}: unavailable (0 tools)")
    return "\n".join(lines)


@command("mcp", "List attached MCP servers and their tools: /mcp list")
def mcp_command(args: str, ctx: CommandContext) -> str:
    if args.strip() not in ("", "list"):
        return "Usage: /mcp list"
    roster = ctx.mcp.roster if ctx.mcp is not None else {}
    return render_mcp_roster(roster)
