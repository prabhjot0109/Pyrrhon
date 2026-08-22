"""Gemini STT over the plain Gemini API key.

Pipecat's GoogleSTTService requires Google Cloud service-account credentials.
Pyrrhon's setup promise is "paste one API key", so this thin service talks to
the Gemini Developer API via google-genai instead. The factory checks
GEMINI_API_KEY before importing this module (M3 error policy) — imports here
may assume pipecat and google-genai are installed.

TTS is NOT here: pipecat's GeminiTTSService accepts `api_key=` with
`use_genai=True`, so the registry points at pipecat directly. Do not
reintroduce a TTS shim.
"""

from __future__ import annotations

from google import genai
from google.genai import types as genai_types
from openai.types.audio import Transcription
from pipecat.services.settings import is_given
from pipecat.services.whisper.base_stt import BaseWhisperSTTService

_DEFAULT_MODEL = "gemini-2.5-flash"

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

    def __init__(self, *, api_key: str, settings=None, **kwargs):
        # settings=, not model=: pipecat deprecated the model kwarg in 1.7.0
        # and the factory now speaks only the Settings dialect, so every row
        # on the table — pipecat's and ours — is constructed the same way.
        model = settings.model if settings and is_given(settings.model) else None
        model = model or _DEFAULT_MODEL
        super().__init__(settings=self.Settings(model=model), api_key="unused", **kwargs)
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
