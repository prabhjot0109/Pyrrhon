from pathlib import Path

from textual.widgets import Input, RichLog

from pyrrhon.core.events import Citation
from pyrrhon.repl import build_agent
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.widgets import CodeViewer, StatusBar
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_app() -> PyrrhonApp:
    agent = build_agent(FIXTURE, llm=FakeLLM([]))
    return PyrrhonApp(repo_root=FIXTURE, agent=agent)


async def test_layout_panes_status_and_focused_input():
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#transcript", RichLog) is not None
        assert app.query_one(CodeViewer) is not None
        assert app.query_one("#prompt", Input).has_focus
        assert "mode: understand" in app.query_one(StatusBar).status_text


async def test_show_citation_jumps_code_viewer():
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        app.show_citation(Citation(file="utils/helpers.py", line=1))
        await pilot.pause()
        viewer = app.query_one(CodeViewer)
        assert viewer.current_file == "utils/helpers.py"
        assert viewer.current_line == 1
        assert app.last_citation == Citation(file="utils/helpers.py", line=1)


async def test_show_citation_unreadable_file_is_error_not_crash():
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        app.show_citation(Citation(file="does/not/exist.py", line=3))
        await pilot.pause()
        viewer = app.query_one(CodeViewer)
        assert viewer.current_file is None  # nothing loaded, app still alive
