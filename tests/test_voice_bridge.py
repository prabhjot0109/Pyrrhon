import asyncio
from pathlib import Path

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Citation, TruncateSpeech
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.session import INTERRUPTED_MARKER, Session
from pyrrhon.voice.bridge import PyrrhonBridgeProcessor
from pyrrhon.voice.playback import PlaybackTracker
from tests.helpers import FakeLLM
from tests.test_playback import FakeClock
from tests.test_session import SlowEchoTool

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"

DOWN = FrameDirection.DOWNSTREAM
UP = FrameDirection.UPSTREAM


class RecordingBridge(PyrrhonBridgeProcessor):
    """Test double: records pushed/broadcast frames instead of needing a live pipeline."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pushed: list = []
        self.interruptions_broadcast = 0

    async def push_frame(self, frame, direction=DOWN):
        self.pushed.append(frame)

    async def broadcast_interruption(self):
        self.interruptions_broadcast += 1


def make_bridge(replies, tools=(), clock=None):
    fake = FakeLLM(list(replies))
    agent = Agent(
        llm=fake,
        tools=list(tools),
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
    )
    session = Session(agent)
    seen_events: list = []
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock or FakeClock())
    bridge = RecordingBridge(session, on_event=seen_events.append, tracker=tracker)
    return bridge, session, seen_events


def transcription(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="local", timestamp="2026-07-03T00:00:00Z")


async def test_final_transcription_runs_a_turn_and_pushes_speech_to_tts():
    bridge, session, seen = make_bridge(
        [LLMReply(text="greet is defined at utils/helpers.py:1.")]
    )
    await bridge._handle_frame(transcription("where is greet defined"), DOWN)
    await asyncio.wait_for(bridge._turn_task, timeout=2)

    texts = [f.text for f in bridge.pushed if isinstance(f, TextFrame)]
    assert texts == ["greet is defined at utils/helpers.py:1."]
    kinds = [type(f) for f in bridge.pushed]
    assert kinds.index(LLMFullResponseStartFrame) < kinds.index(TextFrame)
    assert kinds.index(TextFrame) < kinds.index(LLMFullResponseEndFrame)
    # Screen-bound events went to the TUI callback, not to TTS:
    assert Citation(file="utils/helpers.py", line=1) in seen
    assert session.history[-1]["content"] == "greet is defined at utils/helpers.py:1."


async def test_interim_transcriptions_never_start_turns():
    bridge, session, _ = make_bridge([])
    frame = InterimTranscriptionFrame(
        text="where is", user_id="local", timestamp="2026-07-03T00:00:00Z"
    )
    await bridge._handle_frame(frame, DOWN)
    assert bridge._turn_task is None
    assert session.history == []


async def test_barge_in_mid_turn_cancels_tool_and_repairs_history():
    slow = SlowEchoTool()
    bridge, session, seen = make_bridge(
        [
            LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),)),
            LLMReply(text="never reached"),
        ],
        tools=[slow],
    )
    await bridge._handle_frame(transcription("take your time"), DOWN)
    await asyncio.wait_for(slow.started.wait(), timeout=2)

    # Pipecat 1.5.0: VAD-detected speech is the barge-in signal; the bridge
    # itself broadcasts the InterruptionFrame (no LLM aggregators in this
    # pipeline to do it for us).
    await bridge._handle_frame(VADUserStartedSpeakingFrame(), DOWN)
    for _ in range(5):
        await asyncio.sleep(0)

    assert slow.completed is False
    assert [m["role"] for m in session.history] == ["system", "user"]
    truncates = [e for e in seen if isinstance(e, TruncateSpeech)]
    assert truncates == [TruncateSpeech(played_text="")]  # nothing was spoken yet
    # The bridge told the rest of the pipeline (TTS, output) to flush:
    assert bridge.interruptions_broadcast == 1
    # The VAD frame still travelled on (STT segmentation upstream relies on it):
    assert any(isinstance(f, VADUserStartedSpeakingFrame) for f in bridge.pushed)


async def test_external_interruption_frame_is_acted_on_and_passed_through():
    slow = SlowEchoTool()
    bridge, session, seen = make_bridge(
        [LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),))],
        tools=[slow],
    )
    await bridge._handle_frame(transcription("take your time"), DOWN)
    await asyncio.wait_for(slow.started.wait(), timeout=2)

    await bridge._handle_frame(InterruptionFrame(), DOWN)
    for _ in range(5):
        await asyncio.sleep(0)

    assert [m["role"] for m in session.history] == ["system", "user"]
    # Already an InterruptionFrame — passed through, not re-broadcast:
    assert bridge.interruptions_broadcast == 0
    assert any(isinstance(f, InterruptionFrame) for f in bridge.pushed)


async def test_barge_in_during_playback_truncates_to_played_estimate():
    clock = FakeClock(0.0)
    bridge, session, seen = make_bridge(
        [LLMReply(text="alpha beta gamma delta")], clock=clock
    )
    await bridge._handle_frame(transcription("talk to me"), DOWN)
    await asyncio.wait_for(bridge._turn_task, timeout=2)

    await bridge._handle_frame(BotStartedSpeakingFrame(), UP)
    clock.now = 1.2  # 12 chars at 10 chars/sec → "alpha beta"
    await bridge._handle_frame(VADUserStartedSpeakingFrame(), DOWN)

    assert session.history[-1]["content"] == "alpha beta" + INTERRUPTED_MARKER
    assert TruncateSpeech(played_text="alpha beta") in seen
    assert bridge.interruptions_broadcast == 1


async def test_barge_in_while_idle_is_ignored():
    bridge, session, seen = make_bridge([LLMReply(text="alpha beta")])
    await bridge._handle_frame(transcription("first question"), DOWN)
    await asyncio.wait_for(bridge._turn_task, timeout=2)
    # Bot finished and is silent. The user simply starts the next turn —
    # VAD still fires, but there is nothing to abort and the fully-heard
    # assistant message must NOT be rewritten.
    await bridge._handle_frame(VADUserStartedSpeakingFrame(), DOWN)

    assert session.history[-1]["content"] == "alpha beta"
    assert not [e for e in seen if isinstance(e, TruncateSpeech)]
    assert bridge.interruptions_broadcast == 0
