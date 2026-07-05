from pathlib import Path

from textual.widgets import Input

from pyrrhon.core.events import Citation
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.repl import build_agent
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.widgets import CodeViewer
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_app(replies, repo_root: Path = FIXTURE) -> tuple[PyrrhonApp, FakeLLM]:
    fake = FakeLLM(replies)
    agent = build_agent(repo_root, llm=fake)
    return PyrrhonApp(repo_root=repo_root, agent=agent), fake


async def submit(app: PyrrhonApp, pilot, text: str) -> None:
    app.query_one("#prompt", Input).value = text
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_turn_streams_speech_citation_and_code_jump():
    replies = [
        LLMReply(
            tool_calls=(
                ToolCall(id="c1", name="read_file", arguments={"path": "utils/helpers.py"}),
            )
        ),
        LLMReply(text="greet is defined at utils/helpers.py:1."),
    ]
    app, fake = make_app(replies)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "where is greet defined?")
        assert app.history[-1] == {
            "role": "assistant",
            "content": "greet is defined at utils/helpers.py:1.",
        }
        assert app.last_citation == Citation(file="utils/helpers.py", line=1)
        assert app.query_one(CodeViewer).current_line == 1
        prompt = app.query_one("#prompt", Input)
        assert not prompt.disabled and prompt.has_focus  # ready for the next turn


async def test_slash_command_short_circuits_the_agent():
    app, fake = make_app([])  # any LLM call would raise inside FakeLLM
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/help")
        assert fake.calls == []  # the LLM was never touched
        assert app.history == []  # commands are not conversation
        assert "/model" in app.last_command_response


async def test_unknown_command_is_reported():
    app, fake = make_app([])
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/definitely-not-a-command")
        assert "Unknown command" in app.last_command_response


async def test_init_via_tui(tmp_path: Path):
    app, fake = make_app([], repo_root=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/init")
        assert (tmp_path / ".pyrrhon" / "soul.md").is_file()
        assert "soul file created" in app.last_command_response


async def test_turn_failure_reports_error_and_recovers():
    app, fake = make_app([])  # first chat() call raises inside FakeLLM
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "hello")
        prompt = app.query_one("#prompt", Input)
        assert not prompt.disabled and prompt.has_focus  # session survived the failed turn
