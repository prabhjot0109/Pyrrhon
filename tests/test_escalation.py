from pathlib import Path

import pytest

from pyrrhon.core.agent.escalate import ThinkDeeperTool
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.prompts import (
    DEEP_AGENT_PROMPT,
    DEEP_SYSTEM_PROMPT,
    ESCALATION_NOTE,
)
from pyrrhon.core.events import SpeechChunk, ToolCallFinished
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.tools.base import Tool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


async def test_think_deeper_sends_prompt_question_and_context():
    deep = FakeLLM([LLMReply(text="The layering is clean: app -> utils.")])
    tool = ThinkDeeperTool(deep)
    out = await tool.run(
        question="How do the layers interact?",
        context="app.py:1 imports greet from utils/helpers.py:1",
    )
    assert out == "The layering is clean: app -> utils."
    messages = deep.calls[0]["messages"]
    assert messages[0] == {"role": "system", "content": DEEP_SYSTEM_PROMPT}
    assert "How do the layers interact?" in messages[1]["content"]
    assert "app.py:1 imports greet" in messages[1]["content"]
    assert deep.calls[0]["tools"] is None  # the deep model gets no tools — it reasons over the dossier


async def test_think_deeper_error_paths():
    class ExplodingLLM:
        async def chat(self, messages, tools=None):
            raise RuntimeError("provider down")

    assert (await ThinkDeeperTool(ExplodingLLM()).run(question="q", context="c")).startswith(
        "ERROR:"
    )
    empty = FakeLLM([LLMReply(text=None)])
    assert (await ThinkDeeperTool(empty).run(question="q", context="c")).startswith("ERROR:")


def test_agent_registers_tool_and_note_only_with_deep_llm():
    with_deep = Agent(
        llm=FakeLLM([]),
        tools=[],
        system_prompt="base prompt",
        repo_root=FIXTURE,
        deep_llm=FakeLLM([]),
    )
    assert "think_deeper" in with_deep.tools
    assert ESCALATION_NOTE in with_deep.system_prompt

    without = Agent(
        llm=FakeLLM([]), tools=[], system_prompt="base prompt", repo_root=FIXTURE
    )
    assert "think_deeper" not in without.tools
    assert without.system_prompt == "base prompt"


async def test_full_turn_escalates_through_think_deeper():
    deep = FakeLLM([LLMReply(text="Deep analysis: greet is the only seam between files.")])
    fast = FakeLLM(
        [
            LLMReply(
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="think_deeper",
                        arguments={
                            "question": "What breaks if greet changes?",
                            "context": "greet defined utils/helpers.py:1, called app.py:5",
                        },
                    ),
                )
            ),
            LLMReply(text="Changing greet only affects app.py:5."),
        ]
    )
    agent = Agent(
        llm=fast, tools=[], system_prompt="base", repo_root=FIXTURE, deep_llm=deep
    )
    events = [event async for event in agent.run_turn([], "what breaks if greet changes?")]
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert "Deep analysis" in finished[0].result_preview
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == "Changing greet only affects app.py:5."


class FakeRepoTool(Tool):
    name = "read_file"
    description = "fake"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}},
                  "required": ["path"]}

    def __init__(self):
        self.calls: list[dict] = []

    async def run(self, path: str) -> str:
        self.calls.append({"path": path})
        return f"    1| contents of {path}"


async def test_subagent_investigates_with_its_own_tools():
    repo_tool = FakeRepoTool()
    deep = FakeLLM([
        LLMReply(tool_calls=(
            ToolCall(id="d1", name="read_file", arguments={"path": "app.py"}),
        )),
        LLMReply(text="Report: app.py:1 is the entry point."),
    ])
    tool = ThinkDeeperTool(deep, tools=[repo_tool])
    out = await tool.run(question="entry point?", context="unknown")
    assert out == "Report: app.py:1 is the entry point."
    assert repo_tool.calls == [{"path": "app.py"}]
    # Tooled mode uses the agentic prompt and offers schemas.
    assert deep.calls[0]["messages"][0]["content"] == DEEP_AGENT_PROMPT
    assert deep.calls[0]["tools"] is not None
    # The tool result reached the deep model's context.
    tool_msgs = [m for m in deep.calls[1]["messages"] if m.get("role") == "tool"]
    assert "contents of app.py" in tool_msgs[0]["content"]


async def test_subagent_round_cap_forces_a_report():
    repo_tool = FakeRepoTool()
    deep = FakeLLM([
        LLMReply(tool_calls=(
            ToolCall(id=f"d{i}", name="read_file", arguments={"path": f"f{i}.py"}),
        ))
        for i in range(2)
    ] + [LLMReply(text="Best-effort report.")])
    tool = ThinkDeeperTool(deep, tools=[repo_tool], max_rounds=2)
    out = await tool.run(question="q", context="c")
    assert out == "Best-effort report."
    assert deep.calls[-1]["tools"] is None  # forced report call disables tools


async def test_subagent_duplicate_calls_are_not_rerun():
    repo_tool = FakeRepoTool()
    deep = FakeLLM([
        LLMReply(tool_calls=(
            ToolCall(id="d1", name="read_file", arguments={"path": "same.py"}),
        )),
        LLMReply(tool_calls=(
            ToolCall(id="d2", name="read_file", arguments={"path": "same.py"}),
        )),
        LLMReply(text="report"),
    ])
    tool = ThinkDeeperTool(deep, tools=[repo_tool])
    assert await tool.run(question="q", context="c") == "report"
    assert len(repo_tool.calls) == 1


def test_subagent_refuses_recursive_escalation():
    class Recursive(Tool):
        name = "think_deeper"
        description = "no"
        parameters = {"type": "object", "properties": {}}

        async def run(self) -> str:
            return ""

    with pytest.raises(ValueError):
        ThinkDeeperTool(FakeLLM([]), tools=[Recursive()])


async def test_subagent_tool_failure_returns_error_string():
    deep = FakeLLM([
        LLMReply(tool_calls=(
            ToolCall(id="d1", name="no_such_tool", arguments={}),
        )),
        LLMReply(text="report despite missing tool"),
    ])
    tool = ThinkDeeperTool(deep, tools=[FakeRepoTool()])
    assert await tool.run(question="q", context="c") == "report despite missing tool"
    tool_msgs = [m for m in deep.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs[0]["content"].startswith("ERROR: no tool named")
