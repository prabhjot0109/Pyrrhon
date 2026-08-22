"""Typing `/` offers the commands, the way Claude Code does.

The redesign spec ruled an inline dropdown out and rejected
textual-autocomplete with it. The dependency stays rejected — this is
Textual's own OptionList over the registry the palette already reads — but a
command table nobody can see is a command table nobody uses.
"""

from pathlib import Path

from pyrrhon.bootstrap import build_agent
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.completion import CommandMenu, matches
from pyrrhon.tui.prompt import Prompt
from tests.helpers import FakeLLM


def make_app(repo: Path) -> PyrrhonApp:
    agent = build_agent(repo, llm=FakeLLM([]), home=repo.parent)
    return PyrrhonApp(repo_root=repo, agent=agent)


def test_a_bare_slash_offers_everything():
    names = [name for name, _ in matches("/")]
    assert "help" in names and "model" in names


def test_a_prefix_narrows_it():
    assert [n for n, _ in matches("/mod")] == ["mode", "model"]


def test_an_argument_ends_the_menu():
    """Once a space is typed the user is writing arguments, not a name."""
    assert matches("/model fast groq/x") == []


def test_plain_prose_is_not_a_command():
    assert matches("where is greet defined?") == []
    assert matches("") == []


async def test_typing_a_slash_shows_the_menu(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        menu = app.query_one("#completion", CommandMenu)
        assert not menu.display, "hidden until asked for"
        await pilot.press("slash")
        await pilot.pause()
        assert menu.display
        assert menu.option_count > 1


async def test_enter_completes_instead_of_submitting(sample_repo: Path):
    """Submitting /mod and being told it is unknown, with the answer on
    screen at the time, is the worst of both."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one("#prompt", Prompt)
        prompt.text = "/mod"
        await pilot.pause()
        menu = app.query_one("#completion", CommandMenu)
        assert menu.display
        await pilot.press("enter")
        await pilot.pause()
        assert prompt.text == "/mode "
        assert not menu.display
        assert app.history == [], "nothing was submitted"


async def test_down_moves_the_highlight_not_the_cursor(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one("#prompt", Prompt)
        prompt.text = "/mod"
        await pilot.pause()
        menu = app.query_one("#completion", CommandMenu)
        assert menu.selected == "mode"
        await pilot.press("down")
        await pilot.pause()
        assert menu.selected == "model"
        await pilot.press("enter")
        await pilot.pause()
        assert prompt.text == "/model "


async def test_escape_dismisses_the_menu_without_clearing_the_prompt(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one("#prompt", Prompt)
        prompt.text = "/mod"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not app.query_one("#completion", CommandMenu).display
        assert prompt.text == "/mod", "esc closed the menu, not the question"


async def test_a_complete_name_runs_rather_than_completing_again(sample_repo: Path):
    """Completing what is already complete would demand a second enter."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one("#prompt", Prompt)
        prompt.text = "/help"
        await pilot.pause()
        assert app.query_one("#completion", CommandMenu).display
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not app.query_one("#completion", CommandMenu).display
        assert "/model" in app.last_command_response
