from pathlib import Path

from pyrrhon.commands.registry import CommandContext, command, dispatch
from pyrrhon.repl import build_agent
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


def test_plain_text_is_not_a_command():
    assert dispatch("what does app.py do?", make_ctx()) is None


def test_registered_command_receives_args():
    assert dispatch("/echo-test hello world", make_ctx()) == "echo:hello world"


def test_unknown_command_points_at_help():
    response = dispatch("/doesnotexist", make_ctx())
    assert response is not None
    assert "Unknown command" in response
    assert "/help" in response


def test_help_lists_registered_commands():
    response = dispatch("/help", make_ctx())
    assert "/help — List available commands" in response
    assert "/echo-test — Echo the arguments back (test-only)" in response
