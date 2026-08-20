"""Voice vs. text delivery style: run_turn appends the right style block and a
live /voice toggle (agent.voice_active) refreshes it mid-session."""

import re
from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.prompts import SYSTEM_PROMPT, TEXT_STYLE, VOICE_STYLE
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.providers.llm import LLMReply
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_agent(replies, *, voice_active: bool) -> Agent:
    return Agent(
        llm=FakeLLM(replies),
        tools=[],
        system_prompt="BASE PROMPT",
        repo_root=FIXTURE,
        voice_active=voice_active,
    )


async def run(agent: Agent, history: list[dict], text: str) -> None:
    async for _ in agent.run_turn(history, text):
        pass


async def test_text_channel_appends_text_style_not_voice():
    agent = make_agent([LLMReply(text="ok")], voice_active=False)
    history: list[dict] = []
    await run(agent, history, "hi")
    system = history[0]["content"]
    assert system.startswith("BASE PROMPT")
    assert TEXT_STYLE in system
    assert VOICE_STYLE not in system


async def test_voice_channel_appends_voice_style_not_text():
    agent = make_agent([LLMReply(text="ok")], voice_active=True)
    history: list[dict] = []
    await run(agent, history, "hi")
    system = history[0]["content"]
    assert VOICE_STYLE in system
    assert TEXT_STYLE not in system


async def test_live_toggle_refreshes_style_mid_session():
    # Start in text, then flip to voice like /voice on does mid-conversation.
    agent = make_agent([LLMReply(text="one"), LLMReply(text="two")], voice_active=False)
    history: list[dict] = []
    await run(agent, history, "first")
    assert TEXT_STYLE in history[0]["content"]

    agent.voice_active = True
    await run(agent, history, "second")
    assert VOICE_STYLE in history[0]["content"]
    assert TEXT_STYLE not in history[0]["content"]
    # Still exactly one system message at the head — no duplicate base.
    assert history[0]["role"] == "system"
    assert history[1]["role"] != "system"


def test_base_prompt_carries_the_tool_decision_and_memory_policy():
    # The ReAct "when to open the repo" guidance and memory policy live in the
    # shared base so both channels get them.
    assert "Deciding when to open the repo" in SYSTEM_PROMPT
    assert "Do NOT call any tool" in SYSTEM_PROMPT
    assert "remember tool" in SYSTEM_PROMPT


def test_voice_style_forbids_spoken_coordinates():
    """The prompt is the mechanism; the gate is the safety net behind it."""
    assert "Never say a file path or line number out loud" in VOICE_STYLE
    assert "path:line" in TEXT_STYLE  # text mode keeps citing


async def test_no_path_line_survives_into_speech(tmp_path):
    """The end-to-end guarantee, independent of how many refs a turn carries."""
    (tmp_path / "app.py").write_text("\n".join(f"line {i}" for i in range(1, 21)))
    gate = GroundingGate(tmp_path)
    result = await gate.check("Look at app.py:3 and app.py:9 for the retry.")
    assert not re.search(r"\S+\.py:\d+", result.speech_text)
    assert len(result.citations) == 2
