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
from collections.abc import Callable

from pyrrhon.voice._logging import route_pipecat_logs_to_file

# Must run before the first pipecat import below: pipecat's loguru sink grabs
# the live terminal on import, so redirect it to a file first (see _logging).
route_pipecat_logs_to_file()

from pipecat.frames.frames import (  # noqa: E402 — intentional: after log routing
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
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

from pyrrhon.core.events import Event, SpeechChunk, TruncateSpeech
from pyrrhon.core.session import Session
from pyrrhon.voice.playback import PlaybackTracker


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
            self._start_turn(frame.text)  # consumed: the utterance IS the turn
        elif isinstance(frame, InterimTranscriptionFrame):
            pass  # partials never start turns
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
        async for event in self._session.run_turn(text):
            if isinstance(event, SpeechChunk):
                # Speakable prose → TTS. Everything else is screen-bound.
                self.tracker.speech_queued(event.text)
                await self.push_frame(TextFrame(event.text))
            else:
                self._on_event(event)
        await self.push_frame(LLMFullResponseEndFrame())

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
