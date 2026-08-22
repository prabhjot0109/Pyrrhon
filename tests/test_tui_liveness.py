"""Phase 3: a turn you can watch, and a turn you can stop.

Defects 4 and 5. Session.abort_current_turn() has existed since M3 and was
reachable only from voice barge-in; the keyboard is simply its second caller.
"""

import asyncio
from pathlib import Path

from textual.widgets import Input

from pyrrhon.bootstrap import build_agent
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.messages import WorkingRow
from tests.helpers import FakeLLM


class SlowLLM:
    """Hangs until cancelled, which is the only way to observe a running turn."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def chat(self, messages, tools=None) -> LLMReply:
        self.started.set()
        await asyncio.sleep(60)
        raise AssertionError("the slow turn was never cancelled")


def make_app(llm, repo_root: Path) -> PyrrhonApp:
    agent = build_agent(repo_root, llm=llm, home=repo_root.parent)
    return PyrrhonApp(repo_root=repo_root, agent=agent)


async def start_turn(app: PyrrhonApp, pilot, text: str) -> None:
    app.query_one("#prompt", Input).value = text
    await pilot.press("enter")
    await pilot.pause()


async def test_esc_aborts_a_running_turn(sample_repo: Path):
    llm = SlowLLM()
    app = make_app(llm, sample_repo)
    aborts: list[int] = []
    real_abort = app.session.abort_current_turn

    def counting_abort() -> None:
        aborts.append(1)
        real_abort()

    app.session.abort_current_turn = counting_abort

    async with app.run_test(size=(120, 40)) as pilot:
        await start_turn(app, pilot, "take your time")
        await asyncio.wait_for(llm.started.wait(), timeout=5)
        prompt = app.query_one("#prompt", Input)
        assert prompt.disabled, "a turn is running"

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert aborts, "esc reached Session.abort_current_turn"
        assert not prompt.disabled and prompt.has_focus, "the prompt came back"


async def test_the_session_survives_an_abort(sample_repo: Path):
    """An aborted turn must leave the session able to take the next one."""
    llm = SlowLLM()
    app = make_app(llm, sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await start_turn(app, pilot, "take your time")
        await asyncio.wait_for(llm.started.wait(), timeout=5)
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        # A slash command is the cheapest following turn that touches no LLM.
        await start_turn(app, pilot, "/help")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "/model" in app.last_command_response


async def test_esc_with_no_turn_running_clears_the_prompt(sample_repo: Path):
    """The key is never dead."""
    app = make_app(FakeLLM([]), sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "half-typed question"
        await pilot.press("escape")
        await pilot.pause()
        assert prompt.value == ""
        assert not prompt.disabled


async def test_a_working_row_shows_while_the_turn_runs(sample_repo: Path):
    """Defect 5: the screen used to freeze between submit and first chunk."""
    llm = SlowLLM()
    app = make_app(llm, sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await start_turn(app, pilot, "take your time")
        await asyncio.wait_for(llm.started.wait(), timeout=5)
        assert len(list(app.query(WorkingRow))) == 1, "elapsed time before any tool"
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not list(app.query(WorkingRow)), "no spinner survives an abort"


async def test_no_working_row_survives_a_successful_turn(sample_repo: Path):
    app = make_app(FakeLLM([LLMReply(text="done.")]), sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await start_turn(app, pilot, "hello")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not list(app.query(WorkingRow))


async def test_no_working_row_survives_a_failed_turn(sample_repo: Path):
    # FakeLLM with no replies raises on the first chat() call.
    app = make_app(FakeLLM([]), sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await start_turn(app, pilot, "hello")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not list(app.query(WorkingRow))
        assert not app.query_one("#prompt", Input).disabled
