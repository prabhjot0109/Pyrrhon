import os
import shutil
from pathlib import Path

import pytest

from pyrrhon.core.tools.ast_index import SymbolIndex

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A disposable copy of the fixture repo — the index writes .pyrrhon/cache.db."""
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return dest


def _bump_mtime(path: Path) -> None:
    """Force a visibly newer mtime (filesystem mtime granularity can be coarse)."""
    stamp = path.stat().st_mtime + 10
    os.utime(path, (stamp, stamp))


async def test_init_does_no_io(repo: Path):
    SymbolIndex(repo)
    assert not (repo / ".pyrrhon").exists()


async def test_finds_function_definitions(repo: Path):
    index = SymbolIndex(repo)
    await index.ensure_fresh()
    assert await index.find_symbol("greet") == [("utils/helpers.py", 1, "function")]
    assert await index.find_symbol("main") == [("app.py", 4, "function")]
    assert (repo / ".pyrrhon" / "cache.db").is_file()


async def test_finds_call_references(repo: Path):
    index = SymbolIndex(repo)
    await index.ensure_fresh()
    assert await index.find_references("greet") == [("app.py", 5)]
    assert await index.find_references("main") == [("app.py", 9)]


async def test_unknown_symbol_returns_empty(repo: Path):
    index = SymbolIndex(repo)
    await index.ensure_fresh()
    assert await index.find_symbol("does_not_exist") == []
    assert await index.find_references("does_not_exist") == []


async def test_reindexes_only_changed_files(repo: Path):
    index = SymbolIndex(repo)
    await index.ensure_fresh()
    helpers = repo / "utils" / "helpers.py"
    helpers.write_text(
        'def greet(name: str) -> str:\n'
        '    return f"Hello, {name}!"\n'
        "\n"
        "\n"
        "def shout(name: str) -> str:\n"
        '    return f"HELLO, {name}!"\n',
        encoding="utf-8",
    )
    _bump_mtime(helpers)
    await index.ensure_fresh()
    assert await index.find_symbol("shout") == [("utils/helpers.py", 5, "function")]
    # Class definitions get kind "class":
    (repo / "models.py").write_text("class User:\n    pass\n", encoding="utf-8")
    await index.ensure_fresh()
    assert await index.find_symbol("User") == [("models.py", 1, "class")]


async def test_deleted_files_drop_out_of_the_index(repo: Path):
    index = SymbolIndex(repo)
    await index.ensure_fresh()
    (repo / "app.py").unlink()
    await index.ensure_fresh()
    assert await index.find_symbol("main") == []
    assert await index.find_references("greet") == []
