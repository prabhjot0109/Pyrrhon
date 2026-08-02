from pathlib import Path

from textual.widgets import Input, RichLog

from pyrrhon.core.events import Citation
from pyrrhon.repl import build_agent
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.widgets import CodeViewer, StatusBar
from tests.helpers import FakeLLM

def make_app(repo: Path) -> PyrrhonApp:
    # A disposable copy, never tests/fixtures/sample_repo itself: mounting the
    # TUI warms the symbol index, which writes <repo>/.pyrrhon/cache.db.
    agent = build_agent(repo, llm=FakeLLM([]), home=repo.parent)
    return PyrrhonApp(repo_root=repo, agent=agent)


async def test_layout_panes_status_and_focused_input(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#transcript", RichLog) is not None
        assert app.query_one(CodeViewer) is not None
        assert app.query_one("#prompt", Input).has_focus
        assert "mode: understand" in app.query_one(StatusBar).status_text


async def test_show_citation_jumps_code_viewer(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app.show_citation(Citation(file="utils/helpers.py", line=1))
        await pilot.pause()
        viewer = app.query_one(CodeViewer)
        assert viewer.current_file == "utils/helpers.py"
        assert viewer.current_line == 1
        assert app.last_citation == Citation(file="utils/helpers.py", line=1)


async def test_show_citation_unreadable_file_is_error_not_crash(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app.show_citation(Citation(file="does/not/exist.py", line=3))
        await pilot.pause()
        viewer = app.query_one(CodeViewer)
        assert viewer.current_file is None  # nothing loaded, app still alive


async def test_show_citation_escaping_path_is_rejected(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app.show_citation(Citation(file="../../outside.py", line=1))
        await pilot.pause()
        viewer = app.query_one(CodeViewer)
        assert viewer.current_file is None  # escape rejected, app still alive
        assert "escapes the repo" in str(viewer.render())
