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
    TurnFinished,
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
    "read_image": "Taking a look at that image…",
    "grep": "Searching the repo for that…",
    "glob": "Looking for the right files…",
    "read_result": "Reading on from where that left off…",
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
    "explore": "Having a scout around the repo…",
}


# Spoken when the user has gone quiet for [voice] idle_timeout_sec (off by
# default). Distinct from FILLERS above, and the distinction is the whole
# point: a filler covers an *agent-is-thinking* gap, this covers a
# *user-has-gone-quiet* one. Pipecat's UserIdleController detects the silence
# (Layer C); the line itself is Layer A, because an unprompted sentence about
# the code is a grounding claim like any other.
#
# GROUNDING: same rule as TOOL_FILLERS — these bypass the gate, so they are
# fixed, citation-free strings and interpolate nothing. The same test covers
# both.
#
# The tuple's LENGTH is the nag cap. UserIdleController restarts its timer on
# every BotStoppedSpeakingFrame, so speaking rearms it: without a cap an
# unattended terminal would talk to itself forever. Two prompts, then silence
# until the user actually says something.
IDLE_LINES = (
    "Still there? I can keep going, or dig into whichever part you want.",
    "I'll leave it there — just say the word when you want to pick it up.",
)


# A pipecat service names itself "<Provider><KIND>Service#0" in its error
# frames, and every registry id is that prefix lowercased — the one exception,
# whisper-local, is on-device and never opens a socket to be rejected. Reading
# the row back is what lets the hint name a real key_env (HF_TOKEN, not the
# HUGGINGFACE_API_KEY that string surgery would have invented).
_SERVICE = re.compile(r"(\w+?)(STT|TTS)Service")
_HANDSHAKE = re.compile(r"rejected WebSocket connection: HTTP (\d+)")
# Authentication and billing. Everything else at handshake time is the id in
# the query string, because that is all the client sent.
_AUTH_CODES = {"401", "402", "403"}


def _handshake_hint(text: str) -> str | None:
    """One actionable line for a rejected websocket handshake, or None."""
    from pyrrhon.voice.registry import find

    rejection = _HANDSHAKE.search(text)
    service = _SERVICE.search(text)
    if not rejection or not service:
        return None
    code = rejection.group(1)
    kind = service.group(2).lower()
    provider = find(kind, service.group(1).lower())
    label = provider.label if provider else service.group(1)

    if code in _AUTH_CODES:
        if provider is None or provider.key_env is None:
            return f"{label} refused the connection (HTTP {code}) — check your account."
        return (
            f"{label} refused the connection (HTTP {code}). That is the key, not "
            f"the model: set {provider.key_env} with "
            f"/settings key {provider.key_env} <value>, then /voice off and "
            "/voice on."
        )

    setting = "stt_model" if kind == "stt" else "tts_voice"
    if provider is None:
        return (
            f"{label} rejected the connection (HTTP {code}) — [voice] {setting} "
            "is not an id it knows."
        )
    return (
        f"{label} rejected the connection (HTTP {code}). That is the id, not the "
        f"key: [voice] {setting} is not one {label} knows, which usually means "
        f"it was left over from a different provider. Run "
        f"/settings {kind} {provider.id} to fall back to its own default, or "
        f"/settings {kind} {provider.id} <id> to name one."
    )


def humanize_voice_error(text: str) -> str:
    """Turn a raw pipecat/provider ErrorFrame string into one actionable line.

    Two cases earn a translation. A Groq hosted model gated behind a one-time
    terms click buries an accept-terms URL inside a JSON blob; pull it out. And
    a websocket handshake rejection carries only a status code, which points at
    nothing — see _handshake_hint for why the config key is the answer."""
    if "terms acceptance" in text or "model_terms_required" in text:
        match = re.search(r"https?://\S+", text)
        link = match.group(0).rstrip("'\".,)") if match else "https://console.groq.com"
        return (
            f"This voice model needs a one-time terms acceptance. Open {link} , "
            "accept, then run /voice off and /voice on again "
            "(or switch TTS with /settings tts <provider>)."
        )
    hint = _handshake_hint(text)
    if hint is not None:
        return hint
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
        # How many re-engagement lines have been spoken since the user last
        # said anything. Reset when a transcription starts a turn.
        self._idle_prompts = 0
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

    async def speak_idle_prompt(self) -> None:
        """Re-engage after [voice] idle_timeout_sec of user silence.

        Wired to UserTurnProcessor's on_user_turn_idle in pipeline.py. Silent
        unless the config opts in, because an agent that speaks unprompted is a
        personality choice, not a default.

        Ephemeral like the filler: not registered with the PlaybackTracker, so
        a barge-in never folds it into the assistant message in history. It is
        an invitation to speak, not an answer.
        """
        if self._interruptible() or self._idle_prompts >= len(IDLE_LINES):
            return
        text = IDLE_LINES[self._idle_prompts]
        self._idle_prompts += 1
        self._on_event(SpeechChunk(text=text))
        await self.push_frame(TextFrame(text))

    def _start_turn(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._idle_prompts = 0  # the user is back; re-arm the full budget
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
            # Every exit path, barge-in included — which is the point of
            # putting it here rather than beside the frame below. A cancelled
            # turn never reaches that line, and a turn killed by barge-in is
            # exactly when a stranded spinner is most visible. A screen
            # channel cannot see this task, so this is the only way it learns
            # the turn is over.
            #
            # Unless this turn has already been replaced. _start_turn cancels
            # its predecessor and does *not* await it, so a superseded turn's
            # finally runs after the next turn has begun — and its report
            # would name the turn the screen is currently showing. Only the
            # bridge knows which task is the live one, so the check belongs
            # here rather than in whoever is listening.
            if self._turn_task is asyncio.current_task():
                self._on_event(TurnFinished())
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
