"""run_voice: the Pipecat pipeline over the headless core.

local mic → RNNoise → Silero VAD → UserTurnProcessor → STT ([voice]
stt_provider) → PyrrhonBridgeProcessor → TTS ([voice] tts_provider) →
PlaybackObserver → local speakers

Error policy (spec): voice failures — no mic, missing key, missing audio
stack — degrade to text mode with a clear message via VoiceUnavailableError.
They never crash the app; the text channels keep working.

Pipecat 1.7.0 notes (verified against the installed sources): VAD is an
explicit VADProcessor stage (transport params no longer take a
vad_analyzer); the Groq segmented STT slices audio on the VAD frames that
stage broadcasts; interruptions are frames the bridge broadcasts itself,
not a PipelineParams switch.

Assembly only. Which provider to build is registry.py's answer and how to
build it is factory.py's; this module decides what goes in the pipeline and
in what order.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

from pyrrhon.config.settings import Settings, VoiceSettings
from pyrrhon.core.events import Event
from pyrrhon.core.session import Session
from pyrrhon.voice.bridge import PlaybackObserver, PyrrhonBridgeProcessor
from pyrrhon.voice.factory import (
    VoiceUnavailableError,
    close_voice_service,
    create_stt,
    create_tts,
)
from pyrrhon.voice.playback import PlaybackTracker


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


def _user_turn_processor_cls():
    """Indirection so tests can substitute a fake without the audio stack."""
    from pipecat.turns.user_turn_processor import UserTurnProcessor

    return UserTurnProcessor


def _smart_turn_stop():
    """The semantic end-of-turn strategy, or None if its extra is absent.

    local-smart-turn pulls torch and ships in the `voice` extra, which CI does
    not install. Absence degrades to the timeout strategy rather than refusing
    to start — the same error policy as every other optional piece here.
    """
    try:
        from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
            LocalSmartTurnAnalyzerV3,
        )
        from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
            TurnAnalyzerUserTurnStopStrategy,
        )
    except ImportError:
        return None
    return TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())


def _turn_strategies(voice: VoiceSettings):
    """The stop strategy for the configured mode — always named explicitly.

    UserTurnStrategies defaults `stop` to the smart-turn analyzer when it is
    left None, so passing None in "vad" mode would silently turn the fallback
    back into the thing it is a fallback FROM. Both branches say what they mean.
    """
    from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
        SpeechTimeoutUserTurnStopStrategy,
    )
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    stop = _smart_turn_stop() if voice.turn_detection == "smart" else None
    return UserTurnStrategies(stop=[stop or SpeechTimeoutUserTurnStopStrategy()])


def _build_turn_processor(voice: VoiceSettings):
    """The smart-turn stage, or None when there is nothing for it to do.

    UserTurnProcessor is standalone: unlike the documented
    LLMContextAggregatorPair wiring, it needs no Pipecat LLM aggregators —
    which this pipeline deliberately does not have. It also carries the idle
    timer, so one processor covers both semantic end-of-turn and
    user-has-gone-quiet.

    Barge-in is NOT affected: this broadcasts UserStartedSpeakingFrame, while
    bridge.py keys off VADUserStartedSpeakingFrame, which fires earlier by
    design. Interruption latency is the product; do not unify the two.
    """
    if voice.turn_detection != "smart" and not voice.idle_timeout_sec:
        return None

    cls = _user_turn_processor_cls()
    return cls(
        user_turn_strategies=_turn_strategies(voice),
        user_idle_timeout=voice.idle_timeout_sec,
    )


def _build_observers(voice: VoiceSettings) -> list:
    """Pipecat's per-service latency observers.

    Complementary to core/telemetry.py, not a replacement: ours measures the
    agent loop, which Pipecat cannot see; these measure per-service TTFB/TTFA,
    which our single scalar cannot isolate.
    """
    if not voice.metrics:
        return []
    from pipecat.observers.turn_tracking_observer import TurnTrackingObserver
    from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver

    return [UserBotLatencyObserver(), TurnTrackingObserver()]


def _build_input_filter(voice: VoiceSettings):
    """RNNoise on the mic. Note the capitalization: RNNoiseFilter."""
    if not voice.noise_filter:
        return None
    try:
        from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter
    except ImportError:
        return None  # extra absent: run without it rather than refusing to start
    return RNNoiseFilter()


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
            "Run: uv sync --extra voice — staying in text mode."
        ) from exc

    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # Denoising the mic before VAD and STT see it: fewer false starts
            # on keyboard noise, which on this pipeline means fewer spurious
            # barge-ins.
            audio_in_filter=_build_input_filter(voice),
        )
    )
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2))
    )
    tracker = PlaybackTracker(chars_per_sec=chars_per_sec)
    bridge = PyrrhonBridgeProcessor(session, on_event=on_event, tracker=tracker)

    # Smart turn sits after VAD and before STT: it decides end-of-turn from
    # the speech VAD has already bracketed.
    turn = _build_turn_processor(voice)
    stages = [transport.input(), vad]
    if turn is not None:
        stages.append(turn)
    stages += [stt, bridge, tts, PlaybackObserver(tracker), transport.output()]

    pipeline = Pipeline(stages)
    task = PipelineTask(pipeline, observers=_build_observers(voice))
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
        finally:
            # Runs on /voice off (CancelledError) too — that is the path that
            # was leaking, since toggling voice is normal and repeated.
            await close_voice_service(tts)
            await close_voice_service(stt)
