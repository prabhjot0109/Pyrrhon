"""HuggingFace STT/TTS services — fake huggingface_hub (+ soundfile), no network."""

import sys
import types
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def fake_hf(monkeypatch):
    """Install a fake `huggingface_hub` and return the AsyncInferenceClient stub.

    pyrrhon.voice.huggingface binds AsyncInferenceClient at import time, so the
    module is evicted from the cache first (same reason as the Gemini fixture).
    soundfile stays real — the TTS test monkeypatches its read/write directly.
    """
    monkeypatch.delitem(sys.modules, "pyrrhon.voice.huggingface", raising=False)
    client = types.SimpleNamespace()
    hub = types.ModuleType("huggingface_hub")
    hub.AsyncInferenceClient = lambda token=None: client
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    return client


async def test_transcribe_returns_stripped_text(fake_hf):
    from pyrrhon.voice.huggingface import HuggingFaceSTTService

    fake_hf.automatic_speech_recognition = AsyncMock(
        return_value=types.SimpleNamespace(text="  hi there  ")
    )
    service = HuggingFaceSTTService(api_key="k", model="openai/whisper-large-v3")
    result = await service._transcribe(b"wav-bytes")

    assert result.text == "hi there"
    call = fake_hf.automatic_speech_recognition.call_args
    assert call.kwargs["model"] == "openai/whisper-large-v3"
    assert call.args[0] == b"wav-bytes"


async def test_tts_calls_model_and_yields_wav(fake_hf, monkeypatch):
    from pyrrhon.voice import huggingface as hf

    fake_hf.text_to_speech = AsyncMock(return_value=b"FLAC-or-whatever")

    class _Mono:
        ndim = 1

    # HF returns a container soundfile decodes; we re-encode to WAV so pipecat
    # can auto-detect the rate. Fake both soundfile calls.
    monkeypatch.setattr(hf.soundfile, "read", lambda buf, dtype=None: (_Mono(), 22050))

    def _write(buf, data, samplerate, format=None, subtype=None):
        buf.write(b"RIFF" + b"\x00" * 40 + b"pcm")

    monkeypatch.setattr(hf.soundfile, "write", _write)

    service = hf.HuggingFaceTTSService.__new__(hf.HuggingFaceTTSService)  # skip pipecat init
    service._hf = fake_hf
    service._model = "hexgrad/Kokoro-82M"

    chunks = [c async for c in service._synthesize("hello")]
    assert chunks[0].startswith(b"RIFF")
    assert fake_hf.text_to_speech.call_args.kwargs["model"] == "hexgrad/Kokoro-82M"


def test_hf_tts_requires_an_explicit_model(fake_hf):
    from pyrrhon.voice.huggingface import HuggingFaceTTSService

    with pytest.raises(TypeError):
        HuggingFaceTTSService(api_key="k")  # model is now required
