"""The bridge between Pipecat's frame world and Pyrrhon's event world.

Pipeline position: transport.input() → VAD → STT → PyrrhonBridgeProcessor
→ TTS → PlaybackObserver → transport.output().

Downstream through the bridge: final TranscriptionFrames (consumed — they
become agent turns) and VAD/interruption frames (acted on, then passed
through so the TTS/output flush). Upstream through the bridge:
Bot*SpeakingFrames from the output transport (observed for playback
timing, passed through).

Barge-in (spec, real-time discipline): this pipeline has no Pipecat LLM
aggregators, so nothing else turns VAD speech into an interruption — the
bridge does it. On VADUserStartedSpeakingFrame while a turn is in flight
or the bot is speaking, it broadcasts an InterruptionFrame (flushes TTS
and the output transport), cancels the in-flight turn
(Session.abort_current_turn — in-flight tool calls die), computes how much
prose was actually played (PlaybackTracker), rewrites the last assistant
message to exactly that (Session.truncate_last_assistant), and reports
TruncateSpeech to the screen channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable

from pyrrhon.voice._logging import route_pipecat_logs_to_file

# Must run before the first pipecat import below: pipecat's loguru sink grabs
# the live terminal on import, so redirect it to a file first (see _logging).
route_pipecat_logs_to_file()

from pipecat.frames.frames import (  # noqa: E402 — intentional: after log routing
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSTextFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from pyrrhon.core.events import (
    Event,
    SpeechChunk,
    ToolCallStarted,
    Transcription,
    TruncateSpeech,
    VoiceNotice,
)
from pyrrhon.core.session import Session
from pyrrhon.voice.playback import PlaybackTracker

# If the agent produces no speech within this window (a slow first LLM reply, a
# long tool run before any narration), speak a short filler so the user never
# hears dead air — the Azure Voice Live / Amazon Lex "interim response" pattern.
# Complements the speech-first prompt: that covers mid-turn tool narration; this
# covers the initial gap nothing else fills.
# 1.2s, down from 1.6s: with streaming on every channel the model's own
# narration usually beats the watchdog now, so the filler only has to cover
# the genuinely slow starts — and when it does fire, firing sooner is the
# whole point.
FILLER_DELAY_SEC = 1.2
FILLERS = (
    "Let me take a look…",
    "One sec, digging into that…",
    "Looking through the code now…",
)

# Spoken instead of the generic line when a tool is already running, so the
# filler describes what is actually happening rather than sounding canned.
#
# GROUNDING: the filler bypasses the gate entirely — it is pushed as a raw
# SpeechChunk — so these must be citation-free BY CONSTRUCTION. They are fixed
# strings keyed on the tool NAME and never interpolate the tool's arguments.
# "Reading the file now" is safe; "reading loop.py line 193" would be an
# ungated claim about the code. A test asserts none of them can carry a
# path:line.
TOOL_FILLERS = {
    "read_file": "Reading that file now…",
    "grep": "Searching the repo for that…",
    "glob": "Looking for the right files…",
    "find_symbol": "Finding where that's defined…",
    "symbol_context": "Pulling up that symbol and what calls it…",
    "list_dependencies": "Tracing the imports…",
    "repo_map": "Getting the lay of the land…",
    "git_log": "Checking the history…",
    "git_blame": "Seeing who last touched that…",
    "git_show": "Pulling up that commit…",
    "web_search": "Searching the web…",
    "web_fetch": "Fetching that page…",
    "remember": "Noting that down…",
    "write_spec": "Writing that up…",
    "think_deeper": "Thinking this one through properly…",
}


def humanize_voice_error(text: str) -> str:
    """Turn a raw pipecat/provider ErrorFrame string into one actionable line.

    The common case here is a Groq hosted TTS/STT model gated behind a
    one-time terms click — the raw 400 buries an accept-terms URL inside a
    JSON blob. Pull it out and tell the user exactly what to do."""
    if "terms acceptance" in text or "model_terms_required" in text:
        match = re.search(r"https?://\S+", text)
        link = match.group(0).rstrip("'\".,)") if match else "https://console.groq.com"
        return (
            f"This voice model needs a one-time terms acceptance. Open {link} , "
            "accept, then run /voice off and /voice on again "
            "(or switch TTS with /settings tts <provider>)."
        )
    return f"Voice pipeline error: {text}"


class PyrrhonBridgeProcessor(FrameProcessor):
    def __init__(
        self,
        session: Session,
        *,
        on_event: Callable[[Event], None] | None = None,
        tracker: PlaybackTracker | None = None,
    ):
        super().__init__()
        self._session = session
        self._on_event: Callable[[Event], None] = on_event or (lambda event: None)
        self.tracker = tracker or PlaybackTracker()
        self._turn_task: asyncio.Task | None = None
        self._bot_speaking = False
        self._spoke_this_turn = False
        self._filler_idx = 0
        # Name of the most recent tool this turn, so the filler can describe
        # what is actually happening. Reset per turn.
        self._last_tool: str | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self._handle_frame(frame, direction)

    async def _handle_frame(self, frame: Frame, direction: FrameDirection):
        """All M3 logic lives here so unit tests can drive it without a
        linked pipeline (base process_frame does lifecycle bookkeeping)."""
        if isinstance(frame, VADUserStartedSpeakingFrame):
            # The barge-in signal. Only an actual barge-in (something to
            # interrupt) becomes an interruption; otherwise the user is just
            # starting an ordinary next turn.
            if self._interruptible():
                await self._on_interruption()
                await self.broadcast_interruption()  # flush TTS + output
            await self.push_frame(frame, direction)
        elif isinstance(frame, InterruptionFrame):
            # Interruption initiated elsewhere in the pipeline: act, pass on.
            await self._on_interruption()
            await self.push_frame(frame, direction)
        elif isinstance(frame, TranscriptionFrame):
            # Show the user what was heard (closes the feedback loop), then
            # run the turn. Consumed: the utterance IS the turn, not passed on.
            self._on_event(Transcription(text=frame.text))
            self._start_turn(frame.text)
        elif isinstance(frame, InterimTranscriptionFrame):
            pass  # partials never start turns
        elif isinstance(frame, ErrorFrame):
            # A provider error (e.g. TTS 400) is non-fatal to Pipecat and only
            # hits the log file — surface it so voice never fails silently.
            self._on_event(
                VoiceNotice(humanize_voice_error(str(frame.error)), is_error=True)
            )
            await self.push_frame(frame, direction)  # let the task see it too
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self.tracker.playback_started()
            await self.push_frame(frame, direction)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self.tracker.playback_finished()
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    def _interruptible(self) -> bool:
        turn_running = self._turn_task is not None and not self._turn_task.done()
        return turn_running or self._bot_speaking

    def _start_turn(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._turn_task is not None and not self._turn_task.done():
            # Defensive: a transcription raced ahead of its interruption.
            self._turn_task.cancel()
            self._session.abort_current_turn()
        self.tracker.reset()
        self._turn_task = asyncio.create_task(self._run_turn(text))

    async def _run_turn(self, text: str) -> None:
        await self.push_frame(LLMFullResponseStartFrame())
        self._spoke_this_turn = False
        self._last_tool = None
        filler = asyncio.create_task(self._filler_watchdog())
        try:
            async for event in self._session.run_turn(text):
                if isinstance(event, ToolCallStarted):
                    # Name only — never event.args. See TOOL_FILLERS.
                    self._last_tool = event.name
                    self._on_event(event)
                elif isinstance(event, SpeechChunk):
                    # First real speech cancels the pending filler.
                    if not self._spoke_this_turn:
                        self._spoke_this_turn = True
                        filler.cancel()
                    # Speakable prose → TTS *and* the screen, so the transcript
                    # shows what Pyrrhon is saying, not just plays it.
                    self.tracker.speech_queued(event.text)
                    self._on_event(event)
                    await self.push_frame(TextFrame(event.text))
                else:
                    self._on_event(event)
        finally:
            filler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await filler
        await self.push_frame(LLMFullResponseEndFrame())

    async def _filler_watchdog(self) -> None:
        """Speak one bridging line if the agent is still silent after the delay.

        Ephemeral: the filler is NOT registered with the PlaybackTracker, so a
        barge-in's truncate_last_assistant never folds it into the grounded
        assistant message in history — it is throwaway audio, not an answer.
        """
        try:
            await asyncio.sleep(FILLER_DELAY_SEC)
        except asyncio.CancelledError:
            return
        if self._spoke_this_turn:
            return
        # If a tool is already running, say what it is doing. Falls back to the
        # rotating generic lines when the delay elapsed before any tool call —
        # a slow first LLM reply, which is the other case this covers.
        text = TOOL_FILLERS.get(self._last_tool or "")
        if text is None:
            text = FILLERS[self._filler_idx % len(FILLERS)]
            self._filler_idx += 1
        self._on_event(SpeechChunk(text=text))
        await self.push_frame(TextFrame(text))

    async def _on_interruption(self) -> None:
        if not self._interruptible():
            # Bot idle, nothing in flight: never rewrite a fully-heard answer.
            return
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
        self._session.abort_current_turn()
        played = self.tracker.played_text()
        self._session.truncate_last_assistant(played)
        self._on_event(TruncateSpeech(played_text=played))
        self._bot_speaking = False
        self.tracker.reset()


class PlaybackObserver(FrameProcessor):
    """Between TTS and transport.output(): records word-timestamped playback.

    OpenAI TTS emits no TTSTextFrames, so with the M3 default stack this
    records nothing and the tracker's duration estimate is used. Dropping in
    a word-timestamp-capable TTS (Cartesia/ElevenLabs/Hume) makes truncation
    word-accurate with zero further changes.
    """

    def __init__(self, tracker: PlaybackTracker):
        super().__init__()
        self._tracker = tracker

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame):
            self._tracker.word_played(frame.text)
        await self.push_frame(frame, direction)
