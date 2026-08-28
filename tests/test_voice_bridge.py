import asyncio
import contextlib
from pathlib import Path

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    ErrorFrame,
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
from pyrrhon.core.events import (
    Citation,
    SpeechChunk,
    Transcription,
    TruncateSpeech,
    TurnFinished,
    VoiceNotice,
)
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.session import INTERRUPTED_MARKER, Session
from pyrrhon.voice.bridge import PyrrhonBridgeProcessor, humanize_voice_error
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


async def test_transcription_and_answer_both_reach_the_screen():
    # The screen must show BOTH what STT heard and what Pyrrhon says back —
    # otherwise a working voice loop looks like a dead mic.
    bridge, session, seen = make_bridge([LLMReply(text="It defines a greeting.")])
    await bridge._handle_frame(transcription("what does this do"), DOWN)
    await asyncio.wait_for(bridge._turn_task, timeout=2)

    assert Transcription(text="what does this do") in seen
    assert SpeechChunk(text="It defines a greeting.") in seen
    # The user transcription is emitted before the turn's answer.
    assert seen.index(Transcription(text="what does this do")) < seen.index(
        SpeechChunk(text="It defines a greeting.")
    )
    # Still spoken, too (regression guard on the TTS path):
    assert any(
        isinstance(f, TextFrame) and f.text == "It defines a greeting."
        for f in bridge.pushed
    )


async def test_error_frame_surfaces_actionable_notice():
    bridge, session, seen = make_bridge([])
    raw = (
        "Error code: 400 - {'error': {'message': 'The model requires terms "
        "acceptance. Please accept at https://console.groq.com/playground?"
        "model=canopylabs%2Forpheus-v1-english', 'code': 'model_terms_required'}}"
    )
    await bridge._handle_frame(ErrorFrame(error=raw, fatal=False), DOWN)

    notices = [e for e in seen if isinstance(e, VoiceNotice)]
    assert len(notices) == 1
    assert notices[0].is_error is True
    assert "console.groq.com/playground" in notices[0].text
    assert "/voice off" in notices[0].text
    # The error frame is still passed on so the pipeline task can react.
    assert any(isinstance(f, ErrorFrame) for f in bridge.pushed)


def test_humanize_voice_error_extracts_terms_link_or_falls_back():
    terms = humanize_voice_error(
        "requires terms acceptance at https://console.groq.com/x?m=y', 'code': 1"
    )
    assert "https://console.groq.com/x?m=y" in terms
    assert "terms acceptance" in terms
    # Non-terms errors pass through as a plain pipeline-error line.
    assert humanize_voice_error("connection reset") == (
        "Voice pipeline error: connection reset"
    )


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


async def test_filler_speaks_when_the_agent_is_slow(monkeypatch):
    import pyrrhon.voice.bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "FILLER_DELAY_SEC", 0.02)
    slow = SlowEchoTool()
    bridge, session, seen = make_bridge(
        [LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),))],
        tools=[slow],
    )
    await bridge._handle_frame(transcription("walk me through the whole thing"), DOWN)
    await asyncio.wait_for(slow.started.wait(), timeout=2)
    await asyncio.sleep(0.06)  # let the filler watchdog fire while the tool hangs

    filler_texts = [f.text for f in bridge.pushed if isinstance(f, TextFrame)]
    assert any(t in bridge_mod.FILLERS for t in filler_texts)
    # Ephemeral: the filler never lands in the grounded history.
    assert all(
        m.get("content") not in bridge_mod.FILLERS for m in session.history
    )

    bridge._turn_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await bridge._turn_task


async def test_no_filler_when_the_answer_is_immediate(monkeypatch):
    import pyrrhon.voice.bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "FILLER_DELAY_SEC", 0.05)
    bridge, session, seen = make_bridge([LLMReply(text="Quick answer.")])
    await bridge._handle_frame(transcription("hi"), DOWN)
    await asyncio.wait_for(bridge._turn_task, timeout=2)
    await asyncio.sleep(0.1)  # well past the filler delay

    texts = [f.text for f in bridge.pushed if isinstance(f, TextFrame)]
    assert texts == ["Quick answer."]  # no filler crept in


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


# -- tool-aware filler (M10 Stage 4.2) --------------------------------------


class SlowNamedTool(SlowEchoTool):
    """A hanging tool that can pose as any belt member."""

    def __init__(self, name: str):
        super().__init__()
        self.name = name


async def _fire_filler(monkeypatch, tool_name: str):
    import pyrrhon.voice.bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "FILLER_DELAY_SEC", 0.02)
    tool = SlowNamedTool(tool_name)
    bridge, session, _seen = make_bridge(
        [LLMReply(tool_calls=(ToolCall(id="c1", name=tool_name, arguments={"path": "secret/x.py"}),))],
        tools=[tool],
    )
    await bridge._handle_frame(transcription("walk me through it"), DOWN)
    await asyncio.wait_for(tool.started.wait(), timeout=2)
    await asyncio.sleep(0.06)
    texts = [f.text for f in bridge.pushed if isinstance(f, TextFrame)]
    bridge._turn_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await bridge._turn_task
    return texts


async def test_filler_describes_the_running_tool(monkeypatch):
    import pyrrhon.voice.bridge as bridge_mod

    texts = await _fire_filler(monkeypatch, "read_file")
    assert bridge_mod.TOOL_FILLERS["read_file"] in texts


async def test_tool_filler_never_leaks_the_arguments(monkeypatch):
    """The filler bypasses the grounding gate — it is pushed as a raw
    SpeechChunk — so it must be citation-free BY CONSTRUCTION. The tool was
    called with path='secret/x.py'; none of that may be spoken."""
    texts = await _fire_filler(monkeypatch, "grep")
    assert not any("secret/x.py" in t for t in texts)


def test_no_tool_filler_can_carry_a_citation():
    """A static guard on the table itself: fixed strings keyed on the tool
    name, never interpolated, so none of them can contain a path:line."""
    import re

    import pyrrhon.voice.bridge as bridge_mod

    for name, text in bridge_mod.TOOL_FILLERS.items():
        assert not re.search(r"\S+\.\w+:\d+", text), name
        assert "{" not in text and "%" not in text, name  # no format placeholders


def test_tool_fillers_cover_the_whole_belt():
    """A new tool should get a line rather than silently falling back to the
    generic filler."""
    import pyrrhon.voice.bridge as bridge_mod
    from tests.test_safety import EXPECTED_BELT

    assert EXPECTED_BELT <= set(bridge_mod.TOOL_FILLERS)


# -- Idle re-engagement ------------------------------------------------------
#
# [voice] idle_timeout_sec is off by default. These pin what happens when it
# is not, because a config key whose handler is missing is a key that lies.

async def test_idle_prompt_speaks_and_then_stops_nagging():
    """The nag cap is the tuple's length, and it matters: pipecat rearms the
    idle timer on every BotStoppedSpeakingFrame, so speaking restarts it."""
    import pyrrhon.voice.bridge as bridge_mod

    bridge, _session, seen = make_bridge([])
    for _ in range(len(bridge_mod.IDLE_LINES) + 3):
        await bridge.speak_idle_prompt()

    spoken = [f.text for f in bridge.pushed if isinstance(f, TextFrame)]
    assert spoken == list(bridge_mod.IDLE_LINES)
    assert [e.text for e in seen if isinstance(e, SpeechChunk)] == spoken


async def test_the_user_speaking_re_arms_the_idle_budget():
    import pyrrhon.voice.bridge as bridge_mod

    bridge, _session, _seen = make_bridge([LLMReply(text="Sure.")])
    for _ in range(5):
        await bridge.speak_idle_prompt()
    await bridge._handle_frame(transcription("carry on"), DOWN)
    await bridge._turn_task
    bridge.pushed.clear()

    await bridge.speak_idle_prompt()
    spoken = [f.text for f in bridge.pushed if isinstance(f, TextFrame)]
    assert spoken == [bridge_mod.IDLE_LINES[0]]


async def test_no_idle_prompt_while_a_turn_is_running():
    """Re-engaging over the agent's own answer would be worse than silence."""
    bridge, _session, _seen = make_bridge([LLMReply(text="Thinking.")])
    bridge._bot_speaking = True
    await bridge.speak_idle_prompt()
    assert not [f for f in bridge.pushed if isinstance(f, TextFrame)]


def test_no_idle_line_can_carry_a_citation():
    """Same static guard as TOOL_FILLERS: these bypass the gate too."""
    import re

    import pyrrhon.voice.bridge as bridge_mod

    for text in bridge_mod.IDLE_LINES:
        assert not re.search(r"\S+\.\w+:\d+", text), text
        assert "{" not in text and "%" not in text, text


async def test_the_bridge_reports_the_end_of_a_turn():
    """A screen channel cannot see the turn task, so without this its spinner
    never stops and its status bar keeps saying Pyrrhon is speaking."""
    bridge, session, seen = make_bridge([LLMReply(text="alpha beta")])
    await bridge._handle_frame(transcription("a question"), DOWN)
    await asyncio.wait_for(bridge._turn_task, timeout=2)

    assert isinstance(seen[-1], TurnFinished), (
        f"the end of the turn is reported last, got {seen[-1]!r}"
    )
    assert sum(isinstance(e, TurnFinished) for e in seen) == 1


async def test_a_cancelled_turn_still_reports_its_end():
    """Barge-in cancels the turn task outright. That is the exit path where a
    stranded spinner is most visible, so it is the one that must report."""
    slow = SlowEchoTool()
    bridge, session, seen = make_bridge(
        [LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),))],
        tools=[slow],
    )
    await bridge._handle_frame(transcription("take your time"), DOWN)
    await asyncio.wait_for(slow.started.wait(), timeout=2)

    await bridge._handle_frame(VADUserStartedSpeakingFrame(), DOWN)
    for _ in range(5):
        await asyncio.sleep(0)

    assert any(isinstance(e, TurnFinished) for e in seen), (
        "a turn killed by barge-in still ended"
    )


async def test_a_superseded_turn_does_not_report_the_end_of_the_live_one():
    """_start_turn cancels its predecessor without awaiting it, so the old
    turn's `finally` runs after the new one is already the turn in flight.
    Reporting there tells a screen channel that the turn it is *currently*
    showing has ended, and its spinner stops mid-answer.

    The supersede is staged by hand rather than by pushing a second
    transcription: `_start_turn`'s own defensive path cannot actually start a
    replacement, because `Session.run_turn` refuses while `_current` is
    merely cancelled and not yet done. That is a separate, pre-existing bug
    and not what this pins.
    """
    slow = SlowEchoTool()
    bridge, session, seen = make_bridge(
        [LLMReply(tool_calls=(ToolCall(id="c1", name="slow_echo", arguments={}),))],
        tools=[slow],
    )
    await bridge._handle_frame(transcription("take your time"), DOWN)
    await asyncio.wait_for(slow.started.wait(), timeout=2)
    superseded = bridge._turn_task

    replacement = asyncio.create_task(asyncio.sleep(30))
    bridge._turn_task = replacement
    superseded.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await superseded

    assert not any(isinstance(e, TurnFinished) for e in seen), (
        "the superseded turn reported the end of the turn that replaced it"
    )
    replacement.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await replacement
