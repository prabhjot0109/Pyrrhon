"""STT/TTS provider registry: config-driven, lazily imported, degrades cleanly.

These tests never import pipecat service classes for real — unknown
providers and missing keys fail BEFORE any pipecat import happens, which is
exactly the property the tests pin down.
"""

import sys
import types

import pytest

from pyrrhon.config.settings import VoiceSettings
from pyrrhon.voice.providers import VoiceUnavailableError, create_stt, create_tts


def _fake_service(captured: dict):
    """A stand-in pipecat service class that records its constructor kwargs."""

    class FakeService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    return FakeService


def _install_fake(monkeypatch, module_name: str, class_name: str, captured: dict):
    module = types.ModuleType(module_name)
    setattr(module, class_name, _fake_service(captured))
    monkeypatch.setitem(sys.modules, module_name, module)


def test_unknown_providers_fail_with_the_valid_list():
    with pytest.raises(VoiceUnavailableError) as exc:
        create_stt(VoiceSettings(stt_provider="nope"))
    assert "groq" in str(exc.value)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="nope"))
    assert "cartesia" in str(exc.value)


def test_missing_key_degrades_before_importing_pipecat(monkeypatch):
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="cartesia"))
    assert "CARTESIA_API_KEY" in str(exc.value)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_stt(VoiceSettings(stt_provider="groq"))
    assert "GROQ_API_KEY" in str(exc.value)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_stt(VoiceSettings(stt_provider="deepgram"))
    assert "DEEPGRAM_API_KEY" in str(exc.value)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="deepgram"))
    assert "DEEPGRAM_API_KEY" in str(exc.value)


def test_pipeline_reexports_error_class():
    from pyrrhon.voice.pipeline import VoiceUnavailableError as reexported

    assert reexported is VoiceUnavailableError


def test_whisper_local_passes_the_configured_model(monkeypatch):
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.whisper.stt", "WhisperSTTService", captured)
    create_stt(VoiceSettings(stt_provider="whisper-local", stt_model="distil-medium.en"))
    assert captured == {"model": "distil-medium.en"}


def test_whisper_local_uses_pipecat_default_when_model_unset(monkeypatch):
    captured: dict = {"untouched": True}
    _install_fake(monkeypatch, "pipecat.services.whisper.stt", "WhisperSTTService", captured)
    create_stt(VoiceSettings(stt_provider="whisper-local"))
    assert captured == {"untouched": True}  # no kwargs passed


def test_groq_stt_defaults_to_whisper_large_v3_turbo(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.groq.stt", "GroqSTTService", captured)
    create_stt(VoiceSettings(stt_provider="groq"))
    assert captured["model"] == "whisper-large-v3-turbo"


def test_openai_stt_no_longer_sends_a_groq_model_name(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.openai.stt", "OpenAISTTService", captured)
    create_stt(VoiceSettings(stt_provider="openai"))
    assert "model" not in captured  # pipecat's own default applies


def test_cartesia_requires_an_explicit_voice_id(monkeypatch):
    monkeypatch.setenv("CARTESIA_API_KEY", "k")
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="cartesia"))
    assert "tts_voice" in str(exc.value)


def test_openai_tts_defaults_to_nova(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.openai.tts", "OpenAITTSService", captured)
    create_tts(VoiceSettings(tts_provider="openai"))
    assert captured["voice"] == "nova"


def test_deepgram_tts_defaults_to_aura_thalia(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.deepgram.tts", "DeepgramTTSService", captured)
    create_tts(VoiceSettings(tts_provider="deepgram"))
    assert captured["voice"] == "aura-2-thalia-en"


def test_piper_runs_in_process_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.piper.tts", "PiperTTSService", captured)
    create_tts(VoiceSettings(tts_provider="piper"))
    assert captured["voice_id"] == "en_US-lessac-medium"
    assert captured["download_dir"] == tmp_path / ".pyrrhon" / "piper"


def test_gemini_stt_requires_gemini_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_stt(VoiceSettings(stt_provider="gemini"))
    assert "GEMINI_API_KEY" in str(exc.value)


def test_gemini_stt_builds_with_key_and_default_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pyrrhon.voice.gemini", "GeminiSTTService", captured)
    create_stt(VoiceSettings(stt_provider="gemini"))
    assert captured == {"api_key": "k", "model": "gemini-2.5-flash"}


def test_piper_uses_http_service_when_tts_url_is_set(monkeypatch):
    captured: dict = {}
    module = types.ModuleType("pipecat.services.piper.tts")
    module.PiperHttpTTSService = _fake_service(captured)
    module.PiperTTSService = _fake_service({})
    monkeypatch.setitem(sys.modules, "pipecat.services.piper.tts", module)
    # Fake aiohttp too: real ClientSession() demands a running event loop and
    # would leak an unclosed session in this sync test.
    aiohttp_mod = types.ModuleType("aiohttp")
    aiohttp_mod.ClientSession = lambda: object()
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp_mod)
    create_tts(VoiceSettings(tts_provider="piper", tts_url="http://localhost:5000"))
    assert captured["base_url"] == "http://localhost:5000"
