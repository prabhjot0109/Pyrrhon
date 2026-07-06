"""STT/TTS provider registry: config-driven, lazily imported, degrades cleanly.

These tests never import pipecat service classes for real — unknown
providers and missing keys fail BEFORE any pipecat import happens, which is
exactly the property the tests pin down.
"""

import pytest

from pyrrhon.config.settings import VoiceSettings
from pyrrhon.voice.providers import VoiceUnavailableError, create_stt, create_tts


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
