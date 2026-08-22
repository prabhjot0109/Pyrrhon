"""The factory: key checks before imports, the settings delta, clean degradation."""

import pathlib
import sys
import types

import pytest

from pyrrhon.config.settings import VoiceSettings
from pyrrhon.voice.factory import (
    VoiceUnavailableError,
    close_voice_service,
    create_stt,
    create_tts,
)


def _install_fake(monkeypatch, module_name, class_name, captured):
    class FakeService:
        # Every pipecat service declares one; the factory reads it off the
        # class to type its delta, so a stand-in without it is not a stand-in.
        class Settings(types.SimpleNamespace):
            pass

        def __init__(self, **kwargs):
            captured.update(kwargs)

    module = types.ModuleType(module_name)
    setattr(module, class_name, FakeService)
    monkeypatch.setitem(sys.modules, module_name, module)


def test_unknown_provider_lists_the_valid_ones():
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="nope"))
    assert "piper" in str(exc.value)


def test_missing_key_fails_before_importing_pipecat(monkeypatch):
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="cartesia", tts_voice="v1"))
    assert "CARTESIA_API_KEY" in str(exc.value)


def test_model_and_voice_travel_in_one_settings_delta(monkeypatch):
    """Not model_name= and voice_id=. Pipecat canonicalized both field names in
    1.7.0, which is why the table no longer carries a kwarg column per row."""
    monkeypatch.setenv("GROQ_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.groq.tts", "GroqTTSService", captured)
    create_tts(VoiceSettings(tts_provider="groq", tts_voice="autumn", tts_model="m1"))
    assert captured["api_key"] == "k"
    assert vars(captured["settings"]) == {"model": "m1", "voice": "autumn"}


def test_a_default_voice_reaches_the_delta_when_the_user_set_none(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.openai.tts", "OpenAITTSService", captured)
    create_tts(VoiceSettings(tts_provider="openai"))
    assert vars(captured["settings"]) == {"voice": "nova"}


def test_stt_never_carries_a_voice_field(monkeypatch):
    """STTSettings has no `voice`; sending one would be a TypeError at runtime."""
    monkeypatch.setenv("GROQ_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.groq.stt", "GroqSTTService", captured)
    create_stt(VoiceSettings(stt_provider="groq", stt_model="whisper-large-v3"))
    assert vars(captured["settings"]) == {"model": "whisper-large-v3"}


def test_no_settings_kwarg_at_all_when_nothing_was_configured(monkeypatch):
    """The inherit-the-provider's-default rule: send nothing, not an empty delta.

    An empty Settings() would still be a delta object, and a provider that
    reads `is_given` on its fields must see them untouched.
    """
    monkeypatch.setenv("GROQ_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.groq.stt", "GroqSTTService", captured)
    create_stt(VoiceSettings(stt_provider="groq"))
    assert captured == {"api_key": "k"}


def test_provider_requiring_a_voice_says_so(monkeypatch):
    monkeypatch.setenv("CARTESIA_API_KEY", "k")
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="cartesia"))
    assert "tts_voice" in str(exc.value)


def test_provider_requiring_a_model_says_so(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "k")
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="huggingface"))
    assert "tts_model" in str(exc.value)


def test_missing_extra_names_the_install_command(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    monkeypatch.setitem(sys.modules, "pipecat.services.deepgram.stt", None)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_stt(VoiceSettings(stt_provider="deepgram"))
    assert 'pipecat-ai[deepgram]' in str(exc.value)


def test_a_renamed_upstream_class_degrades_instead_of_crashing(monkeypatch):
    """Tier 1 catches this in CI; here we prove the runtime path is survivable."""
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setitem(
        sys.modules, "pipecat.services.groq.tts", types.ModuleType("empty")
    )
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="groq", tts_voice="autumn"))
    assert "no longer exists" in str(exc.value)


def test_piper_gets_a_stable_download_dir(monkeypatch):
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.piper.tts", "PiperTTSService", captured)
    create_tts(VoiceSettings(tts_provider="piper"))
    assert captured["settings"].voice == "en_US-lessac-medium"
    # A Path, not a str: piper does `download_dir / name` internally.
    assert isinstance(captured["download_dir"], pathlib.Path)
    assert captured["download_dir"].name == "piper"
    assert "api_key" not in captured  # keyless


async def test_tts_url_selects_piper_http_and_stashes_its_session(monkeypatch):
    """[voice] tts_url is privileged config; it must not become a silent no-op.

    The session stash is the contract close_voice_service relies on — pipecat
    does not own a session it was handed, so without it every /voice on leaks
    one plus its connector. Async because aiohttp.ClientSession needs a running
    loop, which is what run_voice actually gives it.
    """
    captured: dict = {}
    _install_fake(
        monkeypatch, "pipecat.services.piper.tts", "PiperHttpTTSService", captured
    )
    service = create_tts(
        VoiceSettings(tts_provider="piper", tts_url="http://localhost:5000")
    )
    assert captured["base_url"] == "http://localhost:5000"
    assert getattr(service, "_pyrrhon_session", None) is captured["aiohttp_session"]

    await close_voice_service(service)
    assert service._pyrrhon_session.closed


async def test_closing_a_service_with_no_session_is_a_noop():
    """Most providers attach nothing; teardown must not care."""
    await close_voice_service(object())
