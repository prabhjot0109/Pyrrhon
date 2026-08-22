"""GeminiSTTService — a thin google-genai wrapper, no network in tests."""

import sys
import types
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def fake_genai(monkeypatch):
    """Install a fake `google.genai` package; return the client instance.

    pyrrhon.voice.gemini binds `genai` at import time, so it must be evicted
    from the module cache FIRST — otherwise a prior import (real SDK) would
    keep pointing at the real client and _transcribe would hit the network.
    """
    monkeypatch.delitem(sys.modules, "pyrrhon.voice.gemini", raising=False)
    client = types.SimpleNamespace()
    client.aio = types.SimpleNamespace(models=types.SimpleNamespace())

    genai = types.ModuleType("google.genai")
    genai.Client = lambda api_key: client
    genai_types = types.ModuleType("google.genai.types")

    class _Passthrough:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    for name in (
        "GenerateContentConfig", "SpeechConfig", "VoiceConfig", "PrebuiltVoiceConfig"
    ):
        setattr(genai_types, name, type(name, (_Passthrough,), {}))
    genai_types.Part = types.SimpleNamespace(
        from_bytes=lambda data, mime_type: {"data": data, "mime_type": mime_type}
    )
    google = types.ModuleType("google")
    google.genai = genai
    genai.types = genai_types
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_types)
    return client


async def test_transcribe_sends_wav_and_returns_text(fake_genai):
    from pyrrhon.voice.gemini import GeminiSTTService

    response = types.SimpleNamespace(text="  hello world  ")
    fake_genai.aio.models.generate_content = AsyncMock(return_value=response)

    service = GeminiSTTService(
        api_key="k", settings=GeminiSTTService.Settings(model="gemini-2.5-flash")
    )
    result = await service._transcribe(b"RIFF-fake-wav")

    assert result.text == "hello world"
    call = fake_genai.aio.models.generate_content.call_args
    assert call.kwargs["model"] == "gemini-2.5-flash"
    assert call.kwargs["contents"][0]["mime_type"] == "audio/wav"


def test_gemini_tts_shim_is_gone():
    """Pipecat's GeminiTTSService takes api_key= now; the shim's reason expired."""
    import pyrrhon.voice.gemini as shim

    assert not hasattr(shim, "GeminiTTSService")
    assert hasattr(shim, "GeminiSTTService")  # GoogleSTTService still needs GCP creds
