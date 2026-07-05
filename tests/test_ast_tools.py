import shutil
from pathlib import Path

import pytest

from pyrrhon.core.tools.ast_index import FindReferencesTool, FindSymbolTool, SymbolIndex

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture
def index(tmp_path: Path) -> SymbolIndex:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return SymbolIndex(dest)


async def test_find_symbol_formats_path_line_kind_name(index: SymbolIndex):
    out = await FindSymbolTool(index).run(name="greet")
    assert out == "utils/helpers.py:1: function greet"


async def test_find_references_formats_path_line(index: SymbolIndex):
    out = await FindReferencesTool(index).run(name="greet")
    assert out == "app.py:5"


async def test_no_matches_message(index: SymbolIndex):
    assert await FindSymbolTool(index).run(name="ghost") == "No matches."
    assert await FindReferencesTool(index).run(name="ghost") == "No matches."


async def test_schemas(index: SymbolIndex):
    sym = FindSymbolTool(index).schema()
    ref = FindReferencesTool(index).schema()
    assert sym["function"]["name"] == "find_symbol"
    assert ref["function"]["name"] == "find_references"
    for schema in (sym, ref):
        assert schema["function"]["parameters"]["required"] == ["name"]
