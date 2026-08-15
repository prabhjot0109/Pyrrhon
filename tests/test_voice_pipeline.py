import asyncio
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
