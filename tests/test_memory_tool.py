from datetime import date
from pathlib import Path

from pyrrhon.core.tools.memory import RememberTool


async def test_remember_creates_file_with_header_and_dated_bullet(tmp_path: Path):
    tool = RememberTool(tmp_path)
    out = await tool.run(fact="The user prefers first-principles answers.")
    assert out.startswith("Remembered:")
    memory = tmp_path / ".pyrrhon" / "memory.md"
    content = memory.read_text(encoding="utf-8")
    assert content.startswith("# Memory\n")
    today = date.today().isoformat()
    assert f"- [{today}] The user prefers first-principles answers.\n" in content


async def test_remember_appends_in_order_without_clobbering(tmp_path: Path):
    tool = RememberTool(tmp_path)
    await tool.run(fact="first fact")
    await tool.run(fact="second fact")
    lines = (
        (tmp_path / ".pyrrhon" / "memory.md")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert lines[0] == "# Memory"
    assert lines[1].endswith("first fact")
    assert lines[2].endswith("second fact")


async def test_remember_flattens_newlines_to_keep_one_bullet_per_fact(tmp_path: Path):
    await RememberTool(tmp_path).run(fact="line one\nline two")
    content = (tmp_path / ".pyrrhon" / "memory.md").read_text(encoding="utf-8")
    assert "line one line two" in content
    assert "line one\nline two" not in content


async def test_remember_preserves_user_edited_memory(tmp_path: Path):
    directory = tmp_path / ".pyrrhon"
    directory.mkdir()
    (directory / "memory.md").write_text(
        "# Memory\n- [2026-01-01] old fact\n", encoding="utf-8"
    )
    await RememberTool(tmp_path).run(fact="new fact")
    content = (directory / "memory.md").read_text(encoding="utf-8")
    assert "old fact" in content
    assert "new fact" in content


async def test_remember_schema_shape():
    schema = RememberTool(Path(".")).schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "remember"
    assert schema["function"]["parameters"]["required"] == ["fact"]
    assert "fact" in schema["function"]["parameters"]["properties"]
