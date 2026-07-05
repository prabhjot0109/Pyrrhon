import sys
from pathlib import Path

from pyrrhon.commands.mcp_cmd import render_mcp_roster
from pyrrhon.config.settings import MCPServerConfig
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"
ECHO_SERVER = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


async def test_build_agent_joins_mcp_tools_to_builtins():
    manager = MCPManager(
        {"echo": MCPServerConfig(command=sys.executable, args=[str(ECHO_SERVER)])}
    )
    tools = await manager.start()
    try:
        fake = FakeLLM([LLMReply(text="ok")])
        agent = build_agent(FIXTURE, llm=fake, extra_tools=tools)
        assert {"read_file", "grep", "glob"} <= set(agent.tools)
        assert "mcp_echo_echo" in agent.tools
    finally:
        await manager.stop()


def test_render_mcp_roster_shows_servers_counts_and_tools():
    class FakeTool:
        def __init__(self, name):
            self.name = name

    roster = {
        "echo": [FakeTool("mcp_echo_echo")],
        "broken": [],
    }
    out = render_mcp_roster(roster)
    assert "echo: 1 tool(s)" in out
    assert "  - mcp_echo_echo" in out
    assert "broken: unavailable (0 tools)" in out


def test_render_mcp_roster_empty():
    out = render_mcp_roster({})
    assert "No MCP servers configured" in out
    assert "[mcp_servers." in out  # tells the user how to add one
