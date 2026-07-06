"""Duplicate-call, size-cap, and budget-exhaustion guards on the tool loop."""

from pathlib import Path

from pyrrhon.core.agent.guards import (
    DUPLICATE_NOTE,
    MAX_TOOL_RESULT_CHARS,
    ToolGuard,
)
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import SpeechChunk, ToolCallFinished
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.tools.base import Tool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class EchoTool(Tool):
    name = "echo"
    description = "echoes"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}},
                  "required": ["text"]}

    def __init__(self):
        self.calls = 0

    async def run(self, text: str) -> str:
        self.calls += 1
        return f"echo: {text}"


def test_guard_flags_exact_duplicates_only():
    guard = ToolGuard()
    assert guard.is_duplicate("grep", {"pattern": "a"}) is False
    assert guard.is_duplicate("grep", {"pattern": "a"}) is True
    assert guard.is_duplicate("grep", {"pattern": "b"}) is False


def test_guard_clips_and_tracks_budget():
    guard = ToolGuard(max_result_chars=10, max_total_chars=15)
    clipped = guard.clip("x" * 50)
    assert clipped.startswith("xxxxxxxxxx")
    assert "truncated" in clipped
    assert guard.exhausted  # 10 + suffix >= 15


def _call(name: str, args: dict, call_id: str = "c1") -> LLMReply:
    return LLMReply(tool_calls=(ToolCall(id=call_id, name=name, arguments=args),))


async def test_duplicate_tool_call_is_skipped_not_rerun():
    tool = EchoTool()
    fast = FakeLLM([
        _call("echo", {"text": "hi"}, "c1"),
        _call("echo", {"text": "hi"}, "c2"),   # exact duplicate
        LLMReply(text="done"),
    ])
    agent = Agent(llm=fast, tools=[tool], system_prompt="p", repo_root=FIXTURE)
    events = [e async for e in agent.run_turn([], "q")]
    assert tool.calls == 1                     # second call never executed
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert DUPLICATE_NOTE.format(name="echo") in finished[1].result_preview \
        or "already called" in finished[1].result_preview


async def test_round_exhaustion_gets_one_forced_answer():
    replies = [_call("echo", {"text": str(i)}, f"c{i}") for i in range(3)]
    replies.append(LLMReply(text="Best-effort answer from evidence."))  # tools=None call
    fast = FakeLLM(replies)
    agent = Agent(
        llm=fast, tools=[EchoTool()], system_prompt="p",
        repo_root=FIXTURE, max_tool_rounds=3,
    )
    history: list[dict] = []
    events = [e async for e in agent.run_turn(history, "q")]
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == "Best-effort answer from evidence."
    # The forced call must disable tools…
    assert fast.calls[-1]["tools"] is None
    # …and its nudge prompt must NOT be persisted.
    assert all("exhausted" not in str(m.get("content")) for m in history)


async def test_forced_answer_falls_back_to_canned_text_on_failure():
    class FlakyLLM:
        def __init__(self, replies):
            self._replies = replies

        async def chat(self, messages, tools=None):
            if tools is None:
                raise RuntimeError("boom")
            return self._replies.pop(0)

    flaky = FlakyLLM([_call("echo", {"text": "x"})])
    agent = Agent(
        llm=flaky, tools=[EchoTool()], system_prompt="p",
        repo_root=FIXTURE, max_tool_rounds=1,
    )
    events = [e async for e in agent.run_turn([], "q")]
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert "budget" in speech[-1].text  # canned fallback


async def test_narration_alongside_tool_calls_is_spoken():
    fast = FakeLLM([
        LLMReply(
            text="Give me a second — I'm tracing that through the codebase.",
            tool_calls=(ToolCall(id="c1", name="echo", arguments={"text": "x"}),),
        ),
        LLMReply(text="Here is the answer."),
    ])
    agent = Agent(llm=fast, tools=[EchoTool()], system_prompt="p", repo_root=FIXTURE)
    events = [e async for e in agent.run_turn([], "q")]
    speech = [e.text for e in events if isinstance(e, SpeechChunk)]
    assert speech[0].startswith("Give me a second")
    assert speech[-1] == "Here is the answer."


async def test_no_narration_event_when_reply_has_no_text():
    fast = FakeLLM([
        LLMReply(tool_calls=(ToolCall(id="c1", name="echo", arguments={"text": "x"}),)),
        LLMReply(text="answer"),
    ])
    agent = Agent(llm=fast, tools=[EchoTool()], system_prompt="p", repo_root=FIXTURE)
    events = [e async for e in agent.run_turn([], "q")]
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert len(speech) == 1
