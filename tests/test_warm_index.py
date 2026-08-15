"""warm_index_in_background: builds the symbol index off the first turn."""

import shutil
from pathlib import Path

from pyrrhon.bootstrap import warm_index_in_background, warm_llm_connection_in_background
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.tools.ast_index import FindSymbolTool, SymbolIndex
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


# -- connection warm-up (M10 A2) --------------------------------------------


class FakeModels:
    def __init__(self, record):
        self._record = record

    async def list(self):
        self._record.append("models.list")
        return []


class FakeOpenAIClient:
    def __init__(self):
        self.calls: list[str] = []
        self.models = FakeModels(self.calls)


class ClientLLM:
    """Shaped like OpenAICompatLLM: exposes the private _client the warm-up
    reaches for."""

    def __init__(self):
        self._client = FakeOpenAIClient()

    async def chat(self, messages, tools=None):  # pragma: no cover
        raise AssertionError("warm-up must never send a completion")


async def test_connection_warm_up_opens_the_pool_before_turn_one():
    llm = ClientLLM()
    agent = Agent(llm=llm, tools=[], system_prompt="p", repo_root=Path("."))
    task = warm_llm_connection_in_background(agent)
    assert task is not None
    await task
    assert llm._client.calls == ["models.list"]


async def test_connection_warm_up_is_a_noop_for_test_doubles():
    """FakeLLM has no _client, which is what keeps the whole suite offline."""
    agent = Agent(llm=FakeLLM([]), tools=[], system_prompt="p", repo_root=Path("."))
    assert warm_llm_connection_in_background(agent) is None


async def test_connection_warm_up_swallows_provider_errors():
    """A 401 still completed a handshake, so the pool is warm either way."""

    class Failing(ClientLLM):
        def __init__(self):
            super().__init__()

            async def boom():
                raise RuntimeError("401 unauthorized")

            self._client.models.list = boom

    agent = Agent(llm=Failing(), tools=[], system_prompt="p", repo_root=Path("."))
    await warm_llm_connection_in_background(agent)  # must not raise


async def test_connection_warm_up_follows_the_fallback_chain():
    """FallbackLLM wraps a list; warm the one that will actually be used."""
    inner = ClientLLM()

    class Chain:
        chain = [inner]

    agent = Agent(llm=Chain(), tools=[], system_prompt="p", repo_root=Path("."))
    await warm_llm_connection_in_background(agent)
    assert inner._client.calls == ["models.list"]
