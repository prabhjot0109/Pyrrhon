import asyncio
from pathlib import Path

import pytest

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import SpeechChunk
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.session import INTERRUPTED_MARKER, Session
from pyrrhon.core.tools.base import Tool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class SlowEchoTool(Tool):
    """A scripted tool that hangs so tests can cancel it mid-flight."""

    name = "slow_echo"
    description = "Test tool: waits a long time, then answers."
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self):
        self.started = asyncio.Event()
        self.completed = False

    async def run(self, **kwargs) -> str:
        self.started.set()
        await asyncio.sleep(30)
        self.completed = True
        return "slow result"


def make_session(replies, tools) -> tuple[Session, FakeLLM]:
    fake = FakeLLM(replies)
    agent = Agent(
        llm=fake,
        tools=tools,
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
    )
    return Session(agent), fake


async def test_normal_turn_streams_events_and_grows_history():
    session, _ = make_session([LLMReply(text="It prints a greeting.")], tools=[])
    assert session.mode == "understand"
    events = [event async for event in session.run_turn("what does app.py do?")]
    assert SpeechChunk(text="It prints a greeting.") in events
    assert [m["role"] for m in session.history] == ["system", "user", "assistant"]


async def test_abort_cancels_in_flight_tool_and_appends_nothing_further():
    slow = SlowEchoTool()
    replies = [
        LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),)),
        LLMReply(text="never reached"),
    ]
    session, _ = make_session(replies, tools=[slow])

    events: list = []

    async def consume():
        async for event in session.run_turn("take your time"):
            events.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(slow.started.wait(), timeout=2)
    session.abort_current_turn()
    await asyncio.wait_for(consumer, timeout=2)

    # Give any (wrongly) surviving work a chance to run, then assert nothing landed.
    for _ in range(5):
        await asyncio.sleep(0)
    assert slow.completed is False  # the in-flight tool call was cancelled
    # The dangling assistant tool_calls message was rolled back; the aborted
    # turn appended nothing further after the user message.
    assert [m["role"] for m in session.history] == ["system", "user"]


async def test_abort_when_idle_is_a_noop():
    session, _ = make_session([LLMReply(text="hi")], tools=[])
    session.abort_current_turn()  # nothing running — must not raise
    events = [event async for event in session.run_turn("hello")]
    assert events  # session still usable after the no-op abort


async def test_second_turn_works_after_abort():
    slow = SlowEchoTool()
    replies = [
        LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),)),
        LLMReply(text="answer to the second question"),
    ]
    session, _ = make_session(replies, tools=[slow])

    consumer = asyncio.create_task(
        asyncio.wait_for(_drain(session.run_turn("first")), timeout=2)
    )
    await asyncio.wait_for(slow.started.wait(), timeout=2)
    session.abort_current_turn()
    await consumer

    events = [event async for event in session.run_turn("second")]
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == "answer to the second question"
    assert session.history[-1]["content"] == "answer to the second question"


async def test_run_turn_while_running_raises():
    slow = SlowEchoTool()
    replies = [LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),))]
    session, _ = make_session(replies, tools=[slow])
    consumer = asyncio.create_task(_drain(session.run_turn("first")))
    await asyncio.wait_for(slow.started.wait(), timeout=2)
    with pytest.raises(RuntimeError, match="already running"):
        async for _ in session.run_turn("second"):
            pass
    session.abort_current_turn()
    await asyncio.wait_for(consumer, timeout=2)


async def test_truncate_last_assistant_rewrites_content():
    session, _ = make_session([LLMReply(text="alpha beta gamma delta")], tools=[])
    async for _ in session.run_turn("talk"):
        pass
    session.truncate_last_assistant("alpha beta")
    assert session.history[-1]["content"] == "alpha beta" + INTERRUPTED_MARKER


async def test_truncate_is_noop_when_last_message_is_not_assistant_text():
    session, _ = make_session([], tools=[])
    session.history[:] = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    session.truncate_last_assistant("heard")
    assert session.history[-1] == {"role": "user", "content": "u"}


async def _drain(aiter) -> None:
    async for _ in aiter:
        pass


async def test_a_replacement_turn_starts_without_awaiting_the_aborted_one():
    """The voice bridge's defensive path, which CLAUDE.md recorded as broken.

    `_start_turn` cancels its predecessor and calls `abort_current_turn()`,
    and neither of those AWAITS the cancellation — so `_current` is still
    not `done()` when the replacement starts, and the user's second sentence
    was answered with `RuntimeError: A turn is already running`. Reachable
    whenever a transcription races ahead of its own interruption, which is a
    normal thing for a person to do.
    """
    slow = SlowEchoTool()
    replies = [
        LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),)),
        LLMReply(text="answer to the second question"),
    ]
    session, _ = make_session(replies, tools=[slow])

    consumer = asyncio.create_task(_drain(session.run_turn("first")))
    await asyncio.wait_for(slow.started.wait(), timeout=2)

    # Exactly the bridge's sequence: abort, then start the replacement, with
    # nothing awaited in between.
    session.abort_current_turn()
    events = [event async for event in session.run_turn("second")]

    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == "answer to the second question"
    await asyncio.wait_for(consumer, timeout=2)


async def test_an_abandoned_turn_never_cancels_its_successor():
    """`_run_turn_events`' finally read `self._current`, which by the time a
    superseded generator is finalized is the REPLACEMENT turn's task. The
    corpse would then cancel the live turn and roll back its history.

    Ordered so the first generator is finalized after the second turn is
    already running, which is the shape a barge-in produces.
    """
    slow = SlowEchoTool()
    replies = [
        LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),)),
        LLMReply(text="answer to the second question"),
    ]
    session, _ = make_session(replies, tools=[slow])

    consumer = asyncio.create_task(_drain(session.run_turn("first")))
    await asyncio.wait_for(slow.started.wait(), timeout=2)
    session.abort_current_turn()

    events = [event async for event in session.run_turn("second")]
    await asyncio.wait_for(consumer, timeout=2)
    for _ in range(5):
        await asyncio.sleep(0)

    assert [e for e in events if isinstance(e, SpeechChunk)]
    # The replacement's answer survived the corpse being finalized.
    assert session.history[-1]["content"] == "answer to the second question"
