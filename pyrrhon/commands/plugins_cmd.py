"""/plugins — list loaded plugins and what each contributed."""

from __future__ import annotations

from pyrrhon.commands.registry import CommandContext, command


@command("plugins", "List loaded plugins and their contributions")
def plugins_command(args: str, ctx: CommandContext) -> str:
    if not ctx.plugins:
        return "No plugins loaded."
    lines: list[str] = []
    for plugin in ctx.plugins:
        contributes = plugin.manifest.contributes
        scope = "repo" if plugin.dir.is_relative_to(ctx.repo_root) else "global"
        parts: list[str] = []
        if contributes.prompts:
            parts.append(f"prompts: {', '.join(contributes.prompts)}")
        if plugin.tools:
            parts.append(f"tools: {', '.join(tool.name for tool in plugin.tools)}")
        elif contributes.tools:
            parts.append("tools: declared but not loaded (repo code untrusted)")
        if contributes.commands:
            parts.append(f"commands: {contributes.commands}")
        if contributes.mcp_servers:
            parts.append(f"mcp servers: {', '.join(contributes.mcp_servers)}")
        if contributes.providers:
            parts.append(f"providers: {', '.join(contributes.providers)}")
        detail = "; ".join(parts) or "no contributions"
        lines.append(
            f"{plugin.manifest.name}@{plugin.manifest.version} [{scope}] — {detail}"
        )
    return "\n".join(lines)
