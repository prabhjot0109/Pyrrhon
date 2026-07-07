"""Voice-mode token streaming: the LLM streams, each sentence is gated and
spoken as it completes (low time-to-first-token), tools still run, and grounding
holds per sentence. Text mode / non-streaming providers are untouched."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from pyrrhon.core.agent.loop import Agent, _pop_sentences
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

    async def run(self, text: str) -> str:
        return f"echo: {text}"


class StreamingFakeLLM:
    """Scripted streaming double: each round is (deltas, LLMReply)."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    async def stream(self, messages, tools=None):
        self.calls += 1
        deltas, reply = self._rounds.pop(0)
        for delta in deltas:
            yield ("text", delta)
        yield ("reply", reply)


class TagGate:
    """Fake grounding gate: proves per-sentence gating by tagging every
    sentence it clears."""

    async def check(self, text):
        return SimpleNamespace(
            speech_text=f"[gated] {text}", citations=[], unverified=()
        )


def make_agent(llm, *, tools=(), gate=None) -> Agent:
    return Agent(
        llm=llm,
        tools=list(tools),
        system_prompt="p",
        repo_root=FIXTURE,
        grounding_gate=gate,
        voice_active=True,
    )


def test_pop_sentences_splits_on_completed_boundaries():
    assert _pop_sentences("Hello wor") == ([], "Hello wor")
    assert _pop_sentences("One. Two") == (["One."], "Two")
    assert _pop_sentences("A. B. C") == (["A.", "B."], "C")


async def test_voice_streams_one_speech_chunk_per_sentence():
    reply = LLMReply(text="First sentence. Second sentence. Third.")
    llm = StreamingFakeLLM(
        [(["First sen", "tence. Second ", "sentence. Third."], reply)]
    )
    agent = make_agent(llm)
    history: list[dict] = []
    events = [e async for e in agent.run_turn(history, "q")]
    speech = [e.text for e in events if isinstance(e, SpeechChunk)]
    assert speech == ["First sentence.", "Second sentence.", "Third."]
    # History records exactly what was spoken.
    assert history[-1]["content"] == "First sentence. Second sentence. Third."


async def test_voice_streaming_runs_tools_then_streams_the_answer():
    llm = StreamingFakeLLM([
        ([], LLMReply(tool_calls=(ToolCall(id="c1", name="echo", arguments={"text": "hi"}),))),
        (["All done. ", "Bye."], LLMReply(text="All done. Bye.")),
    ])
    agent = make_agent(llm, tools=[EchoTool()])
    events = [e async for e in agent.run_turn([], "q")]
    speech = [e.text for e in events if isinstance(e, SpeechChunk)]
    assert speech == ["All done.", "Bye."]
    assert any(isinstance(e, ToolCallFinished) for e in events)
    assert llm.calls == 2


async def test_streamed_sentences_pass_through_the_grounding_gate():
    reply = LLMReply(text="Alpha here. Beta there.")
    llm = StreamingFakeLLM([(["Alpha here. ", "Beta there."], reply)])
    agent = make_agent(llm, gate=TagGate())
    events = [e async for e in agent.run_turn([], "q")]
    speech = [e.text for e in events if isinstance(e, SpeechChunk)]
    # Every spoken sentence went through the gate before reaching TTS.
    assert speech == ["[gated] Alpha here.", "[gated] Beta there."]


async def test_streaming_tool_narration_leaves_clean_history():
    # Round 1 streams narration AND calls a tool; the narration slot must be
    # dropped so history is [system, user, assistant(tool_calls), tool,
    # assistant(final)] — no orphan assistant message.
    llm = StreamingFakeLLM([
        (["Let me look. "],
         LLMReply(text="Let me look.",
                  tool_calls=(ToolCall(id="c1", name="echo", arguments={"text": "x"}),))),
        (["Here it is."], LLMReply(text="Here it is.")),
    ])
    agent = make_agent(llm, tools=[EchoTool()])
    history: list[dict] = []
    events = [e async for e in agent.run_turn(history, "q")]
    roles = [(m["role"], "tool_calls" in m) for m in history]
    assert roles == [
        ("system", False),
        ("user", False),
        ("assistant", True),   # the tool_calls message
        ("tool", False),
        ("assistant", False),  # the final streamed answer
    ]
    assert history[-1]["content"] == "Here it is."


class BlockingStreamLLM:
    """Streams one sentence, then blocks forever — to simulate a barge-in
    mid-answer (the turn task is cancelled while streaming)."""

    def __init__(self):
        self.spoke = asyncio.Event()

    async def stream(self, messages, tools=None):
        yield ("text", "First part is here. ")
        self.spoke.set()
        await asyncio.Event().wait()  # hang until cancelled
        yield ("reply", LLMReply(text="never"))


async def test_barge_in_mid_stream_leaves_partial_answer_in_history():
    llm = BlockingStreamLLM()
    agent = make_agent(llm)
    history: list[dict] = []

    async def drive():
        async for _ in agent.run_turn(history, "q"):
            pass

    task = asyncio.create_task(drive())
    await asyncio.wait_for(llm.spoke.wait(), timeout=2)
    await asyncio.sleep(0)  # let the sentence flush into history
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # The partial spoken answer is recorded as a plain assistant message, so the
    # voice channel's truncate_last_assistant can rewrite it to what was heard.
    assert history[-1]["role"] == "assistant"
    assert history[-1]["content"] == "First part is here."


async def test_non_streaming_provider_in_voice_uses_whole_reply():
    # FakeLLM has no stream() -> voice falls back to the whole-reply path.
    llm = FakeLLM([LLMReply(text="One shot. Two shot.")])
    agent = make_agent(llm)
    events = [e async for e in agent.run_turn([], "q")]
    speech = [e.text for e in events if isinstance(e, SpeechChunk)]
    assert speech == ["One shot. Two shot."]  # single chunk, not per-sentence
