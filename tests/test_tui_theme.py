"""The stylesheet must parse under any theme, not just ours.

Textual builds $variables from the *active* theme alone. Tokens that lived
only in PYRRHON_THEME.variables vanished the moment a user picked another
theme from the command palette, and the app died with

    reference to undefined variable '$ink'

before it drew anything. get_theme_variable_defaults() is the hook for
variables that have to outlive a theme switch.
"""

from pathlib import Path

import pytest

from pyrrhon.bootstrap import build_agent
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.theme import TOKENS
from tests.helpers import FakeLLM


def make_app(repo: Path) -> PyrrhonApp:
    agent = build_agent(repo, llm=FakeLLM([]), home=repo.parent)
    return PyrrhonApp(repo_root=repo, agent=agent)


def test_the_stylesheet_only_uses_tokens_that_always_resolve():
    """A static read of the rule the runtime failure came from."""
    css = (Path("pyrrhon/tui/pyrrhon.tcss")).read_text(encoding="utf-8")
    used = {
        word.strip(" ;:,)").lstrip("$")
        for line in css.splitlines()
        if not line.strip().startswith(("*", "/*"))
        for word in line.split()
        if word.startswith("$")
    }
    # Textual defines these under every theme; the rest must be ours.
    textual_owned = {
        "background", "surface", "panel", "primary", "secondary", "accent",
        "warning", "error", "success", "foreground", "boost", "text",
        "text-muted", "text-disabled",
    }
    unknown = used - textual_owned - set(TOKENS)
    assert not unknown, f"these would be undefined under another theme: {unknown}"


@pytest.mark.parametrize("theme", ["textual-dark", "textual-light", "nord"])
async def test_switching_theme_does_not_break_the_stylesheet(sample_repo: Path, theme):
    """The exact thing the command palette does to a running app."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app.theme = theme
        await pilot.pause()
        assert app.theme == theme
        # Every one of our tokens still resolves, which is what "the stylesheet
        # still parses" means in practice.
        for token in TOKENS:
            assert token in app.get_css_variables()


async def test_our_theme_is_still_the_one_selected_at_startup(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)):
        assert app.theme == "pyrrhon"
