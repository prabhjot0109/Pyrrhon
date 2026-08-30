"""Shared fixtures, plus a fence that keeps tests/fixtures/ pristine.

Several suites point a real Agent or the TUI at tests/fixtures/sample_repo.
Anything that touches the symbol index writes <repo>/.pyrrhon/cache.db there —
and because cache.db is gitignored (`*.db` in .gitignore) that artifact never
shows up in `git status`. It silently survives into the next run, where
test_symbol_index's copytree drags it into a fresh tmp repo and
`test_init_does_no_io` fails for reasons that have nothing to do with the code
under test. Tests that index a repo must use a disposable copy.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from pyrrhon.core.providers.llm import OpenAICompatLLM

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_REPO = FIXTURES / "sample_repo"
POLYGLOT_REPO = FIXTURES / "polyglot_repo"


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """A disposable copy of the sample repo — safe to index and write into.

    Use this instead of pointing at tests/fixtures/sample_repo whenever the
    thing under test can build the symbol index (an Agent, the TUI, the REPL).
    Its parent is tmp_path, so `sample_repo.parent` doubles as an isolated
    fake $HOME for build_agent(home=...).
    """
    dest = tmp_path / "repo"
    shutil.copytree(SAMPLE_REPO, dest)
    return dest


@pytest.fixture
def polyglot_repo(tmp_path: Path) -> Path:
    """A disposable copy of the TS/JS/Go fixture repo — safe to index.

    Same rule as `sample_repo`: the checked-in tree must never be indexed in
    place, because that writes <repo>/.pyrrhon/cache.db into it and the fence
    below fails the offending test.
    """
    dest = tmp_path / "polyglot"
    shutil.copytree(POLYGLOT_REPO, dest)
    return dest


@pytest.fixture(autouse=True)
def _fixtures_stay_pristine():
    """Fail the offending test if it wrote into the checked-in fixture tree.

    Cleans up first so one stray write can't cascade into unrelated failures
    later in the session — the failure should land on the test that caused it.
    """
    yield
    strays = sorted(p for p in FIXTURES.rglob(".pyrrhon"))
    for stray in strays:
        shutil.rmtree(stray, ignore_errors=True)
    assert not strays, (
        f"this test wrote into the checked-in fixture tree: "
        f"{[str(p.relative_to(FIXTURES)) for p in strays]}. "
        "Use the `sample_repo` fixture (a disposable copy) instead."
    )


PROVIDER_BASE_URL = "http://provider.test/v1"


@pytest.fixture
async def mock_llm():
    """Builds an OpenAICompatLLM whose transport is `handler`, and closes it.

    Closing matters more than it looks: an httpx.AsyncClient left open past
    its event loop raises "Event loop is closed" from a finaliser, which
    surfaces as an unraisable warning in whatever test happens to run next.
    tests/conftest.py exists because of exactly that class of cross-test
    leakage.

    max_retries=0 throughout: the SDK's own backoff sleeps for real on a 429,
    and no suite using this is testing the SDK's retry.
    """
    clients: list[AsyncOpenAI] = []

    def build(handler, **kwargs) -> OpenAICompatLLM:
        llm = OpenAICompatLLM(
            model="m", api_key="k", base_url=PROVIDER_BASE_URL, max_retries=0, **kwargs
        )
        llm._client = AsyncOpenAI(
            api_key="k",
            base_url=PROVIDER_BASE_URL,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        clients.append(llm._client)
        return llm

    yield build
    for client in clients:
        await client.close()
