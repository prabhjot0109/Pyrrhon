"""The rate-limit notice reaches both screen channels.

`ProviderRetrying` is the one core event no *turn* emits. The wait happens
inside `llm.chat`, while `run_turn` is blocked awaiting it, so the event cannot
be yielded from the turn's own stream. It rides the attachment shape
`FallbackLLM.on_switch` already established instead — the channel sets a
callback and renders the event through the shared dispatch table.

That wiring lives in each channel's setup rather than in a renderer hook, which
is exactly the kind of line that gets deleted in a refactor with every test
still green. These are the tests that would fail.
"""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from pyrrhon.bootstrap import build_agent
from pyrrhon.core.events import ProviderRetrying
from pyrrhon.core.providers.llm import FallbackLLM, OpenAICompatLLM
from pyrrhon.repl import ConsoleRenderer, ConsoleUI
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.messages import NoticeRow
from tests.helpers import FakeLLM


def test_the_repl_renders_the_notice():
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    ui = ConsoleUI(console)
    renderer = ConsoleRenderer(console, ui, Path("."))

    renderer.render(ProviderRetrying(delay_seconds=20.0, reason="429"))

    out = console.file.getvalue()
    assert "Rate limited" in out and "20s" in out


async def test_the_tui_mounts_the_notice(sample_repo: Path):
    agent = build_agent(sample_repo, llm=FakeLLM([]), home=sample_repo.parent)
    app = PyrrhonApp(repo_root=sample_repo, agent=agent)
    async with app.run_test(size=(120, 40)) as pilot:
        app._renderer.render(ProviderRetrying(delay_seconds=13.0, reason="429"))
        await pilot.pause()
        rows = list(app.query(NoticeRow))
        assert rows, "the wait must be visible; a silent 13s reads as a hang"
        assert "13s" in str(rows[-1].body().content)


async def test_the_tui_wires_the_callback_onto_the_driver(sample_repo: Path):
    """The wiring itself, not the rendering. A driver whose on_retry is never
    set waits silently, which is the failure this whole event exists to
    prevent."""
    agent = build_agent(sample_repo, llm=FakeLLM([]), home=sample_repo.parent)
    app = PyrrhonApp(repo_root=sample_repo, agent=agent)
    async with app.run_test(size=(120, 40)) as pilot:
        assert callable(agent.llm.on_retry)
        agent.llm.on_retry(7.0, "429 from the provider")
        await pilot.pause()
        rows = list(app.query(NoticeRow))
        assert rows and "7s" in str(rows[-1].body().content)


def test_a_chain_fans_the_callback_out_to_every_link():
    """A channel wires this in one line whether the user configured a fallback
    chain or a single provider, so the setter has to reach every link — the
    one that is active now, and the one a fallover switches to later."""
    chain = [
        OpenAICompatLLM(model="a", api_key="k", base_url="http://one.test/v1"),
        OpenAICompatLLM(model="b", api_key="k", base_url="http://two.test/v1"),
    ]
    fallback = FallbackLLM(chain)
    seen: list[float] = []

    fallback.on_retry = lambda delay, reason: seen.append(delay)

    assert all(link.on_retry is not None for link in chain)
    chain[1].on_retry(3.0, "429")
    assert seen == [3.0]
