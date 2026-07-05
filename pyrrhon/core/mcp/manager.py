"""MCP client manager: attach any MCP server's tools to the agent.

Spec seams honored here:
- extension seam #2: servers declared in config, tools exposed automatically;
- error handling: a crashed/unreachable server contributes zero tools plus a
  one-line warning at startup, and a mid-session crash makes every tool from
  that server answer with an ERROR string so the agent knows it's gone;
- anyio rule: the mcp SDK's transports pin cancel scopes to the entering
  task, so start() and stop() MUST be awaited from the same asyncio task.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError

from pyrrhon.config.settings import MCPServerConfig
from pyrrhon.core.tools.base import Tool

logger = logging.getLogger("pyrrhon.mcp")

CONNECT_TIMEOUT_S = 10.0


def _safe(name: str) -> str:
    """Sanitize for the OpenAI tools API name charset [A-Za-z0-9_-]."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


@dataclass
class _ServerState:
    """Shared by every adapter of one server: one crash kills the whole roster."""

    dead: bool = False


class MCPToolAdapter(Tool):
    """One remote MCP tool exposed through the ordinary Tool ABC."""

    def __init__(self, server_name: str, session, remote_tool, state: _ServerState):
        self.server_name = server_name
        self._session = session
        self._remote_name = remote_tool.name
        self._state = state
        self.name = f"mcp_{_safe(server_name)}_{_safe(remote_tool.name)}"
        self.description = remote_tool.description or (
            f"Tool '{remote_tool.name}' provided by MCP server '{server_name}'."
        )
        self.parameters = remote_tool.inputSchema or {
            "type": "object",
            "properties": {},
        }

    async def run(self, **kwargs) -> str:
        if self._state.dead:
            return (
                f"ERROR: mcp server '{self.server_name}' crashed earlier this "
                "session; its tools are unavailable."
            )
        try:
            result = await self._session.call_tool(self._remote_name, kwargs)
        except McpError as exc:
            # Protocol-level error: the request failed but the server lives.
            return f"ERROR: mcp server '{self.server_name}' failed: {exc}"
        except Exception as exc:
            # Transport-level failure: assume the server is gone for good.
            self._state.dead = True
            logger.warning("mcp server '%s' crashed: %s", self.server_name, exc)
            return f"ERROR: mcp server '{self.server_name}' failed: {exc}"
        texts = [
            block.text
            for block in result.content
            if isinstance(block, types.TextContent)
        ]
        text = "\n".join(texts).strip() or "(no text content)"
        if result.isError:
            return f"ERROR: mcp server '{self.server_name}' failed: {text}"
        return text


class MCPManager:
    """Owns the client sessions for every configured MCP server."""

    def __init__(self, configs: dict[str, MCPServerConfig]):
        self._configs = configs
        self._stacks: dict[str, AsyncExitStack] = {}
        self.roster: dict[str, list[Tool]] = {}

    async def start(self) -> list[Tool]:
        """Connect all configured servers; failures are logged, never raised."""
        tools: list[Tool] = []
        for name, cfg in self._configs.items():
            try:
                adapters = await self._connect(name, cfg)
            except Exception as exc:
                logger.warning(
                    "mcp server '%s' unavailable, contributing 0 tools: %s",
                    name,
                    exc,
                )
                self.roster[name] = []
                continue
            self.roster[name] = adapters
            tools.extend(adapters)
        return tools

    async def _connect(self, name: str, cfg: MCPServerConfig) -> list[Tool]:
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_S):
                if cfg.command is not None:
                    params = StdioServerParameters(command=cfg.command, args=cfg.args)
                    read, write = await stack.enter_async_context(
                        stdio_client(params)
                    )
                else:
                    read, write, _get_session_id = await stack.enter_async_context(
                        streamablehttp_client(cfg.url)
                    )
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listing = await session.list_tools()
        except BaseException:
            await stack.aclose()
            raise
        self._stacks[name] = stack
        state = _ServerState()
        return [
            MCPToolAdapter(name, session, remote_tool, state)
            for remote_tool in listing.tools
        ]

    async def stop(self) -> None:
        """Close every session. Must run in the same task that ran start()."""
        for name, stack in reversed(list(self._stacks.items())):
            try:
                await stack.aclose()
            except Exception as exc:
                logger.warning("error closing mcp server '%s': %s", name, exc)
        self._stacks.clear()
        self.roster.clear()
