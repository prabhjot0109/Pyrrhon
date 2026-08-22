"""Hugging Face voice services over the Inference Providers API (HF_TOKEN).

Pipecat has no native Hugging Face STT/TTS service, so these thin wrappers
talk to the HF Inference API via huggingface_hub's AsyncInferenceClient — the
same HF_TOKEN that drives the huggingface LLM provider. The registry checks
HF_TOKEN before importing this module (M3 error policy), so imports here may
assume pipecat, huggingface_hub, and soundfile are installed.

HF ASR returns text; HF TTS returns an audio file whose container is
model-dependent (often FLAC or WAV), so we decode it with soundfile and hand
pipecat a fresh WAV — its iterator then auto-detects the sample rate from the
header regardless of what the model emitted.
"""

from __future__ import annotations

import io
from collections.abc import AsyncGenerator

import soundfile
from huggingface_hub import AsyncInferenceClient
from openai.types.audio import Transcription
from pipecat.frames.frames import ErrorFrame, Frame
from pipecat.services.settings import TTSSettings, is_given
from pipecat.services.tts_service import TTSService
from pipecat.services.whisper.base_stt import BaseWhisperSTTService

_DEFAULT_STT_MODEL = "openai/whisper-large-v3"


class HuggingFaceSTTService(BaseWhisperSTTService):
    """VAD-segmented STT via HF automatic-speech-recognition.

    Subclasses BaseWhisperSTTService for its segment handling, metrics, and
    TranscriptionFrame plumbing; the OpenAI client it builds internally is
    never used (_transcribe is fully overridden).
    """

    def __init__(self, *, api_key: str, settings=None, **kwargs):
        model = settings.model if settings and is_given(settings.model) else None
        model = model or _DEFAULT_STT_MODEL
        super().__init__(settings=self.Settings(model=model), api_key="unused", **kwargs)
        self._hf = AsyncInferenceClient(token=api_key)
        self._hf_model = model

    async def _transcribe(self, audio: bytes) -> Transcription:
        result = await self._hf.automatic_speech_recognition(audio, model=self._hf_model)
        text = getattr(result, "text", None) or ""
        return Transcription(text=text.strip())


class HuggingFaceTTSService(TTSService):
    """Non-streaming HF TTS: one text_to_speech call per sentence chunk.

    The pickable thing for HF TTS is the *model* (set via [voice] tts_model),
    not a voice — most HF TTS models take no voice argument. Latency is one
    full API round-trip; fine for Pyrrhon's sentence-chunked speech.

    `model` is REQUIRED and has no default. The old default was
    hexgrad/Kokoro-82M, which pipecat's native KokoroTTSService serves better
    (on-device ONNX, no round-trip) — so shipping it here pointed users at the
    slower of two paths for the one model they were most likely to get.
    """

    # Pipecat's TTSService declares no Settings class of its own; naming the
    # base one here is what puts this shim on the same construction path as
    # every pipecat row, so voice/factory.py needs no special case for it.
    Settings = TTSSettings

    def __init__(self, *, api_key: str, settings=None, **kwargs):
        # A store, not a delta: the model is required (requires_model on the
        # table), and voice/language are None because HF TTS picks by model id
        # rather than by voice. validate_complete() rejects NOT_GIVEN here.
        store = self.Settings(model=None, voice=None, language=None)
        if settings is not None:
            store.apply_update(settings)
        if not store.model:
            # requires_model on the table already stops this at the factory.
            # Repeated here because the check has to survive direct
            # construction: without a model id every synthesis call fails with
            # an opaque HF error instead of one actionable line.
            raise ValueError(
                "Hugging Face TTS needs [voice] tts_model set to an HF model id."
            )
        super().__init__(
            push_start_frame=True, push_stop_frames=True, settings=store, **kwargs
        )
        self._hf = AsyncInferenceClient(token=api_key)
        self._model = store.model

    async def _synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """One WAV chunk per call — split out so tests can drive it directly."""
        raw = await self._hf.text_to_speech(text, model=self._model)
        data, sample_rate = soundfile.read(io.BytesIO(raw), dtype="int16")
        if getattr(data, "ndim", 1) > 1:
            data = data[:, 0]  # collapse to mono
        buffer = io.BytesIO()
        soundfile.write(buffer, data, sample_rate, format="WAV", subtype="PCM_16")
        yield buffer.getvalue()

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        try:
            await self.start_tts_usage_metrics(text)
            async for frame in self._stream_audio_frames_from_iterator(
                self._synthesize(text),
                strip_wav_header=True,  # rate travels in the WAV header
                context_id=context_id,
            ):
                await self.stop_ttfb_metrics()
                yield frame
        except Exception as exc:
            yield ErrorFrame(error=f"Hugging Face TTS failed: {exc}")
        finally:
            await self.stop_ttfb_metrics()
