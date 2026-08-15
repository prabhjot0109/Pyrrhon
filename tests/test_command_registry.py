import logging
from pathlib import Path

from pyrrhon.bootstrap import build_agent
from pyrrhon.commands.registry import CommandContext, command, dispatch
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class StubUI:
    """Minimal duck-typed ui for CommandContext — what registry tests need, nothing more."""

    def __init__(self):
        self.notes: list[str] = []
        self.last_citation = None

    def notify(self, text: str) -> None:
        self.notes.append(text)


def make_ctx() -> CommandContext:
    agent = build_agent(FIXTURE, llm=FakeLLM([]))
    return CommandContext(repo_root=FIXTURE, agent=agent, ui=StubUI())


@command("echo-test", "Echo the arguments back (test-only)")
def echo_test(args: str, ctx: CommandContext) -> str:
    return f"echo:{args}"


@command("echo-async-test", "Echo the arguments back from an async handler (test-only)")
async def echo_async_test(args: str, ctx: CommandContext) -> str:
    return f"async-echo:{args}"


async def test_plain_text_is_not_a_command():
    assert await dispatch("what does app.py do?", make_ctx()) is None


async def test_registered_command_receives_args():
    assert await dispatch("/echo-test hello world", make_ctx()) == "echo:hello world"


async def test_async_handlers_are_awaited():
    # M3 commands (/voice off) await pipeline teardown; sync handlers still work.
    assert await dispatch("/echo-async-test hi", make_ctx()) == "async-echo:hi"


async def test_unknown_command_points_at_help():
    response = await dispatch("/doesnotexist", make_ctx())
    assert response is not None
    assert "Unknown command" in response
    assert "/help" in response


async def test_help_lists_registered_commands():
    response = await dispatch("/help", make_ctx())
    assert "/help — List available commands" in response
    assert "/echo-test — Echo the arguments back (test-only)" in response


def test_re_registering_a_command_name_warns(caplog):
    """Tools warn on collision (repl.py:166); commands silently overwrote, so a
    plugin could replace /settings or /help with nothing in the log."""
    with caplog.at_level(logging.WARNING, logger="pyrrhon.commands"):

        @command("collide-probe", "first")
        def _first(args, ctx):
            return "first"

        @command("collide-probe", "second")
        def _second(args, ctx):
            return "second"

    assert any("collide-probe" in record.message for record in caplog.records)
