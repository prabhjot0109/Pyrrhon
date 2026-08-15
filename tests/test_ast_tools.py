import shutil
from pathlib import Path

import pytest

from pyrrhon.core.tools.ast_index import FindSymbolTool, SymbolIndex

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture
def index(tmp_path: Path) -> SymbolIndex:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return SymbolIndex(dest)


async def test_find_symbol_formats_path_line_kind_name(index: SymbolIndex):
    out = await FindSymbolTool(index).run(name="greet")
    assert out == "utils/helpers.py:1: function greet"


async def test_no_matches_message(index: SymbolIndex):
    assert await FindSymbolTool(index).run(name="ghost") == "No matches."


async def test_schema(index: SymbolIndex):
    sym = FindSymbolTool(index).schema()
    assert sym["function"]["name"] == "find_symbol"
    assert sym["function"]["parameters"]["required"] == ["name"]
