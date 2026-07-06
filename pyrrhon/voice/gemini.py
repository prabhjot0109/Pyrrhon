"""Gemini voice services over the plain Gemini API key.

Pipecat's own Google services (GoogleSTTService, GeminiTTSService) require
Google Cloud service-account credentials. Pyrrhon's setup promise is "paste
one API key", so these thin services talk to the Gemini Developer API via
the google-genai SDK instead. The registry checks GEMINI_API_KEY before
importing this module (M3 error policy) — imports here may assume pipecat
and google-genai are installed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from openai.types.audio import Transcription
from pipecat.frames.frames import ErrorFrame, Frame
from pipecat.services.tts_service import TTSService
from pipecat.services.whisper.base_stt import BaseWhisperSTTService

from google import genai
from google.genai import types as genai_types

GEMINI_TTS_SAMPLE_RATE = 24000  # Gemini TTS always outputs 24 kHz 16-bit mono PCM

_TRANSCRIBE_PROMPT = (
    "Transcribe this audio verbatim. Reply with only the transcription text — "
    "no preamble, no punctuation commentary."
)


class GeminiSTTService(BaseWhisperSTTService):
    """VAD-segmented STT: each speech segment is one generate_content call.

    Subclasses BaseWhisperSTTService purely for its segment handling, metrics,
    and TranscriptionFrame plumbing; the OpenAI client it builds internally is
    never used (_transcribe is fully overridden).
    """

    def __init__(self, *, api_key: str, model: str = "gemini-2.5-flash", **kwargs):
        super().__init__(model=model, api_key="unused", **kwargs)
        self._gemini = genai.Client(api_key=api_key)
        self._gemini_model = model

    async def _transcribe(self, audio: bytes) -> Transcription:
        response = await self._gemini.aio.models.generate_content(
            model=self._gemini_model,
            contents=[
                genai_types.Part.from_bytes(data=audio, mime_type="audio/wav"),
                _TRANSCRIBE_PROMPT,
            ],
        )
        return Transcription(text=(response.text or "").strip())


class GeminiTTSService(TTSService):
    """Non-streaming Gemini TTS: one generate_content call per sentence chunk.

    Latency note: first-audio is one full API round-trip (~1s) — fine for
    Pyrrhon's sentence-chunked speech, slower than Cartesia/ElevenLabs
    streaming. Voices: Kore, Puck, Charon, Aoede, ... (Gemini prebuilt set).
    """

    def __init__(
        self,
        *,
        api_key: str,
        voice: str = "Kore",
        model: str = "gemini-2.5-flash-preview-tts",
        **kwargs,
    ):
        super().__init__(push_start_frame=True, push_stop_frames=True, **kwargs)
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._voice = voice

    async def _synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """One PCM chunk per call — split out so tests can drive it directly."""
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=text,
            config=genai_types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=genai_types.SpeechConfig(
                    voice_config=genai_types.VoiceConfig(
                        prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                            voice_name=self._voice
                        )
                    )
                ),
            ),
        )
        yield response.candidates[0].content.parts[0].inline_data.data

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        try:
            await self.start_tts_usage_metrics(text)
            async for frame in self._stream_audio_frames_from_iterator(
                self._synthesize(text),
                in_sample_rate=GEMINI_TTS_SAMPLE_RATE,
                context_id=context_id,
            ):
                await self.stop_ttfb_metrics()
                yield frame
        except Exception as exc:
            yield ErrorFrame(error=f"Gemini TTS failed: {exc}")
        finally:
            await self.stop_ttfb_metrics()
