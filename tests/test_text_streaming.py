"""Text-mode streaming: the screen paths stream too, in markdown blocks.

Before M10 `streaming` was gated on `voice_active`, so every typed turn
buffered the whole reply and time-to-first-output equalled time-to-last-token.
Streaming on the text path needs a different chunk unit than voice: TEXT_STYLE
invites tables and fenced code, and the REPL calls Markdown() once per
SpeechChunk, so a chunk boundary is a rendering boundary. Cutting mid-table
renders as garbage.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pyrrhon.core.agent.loop import (
    CONTEXT_FULL_MESSAGE,
    CUT_OFF_MARKER,
    Agent,
    _pop_blocks,
)
from pyrrhon.core.events import SpeechChunk
from pyrrhon.core.providers.llm import (
    ContextLengthExceededError,
    LLMReply,
    ToolCall,
)
from pyrrhon.core.tools.base import Tool
from tests.helpers import FakeLLM, StreamingFakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class EchoTool(Tool):
    name = "echo"
    description = "echoes"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def run(self, text: str = "") -> str:
        return f"echo: {text}"


def make_agent(llm, *, tools=(), gate=None, **kwargs) -> Agent:
    """voice_active defaults to False — this file is all about the text path."""
    return Agent(
        llm=llm,
        tools=list(tools),
        system_prompt="p",
        repo_root=FIXTURE,
        grounding_gate=gate,
        **kwargs,
    )


# -- the splitter ------------------------------------------------------------


def test_partial_text_is_held_back():
    assert _pop_blocks("Partial para") == ([], "Partial para")


def test_a_single_newline_is_not_a_block_boundary():
    """Only a blank line ends a block. Flushing on every "\\n" would split a
    paragraph mid-way as it streams."""
    assert _pop_blocks("Para one.\n") == ([], "Para one.\n")


def test_blank_line_ends_a_block():
    assert _pop_blocks("Para one.\n\nPara two") == (["Para one."], "Para two")


def test_a_blank_line_inside_a_code_fence_does_not_split_it():
    """The case sentence-splitting gets catastrophically wrong."""
    blocks, rest = _pop_blocks("Intro.\n\n```py\nx = 1\n\ny = 2\n```\n\nAfter")
    assert blocks == ["Intro.", "```py\nx = 1\n\ny = 2\n```"]
    assert rest == "After"


def test_tables_and_lists_survive_whole():
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert _pop_blocks(f"{table}\n\nnext") == ([table], "next")
    assert _pop_blocks("- one\n- two\n\ndone") == (["- one\n- two"], "done")


def test_runs_of_blank_lines_do_not_emit_empty_blocks():
    assert _pop_blocks("a\n\n\n\nb") == (["a"], "b")


# -- streaming through the agent ---------------------------------------------


async def test_text_turn_streams_one_chunk_per_markdown_block():
    reply = LLMReply(text="First para.\n\nSecond para.")
    llm = StreamingFakeLLM([(["First ", "para.\n\nSecond ", "para."], reply)])
    agent = make_agent(llm)
    history: list[dict] = []
    events = [e async for e in agent.run_turn(history, "q")]

    chunks = [e.text for e in events if isinstance(e, SpeechChunk)]
    assert chunks == ["First para.", "Second para."]
    # History joins blocks with a blank line, so it round-trips as the markdown
    # that was actually rendered — not the space-joined voice form.
    assert history[-1]["content"] == "First para.\n\nSecond para."


async def test_a_streamed_code_fence_arrives_as_one_chunk():
    """The chunk is the rendering unit, so a fence must never be split across
    two Markdown() calls."""
    body = "Here:\n\n```py\ndef f():\n\n    return 1\n```"
    llm = StreamingFakeLLM([(list(body), LLMReply(text=body))])
    agent = make_agent(llm)
    events = [e async for e in agent.run_turn([], "q")]

    chunks = [e.text for e in events if isinstance(e, SpeechChunk)]
    assert chunks == ["Here:", "```py\ndef f():\n\n    return 1\n```"]


async def test_text_streaming_still_runs_tools():
    llm = StreamingFakeLLM([
        ([], LLMReply(tool_calls=(ToolCall(id="c1", name="echo", arguments={"text": "hi"}),))),
        (["All done."], LLMReply(text="All done.")),
    ])
    agent = make_agent(llm, tools=[EchoTool()])
    events = [e async for e in agent.run_turn([], "q")]
    assert [e.text for e in events if isinstance(e, SpeechChunk)] == ["All done."]
    assert llm.calls == 2


async def test_voice_and_text_split_the_same_reply_differently():
    """One reply, two channels, two chunk units — voice by sentence, text by
    block. This is the whole reason the splitter is channel-aware."""
    body = "One. Two.\n\nThree."

    async def chunks_for(voice: bool) -> list[str]:
        llm = StreamingFakeLLM([([body], LLMReply(text=body))])
        agent = make_agent(llm, voice_active=voice)
        return [
            e.text async for e in agent.run_turn([], "q") if isinstance(e, SpeechChunk)
        ]

    assert await chunks_for(True) == ["One.", "Two.", "Three."]
    assert await chunks_for(False) == ["One. Two.", "Three."]


async def test_non_streaming_provider_keeps_the_whole_reply_path():
    """FakeLLM exposes no stream(); the hasattr guard is what makes turning
    streaming on for every channel a safe change for the existing suite."""
    llm = FakeLLM([LLMReply(text="One shot.\n\nTwo shot.")])
    agent = make_agent(llm)
    events = [e async for e in agent.run_turn([], "q")]
    chunks = [e.text for e in events if isinstance(e, SpeechChunk)]
    assert chunks == ["One shot.\n\nTwo shot."]  # single chunk, not per-block


# -- the grounding retry is off on streamed turns ----------------------------


class CountingGate:
    """Flags one bogus citation so the retry path would fire if it were live."""

    def __init__(self):
        self.checks = 0

    async def check(self, text):
        self.checks += 1
        return SimpleNamespace(
            speech_text=text, citations=[], unverified=("nope.py:9999",)
        )


async def test_streamed_turns_do_not_run_the_self_correction_retry():
    """Un-saying text that is already on screen would violate "history records
    what was heard", so streamed output is final — strip, hedge, move on."""
    llm = StreamingFakeLLM([(["Claim at nope.py:9999."],
                             LLMReply(text="Claim at nope.py:9999."))])
    agent = make_agent(llm, gate=CountingGate(), allow_retry=True)
    await agent.run_turn([], "q").__anext__()
    # One round only: a retry would have needed a second scripted round and
    # StreamingFakeLLM would have raised IndexError popping from an empty list.
    assert llm.calls == 1


async def test_non_streaming_turns_still_retry():
    """The screen path's one self-correction round-trip is unchanged for
    providers that do not stream."""
    llm = FakeLLM([
        LLMReply(text="Claim at nope.py:9999."),
        LLMReply(text="I'm not certain where that lives."),
    ])
    agent = make_agent(llm, gate=CountingGate(), allow_retry=True)
    [e async for e in agent.run_turn([], "q")]
    assert len(llm.calls) == 2  # original + one correction


class StreamThatDiesMidAnswer:
    """Streams two blocks, then the provider rejects the round."""

    async def stream(self, messages, tools=None):
        yield ("text", "Here is the first paragraph.\n\n")
        yield ("text", "And a second one.\n\n")
        raise ContextLengthExceededError("prompt too long")

    async def chat(self, messages, tools=None):
        return LLMReply(text="unused")


async def test_a_dead_stream_leaves_exactly_one_assistant_message(tmp_path):
    agent = Agent(
        llm=StreamThatDiesMidAnswer(), tools=[], system_prompt="s", repo_root=tmp_path
    )
    history: list[dict] = []
    async for _event in agent.run_turn(history, "explain the loop"):
        pass

    assistants = [m for m in history if m["role"] == "assistant"]
    assert len(assistants) == 1, f"expected one assistant turn, got {len(assistants)}"
    # What the user actually heard is preserved, and marked as incomplete.
    assert assistants[0]["content"].startswith("Here is the first paragraph.")
    assert assistants[0]["content"].endswith(CUT_OFF_MARKER)


async def test_no_two_assistant_messages_are_ever_adjacent(tmp_path):
    agent = Agent(
        llm=StreamThatDiesMidAnswer(), tools=[], system_prompt="s", repo_root=tmp_path
    )
    history: list[dict] = []
    async for _event in agent.run_turn(history, "explain the loop"):
        pass
    roles = [m["role"] for m in history]
    # Pairwise, so the tail is deliberately ragged: strict= would be wrong here.
    assert not any(a == b == "assistant" for a, b in zip(roles, roles[1:], strict=False))


async def test_the_user_still_hears_the_error(tmp_path):
    agent = Agent(
        llm=StreamThatDiesMidAnswer(), tools=[], system_prompt="s", repo_root=tmp_path
    )
    spoken = [
        event.text
        async for event in agent.run_turn([], "explain the loop")
        if isinstance(event, SpeechChunk)
    ]
    assert CONTEXT_FULL_MESSAGE in spoken
