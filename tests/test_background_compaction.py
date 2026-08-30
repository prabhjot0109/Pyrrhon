"""History summarization moved off the critical path (M10 Stage 1.3).

maybe_summarize is a full LLM round trip. It used to run inside
Agent.run_turn, in front of the first token of every over-budget turn — the
worst possible place for it in a product whose metric is time-to-first-word.
Session now runs it AFTER the turn, during the user's read/think/speak time.

Ownership sits on Session rather than the turn task on purpose: the turn task
is cancelled on barge-in, and a compaction cancelled halfway would be lost
work. The turn task and the compaction must also never overlap, because
maybe_summarize splices history[1:split] on the same list the agent loop
iterates.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.context import SUMMARY_HEADER
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.session import Session
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class HangingLLM:
    """chat() answers the turn, then blocks forever on the compaction call."""

    def __init__(self, first: LLMReply):
        self._first = first
        self.calls = 0
        self.blocked = asyncio.Event()

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return self._first
        self.blocked.set()
        await asyncio.Event().wait()  # hang until cancelled


def make_session(llm, *, budget: int = 10, keep_last: int = 1) -> Session:
    agent = Agent(
        llm=llm,
        tools=[],
        system_prompt="p",
        repo_root=FIXTURE,
        context_budget_tokens=budget,
        context_keep_last=keep_last,
    )
    return Session(agent)


def bulk(session: Session, count: int = 8) -> None:
    """Pad history so it is comfortably over the (tiny) test budget."""
    session.history.append({"role": "system", "content": "p"})
    for i in range(count):
        session.history.append({"role": "user", "content": f"question {i} " * 20})
        session.history.append({"role": "assistant", "content": f"answer {i} " * 20})


async def drain(session: Session, text: str) -> None:
    async for _ in session.run_turn(text):
        pass


async def test_the_turn_itself_makes_no_summarize_call():
    """The whole point: an over-budget turn no longer pays a round trip before
    its first token."""
    llm = FakeLLM([LLMReply(text="answer."), LLMReply(text="a summary")])
    session = make_session(llm)
    bulk(session)

    await drain(session, "q")
    assert len(llm.calls) == 1  # the answer, and nothing else

    # It was deferred, not dropped.
    assert session._compaction is not None
    await session._compaction
    assert len(llm.calls) == 2
    assert any(
        SUMMARY_HEADER in m.get("content", "")
        for m in session.history
        if m.get("role") == "system"
    )


async def test_nothing_is_scheduled_when_history_is_under_budget():
    llm = FakeLLM([LLMReply(text="short.")])
    session = make_session(llm, budget=1_000_000)
    bulk(session)
    await drain(session, "q")
    assert session._compaction is None


async def test_compaction_is_disabled_when_the_budget_is_zero():
    llm = FakeLLM([LLMReply(text="short.")])
    session = make_session(llm, budget=0)
    bulk(session)
    await drain(session, "q")
    assert session._compaction is None


async def test_a_cancelled_turn_schedules_nothing():
    """A barge-in has just rolled back the turn's tail and the user is talking
    again; a background LLM call is the last thing that moment needs."""

    class Hanging:
        async def chat(self, messages, tools=None):
            await asyncio.sleep(10)

    session = make_session(Hanging())
    bulk(session)

    task = asyncio.create_task(drain(session, "q"))
    await asyncio.sleep(0.05)
    session.abort_current_turn()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert session._compaction is None


async def test_the_next_turn_cancels_a_still_running_compaction():
    """Cancel, don't await: awaiting would hand turn N+1 exactly the round
    trip this change removed from turn N."""
    llm = HangingLLM(LLMReply(text="answer."))
    session = make_session(llm)
    bulk(session)

    await drain(session, "q1")
    compaction = session._compaction
    assert compaction is not None
    await asyncio.wait_for(llm.blocked.wait(), timeout=2)  # it really is in flight

    # A second turn starts while compaction hangs. It must not block on it.
    session.agent.llm = FakeLLM([LLMReply(text="second answer.")])
    await asyncio.wait_for(drain(session, "q2"), timeout=2)

    assert compaction.cancelled() or compaction.done()
    assert session.history[-1]["content"] == "second answer."


async def test_a_cancelled_compaction_leaves_history_untouched():
    """Safe because maybe_summarize's only await (llm.chat) happens before any
    mutation — the splice that follows is synchronous."""
    llm = HangingLLM(LLMReply(text="answer."))
    session = make_session(llm)
    bulk(session)

    await drain(session, "q")
    await asyncio.wait_for(llm.blocked.wait(), timeout=2)
    before = [dict(m) for m in session.history]

    session._cancel_compaction()
    await asyncio.sleep(0)

    assert session.history == before


async def test_only_one_compaction_is_outstanding_at_a_time():
    llm = FakeLLM([LLMReply(text="a."), LLMReply(text="s1"),
                   LLMReply(text="b."), LLMReply(text="s2")])
    session = make_session(llm)
    bulk(session)

    await drain(session, "q1")
    first = session._compaction
    await first

    await drain(session, "q2")
    second = session._compaction
    assert second is not first
    if second is not None:
        await second


async def test_background_compaction_records_its_duration():
    """Compaction happens off the turn, so it belongs on the Session, not on a
    TurnTrace that has already been finished and published."""
    llm = FakeLLM([LLMReply(text="answer."), LLMReply(text="a summary")])
    session = make_session(llm)
    bulk(session)

    assert session.last_compaction_ms is None  # nothing has compacted yet
    await drain(session, "q")
    assert session._compaction is not None
    await session._compaction

    assert session.last_compaction_ms is not None
    assert session.last_compaction_ms >= 0.0


async def test_a_turn_that_fitted_its_own_history_schedules_nothing_after():
    """M16b: the pre-flight ladder and the background pass answer the same
    question against the same budget, so a turn that already fitted its history
    must not queue a round trip to fit it again.

    Both sides read Agent.request_budget, which is what makes that true — a
    background pass budgeting against a looser number would fire on a history
    the turn had just declared fine.
    """
    llm = FakeLLM([LLMReply(text="answer.")])
    session = make_session(llm, budget=3000)
    session.history.extend([
        {"role": "system", "content": "p"},
        {"role": "user", "content": "an earlier question"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "content": "X" * 20_000},
        {"role": "assistant", "content": "an earlier answer"},
    ])

    await drain(session, "and now?")

    assert "elided" in session.history[3]["content"]  # the ladder ran
    assert session._compaction is None                # and nothing came after
    assert len(llm.calls) == 1
