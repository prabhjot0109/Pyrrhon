"""STT/TTS provider registry: mirrors the LLM slot pattern for audio.

Key check happens BEFORE the pipecat import, and each provider's import is
lazy — a missing optional extra degrades to text mode with an actionable
message instead of crashing at import time (M3 error policy).

Latency notes for the config-curious (Coval/CodeSOTA 2026 benchmarks):
OpenAI TTS ~380ms+ to first audio (default only because it needs no new
key); cartesia (~90-190ms) is the recommended real-time choice; elevenlabs
Flash ~75-290ms; deepgram Aura-2 ~120-313ms; piper is local, free, ~35ms on
CPU behind its HTTP server.
"""

from __future__ import annotations

import os

from pyrrhon.config.settings import VoiceSettings

STT_PROVIDERS = ("groq", "openai", "deepgram", "whisper-local")
TTS_PROVIDERS = ("openai", "cartesia", "elevenlabs", "deepgram", "piper")


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
        try:
            # Pipecat 1.5.0 ships two Piper services; the HTTP one talks to a
            # local `piper --http` server, keyless.
            from pipecat.services.piper.tts import PiperHttpTTSService
        except ImportError as exc:
            raise _import_error(exc, "piper") from exc
        import aiohttp

        return PiperHttpTTSService(
            base_url=voice.tts_url or "http://localhost:5000",
            aiohttp_session=aiohttp.ClientSession(),
        )
    raise VoiceUnavailableError(
        f"Unknown tts_provider '{provider}'. Valid: {', '.join(TTS_PROVIDERS)}."
    )
