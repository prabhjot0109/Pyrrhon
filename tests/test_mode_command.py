from pathlib import Path

import pyrrhon.commands.mode_cmd  # noqa: F401  (registers /mode)
from pyrrhon.bootstrap import build_agent
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.session import Session
from tests.helpers import FakeLLM


class StubUI:
    def __init__(self):
        self.lines: list[str] = []

    def notify(self, text: str) -> None:
        self.lines.append(text)


def make_ctx(tmp_path: Path) -> CommandContext:
    agent = Agent(
        llm=FakeLLM([]), tools=[], system_prompt="BASE", repo_root=tmp_path
    )
    session = Session(agent)
    return CommandContext(
        repo_root=tmp_path, agent=agent, ui=StubUI(), session=session
    )


async def test_mode_command_switches_to_design(tmp_path: Path):
    ctx = make_ctx(tmp_path)
    out = await dispatch("/mode design", ctx)
    assert ctx.session.mode == "design"
    assert "design" in out


async def test_mode_command_rejects_garbage_without_changing_state(tmp_path: Path):
    ctx = make_ctx(tmp_path)
    out = await dispatch("/mode prophecy", ctx)
    assert ctx.session.mode == "understand"
    assert out.startswith("ERROR:")


async def test_mode_command_without_args_reports_current_mode(tmp_path: Path):
    ctx = make_ctx(tmp_path)
    out = await dispatch("/mode", ctx)
    assert "understand" in out
    assert ctx.session.mode == "understand"


def test_build_agent_always_registers_write_spec(tmp_path: Path):
    agent = build_agent(tmp_path, llm=FakeLLM([]))
    assert "write_spec" in agent.tools
