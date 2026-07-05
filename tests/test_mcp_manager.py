import sys
from pathlib import Path

from pyrrhon.config.settings import MCPServerConfig
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.mcp.manager import MCPToolAdapter, _ServerState

ECHO_SERVER = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


def echo_config() -> MCPServerConfig:
    return MCPServerConfig(command=sys.executable, args=[str(ECHO_SERVER)])


async def test_start_exposes_prefixed_tools_and_call_roundtrips():
    manager = MCPManager({"echo": echo_config()})
    tools = await manager.start()
    try:
        assert [t.name for t in tools] == ["mcp_echo_echo"]
        assert len(manager.roster["echo"]) == 1
        schema = tools[0].schema()
        assert schema["function"]["name"] == "mcp_echo_echo"
        assert "text" in schema["function"]["parameters"]["properties"]
        assert await tools[0].run(text="hi") == "echo: hi"
    finally:
        await manager.stop()


async def test_unreachable_server_contributes_zero_tools(caplog):
    bad = MCPServerConfig(command="pyrrhon-no-such-binary-xyz", args=[])
    manager = MCPManager({"broken": bad, "echo": echo_config()})
    with caplog.at_level("WARNING", logger="pyrrhon.mcp"):
        tools = await manager.start()
    try:
        assert [t.name for t in tools] == ["mcp_echo_echo"]  # broken skipped
        assert manager.roster["broken"] == []
        assert "broken" in caplog.text
    finally:
        await manager.stop()


class _ExplodingSession:
    async def call_tool(self, name, arguments):
        raise RuntimeError("pipe closed")


class _RemoteTool:
    name = "echo"
    description = "d"
    inputSchema = {"type": "object", "properties": {}}


async def test_crashed_server_marks_all_its_tools_unavailable():
    state = _ServerState()
    adapter = MCPToolAdapter("flaky", _ExplodingSession(), _RemoteTool(), state)
    first = await adapter.run()
    assert first.startswith("ERROR: mcp server 'flaky' failed:")
    assert state.dead is True
    second = await adapter.run()
    assert "crashed earlier this session" in second
