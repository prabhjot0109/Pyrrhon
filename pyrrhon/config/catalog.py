"""Every provider a user can pick, as data — the wizard and /settings render this.

Sync rule (pinned by tests/test_catalog.py): LLM ids mirror
BUILTIN_PROVIDERS; STT/TTS ids mirror the voice registry tuples. For TTS
choices, default_model carries the default VOICE (the registry's own
per-provider fallback), since a voice is the thing users actually pick.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderChoice:
    id: str
    label: str
    key_env: str | None          # None: keyless (local server / on-device model)
    default_model: str | None    # LLM/STT: model id; TTS: default voice
    note: str = ""


LLM_CHOICES: tuple[ProviderChoice, ...] = (
    ProviderChoice("groq", "Groq", "GROQ_API_KEY", "llama-3.3-70b-versatile",
                   "fast open-weights inference; generous free tier"),
    ProviderChoice("openai", "OpenAI", "OPENAI_API_KEY", "gpt-4o-mini",
                   "GPT models"),
    ProviderChoice("gemini", "Google Gemini", "GEMINI_API_KEY", "gemini-2.5-flash",
                   "gemini-2.5-pro for the deep slot"),
    ProviderChoice("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "deepseek-chat",
                   "deepseek-reasoner for deep reasoning"),
    ProviderChoice("cerebras", "Cerebras", "CEREBRAS_API_KEY", "llama-3.3-70b",
                   "fastest tokens/sec around"),
    ProviderChoice("openrouter", "OpenRouter", "OPENROUTER_API_KEY",
                   "deepseek/deepseek-chat", "one key, many models"),
    ProviderChoice("huggingface", "Hugging Face", "HF_TOKEN",
                   "meta-llama/Llama-3.3-70B-Instruct",
                   "HF Inference Providers router"),
    ProviderChoice("ollama", "Ollama (local)", None, "llama3.2",
                   "runs on your machine — `ollama pull <model>` first"),
    ProviderChoice("lmstudio", "LM Studio (local)", None, "local-model",
                   "uses whatever model LM Studio has loaded"),
)

STT_CHOICES: tuple[ProviderChoice, ...] = (
    ProviderChoice("groq", "Groq Whisper", "GROQ_API_KEY", "whisper-large-v3-turbo",
                   "fast hosted Whisper"),
    ProviderChoice("openai", "OpenAI", "OPENAI_API_KEY", None,
                   "hosted transcription"),
    ProviderChoice("gemini", "Google Gemini", "GEMINI_API_KEY", "gemini-2.5-flash",
                   "transcription via the Gemini API"),
    ProviderChoice("deepgram", "Deepgram", "DEEPGRAM_API_KEY", None,
                   "streaming STT"),
    ProviderChoice("whisper-local", "Whisper (local)", None, None,
                   "on-device: tiny|base|small|medium|large-v3 or an HF id"),
)

TTS_CHOICES: tuple[ProviderChoice, ...] = (
    ProviderChoice("openai", "OpenAI", "OPENAI_API_KEY", "nova",
                   "no extra key if you already use OpenAI"),
    ProviderChoice("gemini", "Google Gemini", "GEMINI_API_KEY", "Kore",
                   "Gemini TTS voices: Kore, Puck, Charon, ..."),
    ProviderChoice("cartesia", "Cartesia", "CARTESIA_API_KEY", None,
                   "lowest latency; needs a voice id from your account"),
    ProviderChoice("elevenlabs", "ElevenLabs", "ELEVENLABS_API_KEY", None,
                   "needs a voice id from your account"),
    ProviderChoice("deepgram", "Deepgram Aura", "DEEPGRAM_API_KEY",
                   "aura-2-thalia-en", "low-latency hosted voices"),
    ProviderChoice("piper", "Piper (local)", None, "en_US-lessac-medium",
                   "free, on-device, no key and no server"),
)
