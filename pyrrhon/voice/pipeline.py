"""run_voice: the Pipecat pipeline over the headless core.

local mic → Silero VAD → Groq Whisper STT → PyrrhonBridgeProcessor
→ OpenAI TTS → PlaybackObserver → local speakers

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
import os
from collections.abc import Callable

from pyrrhon.config.settings import Settings
from pyrrhon.core.events import Event
from pyrrhon.core.session import Session
from pyrrhon.voice.bridge import PlaybackObserver, PyrrhonBridgeProcessor
from pyrrhon.voice.playback import PlaybackTracker


class VoiceUnavailableError(RuntimeError):
    """Voice could not start or died; the caller stays in text mode."""


@contextlib.contextmanager
def speech_path(session: Session):
    """Split-path grounding policy (spec): while voice drives the session,
    the agent must never take the grounding retry loop — a retry costs a
    full LLM turnaround and breaks the latency budget. The grounding *gate*
    still runs; unverifiable file:line claims are stripped from speech and
    replaced with an honest 'I couldn't verify that.'"""
    previous = session.agent.allow_retry
    session.agent.allow_retry = False
    try:
        yield
    finally:
        session.agent.allow_retry = previous


def _require_env(name: str, what: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise VoiceUnavailableError(
            f"{what} needs {name} set — staying in text mode."
        )
    return value


async def run_voice(
    session: Session,
    settings: Settings,
    *,
    on_event: Callable[[Event], None] | None = None,
) -> None:
    """Build and run the voice pipeline until cancelled (/voice off)."""
    groq_key = _require_env("GROQ_API_KEY", "Groq Whisper STT")
    openai_key = _require_env("OPENAI_API_KEY", "OpenAI TTS")

    try:
        # Imported here, not at module top: the `local` extra (PyAudio) may
        # be absent; that must degrade, not crash at import time.
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineTask
        from pipecat.processors.audio.vad_processor import VADProcessor
        from pipecat.services.groq.stt import GroqSTTService
        from pipecat.services.openai.tts import OpenAITTSService
        from pipecat.transports.local.audio import (
            LocalAudioTransport,
            LocalAudioTransportParams,
        )
    except ImportError as exc:
        raise VoiceUnavailableError(
            f"Voice dependencies missing ({exc}). "
            'Run: uv add "pipecat-ai[local,silero,groq,openai]" — staying in text mode.'
        ) from exc

    voice = getattr(settings, "voice", None)
    stt_model = voice.stt_model if voice else "whisper-large-v3-turbo"
    tts_voice = voice.tts_voice if voice else "nova"
    chars_per_sec = voice.chars_per_sec if voice else 15.0

    transport = LocalAudioTransport(
        LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2))
    )
    stt = GroqSTTService(api_key=groq_key, model=stt_model)
    tts = OpenAITTSService(api_key=openai_key, voice=tts_voice)
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
