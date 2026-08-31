"""M16e's invariant: a location enters history only through a tool result.

The delivery contract already says the model should re-derive a location from
a tool result rather than from its own recollection, and `_emit_final` already
records gated prose so the final answer carries none. This file asserts the
same thing about every OTHER way a message reaches history, because each of
those was written for its own reason and none of them was thinking about
grounding.

Two roles are exempt and the exemption is the point. A `tool` message IS the
admissible source. A `user` message is the user's own words — echoing back a
path they typed is not a claim Pyrrhon made, and stripping it would destroy
the question.
"""

from __future__ import annotations

from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.context import maybe_summarize
from pyrrhon.core.grounding.citations import extract_references
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.tools.repo import ReadFileTool
from tests.helpers import FakeLLM, StreamingFakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"

EXEMPT_ROLES = {"tool", "user"}


def locations_in_history(history: list[dict]) -> list[tuple[int, str, str]]:
    """Every path:line Pyrrhon itself put into history, as (index, role, ref).

    Reuses the gate's own extractor rather than a second regex: an invariant
    that recognises a different set of references from the thing it protects
    is worse than no invariant.
    """
    found: list[tuple[int, str, str]] = []
    for index, message in enumerate(history):
        role = message.get("role", "?")
        if role in EXEMPT_ROLES:
            continue
        content = message.get("content")
        if isinstance(content, str):
            found += [(index, role, f"{p}:{n}") for p, n in extract_references(content)]
        for call in message.get("tool_calls") or ():
            arguments = call.get("function", {}).get("arguments", "")
            found += [
                (index, f"{role}.tool_calls", f"{p}:{n}")
                for p, n in extract_references(arguments)
            ]
    return found


def make_agent(llm) -> Agent:
    return Agent(
        llm=llm,
        tools=[ReadFileTool(FIXTURE)],
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
        grounding_gate=GroundingGate(FIXTURE),
        allow_retry=False,
    )


async def drain(agent: Agent, history: list[dict], text: str) -> None:
    async for _ in agent.run_turn(history, text):
        pass


async def test_narration_beside_a_tool_call_carries_no_location():
    """The assistant message that carries the tool calls also carries the
    model's narration, and that narration is gated for SPEECH only. Recording
    the raw text feeds the model's own unverified coordinate back to it on the
    next round — the recollection path M16e exists to close."""
    agent = make_agent(
        FakeLLM(
            [
                LLMReply(
                    text="Let me look at utils/helpers.py:1 and bogus/nowhere.py:7.",
                    tool_calls=[
                        ToolCall(id="1", name="read_file", arguments={"path": "app.py"})
                    ],
                ),
                LLMReply(text="greet is defined at utils/helpers.py:1."),
            ]
        )
    )
    history: list[dict] = []
    await drain(agent, history, "where is greet?")

    assert locations_in_history(history) == []


async def test_a_streamed_answer_that_gates_to_nothing_is_not_recorded_raw():
    """When every chunk gates away, `spoken_text` is empty and the fallback to
    `reply.text` puts the ungated answer into history. Reachable whenever the
    answer is little more than the citation itself."""
    agent = make_agent(
        StreamingFakeLLM(
            [
                (
                    ["utils/helpers.py:1"],
                    LLMReply(text="utils/helpers.py:1"),
                )
            ]
        )
    )
    history: list[dict] = []
    await drain(agent, history, "where is greet?")

    assert locations_in_history(history) == []


async def test_a_summary_carries_no_location():
    """SUMMARY_PROMPT used to demand every path:line be kept EXACTLY. That was
    right when history was the model's source of code facts; under upstream
    verification it is the one instruction that manufactures stale coordinates,
    because a summary outlives the turn whose tool result justified it."""
    history = [
        {"role": "system", "content": "base prompt"},
        {"role": "user", "content": "where is greet?"},
        {"role": "assistant", "content": "greet is in the helpers module."},
        {"role": "user", "content": "and what calls it?"},
        {"role": "assistant", "content": "the app entry point does."},
    ]
    summarizer = FakeLLM(
        [LLMReply(text="Found greet at utils/helpers.py:1, called from app.py:3.")]
    )

    assert await maybe_summarize(history, summarizer, budget_tokens=0, keep_last=2)

    prompt = summarizer.calls[0]["messages"][0]["content"]
    assert "EXACTLY" not in prompt
    assert locations_in_history(history) == []
