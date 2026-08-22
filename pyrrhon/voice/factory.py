"""Generic construction of the provider named in [voice], driven by registry.py.

Order matters and is load-bearing: the key check runs BEFORE the pipecat
import, so a missing key degrades to text mode with an actionable message
instead of dragging in the audio stack first (M3 error policy). The class is
loaded after that check and before the settings delta is built, because the
delta's type is the service's own `Cls.Settings`.

Everything here is provider-agnostic. The one exception is Piper's HTTP mode,
which is selected by [voice] tts_url and needs an aiohttp session whose
lifetime something must own — see create_tts.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

from pyrrhon.config.settings import VoiceSettings
from pyrrhon.voice.registry import (
    PIPER_HTTP,
    VoiceProvider,
    find,
    stt_providers,
    tts_providers,
)


class VoiceUnavailableError(RuntimeError):
    """Voice could not start or died; the caller stays in text mode."""


def _resolve(kind: str, provider_id: str) -> VoiceProvider:
    provider = find(kind, provider_id)
    if provider is None:
        options = stt_providers() if kind == "stt" else tts_providers()
        valid = ", ".join(p.id for p in options)
        raise VoiceUnavailableError(
            f"Unknown {kind}_provider '{provider_id}'. Valid: {valid}."
        )
    return provider


def _api_key(provider: VoiceProvider) -> str | None:
    if provider.key_env is None:
        return None
    value = os.environ.get(provider.key_env, "")
    if not value:
        raise VoiceUnavailableError(
            f"{provider.label} needs {provider.key_env} set — staying in text mode."
        )
    return value


def _load(provider: VoiceProvider):
    try:
        module = importlib.import_module(provider.module)
    except ImportError as exc:
        if provider.extra:
            raise VoiceUnavailableError(
                f"{provider.label} needs an extra that is not installed ({exc}). "
                f'Run: uv add "pipecat-ai[{provider.extra}]" — staying in text mode.'
            ) from exc
        raise VoiceUnavailableError(
            f"{provider.label} is unavailable ({exc}) — staying in text mode."
        ) from exc
    try:
        return getattr(module, provider.cls)
    except AttributeError as exc:  # upstream renamed or removed the class
        raise VoiceUnavailableError(
            f"{provider.label}: {provider.module}.{provider.cls} no longer exists "
            "in the installed pipecat — staying in text mode."
        ) from exc


def _settings(cls, provider: VoiceProvider, model: str | None, voice: str | None):
    """The `settings=` delta for `cls`, or None when nothing was configured.

    Pipecat 1.7.0 deprecated the per-service `model=`/`voice_id=` kwargs in
    favour of `settings=Cls.Settings(...)`, and in doing so made the field
    names uniform: `model` everywhere, `voice` on every TTS service. That is
    what let the table drop its `model_kwarg`/`voice_kwarg` columns — the
    disagreement they encoded no longer exists upstream.

    A Settings object passed to __init__ is a DELTA merged over the service's
    own store (see pipecat's piper/tts.py), so leaving a field out still
    inherits the provider's default. The "only send what was configured" rule
    survives the migration unchanged; it is now expressed as a sparse delta
    rather than an absent kwarg.
    """
    delta: dict = {}
    if model:
        delta["model"] = model
    if provider.kind == "tts" and voice:
        delta["voice"] = voice
    if not delta:
        return None
    return cls.Settings(**delta)


def _build(provider: VoiceProvider, model: str | None, voice: str | None):
    """Construct `provider`, sending only what was configured."""
    if provider.requires_voice and not voice:
        raise VoiceUnavailableError(
            f"{provider.label} needs tts_voice set to one of your voice ids "
            "in [voice] — staying in text mode."
        )
    if provider.requires_model and not model:
        raise VoiceUnavailableError(
            f"{provider.label} needs {provider.kind}_model set to a model id "
            "in [voice] — staying in text mode."
        )

    kwargs: dict = dict(provider.extra_kwargs)
    # A directory we hand a provider has to exist first: piper downloads its
    # voice model straight into download_dir and does not create it, so on a
    # fresh machine construction died with FileNotFoundError. The table names
    # the path; making it real is the factory's job.
    download_dir = kwargs.get("download_dir")
    if download_dir is not None:
        Path(download_dir).mkdir(parents=True, exist_ok=True)
    # Still before _load: a missing key must degrade to text mode without ever
    # dragging in the audio stack (M3 error policy).
    key = _api_key(provider)
    if key is not None:
        kwargs["api_key"] = key

    cls = _load(provider)
    settings = _settings(cls, provider, model, voice or provider.default_voice)
    if settings is not None:
        kwargs["settings"] = settings
    return cls(**kwargs)


def _piper_http(url: str):
    """Piper against a running `piper --http`, selected by [voice] tts_url.

    Not a VOICE_PROVIDERS row: pipecat does not own a session it was handed,
    so the session has to be created here and stashed for close_voice_service.
    That is a lifetime, not a kwarg, and the table only carries kwargs.
    """
    import aiohttp

    cls = _load(PIPER_HTTP)
    session = aiohttp.ClientSession()
    service = cls(base_url=url, aiohttp_session=session)
    service._pyrrhon_session = session
    return service


def create_stt(voice: VoiceSettings):
    provider = _resolve("stt", voice.stt_provider)
    return _build(provider, voice.stt_model, None)


def create_tts(voice: VoiceSettings):
    if voice.tts_provider == "piper" and voice.tts_url:
        return _piper_http(voice.tts_url)
    provider = _resolve("tts", voice.tts_provider)
    return _build(provider, voice.tts_model, voice.tts_voice)


async def close_voice_service(service: object) -> None:
    """Close any resource a factory attached to `service`. Safe on anything."""
    session = getattr(service, "_pyrrhon_session", None)
    if session is None:
        return
    try:
        await session.close()
    except Exception:  # teardown must never mask the reason we are tearing down
        pass
