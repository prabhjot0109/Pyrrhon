"""Gemini voice services over the plain Gemini API key.

Pipecat's own Google services (GoogleSTTService, GeminiTTSService) require
Google Cloud service-account credentials. Pyrrhon's setup promise is "paste
one API key", so these thin services talk to the Gemini Developer API via
the google-genai SDK instead. The registry checks GEMINI_API_KEY before
importing this module (M3 error policy) — imports here may assume pipecat
and google-genai are installed.
"""

from __future__ import annotations

from openai.types.audio import Transcription
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
