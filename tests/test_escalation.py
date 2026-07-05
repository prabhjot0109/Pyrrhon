from pathlib import Path

from pyrrhon.core.agent.escalate import ThinkDeeperTool
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.prompts import DEEP_SYSTEM_PROMPT, ESCALATION_NOTE
from pyrrhon.core.events import SpeechChunk, ToolCallFinished
from pyrrhon.core.providers.llm import LLMReply, ToolCall
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
