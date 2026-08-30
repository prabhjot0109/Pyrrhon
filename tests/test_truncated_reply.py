"""A reply cut off at `max_tokens` is not an answer.

The fault this covers is the quietest of M16a's four. Every other one produces
a visible error; this one produces HTTP 200, a well-formed body, and a
confident half-sentence that goes through the grounding gate and gets
**spoken**. Nothing on screen says it was cut off and the listener has no
citation to check it against.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from pyrrhon.core.agent.loop import (
    RESUME_INSTRUCTION,
    TRUNCATED_MARKER,
    TRUNCATED_MESSAGE,
    Agent,
)
from pyrrhon.core.events import SpeechChunk
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.tools.repo import ReadFileTool

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


# --- The field is populated on both paths -----------------------------------


def _completion(content: str, finish_reason: str) -> dict:
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content},
             "finish_reason": finish_reason}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


def _sse(deltas: list[str], finish_reason: str) -> bytes:
    lines = []
    for i, delta in enumerate(deltas):
        last = i == len(deltas) - 1
        lines.append(
            "data: " + json.dumps({
                "id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                "choices": [{"index": 0, "delta": {"content": delta},
                             "finish_reason": finish_reason if last else None}],
            }) + "\n\n"
        )
    # The usage chunk carries an EMPTY choices list, which is exactly why it
    # cannot clobber the finish_reason the last content delta carried.
    lines.append(
        "data: " + json.dumps({
            "id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
            "choices": [],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }) + "\n\n"
    )
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _serving(content: str, finish_reason: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("stream"):
            return httpx.Response(
                200,
                content=_sse([content], finish_reason),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=_completion(content, finish_reason))

    return handler


@pytest.mark.parametrize("reason", ["length", "stop"])
async def test_chat_reports_the_finish_reason(mock_llm, reason):
    llm = mock_llm(_serving("half a sen", reason))
    reply = await llm.chat([{"role": "user", "content": "hi"}])
    assert reply.finish_reason == reason


@pytest.mark.parametrize("reason", ["length", "stop"])
async def test_stream_reports_the_finish_reason(mock_llm, reason):
    """The path in doubt: the field rides the last CONTENT delta, and the
    usage chunk that follows it has no choices to overwrite it with."""
    llm = mock_llm(_serving("half a sen", reason))
    events = [e async for e in llm.stream([{"role": "user", "content": "hi"}])]
    kind, reply = events[-1]
    assert kind == "reply"
    assert reply.finish_reason == reason
    assert reply.usage is not None and reply.usage.prompt == 5


def test_the_field_defaults_so_existing_doubles_keep_working():
    """tests/helpers.py:FakeLLM builds LLMReply positionally and by keyword
    without ever naming this field. The default is what buys that."""
    assert LLMReply(text="hi").finish_reason is None


# --- The resume ladder ------------------------------------------------------


class ScriptedLLM:
    """Whole-reply double: returns scripted LLMReplys in order."""

    def __init__(self, script):
        self._script = list(script)
        self.model = "scripted"
        self.calls: list[list[dict]] = []

    async def chat(self, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        return self._script.pop(0)


class ScriptedStreamLLM:
    """Streaming double: each round is (deltas, LLMReply)."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls: list[list[dict]] = []

    async def stream(self, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        deltas, reply = self._rounds.pop(0)
        for delta in deltas:
            yield ("text", delta)
        yield ("reply", reply)


def make_agent(llm) -> Agent:
    return Agent(
        llm=llm,
        tools=[ReadFileTool(FIXTURE)],
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
    )


async def collect(agent, history, text):
    return [event async for event in agent.run_turn(history, text)]


def spoken(events) -> list[str]:
    return [e.text for e in events if isinstance(e, SpeechChunk)]


async def test_a_truncated_whole_reply_resumes_once():
    llm = ScriptedLLM([
        LLMReply(text="The loop starts in", finish_reason="length"),
        LLMReply(text="run_turn and ends there.", finish_reason="stop"),
    ])
    agent = make_agent(llm)
    history: list[dict] = []
    events = await collect(agent, history, "where does the loop start?")

    # The fragment reaches the user rather than being swallowed by the resume.
    assert spoken(events) == ["The loop starts in", "run_turn and ends there."]
    # It is sealed as the assistant turn, and the meta instruction follows it,
    # so the second round sees what it is continuing.
    assert RESUME_INSTRUCTION in [m.get("content") for m in llm.calls[1]]
    assert history[-2]["content"] == RESUME_INSTRUCTION
    assert history[-1]["content"] == "run_turn and ends there."


async def test_a_truncated_stream_resumes_once():
    llm = ScriptedStreamLLM([
        (["The loop starts in"], LLMReply(text="The loop starts in", finish_reason="length")),
        (["run_turn and ends there."],
         LLMReply(text="run_turn and ends there.", finish_reason="stop")),
    ])
    agent = make_agent(llm)
    history: list[dict] = []
    events = await collect(agent, history, "where does the loop start?")

    assert spoken(events) == ["The loop starts in", "run_turn and ends there."]
    assert len(llm.calls) == 2
    assert RESUME_INSTRUCTION in [m.get("content") for m in llm.calls[1]]
    # The streamed partial was written into history by _stream_round already;
    # the resume must not append a duplicate beside it.
    assert [m["content"] for m in history if m["role"] == "assistant"] == [
        "The loop starts in", "run_turn and ends there."
    ]


async def test_a_second_truncation_degrades_and_names_the_key():
    llm = ScriptedLLM([
        LLMReply(text="The loop starts in", finish_reason="length"),
        LLMReply(text="run_turn, and then it", finish_reason="length"),
    ])
    agent = make_agent(llm)
    events = await collect(agent, [], "where does the loop start?")
    said = spoken(events)

    assert len(llm.calls) == 2  # exactly one resume, never two
    assert said[-1].endswith(TRUNCATED_MESSAGE)
    assert "max_tokens" in said[-1]
    # The second fragment is not lost, but it is not presented as an answer.
    assert TRUNCATED_MARKER.strip() in said[-1]


async def test_a_second_truncation_on_the_streaming_path_degrades():
    llm = ScriptedStreamLLM([
        (["The loop starts in"], LLMReply(text="The loop starts in", finish_reason="length")),
        (["run_turn, and then it"],
         LLMReply(text="run_turn, and then it", finish_reason="length")),
    ])
    agent = make_agent(llm)
    history: list[dict] = []
    events = await collect(agent, history, "where does the loop start?")

    assert spoken(events)[-1] == TRUNCATED_MESSAGE
    # Sealed onto the streamed slot, not appended beside it: two adjacent
    # assistant messages are rejected by strict chat endpoints.
    assert history[-1]["role"] == "assistant"
    assert history[-1]["content"].endswith(TRUNCATED_MARKER)


async def test_a_finished_reply_is_untouched():
    llm = ScriptedLLM([LLMReply(text="It prints a greeting.", finish_reason="stop")])
    events = await collect(make_agent(llm), [], "what is this?")
    assert spoken(events) == ["It prints a greeting."]
    assert len(llm.calls) == 1


async def test_a_provider_that_omits_the_reason_behaves_as_before():
    llm = ScriptedLLM([LLMReply(text="It prints a greeting.")])
    events = await collect(make_agent(llm), [], "what is this?")
    assert spoken(events) == ["It prints a greeting."]


async def test_the_resume_budget_is_per_round_and_resets():
    """A long investigation must not be denied a resume because an earlier
    round used one. The tally clears whenever a round lands intact."""
    tool_call = LLMReply(
        text=None,
        tool_calls=(
            __import__("pyrrhon.core.providers.llm", fromlist=["ToolCall"]).ToolCall(
                id="1", name="read_file", arguments={"path": "hello.py"}
            ),
        ),
        finish_reason="tool_calls",
    )
    llm = ScriptedLLM([
        LLMReply(text="Let me look. First I", finish_reason="length"),
        tool_call,
        LLMReply(text="It greets you, and", finish_reason="length"),
        LLMReply(text="that is all it does.", finish_reason="stop"),
    ])
    agent = make_agent(llm)
    events = await collect(agent, [], "what does hello.py do?")
    assert spoken(events)[-1] == "that is all it does."
    assert len(llm.calls) == 4  # both resumes were granted
