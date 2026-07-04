from pathlib import Path

from pyrrhon.commands.init_cmd import init_pyrrhon_dir
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def test_init_creates_soul_template_once(tmp_path: Path):
    path, created = init_pyrrhon_dir(tmp_path)
    assert created is True
    assert path == tmp_path / ".pyrrhon" / "soul.md"
    assert "## Who I am" in path.read_text(encoding="utf-8")

    path.write_text("my edits", encoding="utf-8")
    _, created_again = init_pyrrhon_dir(tmp_path)
    assert created_again is False
    assert path.read_text(encoding="utf-8") == "my edits"  # never clobbered


async def test_build_agent_wires_tools_and_answers(tmp_path: Path):
    fake = FakeLLM([LLMReply(text="app.py:1 imports greet.")])
    agent = build_agent(FIXTURE, llm=fake)
    assert set(agent.tools) == {"read_file", "grep", "glob"}

    events = [event async for event in agent.run_turn([], "hi")]
    texts = [e.text for e in events if hasattr(e, "text")]
    assert "app.py:1 imports greet." in texts


def test_build_agent_wires_grounding_gate():
    fake = FakeLLM([])
    agent = build_agent(FIXTURE, llm=fake)
    assert isinstance(agent.grounding_gate, GroundingGate)
    assert agent.grounding_gate.root == FIXTURE
    assert agent.allow_retry is True  # REPL is a screen channel
