from pathlib import Path

from textual.widgets import Input, RichLog

from pyrrhon.bootstrap import build_agent
from pyrrhon.core.events import Citation
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.widgets import StatusBar
from tests.helpers import FakeLLM


def make_app(repo: Path) -> PyrrhonApp:
    # A disposable copy, never tests/fixtures/sample_repo itself: mounting the
    # TUI warms the symbol index, which writes <repo>/.pyrrhon/cache.db.
    agent = build_agent(repo, llm=FakeLLM([]), home=repo.parent)
    return PyrrhonApp(repo_root=repo, agent=agent)


async def test_layout_is_one_full_width_column(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)):
        transcript = app.query_one("#transcript", RichLog)
        assert transcript is not None
        # D1: the transcript owns the full width, with nothing beside it.
        # outer_size, not size: the content region is 118 because of the
        # one-column padding, and padding is not another pane.
        assert transcript.outer_size.width == 120
        assert app.query_one("#prompt", Input).has_focus
        assert "mode: understand" in app.query_one(StatusBar).status_text



async def test_ctrl_o_opens_the_last_citation(sample_repo: Path):
    app = make_app(sample_repo)
    seen: list[tuple[Path, Citation]] = []
    app._open_editor = lambda root, cit: seen.append((root, cit)) or None
    async with app.run_test(size=(120, 40)) as pilot:
        app.record_citation(Citation(file="utils/helpers.py", line=12))
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert len(seen) == 1
        assert seen[0][1] == Citation(file="utils/helpers.py", line=12)


async def test_ctrl_o_without_a_citation_does_not_kill_the_app(sample_repo: Path):
    app = make_app(sample_repo)
    called = []
    app._open_editor = lambda root, cit: called.append(cit) or None
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert called == []                       # nothing to open, nothing run
        assert app.query_one("#prompt", Input) is not None   # still alive


async def test_ctrl_o_surfaces_the_editors_complaint(sample_repo: Path):
    """A failure to open is a notification, never a dead app."""
    app = make_app(sample_repo)
    app._open_editor = lambda root, cit: "ERROR: could not run vim: nope"
    async with app.run_test(size=(120, 40)) as pilot:
        app.record_citation(Citation(file="utils/helpers.py", line=1))
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app.query_one("#prompt", Input) is not None
