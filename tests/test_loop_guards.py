"""Duplicate-call, size-cap, and budget-exhaustion guards on the tool loop."""

from pathlib import Path

from pyrrhon.core.agent.guards import (
    DUPLICATE_NOTE,
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


async def test_guard_clips_and_tracks_budget():
    # No store: truncation is still what a guard without one does, and the
    # deep subagent's guard has none.
    guard = ToolGuard(max_result_chars=10, max_total_chars=15)
    clipped = await guard.clip("x" * 50)
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


# -- range containment (M16c) ------------------------------------------------


def _covered(**ranges):
    return lambda path: ranges.get(path, [])


def test_a_range_already_shown_is_a_duplicate_even_with_new_arguments():
    """Exact-argument matching catches a repeat nobody makes. What actually
    happens is a re-request at a narrower range inside one already read."""
    guard = ToolGuard(covered=_covered(**{"loop.py": [(1, 400)]}))
    assert guard.is_duplicate("read_file", {"path": "loop.py", "start_line": 40, "end_line": 90})


def test_a_range_reaching_past_what_was_shown_is_not_a_duplicate():
    guard = ToolGuard(covered=_covered(**{"loop.py": [(1, 400)]}))
    assert not guard.is_duplicate(
        "read_file", {"path": "loop.py", "start_line": 350, "end_line": 500}
    )


def test_an_unbounded_read_is_not_a_duplicate_of_a_bounded_one():
    """read_file with only a start reads to EOF, so 1-400 does not contain it."""
    guard = ToolGuard(covered=_covered(**{"loop.py": [(1, 400)]}))
    assert not guard.is_duplicate("read_file", {"path": "loop.py"})


def test_blame_defaults_to_one_line_where_read_file_defaults_to_the_file():
    """The two spell the span the same way and mean different things by it:
    `-L n,n` blames one line. Reading them as if they agreed would suppress a
    whole-file read as a repeat of a single blamed line."""
    guard = ToolGuard(covered=_covered(**{"loop.py": [(40, 40)]}))
    assert guard.is_duplicate("git_blame", {"path": "loop.py", "start_line": 40})
    assert not guard.is_duplicate("read_file", {"path": "loop.py", "start_line": 40})


def test_containment_is_off_for_tools_that_name_no_range():
    guard = ToolGuard(covered=_covered(**{"loop.py": [(1, 400)]}))
    assert not guard.is_duplicate("grep", {"pattern": "def ", "path": "loop.py"})


def test_without_a_ledger_only_exact_repeats_count():
    """The deep subagent keeps no ledger, so its guard must behave as before."""
    guard = ToolGuard()
    args = {"path": "loop.py", "start_line": 40, "end_line": 90}
    assert not guard.is_duplicate("read_file", args)
    assert guard.is_duplicate("read_file", args)
