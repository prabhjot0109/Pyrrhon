"""run_voice: the Pipecat pipeline over the headless core.

local mic → Silero VAD → STT ([voice] stt_provider) → PyrrhonBridgeProcessor
→ TTS ([voice] tts_provider) → PlaybackObserver → local speakers

Error policy (spec): voice failures — no mic, missing key, missing audio
stack — degrade to text mode with a clear message via VoiceUnavailableError.
They never crash the app; the text channels keep working.

Pipecat 1.5.0 notes (verified against the installed sources): VAD is an
explicit VADProcessor stage (transport params no longer take a
vad_analyzer); the Groq segmented STT slices audio on the VAD frames that
stage broadcasts; interruptions are frames the bridge broadcasts itself,
not a PipelineParams switch.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

from pyrrhon.config.settings import Settings, VoiceSettings
from pyrrhon.core.events import Event
from pyrrhon.core.session import Session
from pyrrhon.voice.bridge import PlaybackObserver, PyrrhonBridgeProcessor
from pyrrhon.voice.playback import PlaybackTracker
from pyrrhon.voice.providers import VoiceUnavailableError, create_stt, create_tts


@contextlib.contextmanager
def speech_path(session: Session):
    """Split-path grounding + delivery policy (spec): while voice drives the
    session, the agent must never take the grounding retry loop — a retry costs
    a full LLM turnaround and breaks the latency budget. The grounding *gate*
    still runs; unverifiable file:line claims are stripped from speech and
    replaced with an honest 'I couldn't verify that.' We also mark the agent
    voice-active so run_turn appends the spoken (VOICE_STYLE) delivery instead
    of the written one; both flip back on /voice off."""
    previous_retry = session.agent.allow_retry
    previous_voice = session.agent.voice_active
    session.agent.allow_retry = False
    session.agent.voice_active = True
    try:
        yield
    finally:
        session.agent.allow_retry = previous_retry
        session.agent.voice_active = previous_voice


async def run_voice(
    session: Session,
    settings: Settings,
    *,
    on_event: Callable[[Event], None] | None = None,
) -> None:
    """Build and run the voice pipeline until cancelled (/voice off)."""
    # Provider factories run first: key checks fail with an actionable
    # message before any audio-stack import is attempted.
    voice = getattr(settings, "voice", None) or VoiceSettings()
    stt = create_stt(voice)
    tts = create_tts(voice)
    chars_per_sec = voice.chars_per_sec

    try:
        # Imported here, not at module top: the `local` extra (PyAudio) may
        # be absent; that must degrade, not crash at import time.
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineTask
        from pipecat.processors.audio.vad_processor import VADProcessor
        from pipecat.transports.local.audio import (
            LocalAudioTransport,
            LocalAudioTransportParams,
        )
    except ImportError as exc:
        raise VoiceUnavailableError(
            f"Voice dependencies missing ({exc}). "
            'Run: uv add "pipecat-ai[local,silero,groq,openai]" — staying in text mode.'
        ) from exc

    transport = LocalAudioTransport(
        LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2))
    )
    tracker = PlaybackTracker(chars_per_sec=chars_per_sec)
    bridge = PyrrhonBridgeProcessor(session, on_event=on_event, tracker=tracker)

    pipeline = Pipeline(
        [
            transport.input(),
            vad,
            stt,
            bridge,
            tts,
            PlaybackObserver(tracker),
            transport.output(),
        ]
    )
    task = PipelineTask(pipeline)
    # handle_sigint=False: required on Windows, and Pyrrhon owns its lifecycle
    # via /voice off, not Ctrl-C inside the pipeline.
    runner = PipelineRunner(handle_sigint=False)

    with speech_path(session):
        try:
            await runner.run(task)
        except Exception as exc:  # no mic / device died / provider hiccup
            # CancelledError is BaseException — /voice off passes through.
            raise VoiceUnavailableError(
                f"Voice pipeline failed ({exc}) — staying in text mode."
            ) from exc
