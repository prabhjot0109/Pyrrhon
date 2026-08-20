import asyncio
import sys
from types import SimpleNamespace

import pytest

import pyrrhon.voice.pipeline as pipeline_mod
from pyrrhon.voice import VoiceController
from pyrrhon.voice.pipeline import VoiceUnavailableError, run_voice, speech_path


def fake_session() -> SimpleNamespace:
    # Duck-typed stand-in: run_voice/speech_path only touch .agent.allow_retry
    # and .agent.voice_active before any audio work happens.
    return SimpleNamespace(agent=SimpleNamespace(allow_retry=True, voice_active=False))


async def test_missing_groq_key_degrades_with_clear_message(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(VoiceUnavailableError, match="GROQ_API_KEY"):
        await run_voice(fake_session(), SimpleNamespace(voice=None))


async def test_missing_openai_key_degrades_with_clear_message(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(VoiceUnavailableError, match="OPENAI_API_KEY"):
        await run_voice(fake_session(), SimpleNamespace(voice=None))


def test_speech_path_disables_retry_and_restores_even_on_error():
    session = fake_session()
    with speech_path(session):
        assert session.agent.allow_retry is False
        # Voice drives -> spoken delivery style.
        assert session.agent.voice_active is True
    assert session.agent.allow_retry is True
    assert session.agent.voice_active is False

    with pytest.raises(RuntimeError):
        with speech_path(session):
            assert session.agent.allow_retry is False
            assert session.agent.voice_active is True
            raise RuntimeError("pipeline blew up")
    assert session.agent.allow_retry is True
    assert session.agent.voice_active is False


async def test_controller_start_stop_toggles_background_task(monkeypatch):
    ran = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_run_voice(session, settings, *, on_event=None):
        ran.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(pipeline_mod, "run_voice", fake_run_voice)
    controller = VoiceController(fake_session(), SimpleNamespace(voice=None))

    message = controller.start()
    assert "on" in message.lower()
    await asyncio.wait_for(ran.wait(), timeout=2)
    assert controller.running is True
    assert "already" in controller.start().lower()  # double-start is friendly

    message = await controller.stop()
    assert "off" in message.lower()
    await asyncio.wait_for(cancelled.wait(), timeout=2)
    assert controller.running is False
    assert "not" in (await controller.stop()).lower()  # double-stop is friendly


async def test_controller_reports_unavailable_voice_instead_of_crashing(monkeypatch):
    async def failing_run_voice(session, settings, *, on_event=None):
        raise VoiceUnavailableError(
            "Groq Whisper STT needs GROQ_API_KEY set — staying in text mode."
        )

    notices: list[str] = []
    monkeypatch.setattr(pipeline_mod, "run_voice", failing_run_voice)
    controller = VoiceController(
        fake_session(), SimpleNamespace(voice=None), notify=notices.append
    )
    controller.start()
    for _ in range(10):
        await asyncio.sleep(0)
    assert controller.running is False
    assert notices and "GROQ_API_KEY" in notices[0]


# -- M15a: smart turn detection ---------------------------------------------


def _voice(**kwargs):
    from pyrrhon.config.settings import VoiceSettings

    return VoiceSettings(**kwargs)


def test_smart_turn_is_the_default():
    assert _voice().turn_detection == "smart"


def test_pipeline_places_a_user_turn_processor_after_vad(monkeypatch):
    """Smart turn runs in UserTurnProcessor, which needs no LLM aggregators."""
    from pyrrhon.voice.pipeline import _build_turn_processor

    built = {}

    class FakeTurnProcessor:
        def __init__(self, **kwargs):
            built.update(kwargs)

    monkeypatch.setattr(
        "pyrrhon.voice.pipeline._user_turn_processor_cls", lambda: FakeTurnProcessor
    )
    # Faked too: building the real strategies loads LocalSmartTurnAnalyzerV3,
    # which needs torch from the `voice` extra that CI does not install.
    monkeypatch.setattr(
        "pyrrhon.voice.pipeline._turn_strategies", lambda voice: "strategies"
    )
    processor = _build_turn_processor(
        _voice(turn_detection="smart", idle_timeout_sec=12.0)
    )

    assert isinstance(processor, FakeTurnProcessor)
    assert built["user_idle_timeout"] == 12.0
    assert built["user_turn_strategies"] is not None


def test_vad_mode_builds_no_turn_analyzer():
    from pyrrhon.voice.pipeline import _build_turn_processor

    assert _build_turn_processor(_voice(turn_detection="vad", idle_timeout_sec=0.0)) is None


def test_vad_mode_with_an_idle_timer_does_not_smuggle_smart_turn_back(monkeypatch):
    """UserTurnStrategies DEFAULTS to smart turn when stop=None.

    So "vad" plus an idle timeout must name its stop strategy explicitly, or
    the fallback silently becomes the thing it is a fallback from.
    """
    from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
        SpeechTimeoutUserTurnStopStrategy,
    )

    from pyrrhon.voice.pipeline import _build_turn_processor

    built = {}

    class FakeTurnProcessor:
        def __init__(self, **kwargs):
            built.update(kwargs)

    monkeypatch.setattr(
        "pyrrhon.voice.pipeline._user_turn_processor_cls", lambda: FakeTurnProcessor
    )
    _build_turn_processor(_voice(turn_detection="vad", idle_timeout_sec=30.0))

    stop = built["user_turn_strategies"].stop
    assert len(stop) == 1
    assert isinstance(stop[0], SpeechTimeoutUserTurnStopStrategy)


def test_smart_turn_degrades_to_the_timeout_strategy_without_its_extra(monkeypatch):
    """local-smart-turn pulls torch and is not installed in CI.

    Its absence must degrade, not crash: the same error policy that keeps a
    missing audio stack out of the text channels.
    """
    from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
        SpeechTimeoutUserTurnStopStrategy,
    )

    from pyrrhon.voice.pipeline import _turn_strategies

    monkeypatch.setitem(
        sys.modules, "pipecat.audio.turn.smart_turn.local_smart_turn_v3", None
    )
    stop = _turn_strategies(_voice(turn_detection="smart")).stop
    assert len(stop) == 1
    assert isinstance(stop[0], SpeechTimeoutUserTurnStopStrategy)


# -- M15a: noise filter and per-service latency observers --------------------


def test_observers_are_built_when_metrics_enabled():
    from pyrrhon.voice.pipeline import _build_observers

    assert len(_build_observers(_voice(metrics=True))) == 2
    assert _build_observers(_voice(metrics=False)) == []


def test_noise_filter_is_skipped_when_disabled():
    from pyrrhon.voice.pipeline import _build_input_filter

    assert _build_input_filter(_voice(noise_filter=False)) is None


def test_noise_filter_degrades_when_its_extra_is_absent(monkeypatch):
    """rnnoise ships in the `voice` extra; run without it rather than refuse."""
    from pyrrhon.voice.pipeline import _build_input_filter

    monkeypatch.setitem(sys.modules, "pipecat.audio.filters.rnnoise_filter", None)
    assert _build_input_filter(_voice(noise_filter=True)) is None


def test_tracing_is_off_unless_explicitly_enabled(monkeypatch):
    """Opt-in: the default config must not reach for an exporter at all."""
    from pyrrhon.config.settings import TelemetrySettings
    from pyrrhon.voice.pipeline import _setup_tracing

    called = []
    monkeypatch.setitem(sys.modules, "pipecat.utils.tracing.setup", None)
    _setup_tracing(SimpleNamespace(telemetry=TelemetrySettings()))
    _setup_tracing(SimpleNamespace())  # a Settings that predates the section
    assert called == []


def test_tracing_degrades_when_the_otel_packages_are_absent(monkeypatch):
    from pyrrhon.config.settings import TelemetrySettings
    from pyrrhon.voice.pipeline import _setup_tracing

    monkeypatch.setitem(
        sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", None
    )
    settings = SimpleNamespace(
        telemetry=TelemetrySettings(otel_enabled=True, otlp_endpoint="http://x/v1")
    )
    _setup_tracing(settings)  # must not raise
