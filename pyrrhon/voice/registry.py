"""The STT/TTS provider table: what Pyrrhon offers, as data.

This module imports NO pipecat. It names pipecat classes as strings so the
table can be read — and tested — on a machine where the extras are not
installed, which is precisely the case that used to ship broken.

Curation criterion: a provider earns a row if it runs from one API key or no
key, and streams. That excludes telephony and avatar services. Where pipecat
ships a service we point at pipecat's; the only `pyrrhon.` rows are the two
things pipecat has no service for at all (see `pyrrhon/voice/huggingface.py`
and `pyrrhon/voice/gemini.py`).

No default MODEL is recorded here. Where pipecat or the provider supplies a
default, we pass nothing and inherit it — a default we do not set cannot go
stale. `default_voice` appears only where no upstream default exists.

There are no per-row kwarg-name columns, and there must not be again. Model and
voice reach a service through `settings=Cls.Settings(model=…, voice=…)`, whose
field names pipecat has made uniform across all 20 rows — the disagreement the
old `model_kwarg`/`voice_kwarg` columns existed to paper over is gone upstream.
`tests/test_voice_registry.py::test_tier1_every_provider_declares_a_settings_class`
is what keeps that true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Kind = Literal["stt", "tts"]


@dataclass(frozen=True)
class VoiceProvider:
    id: str
    kind: Kind
    module: str                       # import path, resolved lazily
    cls: str                          # class name inside that module
    label: str
    key_env: str | None = None        # None: keyless
    extra: str | None = None          # the pipecat extra that supplies it
    default_voice: str | None = None  # only where upstream has none
    requires_voice: bool = False
    requires_model: bool = False
    # compare=False keeps the row hashable: a frozen dataclass derives __hash__
    # from its comparison fields, and a dict is unhashable. Excluding it costs
    # nothing — (kind, id) is unique by test, so no two rows differ only here.
    extra_kwargs: dict = field(default_factory=dict, compare=False)
    note: str = ""
    # True only where a tier 3 live smoke has actually passed, with the date in
    # the M15a plan's Implementation Record. It is NOT "we believe this works":
    # catalog.availability() renders anything else as `ready, unverified`, which
    # is the spec's answer to curating more rows than we hold keys for. Never
    # set it from a table review — set it from a run.
    verified: bool = False


VOICE_PROVIDERS: tuple[VoiceProvider, ...] = (
    # ---- STT -------------------------------------------------------------
    VoiceProvider(
        id="groq", kind="stt", label="Groq Whisper",
        module="pipecat.services.groq.stt", cls="GroqSTTService",
        key_env="GROQ_API_KEY", extra="groq",
        note="fast hosted Whisper; no extra key if you already use Groq",
        verified=True,  # tier 3, 2026-08-22
    ),
    VoiceProvider(
        id="openai", kind="stt", label="OpenAI",
        module="pipecat.services.openai.stt", cls="OpenAISTTService",
        key_env="OPENAI_API_KEY", extra="openai",
        note="hosted transcription",
    ),
    VoiceProvider(
        id="deepgram", kind="stt", label="Deepgram",
        module="pipecat.services.deepgram.stt", cls="DeepgramSTTService",
        key_env="DEEPGRAM_API_KEY", extra="deepgram",
        note="streaming STT, very low latency",
        verified=True,  # tier 3, 2026-08-29
    ),
    VoiceProvider(
        id="cartesia", kind="stt", label="Cartesia",
        module="pipecat.services.cartesia.stt", cls="CartesiaSTTService",
        key_env="CARTESIA_API_KEY", extra="cartesia",
        note="streaming STT with keyterm biasing",
    ),
    VoiceProvider(
        id="assemblyai", kind="stt", label="AssemblyAI",
        module="pipecat.services.assemblyai.stt", cls="AssemblyAISTTService",
        key_env="ASSEMBLYAI_API_KEY", extra="assemblyai",
        note="streaming STT with multi-language steering",
    ),
    VoiceProvider(
        id="gladia", kind="stt", label="Gladia",
        module="pipecat.services.gladia.stt", cls="GladiaSTTService",
        key_env="GLADIA_API_KEY", extra="gladia",
        note="streaming STT",
    ),
    VoiceProvider(
        id="whisper-local", kind="stt", label="Whisper (local)",
        module="pipecat.services.whisper.stt", cls="WhisperSTTService",
        key_env=None, extra="whisper",
        note="on-device: tiny|base|small|medium|large-v3 or an HF id",
    ),
    VoiceProvider(
        id="moonshine", kind="stt", label="Moonshine (local)",
        module="pipecat.services.moonshine.stt", cls="MoonshineSTTService",
        key_env=None, extra="moonshine",
        note="on-device, tuned for short utterances",
    ),
    VoiceProvider(
        id="gemini", kind="stt", label="Google Gemini",
        module="pyrrhon.voice.gemini", cls="GeminiSTTService",
        key_env="GEMINI_API_KEY", extra=None,
        note="Gemini Developer API on a plain key (pipecat's needs a GCP service account)",
    ),
    VoiceProvider(
        id="huggingface", kind="stt", label="Hugging Face",
        module="pyrrhon.voice.huggingface", cls="HuggingFaceSTTService",
        key_env="HF_TOKEN", extra=None,
        note="any HF ASR model id via Inference Providers — set stt_model",
    ),
    # ---- TTS -------------------------------------------------------------
    VoiceProvider(
        id="piper", kind="tts", label="Piper (local)",
        module="pipecat.services.piper.tts", cls="PiperTTSService",
        key_env=None, extra="piper", default_voice="en_US-lessac-medium",
        # Piper downloads its voice model on first use; give it a stable home
        # instead of the process working directory. A Path, NOT a str: piper's
        # download_voices does `download_dir / name` internally, and a str
        # raises TypeError there. Tier 3 is what caught this.
        extra_kwargs={"download_dir": Path.home() / ".pyrrhon" / "piper"},
        note="free, on-device, no key and no server",
        verified=True,  # tier 3, 2026-08-22
    ),
    VoiceProvider(
        id="openai", kind="tts", label="OpenAI",
        module="pipecat.services.openai.tts", cls="OpenAITTSService",
        key_env="OPENAI_API_KEY", extra="openai", default_voice="nova",
        note="no extra key if you already use OpenAI",
    ),
    VoiceProvider(
        id="groq", kind="tts", label="Groq",
        module="pipecat.services.groq.tts", cls="GroqTTSService",
        key_env="GROQ_API_KEY", extra="groq", requires_voice=True,
        note="hosted TTS on your Groq key; needs an explicit voice id",
        verified=True,  # tier 3, 2026-08-22
    ),
    VoiceProvider(
        id="cartesia", kind="tts", label="Cartesia",
        module="pipecat.services.cartesia.tts", cls="CartesiaTTSService",
        key_env="CARTESIA_API_KEY", extra="cartesia", requires_voice=True,
        note="lowest latency; needs a voice id from your account",
    ),
    VoiceProvider(
        id="elevenlabs", kind="tts", label="ElevenLabs",
        module="pipecat.services.elevenlabs.tts", cls="ElevenLabsTTSService",
        key_env="ELEVENLABS_API_KEY", extra="elevenlabs", requires_voice=True,
        note="needs a voice id from your account",
    ),
    VoiceProvider(
        id="deepgram", kind="tts", label="Deepgram Aura",
        module="pipecat.services.deepgram.tts", cls="DeepgramTTSService",
        key_env="DEEPGRAM_API_KEY", extra="deepgram",
        note="low-latency hosted voices",
        verified=True,  # tier 3, 2026-08-22
    ),
    VoiceProvider(
        id="rime", kind="tts", label="Rime",
        module="pipecat.services.rime.tts", cls="RimeTTSService",
        key_env="RIME_API_KEY", extra="rime", requires_voice=True,
        note="low-latency conversational voices",
    ),
    VoiceProvider(
        id="inworld", kind="tts", label="Inworld",
        module="pipecat.services.inworld.tts", cls="InworldTTSService",
        key_env="INWORLD_API_KEY", extra="inworld", requires_voice=True,
        note="expressive hosted voices",
    ),
    VoiceProvider(
        id="kokoro", kind="tts", label="Kokoro (local)",
        module="pipecat.services.kokoro.tts", cls="KokoroTTSService",
        key_env=None, extra="kokoro",
        note="free, on-device, ONNX",
    ),
    VoiceProvider(
        id="gemini", kind="tts", label="Google Gemini",
        module="pipecat.services.google.tts", cls="GeminiTTSService",
        key_env="GEMINI_API_KEY", extra="google", default_voice="Kore",
        extra_kwargs={"use_genai": True},
        note="Gemini TTS on a plain API key",
    ),
    VoiceProvider(
        id="huggingface", kind="tts", label="Hugging Face",
        module="pyrrhon.voice.huggingface", cls="HuggingFaceTTSService",
        key_env="HF_TOKEN", extra=None, requires_model=True,
        note="any HF TTS model id — set tts_model; no default",
    ),
)

# Selected by [voice] tts_url rather than by tts_provider: it is the same
# `piper` choice pointed at a running `piper --http` instead of the in-process
# model. Kept OUT of VOICE_PROVIDERS so it never appears as a menu entry the
# user could pick without also setting a URL. factory.create_tts owns it,
# because it needs an aiohttp session whose lifetime the factory must manage —
# that is behaviour, and a data table cannot express it.
PIPER_HTTP = VoiceProvider(
    id="piper-http", kind="tts", label="Piper (local server)",
    module="pipecat.services.piper.tts", cls="PiperHttpTTSService",
    key_env=None, extra="piper",
    note="talks to a running `piper --http` at [voice] tts_url",
)


def stt_providers() -> tuple[VoiceProvider, ...]:
    return tuple(p for p in VOICE_PROVIDERS if p.kind == "stt")


def tts_providers() -> tuple[VoiceProvider, ...]:
    return tuple(p for p in VOICE_PROVIDERS if p.kind == "tts")


def find(kind: str, provider_id: str) -> VoiceProvider | None:
    for provider in VOICE_PROVIDERS:
        if provider.kind == kind and provider.id == provider_id:
            return provider
    return None
