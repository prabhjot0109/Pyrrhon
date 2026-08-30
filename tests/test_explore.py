"""`explore`, and the one property the whole milestone rests on.

The firewall's claim is not that the scout answers better. It is that the raw
tool output never reaches the parent's context. That gets a test rather than
being an assumed property of the code, because it is the only thing separating
this tool from a slower way to run the same greps.
"""

from __future__ import annotations

import pytest

from pyrrhon.core.agent.prompts import EXPLORE_AGENT_PROMPT
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.tools.base import Tool
from pyrrhon.core.tools.explore import MAX_REPORT_CHARS, ExploreTool
from tests.helpers import FakeLLM


class FakeGrep(Tool):
    name = "grep"
    description = "fake"
    parameters = {
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
        "required": ["pattern"],
    }

    def __init__(self, output: str):
        self.output = output
        self.calls: list[dict] = []

    async def run(self, pattern: str) -> str:
        self.calls.append({"pattern": pattern})
        return self.output


def _grep_call(pattern: str = "barge_in") -> ToolCall:
    return ToolCall(id="g1", name="grep", arguments={"pattern": pattern})


REPORT = (
    "FOUND: barge-in is handled in the voice bridge.\n"
    "WHERE:\n- voice/bridge.py:12 — cancels the turn task.\nMISSING: nothing."
)


async def test_the_scout_returns_a_report_not_its_tool_output():
    scout_llm = FakeLLM(
        [LLMReply(tool_calls=(_grep_call(),)), LLMReply(text=REPORT)]
    )
    grep = FakeGrep("voice/bridge.py:12: def _on_interruption")
    report = await ExploreTool(scout_llm, tools=[grep]).run(
        question="where is barge-in handled?"
    )
    assert report == REPORT
    assert grep.calls == [{"pattern": "barge_in"}]
    assert scout_llm.calls[0]["messages"][0]["content"] == EXPLORE_AGENT_PROMPT


async def test_a_megabyte_of_grep_output_still_yields_a_short_report():
    """The firewall's whole point, at the size where it matters.

    The scout's own guard clips the result before the scout ever sees it, and
    the parent sees only what the scout wrote. Neither cost is the parent's.
    """
    scout_llm = FakeLLM(
        [LLMReply(tool_calls=(_grep_call(),)), LLMReply(text=REPORT)]
    )
    haystack = "src/mod.py:1: match\n" * 60_000
    report = await ExploreTool(scout_llm, tools=[FakeGrep(haystack)]).run(
        question="where is it?"
    )
    assert report == REPORT
    sent = str(scout_llm.calls[1]["messages"])
    assert len(sent) < len(haystack) / 10  # even the SCOUT never carried it whole


async def test_an_overlong_report_is_bounded_in_code_not_by_the_prompt():
    """A prompt asking for 200 words is a request; this is the contract."""
    scout_llm = FakeLLM([LLMReply(text="x" * (MAX_REPORT_CHARS * 3))])
    report = await ExploreTool(scout_llm, tools=[FakeGrep("")]).run(question="q")
    assert len(report) == MAX_REPORT_CHARS
    assert report.endswith("200 words]")


async def test_a_hint_is_handed_over_and_an_empty_one_adds_nothing():
    with_hint = FakeLLM([LLMReply(text=REPORT)])
    await ExploreTool(with_hint, tools=[FakeGrep("")]).run(
        question="where is barge-in?", hint="bridge.py:12 looked relevant"
    )
    task = with_hint.calls[0]["messages"][1]["content"]
    assert "where is barge-in?" in task
    assert "bridge.py:12 looked relevant" in task

    without = FakeLLM([LLMReply(text=REPORT)])
    await ExploreTool(without, tools=[FakeGrep("")]).run(question="where is barge-in?")
    assert without.calls[0]["messages"][1]["content"] == "where is barge-in?"


def test_the_scout_refuses_a_belt_that_could_dispatch_another_scout():
    for name in ("explore", "think_deeper"):
        dispatcher = type(
            "Dispatcher",
            (Tool,),
            {
                "name": name,
                "description": "no",
                "parameters": {"type": "object", "properties": {}},
                "run": lambda self: None,
            },
        )()
        with pytest.raises(ValueError, match="depth is 1"):
            ExploreTool(FakeLLM([]), tools=[dispatcher])


async def test_the_scouts_tool_output_never_enters_the_parents_history(tmp_path):
    """The claim the milestone is built on, checked end to end.

    A full turn dispatches explore, and the only thing about the scout's work
    that survives into `history` is its report. The grep output the scout read
    is discarded with its context.
    """
    from pyrrhon.bootstrap import build_agent

    haystack = "src/deep/mod.py:41: THE RAW GREP OUTPUT\n" * 200
    scout_llm = FakeLLM(
        [LLMReply(tool_calls=(_grep_call(),)), LLMReply(text=REPORT)]
    )
    agent = build_agent(tmp_path, llm=FakeLLM([]), deep_llm=FakeLLM([]), home=tmp_path)
    scout = agent.tools["explore"]
    scout.llm = scout_llm
    scout.tools = {"grep": FakeGrep(haystack)}
    agent.llm = FakeLLM(
        [
            LLMReply(
                tool_calls=(
                    ToolCall(
                        id="e1",
                        name="explore",
                        arguments={"question": "where is barge-in handled?"},
                    ),
                )
            ),
            LLMReply(text="Barge-in lives in the voice bridge."),
        ]
    )
    history: list[dict] = []
    async for _ in agent.run_turn(history, "where is barge-in handled?"):
        pass

    # Joined from the message contents, not repr(history): a repr escapes the
    # newlines the report is formatted with and the match would never land.
    transcript = "\n".join(
        message["content"]
        for message in history
        if isinstance(message.get("content"), str)
    )
    assert REPORT in transcript
    assert "THE RAW GREP OUTPUT" not in transcript


def test_the_scout_is_driven_by_the_fast_slot_not_the_deep_one(tmp_path):
    """Assumption 2, pinned.

    Routing a locating question to the deep model makes every exploratory
    question pay escalation latency, which is the cost this tool exists to
    avoid. The two slots are distinct objects here, so the wiring cannot pass
    by coincidence.
    """
    from pyrrhon.bootstrap import build_agent

    fast, deep = FakeLLM([]), FakeLLM([])
    agent = build_agent(tmp_path, llm=fast, deep_llm=deep, home=tmp_path)
    assert agent.tools["explore"].llm is fast
    assert agent.tools["think_deeper"].deep_llm is deep


async def test_a_dispatch_reports_each_round_to_whoever_asked(tmp_path):
    """The firewall is visible: a long dispatch is distinguishable from a hang.

    Reached by callback rather than through the turn's event stream, so the
    check is that the events ARRIVE while the tool call is still in flight —
    before the ToolCallFinished that ends it.
    """
    from pyrrhon.bootstrap import build_agent
    from pyrrhon.core.events import SubagentProgress, ToolCallFinished

    agent = build_agent(tmp_path, llm=FakeLLM([]), deep_llm=FakeLLM([]), home=tmp_path)
    scout = agent.tools["explore"]
    scout.llm = FakeLLM(
        [
            LLMReply(tool_calls=(_grep_call(),)),
            LLMReply(tool_calls=(_grep_call("second"),)),
            LLMReply(text=REPORT),
        ]
    )
    scout.tools = {"grep": FakeGrep("src/a.py:1: hit")}

    seen: list[object] = []
    agent.on_progress = seen.append
    agent.llm = FakeLLM(
        [
            LLMReply(
                tool_calls=(
                    ToolCall(id="e1", name="explore", arguments={"question": "where?"}),
                )
            ),
            LLMReply(text="It is in the bridge."),
        ]
    )
    events = [e async for e in agent.run_turn([], "where?")]

    assert seen == [
        SubagentProgress("explore", 1, "grep"),
        SubagentProgress("explore", 2, "grep"),
    ]
    # Both landed before the call resolved, which is what makes them progress
    # rather than a summary of work already finished.
    assert [e for e in events if isinstance(e, ToolCallFinished)]


async def test_a_progress_sink_that_raises_never_kills_the_turn(tmp_path):
    from pyrrhon.bootstrap import build_agent

    agent = build_agent(tmp_path, llm=FakeLLM([]), deep_llm=FakeLLM([]), home=tmp_path)
    scout = agent.tools["explore"]
    scout.llm = FakeLLM([LLMReply(tool_calls=(_grep_call(),)), LLMReply(text=REPORT)])
    scout.tools = {"grep": FakeGrep("src/a.py:1: hit")}

    def explode(event):
        raise RuntimeError("the renderer fell over")

    agent.on_progress = explode
    agent.llm = FakeLLM(
        [
            LLMReply(
                tool_calls=(
                    ToolCall(id="e1", name="explore", arguments={"question": "where?"}),
                )
            ),
            LLMReply(text="It is in the bridge."),
        ]
    )
    history: list[dict] = []
    async for _ in agent.run_turn(history, "where?"):
        pass
    assert history[-1]["content"] == "It is in the bridge."


async def test_a_citation_the_scout_verified_survives_the_gate(tmp_path):
    """Task 4's whole point, under the mode where it is load-bearing.

    `require_provenance` is off by default, so this constructs the gate that
    enforces it. What must hold: a path:line the scout OPENED comes back as a
    promoted citation in the parent's answer, and one it merely named does
    not. Without absorption both are stripped, and the firewall would have
    made grounding worse than the round-by-round grinding it replaces.
    """
    from pyrrhon.core.agent.loop import Agent
    from pyrrhon.core.grounding.gate import GroundingGate

    (tmp_path / "seen.py").write_text("x = 1\n" * 50, encoding="utf-8")
    (tmp_path / "unseen.py").write_text("y = 2\n" * 50, encoding="utf-8")

    class FakeReader(Tool):
        name = "read_file"
        description = "fake"
        parameters = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

        async def run(self, path: str) -> str:
            return "\n".join(f"    {n}| x = 1" for n in range(1, 21))

    scout = ExploreTool(
        FakeLLM(
            [
                LLMReply(
                    tool_calls=(
                        ToolCall(id="r1", name="read_file", arguments={"path": "seen.py"}),
                    )
                ),
                LLMReply(text="FOUND: it is at seen.py:12, and maybe unseen.py:8."),
            ]
        ),
        tools=[FakeReader()],
    )
    agent = Agent(
        llm=FakeLLM(
            [
                LLMReply(
                    tool_calls=(
                        ToolCall(id="e1", name="explore", arguments={"question": "where?"}),
                    )
                ),
                LLMReply(text="It is at seen.py:12, and possibly unseen.py:8."),
            ]
        ),
        tools=[scout],
        system_prompt="base",
        repo_root=tmp_path,
        grounding_gate=GroundingGate(tmp_path, require_provenance=True),
    )
    scout._absorb = lambda sub: agent._evidence.absorb(sub)

    citations = [
        f"{e.file}:{e.line}"
        for e in await _collect_citations(agent, "where?")
    ]
    assert citations == ["seen.py:12"]
    assert agent.last_unseen == ("unseen.py:8",)


async def _collect_citations(agent, question: str):
    from pyrrhon.core.events import Citation

    return [e async for e in agent.run_turn([], question) if isinstance(e, Citation)]
