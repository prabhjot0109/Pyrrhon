"""A minimal real MCP server the tests launch over stdio."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the input text back, prefixed."""
    return f"echo: {text}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
