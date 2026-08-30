import io
from pathlib import Path

from rich.console import Console

from pyrrhon.bootstrap import build_agent
from pyrrhon.commands.debug_cmd import format_history
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.session import Session
from pyrrhon.repl import ConsoleRenderer, ConsoleUI, _turn
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


async def test_repl_turn_consumes_a_session():
    fake = FakeLLM([LLMReply(text="greet lives at utils/helpers.py:1.")])
    session = Session(build_agent(FIXTURE, llm=fake))
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False)

    ui = ConsoleUI(console)
    renderer = ConsoleRenderer(console, ui, session.agent.repo_root)
    await _turn(session, "where is greet defined?", renderer)

    out = buffer.getvalue()
    assert "greet lives at" in out
    assert session.history[-1]["role"] == "assistant"


def test_format_history_shows_roles_and_previews():
    history = [
        {"role": "system", "content": "You are Pyrrhon."},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "    1| def greet..."},
        {"role": "assistant", "content": "line one\nline two"},
    ]
    out = format_history(history)
    assert "[0] system: You are Pyrrhon." in out
    assert "[2] assistant: <tool calls: read_file>" in out
    assert "line one\\nline two" in out  # newlines escaped, one row per message


def test_format_history_empty():
    assert format_history([]) == "(history empty)"
