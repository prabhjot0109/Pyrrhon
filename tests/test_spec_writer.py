from pathlib import Path

from pyrrhon.core.tools.spec_writer import SPEC_FILENAMES, WriteSpecTool


async def test_writes_allowed_filename_creating_dir(tmp_path: Path):
    tool = WriteSpecTool(tmp_path)
    out = await tool.run(filename="PRD.md", content="# PRD\n\nWhy: because reasons.\n")
    assert out.startswith("Wrote docs/design/PRD.md")
    written = tmp_path / "docs" / "design" / "PRD.md"
    assert written.read_text(encoding="utf-8") == "# PRD\n\nWhy: because reasons.\n"


async def test_overwrite_is_allowed_and_reported(tmp_path: Path):
    tool = WriteSpecTool(tmp_path)
    await tool.run(filename="risks.md", content="v1")
    out = await tool.run(filename="risks.md", content="v2 — conversation moved on")
    assert out.startswith("Overwrote docs/design/risks.md")
    written = tmp_path / "docs" / "design" / "risks.md"
    assert written.read_text(encoding="utf-8") == "v2 — conversation moved on"


async def test_rejects_any_filename_outside_the_allowlist(tmp_path: Path):
    tool = WriteSpecTool(tmp_path)
    for bad in ("notes.md", "../evil.md", "PRD.txt", "prd.md", "docs/PRD.md", ""):
        out = await tool.run(filename=bad, content="x")
        assert out.startswith("ERROR:"), f"accepted forbidden filename {bad!r}"
    assert not (tmp_path / "docs").exists()  # nothing was written, no dir created


def test_schema_enumerates_exactly_the_six_artifacts(tmp_path: Path):
    schema = WriteSpecTool(tmp_path).schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "write_spec"
    props = schema["function"]["parameters"]["properties"]
    assert props["filename"]["enum"] == list(SPEC_FILENAMES)
    assert SPEC_FILENAMES == (
        "PRD.md", "HLD.md", "LLD.md", "api.md", "database.md", "risks.md"
    )
    assert schema["function"]["parameters"]["required"] == ["filename", "content"]
