"""warm_index_in_background: builds the symbol index off the first turn."""

import shutil
from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.tools.ast_index import FindSymbolTool, SymbolIndex
from pyrrhon.repl import warm_index_in_background
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


async def test_warm_index_builds_in_background(tmp_path: Path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    index = SymbolIndex(repo)
    agent = Agent(
        llm=FakeLLM([]),
        tools=[FindSymbolTool(index)],
        system_prompt="p",
        repo_root=repo,
    )
    task = warm_index_in_background(agent)
    assert task is not None
    await task
    assert index._last_fresh_at is not None            # the walk ran
    assert (repo / ".pyrrhon" / "cache.db").is_file()   # index persisted


async def test_warm_index_is_a_noop_without_the_index_tool():
    agent = Agent(
        llm=FakeLLM([]), tools=[], system_prompt="p", repo_root=Path(".")
    )
    assert warm_index_in_background(agent) is None
