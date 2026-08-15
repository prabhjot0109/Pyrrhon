"""STT/TTS provider registry: mirrors the LLM slot pattern for audio.

STT: groq | openai | gemini | huggingface | deepgram | whisper-local
TTS: openai | groq | gemini | huggingface | cartesia | elevenlabs | deepgram | piper

Key check happens BEFORE the pipecat import, and each provider's import is
lazy — a missing optional extra degrades to text mode with an actionable
message instead of crashing at import time (M3 error policy).

Latency notes for the config-curious (Coval/CodeSOTA 2026 benchmarks):
OpenAI TTS ~380ms+ to first audio (default only because it needs no new
key); gemini TTS is one full round-trip (~1s, no streaming); cartesia
(~90-190ms) is the recommended real-time choice; elevenlabs Flash
~75-290ms; deepgram Aura-2 ~120-313ms; piper is local, free, in-process
(~35ms on CPU).
"""

from __future__ import annotations

import os
from pathlib import Path

from pyrrhon.config.settings import VoiceSettings

STT_PROVIDERS = ("groq", "openai", "gemini", "huggingface", "deepgram", "whisper-local")
TTS_PROVIDERS = (
    "openai", "groq", "gemini", "huggingface", "cartesia", "elevenlabs", "deepgram", "piper"
)


class VoiceUnavailableError(RuntimeError):
    """Voice could not start or died; the caller stays in text mode."""


def _key(env: str, what: str) -> str:
    value = os.environ.get(env, "")
    if not value:
        raise VoiceUnavailableError(f"{what} needs {env} set — staying in text mode.")
    return value


def _import_error(exc: ImportError, extra: str) -> VoiceUnavailableError:
    return VoiceUnavailableError(
        f"Voice dependency missing ({exc}). "
        f'Run: uv add "pipecat-ai[{extra}]" — staying in text mode.'
    )


def create_stt(voice: VoiceSettings):
    provider = voice.stt_provider
    if provider == "groq":
        key = _key("GROQ_API_KEY", "Groq Whisper STT")
        try:
            from pipecat.services.groq.stt import GroqSTTService
        except ImportError as exc:
            raise _import_error(exc, "groq") from exc
        return GroqSTTService(api_key=key, model=voice.stt_model or "whisper-large-v3-turbo")
    if provider == "openai":
        key = _key("OPENAI_API_KEY", "OpenAI STT")
        try:
            from pipecat.services.openai.stt import OpenAISTTService
        except ImportError as exc:
            raise _import_error(exc, "openai") from exc
        kwargs = {"api_key": key}
        if voice.stt_model:
            kwargs["model"] = voice.stt_model
        return OpenAISTTService(**kwargs)
    if provider == "gemini":
        key = _key("GEMINI_API_KEY", "Gemini STT")
        try:
            from pyrrhon.voice.gemini import GeminiSTTService
        except ImportError as exc:
            raise VoiceUnavailableError(
                f"Voice dependency missing ({exc}). Run: uv add google-genai "
                "— staying in text mode."
            ) from exc
        return GeminiSTTService(api_key=key, model=voice.stt_model or "gemini-2.5-flash")
    if provider == "huggingface":
        key = _key("HF_TOKEN", "Hugging Face STT")
        try:
            from pyrrhon.voice.huggingface import HuggingFaceSTTService
        except ImportError as exc:
            raise VoiceUnavailableError(
                f"Voice dependency missing ({exc}). Run: uv add huggingface_hub "
                "soundfile — staying in text mode."
            ) from exc
        return HuggingFaceSTTService(
            api_key=key, model=voice.stt_model or "openai/whisper-large-v3"
        )
    if provider == "deepgram":
        key = _key("DEEPGRAM_API_KEY", "Deepgram STT")
        try:
            from pipecat.services.deepgram.stt import DeepgramSTTService
        except ImportError as exc:
            raise _import_error(exc, "deepgram") from exc
        return DeepgramSTTService(api_key=key)
    if provider == "whisper-local":
        try:
            from pipecat.services.whisper.stt import WhisperSTTService
        except ImportError as exc:
            raise _import_error(exc, "whisper") from exc
        # faster-whisper, runs locally, no key. stt_model picks the size:
        # tiny | base | small | medium | large-v3, or an HF id such as
        # "Systran/faster-distil-whisper-medium.en".
        if voice.stt_model:
            return WhisperSTTService(model=voice.stt_model)
        return WhisperSTTService()
    raise VoiceUnavailableError(
        f"Unknown stt_provider '{provider}'. Valid: {', '.join(STT_PROVIDERS)}."
    )


def create_tts(voice: VoiceSettings):
    provider = voice.tts_provider
    if provider == "openai":
        key = _key("OPENAI_API_KEY", "OpenAI TTS")
        try:
            from pipecat.services.openai.tts import OpenAITTSService
        except ImportError as exc:
            raise _import_error(exc, "openai") from exc
        kwargs = {"api_key": key, "voice": voice.tts_voice or "nova"}
        if voice.tts_model:
            kwargs["model"] = voice.tts_model
        return OpenAITTSService(**kwargs)
    if provider == "groq":
        key = _key("GROQ_API_KEY", "Groq TTS")
        try:
            from pipecat.services.groq.tts import GroqTTSService
        except ImportError as exc:
            raise _import_error(exc, "groq") from exc
        return GroqTTSService(
            api_key=key,
            model_name=voice.tts_model or "canopylabs/orpheus-v1-english",
            voice_id=voice.tts_voice or "autumn",
        )
    if provider == "huggingface":
        key = _key("HF_TOKEN", "Hugging Face TTS")
        try:
            from pyrrhon.voice.huggingface import HuggingFaceTTSService
        except ImportError as exc:
            raise VoiceUnavailableError(
                f"Voice dependency missing ({exc}). Run: uv add huggingface_hub "
                "soundfile — staying in text mode."
            ) from exc
        # HF TTS selects a model, not a voice — most HF TTS models take no voice.
        return HuggingFaceTTSService(
            api_key=key, model=voice.tts_model or "hexgrad/Kokoro-82M"
        )
    if provider == "gemini":
        key = _key("GEMINI_API_KEY", "Gemini TTS")
        try:
            from pyrrhon.voice.gemini import GeminiTTSService
        except ImportError as exc:
            raise VoiceUnavailableError(
                f"Voice dependency missing ({exc}). Run: uv add google-genai "
                "— staying in text mode."
            ) from exc
        return GeminiTTSService(
            api_key=key,
            voice=voice.tts_voice or "Kore",
            model=voice.tts_model or "gemini-2.5-flash-preview-tts",
        )
    if provider == "cartesia":
        key = _key("CARTESIA_API_KEY", "Cartesia TTS")
        if not voice.tts_voice:
            raise VoiceUnavailableError(
                f"{provider} TTS needs tts_voice set to one of your voice ids "
                "in [voice] — staying in text mode."
            )
        try:
            from pipecat.services.cartesia.tts import CartesiaTTSService
        except ImportError as exc:
            raise _import_error(exc, "cartesia") from exc
        kwargs = {"api_key": key, "voice_id": voice.tts_voice}
        if voice.tts_model:
            kwargs["model"] = voice.tts_model
        return CartesiaTTSService(**kwargs)
    if provider == "elevenlabs":
        key = _key("ELEVENLABS_API_KEY", "ElevenLabs TTS")
        if not voice.tts_voice:
            raise VoiceUnavailableError(
                f"{provider} TTS needs tts_voice set to one of your voice ids "
                "in [voice] — staying in text mode."
            )
        try:
            from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        except ImportError as exc:
            raise _import_error(exc, "elevenlabs") from exc
        kwargs = {"api_key": key, "voice_id": voice.tts_voice}
        if voice.tts_model:
            kwargs["model"] = voice.tts_model
        return ElevenLabsTTSService(**kwargs)
    if provider == "deepgram":
        key = _key("DEEPGRAM_API_KEY", "Deepgram TTS")
        try:
            from pipecat.services.deepgram.tts import DeepgramTTSService
        except ImportError as exc:
            raise _import_error(exc, "deepgram") from exc
        # tts_voice carries the Aura voice model (e.g. "aura-2-thalia-en").
        return DeepgramTTSService(api_key=key, voice=voice.tts_voice or "aura-2-thalia-en")
    if provider == "piper":
        if voice.tts_url:
            # Explicit server mode: talk to a running `piper --http`.
            try:
                from pipecat.services.piper.tts import PiperHttpTTSService
            except ImportError as exc:
                raise _import_error(exc, "piper") from exc
            import aiohttp

            session = aiohttp.ClientSession()
            service = PiperHttpTTSService(
                base_url=voice.tts_url, aiohttp_session=session
            )
            # Stashed so the pipeline can close it on teardown. Pipecat does not
            # own a session it was handed, so without this every /voice on leaks
            # one plus its connector.
            service._pyrrhon_session = session
            return service
        # Default: in-process Piper — downloads the voice model on first use,
        # nothing else to run. Local, free, keyless.
        try:
            from pipecat.services.piper.tts import PiperTTSService
        except ImportError as exc:
            raise _import_error(exc, "piper") from exc

        return PiperTTSService(
            voice_id=voice.tts_voice or "en_US-lessac-medium",
            download_dir=Path.home() / ".pyrrhon" / "piper",
        )
    raise VoiceUnavailableError(
        f"Unknown tts_provider '{provider}'. Valid: {', '.join(TTS_PROVIDERS)}."
    )


async def close_voice_service(service: object) -> None:
    """Close any resource a factory attached to `service`. Safe on anything."""
    session = getattr(service, "_pyrrhon_session", None)
    if session is None:
        return
    try:
        await session.close()
    except Exception:  # teardown must never mask the reason we are tearing down
        pass
