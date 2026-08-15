from pathlib import Path

import pytest

from pyrrhon.core.agent.design_prompts import DESIGN_PROMPT
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.session import MODE_PREFIX, UNDERSTAND_MARKER, Session
from tests.helpers import FakeLLM


def make_session() -> Session:
    agent = Agent(
        llm=FakeLLM([]),
        tools=[],
        system_prompt="BASE TEACHING PROMPT",
        repo_root=Path("."),
    )
    return Session(agent)


def test_set_mode_rejects_unknown_mode():
    session = make_session()
    with pytest.raises(ValueError, match="prophecy"):
        session.set_mode("prophecy")
    assert session.mode == "understand"


def test_switch_to_design_layers_prompt_on_top_of_base():
    session = make_session()
    session.set_mode("design")
    assert session.mode == "design"
    assert session.agent.mode == "design"
    assert session.history[0] == {"role": "system", "content": "BASE TEACHING PROMPT"}
    # MODE_PREFIX tags the message so a later switch can find and rewrite it.
    assert session.history[1] == {
        "role": "system",
        "content": MODE_PREFIX + DESIGN_PROMPT,
    }


def test_switch_back_to_understand_injects_marker_not_a_second_base():
    session = make_session()
    session.set_mode("design")
    session.set_mode("understand")
    assert session.mode == "understand"
    assert session.agent.mode == "understand"
    assert session.history[-1] == {
        "role": "system",
        "content": MODE_PREFIX + UNDERSTAND_MARKER,
    }
    base_count = sum(
        1 for m in session.history if m["content"] == "BASE TEACHING PROMPT"
    )
    assert base_count == 1  # the turn-one base prompt stays, exactly once


def test_setting_the_current_mode_is_a_noop():
    session = make_session()
    session.set_mode("understand")
    assert session.history == []
    assert session.mode == "understand"


def _mode_messages(history):
    return [
        m for m in history
        if m.get("role") == "system" and str(m.get("content", "")).startswith(MODE_PREFIX)
    ]


def test_toggling_modes_never_adds_a_second_mode_message():
    session = make_session()
    for _ in range(10):
        session.set_mode("design")
        session.set_mode("understand")
    assert len(_mode_messages(session.history)) == 1


def test_the_surviving_mode_message_reflects_the_current_mode():
    session = make_session()
    session.set_mode("design")
    session.set_mode("understand")
    session.set_mode("design")
    assert session.mode == "design"
    assert DESIGN_PROMPT in _mode_messages(session.history)[0]["content"]
