from pathlib import Path

from pyrrhon.core.tools.repo import GlobTool, GrepTool, ReadFileTool

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


async def test_read_file_returns_numbered_lines():
    out = await ReadFileTool(FIXTURE).run(path="utils/helpers.py")
    assert "    1| def greet(name: str) -> str:" in out


async def test_read_file_rejects_escape_and_missing():
    tool = ReadFileTool(FIXTURE)
    assert (await tool.run(path="../outside.txt")).startswith("ERROR:")
    assert (await tool.run(path="nope.py")).startswith("ERROR:")


async def test_grep_reports_posix_path_line_and_text():
    out = await GrepTool(FIXTURE).run(pattern=r"def greet")
    assert "utils/helpers.py:1: def greet(name: str) -> str:" in out


async def test_grep_bad_regex_is_an_error_string():
    assert (await GrepTool(FIXTURE).run(pattern="(unclosed")).startswith("ERROR:")


async def test_glob_lists_matching_files():
    out = await GlobTool(FIXTURE).run(pattern="**/*.py")
    assert "app.py" in out and "utils/helpers.py" in out
    assert "README.md" not in out


async def test_glob_absolute_pattern_is_an_error_string():
    assert (await GlobTool(FIXTURE).run(pattern="/etc/*")).startswith("ERROR:")


async def test_tool_schema_shape():
    schema = ReadFileTool(FIXTURE).schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "read_file"
    assert "path" in schema["function"]["parameters"]["properties"]
