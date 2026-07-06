"""Ranked repo map: most-referenced symbols per file, token-budgeted."""

from pyrrhon.core.tools.ast_index import RepoMapTool, SymbolIndex


def _make_repo(tmp_path):
    (tmp_path / "core.py").write_text(
        "def hot():\n    return 1\n\ndef cold():\n    return 2\n", encoding="utf-8"
    )
    for i in range(3):  # three files call hot(); nothing calls cold()
        (tmp_path / f"user{i}.py").write_text(
            "from core import hot\n\ndef go():\n    return hot()\n", encoding="utf-8"
        )
    return tmp_path


async def test_repo_map_ranks_hot_symbols_first(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    await index.ensure_fresh()
    repo_map = await index.build_repo_map()
    assert "core.py" in repo_map
    assert repo_map.index("hot") < repo_map.index("cold")   # within core.py
    assert repo_map.splitlines()[0].startswith("core.py")   # hottest file first


async def test_repo_map_respects_char_budget(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    await index.ensure_fresh()
    small = await index.build_repo_map(max_chars=80)
    assert len(small) <= 80 + 40  # budget plus one truncation notice line


async def test_repo_map_tool_runs_end_to_end(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    out = await RepoMapTool(index).run()
    assert "core.py" in out
    assert ":" in out  # symbols carry line numbers for citation
